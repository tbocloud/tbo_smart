"""Daily receivable reminders and escalation, per payment-term row (docs/crm-finance-workflow.md
§6.1, Phase 2).

ERPNext's Notification cannot read child-row due dates and Dunning is manual, so this job
reads every open Payment Schedule row of submitted invoices and runs, at most once per row,
the step its due date has reached:

| Step | When | Client email (setting) | Internal | Level |
| --- | --- | --- | --- | --- |
| D-3 | 3 days before due | "Payment due on" | - | 0 |
| D0 | on the due date | "Payment due today" | collection task for the accounts owner | 1 |
| D+7 | 7 days overdue | reminder | task escalated, accounts owner and deal owner notified | 2 |
| D+15 | 15 days overdue | - | draft Dunning "Reminder 2", sales head, accounts lead and the deal owner's manager notified | 3 |
| D+30 | 30 days overdue | - | draft Dunning "Final Notice", Finance Manager notified, in the digest | 4 |
| Weekly | every 7 days after D+30 | reminder (own setting) | task re-dated | 4 |

A missed day never sends a backlog: only the latest step that is due runs. The row's
`tbo_last_reminder` / `tbo_last_reminder_on` make the job idempotent. Approved receivable
exceptions apply: an extension moves the row's effective due date (the invoice is not
touched), a pause skips the row until its date, a waiver stops it.
"""

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import create_dunning
from frappe import _
from frappe.core.doctype.communication.email import make
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import cint, date_diff, fmt_money, formatdate, getdate, today

from tbo_smart.finance import get_setting, receivables_enabled
from tbo_smart.finance.setup import DUNNING_TYPES

# (step, days past due, escalation level), in order
STEPS = (("D-3", -3, 0), ("D0", 0, 1), ("D+7", 7, 2), ("D+15", 15, 3), ("D+30", 30, 4))
WEEKLY = "Weekly"
WEEKLY_EVERY = 7
ORDER = [step for step, _days, _level in STEPS] + [WEEKLY]
LEVELS = {step: level for step, _days, level in STEPS} | {WEEKLY: 4}
CLIENT_TEMPLATES = {
	"D-3": "TBO Payment Due Soon",
	"D0": "TBO Payment Due Today",
	"D+7": "TBO Payment Reminder",
	WEEKLY: "TBO Payment Reminder",
}
DUNNING_STEPS = {"D+15": "Reminder 2", "D+30": "Final Notice"}
OPEN_TASK = ("Done", "Canceled")


def due_step(days_past_due: int, last: str | None, last_on, on_date) -> str | None:
	"""The step a term `days_past_due` days after its due date should run today, or None.

	Only the latest step that is due runs, and never one the row has already passed."""
	reached = [step for step, days, _level in STEPS if days_past_due >= days]
	if not reached:
		return None
	target = reached[-1]
	if target == "D+30" and last in ("D+30", WEEKLY):
		if days_past_due < 30 + WEEKLY_EVERY:
			return None
		if last_on and date_diff(on_date, last_on) < WEEKLY_EVERY:
			return None
		return WEEKLY
	if last in ORDER and ORDER.index(last) >= ORDER.index(target):
		return None
	return target


class ReminderRun:
	def __init__(self, on_date=None):
		self.today = getdate(on_date or today())
		self.company = get_setting("invoicing_company")
		self.send_client = cint(get_setting("send_client_reminders"))
		self.send_weekly = cint(get_setting("weekly_client_reminders"))
		self.accounts_lead = get_setting("accounts_lead")
		self.done = []

	def run(self) -> list[dict]:
		rows = self.load_rows()
		exceptions = load_exceptions({row.invoice for row in rows}, self.today)
		deals = load_deals({row.crm_deal for row in rows if row.crm_deal})
		for row in rows:
			due = exceptions.extended.get(row.name) or exceptions.extended.get(row.invoice) or row.due_date
			if (
				row.invoice in exceptions.waived
				or row.name in exceptions.paused
				or row.invoice in exceptions.paused
			):
				continue
			step = due_step(
				date_diff(self.today, due), row.tbo_last_reminder, row.tbo_last_reminder_on, self.today
			)
			if not step:
				continue
			try:
				self.apply(row, step, getdate(due), deals.get(row.crm_deal))
			except Exception:
				frappe.log_error(title=_("TBO reminders: {0} failed for {1}").format(step, row.invoice))
		return self.done

	def load_rows(self) -> list:
		Term = frappe.qb.DocType("Payment Schedule")
		Invoice = frappe.qb.DocType("Sales Invoice")
		query = (
			frappe.qb.from_(Term)
			.join(Invoice)
			.on((Term.parent == Invoice.name) & (Term.parenttype == "Sales Invoice"))
			.select(
				Term.name,
				Term.parent.as_("invoice"),
				Term.due_date,
				Term.outstanding,
				Term.payment_term,
				Term.tbo_last_reminder,
				Term.tbo_last_reminder_on,
				Invoice.customer,
				Invoice.customer_name,
				Invoice.company,
				Invoice.currency,
				Invoice.contact_email,
				Invoice.crm_deal,
			)
			.where(
				(Invoice.docstatus == 1)
				& (Invoice.is_return == 0)
				& (Invoice.outstanding_amount > 0)
				& (Term.outstanding > 0)
				& Term.due_date.isnotnull()
			)
			.orderby(Term.due_date)
		)
		if self.company:
			query = query.where(Invoice.company == self.company)
		return query.run(as_dict=True)

	# ---------- one step ----------

	def apply(self, row, step: str, due, deal) -> None:
		level = LEVELS[step]
		owner = (deal and deal.tbo_accounts_owner) or self.accounts_lead or (deal and deal.deal_owner)
		if self.should_email_client(step):
			try:
				self.email_client(row, step, due, deal)
			except Exception:
				# a mail problem (no outgoing account, bad address) must not stop the internal steps
				frappe.log_error(title=_("TBO reminders: client email failed for {0}").format(row.invoice))
		if level >= 1 and owner:
			self.upsert_task(row, owner, level, deal)
		if step in DUNNING_STEPS:
			self.make_dunning(row.invoice, row.company, DUNNING_STEPS[step])
		self.notify_step(row, step, due, owner, deal)
		frappe.db.set_value(
			"Payment Schedule",
			row.name,
			{"tbo_last_reminder": step, "tbo_last_reminder_on": self.today},
			update_modified=False,
		)
		self.done.append({"invoice": row.invoice, "term": row.name, "step": step})

	def should_email_client(self, step: str) -> bool:
		if not self.send_client or step not in CLIENT_TEMPLATES:
			return False
		return step != WEEKLY or bool(self.send_weekly)

	def email_client(self, row, step: str, due, deal) -> None:
		recipient = row.contact_email or frappe.db.get_value("Customer", row.customer, "email_id")
		if not recipient:
			return
		template = frappe.get_doc("Email Template", CLIENT_TEMPLATES[step])
		context = {
			"customer_name": row.customer_name,
			"invoice": row.invoice,
			"amount": fmt_money(row.outstanding, currency=row.currency),
			"due_date": formatdate(due),
			"days_overdue": max(date_diff(self.today, due), 0),
			"company": row.company,
		}
		cc = frappe.db.get_value("User", deal.deal_owner, "email") if deal and deal.deal_owner else None
		sender = get_setting("reminder_sender")
		sent = make(
			doctype="Sales Invoice",
			name=row.invoice,
			subject=frappe.render_template(template.subject, context),
			content=frappe.render_template(template.response_html or template.response, context),
			recipients=recipient,
			cc=cc,
			sender=frappe.db.get_value("Email Account", sender, "email_id") if sender else None,
		)
		communication = frappe.get_doc("Communication", sent["name"])
		if deal:
			communication.add_link("CRM Deal", deal.name)
			communication.save(ignore_permissions=True)
		communication.send_email(
			print_format=frappe.get_meta("Sales Invoice").default_print_format or "Standard"
		)

	def upsert_task(self, row, owner: str, level: int, deal) -> None:
		name = frappe.db.get_value(
			"CRM Task",
			{
				"tbo_sales_invoice": row.invoice,
				"tbo_task_type": "Collection",
				"status": ("not in", OPEN_TASK),
			},
		)
		task = frappe.get_doc("CRM Task", name) if name else frappe.new_doc("CRM Task")
		task.update(
			{
				"title": _("Collect {0} ({1})").format(row.invoice, row.customer_name),
				"tbo_task_type": "Collection",
				"tbo_sales_invoice": row.invoice,
				"tbo_escalation_level": max(level, cint(task.get("tbo_escalation_level"))),
				"due_date": self.today,
				"priority": "High" if level >= 2 else "Medium",
			}
		)
		if not name:
			task.update(
				{
					"assigned_to": owner,
					"status": "Todo",
					"reference_doctype": "CRM Deal" if deal else None,
					"reference_docname": deal.name if deal else None,
				}
			)
		task.flags.ignore_permissions = True
		task.save()

	def make_dunning(self, invoice: str, company: str, kind: str) -> None:
		dunning = create_dunning(invoice, ignore_permissions=True)
		dunning.dunning_type = ensure_dunning_type(company, kind)
		dunning.update({"rate_of_interest": 0, "dunning_fee": 0})
		dunning.get_dunning_letter_text()
		dunning.insert(ignore_permissions=True)

	def notify_step(self, row, step: str, due, owner: str | None, deal) -> None:
		users = set()
		if step in ("D+7", "D+15", "D+30", WEEKLY):
			users |= {owner, deal and deal.deal_owner}
		if step == "D+15":
			users |= {get_setting("escalation_sales_head"), self.accounts_lead, get_sales_manager(deal)}
		if step == "D+30":
			users.add(get_setting("escalation_finance_manager"))
		users = {user for user in users if user}
		if not users:
			return
		subject = _("{0}: {1} {2} on {3} is {4} days overdue").format(
			step,
			row.customer_name,
			fmt_money(row.outstanding, currency=row.currency),
			row.invoice,
			max(date_diff(self.today, due), 0),
		)
		enqueue_create_notification(
			list(users),
			{
				"type": "Alert",
				"document_type": "Sales Invoice",
				"document_name": row.invoice,
				"subject": subject,
				"from_user": "Administrator",
			},
		)


def load_exceptions(invoices: set, on_date) -> frappe._dict:
	"""Approved receivable exceptions on these invoices: {extended: {term or invoice: date},
	paused: {term or invoice}, waived: {invoice}}."""
	result = frappe._dict(extended={}, paused=set(), waived=set())
	if not invoices:
		return result
	rows = frappe.get_all(
		"TBO Finance Exception",
		filters={
			"category": "Receivable",
			"status": "Approved",
			"reference_doctype": "Sales Invoice",
			"reference_name": ("in", list(invoices)),
		},
		fields=["reference_name", "payment_schedule", "requested_action", "new_due_date", "pause_until"],
		order_by="approved_on asc",
	)
	for row in rows:
		key = row.payment_schedule or row.reference_name
		if row.requested_action == "Extend due date" and row.new_due_date:
			result.extended[key] = row.new_due_date
		elif (
			row.requested_action == "Pause reminders"
			and row.pause_until
			and getdate(row.pause_until) >= getdate(on_date)
		):
			result.paused.add(key)
		elif row.requested_action == "Waive balance":
			result.waived.add(row.reference_name)
	return result


def load_deals(names: set) -> dict:
	if not names:
		return {}
	rows = frappe.get_all(
		"CRM Deal",
		filters={"name": ("in", list(names))},
		fields=["name", "deal_owner", "tbo_accounts_owner"],
	)
	return {row.name: row for row in rows}


def get_sales_manager(deal) -> str | None:
	"""The deal owner's manager in the CRM sales hierarchy, if any (the BDM)."""
	if not deal or not deal.deal_owner:
		return None
	parent = frappe.db.get_value("CRM Sales Hierarchy", {"user": deal.deal_owner}, "reports_to")
	return frappe.db.get_value("CRM Sales Hierarchy", parent, "user") if parent else None


def ensure_dunning_type(company: str, kind: str) -> str:
	"""The company's Dunning Type for `kind` ("Reminder 2", "Final Notice"), created with an
	English letter, no fee and no interest when missing."""
	name = frappe.db.get_value("Dunning Type", {"dunning_type": kind, "company": company})
	if name:
		return name
	doc = frappe.get_doc(
		{
			"doctype": "Dunning Type",
			"dunning_type": kind,
			"company": company,
			"dunning_fee": 0,
			"rate_of_interest": 0,
			"dunning_letter_text": [
				{
					"language": "en",
					"is_default_language": 1,
					"body_text": DUNNING_TYPES[kind],
					"closing_text": "",
				}
			],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def run_reminders() -> None:
	"""Scheduler entry point (daily)."""
	if receivables_enabled():
		ReminderRun().run()
