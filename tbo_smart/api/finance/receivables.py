"""Receivables page and collection follow-ups (docs/crm-finance-workflow.md §7, Phase 2).

Outstanding and due dates come from the submitted Sales Invoices and their Payment Schedule
rows (ERPNext keeps `outstanding_amount` and each term's `outstanding` in step with the
payment ledger). Accounts, finance and management roles see every invoice; a salesperson
sees the invoices of their own (or their team's) deals.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, now_datetime, today

from tbo_smart.api.mobile.capabilities import get_capabilities
from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.finance import receivables_enabled
from tbo_smart.finance.access import (
	can_see_amounts,
	get_deal_owners_in_scope,
	is_finance_user,
	sees_all_receivables,
)
from tbo_smart.finance.reminders import ORDER
from tbo_smart.finance.stage import write_deal
from tbo_smart.sales.activities import CLOSED_TASK_STATUSES, OPEN

BUCKETS = (("not_due", None), ("0_30", 30), ("31_60", 60), ("61_90", 90), ("90_plus", None))
MAX_ROWS = 2000


def require_receivables() -> list[str] | None:
	"""Refuse unless Phase 2 is on and the user may see receivables; return the deal-owner scope."""
	if not receivables_enabled():
		frappe.throw(_("Receivables are not switched on."), frappe.ValidationError)
	if not (sees_all_receivables() or is_finance_user() or get_capabilities().crm):
		frappe.throw(_("You cannot see receivables."), frappe.PermissionError)
	if not can_see_amounts():
		frappe.throw(
			_("Receivable amounts are visible to managers and accounts only."), frappe.PermissionError
		)
	return None if is_finance_user() else get_deal_owners_in_scope()


def bucket_for(days_past_due: int) -> str:
	if days_past_due <= 0:
		return "not_due"
	for key, limit in BUCKETS[1:-1]:
		if days_past_due <= limit:
			return key
	return "90_plus"


@frappe.whitelist()
def get_receivables(
	company: str | None = None,
	customer: str | None = None,
	deal_owner: str | None = None,
	accounts_owner: str | None = None,
) -> dict:
	"""Open invoices with ageing by term due date (not due, 0-30, 31-60, 61-90, 90+ days past
	due), escalation level, last reminder, promise to pay and open exceptions; plus totals per
	customer and per currency."""
	owners = require_receivables()
	invoices = load_invoices(owners, company, customer, deal_owner, accounts_owner)
	names = [row.name for row in invoices]
	terms = load_terms(names)
	tasks = load_tasks(names)
	exceptions = load_open_exceptions(names)
	now = getdate(today())
	users = get_full_names(
		{row.deal_owner for row in invoices if row.deal_owner}
		| {row.accounts_owner for row in invoices if row.accounts_owner}
	)
	rows = []
	for invoice in invoices:
		rows.append(
			build_row(
				invoice,
				terms.get(invoice.name, []),
				tasks.get(invoice.name),
				exceptions.get(invoice.name, []),
				now,
				users,
			)
		)
	return {
		"rows": rows,
		"customers": summarize(rows, "customer"),
		"totals": summarize(rows, None),
		"today": now,
		"truncated": len(invoices) >= MAX_ROWS,
	}


def load_invoices(owners, company, customer, deal_owner, accounts_owner) -> list:
	Invoice = frappe.qb.DocType("Sales Invoice")
	Deal = frappe.qb.DocType("Opportunity")
	query = (
		frappe.qb.from_(Invoice)
		.left_join(Deal)
		.on(Invoice.opportunity == Deal.name)
		.select(
			Invoice.name,
			Invoice.customer,
			Invoice.customer_name,
			Invoice.company,
			Invoice.currency,
			Invoice.posting_date,
			Invoice.due_date,
			Invoice.grand_total,
			Invoice.rounded_total,
			Invoice.outstanding_amount,
			Invoice.opportunity,
			Deal.opportunity_owner.as_("deal_owner"),
			Deal.tbo_accounts_owner.as_("accounts_owner"),
		)
		.where((Invoice.docstatus == 1) & (Invoice.outstanding_amount > 0) & (Invoice.is_return == 0))
		.orderby(Invoice.due_date)
		.limit(MAX_ROWS)
	)
	if owners is not None:
		query = query.where(Deal.opportunity_owner.isin(owners or [""]))
	for field, value in (
		(Invoice.company, company),
		(Invoice.customer, customer),
		(Deal.opportunity_owner, deal_owner),
		(Deal.tbo_accounts_owner, accounts_owner),
	):
		if value:
			query = query.where(field == value)
	return query.run(as_dict=True)


def load_terms(names: list[str]) -> dict:
	if not names:
		return {}
	Term = frappe.qb.DocType("Payment Schedule")
	terms = {}
	for row in (
		frappe.qb.from_(Term)
		.select(
			Term.name,
			Term.parent,
			Term.payment_term,
			Term.due_date,
			Term.outstanding,
			Term.tbo_last_reminder,
			Term.tbo_last_reminder_on,
		)
		.where((Term.parenttype == "Sales Invoice") & Term.parent.isin(names))
		.orderby(Term.due_date)
	).run(as_dict=True):
		terms.setdefault(row.parent, []).append(row)
	return terms


def load_tasks(names: list[str]) -> dict:
	"""The open collection task of each invoice (newest if several)."""
	if not names:
		return {}
	rows = frappe.get_all(
		"ToDo",
		filters={
			"tbo_sales_invoice": ("in", names),
			"tbo_task_type": "Collection",
			"status": ("not in", CLOSED_TASK_STATUSES),
		},
		fields=[
			"name",
			"tbo_sales_invoice",
			"allocated_to as assigned_to",
			"date as due_date",
			"tbo_promised_date",
			"tbo_promised_amount",
			"tbo_escalation_level",
		],
		order_by="creation asc",
	)
	return {row.tbo_sales_invoice: row for row in rows}


def load_open_exceptions(names: list[str]) -> dict:
	if not names:
		return {}
	rows = frappe.get_all(
		"TBO Finance Exception",
		filters={
			"reference_doctype": "Sales Invoice",
			"reference_name": ("in", names),
			"status": ("in", ["Open", "Pending Approval", "Approved"]),
		},
		fields=[
			"name",
			"reference_name",
			"exception_type",
			"requested_action",
			"status",
			"pause_until",
			"new_due_date",
		],
	)
	result = {}
	for row in rows:
		result.setdefault(row.reference_name, []).append(row)
	return result


def build_row(invoice, terms: list, task, exceptions: list, now, users: dict) -> dict:
	buckets = {key: 0.0 for key, _limit in BUCKETS}
	if terms:
		for term in terms:
			if flt(term.outstanding) > 0:
				buckets[bucket_for(date_diff(now, term.due_date))] += flt(term.outstanding)
	else:
		buckets[bucket_for(date_diff(now, invoice.due_date or invoice.posting_date))] += flt(
			invoice.outstanding_amount
		)
	last = max(
		(term for term in terms if term.tbo_last_reminder in ORDER),
		key=lambda term: (ORDER.index(term.tbo_last_reminder), term.tbo_last_reminder_on or now),
		default=None,
	)
	overdue = sum(value for key, value in buckets.items() if key != "not_due")
	return {
		"invoice": invoice.name,
		"customer": invoice.customer,
		"customer_name": invoice.customer_name,
		"company": invoice.company,
		"currency": invoice.currency,
		"posting_date": invoice.posting_date,
		"due_date": invoice.due_date,
		"grand_total": flt(invoice.rounded_total or invoice.grand_total),
		"outstanding": flt(invoice.outstanding_amount),
		"overdue": flt(min(overdue, flt(invoice.outstanding_amount)), 2),
		"buckets": {key: flt(value, 2) for key, value in buckets.items()},
		"days_overdue": max(
			(date_diff(now, term.due_date) for term in terms if flt(term.outstanding) > 0), default=0
		),
		"opportunity": invoice.opportunity,
		"deal_owner": invoice.deal_owner,
		"deal_owner_name": users.get(invoice.deal_owner),
		"accounts_owner": invoice.accounts_owner,
		"accounts_owner_name": users.get(invoice.accounts_owner),
		"escalation_level": cint(task.tbo_escalation_level) if task else 0,
		"last_reminder": last.tbo_last_reminder if last else None,
		"last_reminder_on": last.tbo_last_reminder_on if last else None,
		"promised_date": task.tbo_promised_date if task else None,
		"promised_amount": flt(task.tbo_promised_amount) if task and task.tbo_promised_amount else None,
		"task": task.name if task else None,
		"exceptions": exceptions,
		"terms": [
			{
				"name": term.name,
				"payment_term": term.payment_term,
				"due_date": term.due_date,
				"outstanding": flt(term.outstanding),
			}
			for term in terms
		],
	}


def summarize(rows: list[dict], key: str | None) -> list[dict]:
	"""Totals per customer and currency (key="customer") or per currency (key=None)."""
	groups = {}
	for row in rows:
		group_key = (row[key] if key else None, row["currency"])
		group = groups.setdefault(
			group_key,
			{
				"customer": row["customer"] if key else None,
				"customer_name": row["customer_name"] if key else None,
				"currency": row["currency"],
				"invoices": 0,
				"outstanding": 0.0,
				"overdue": 0.0,
				"buckets": {bucket: 0.0 for bucket, _limit in BUCKETS},
			},
		)
		group["invoices"] += 1
		group["outstanding"] += row["outstanding"]
		group["overdue"] += row["overdue"]
		for bucket, value in row["buckets"].items():
			group["buckets"][bucket] += value
	result = []
	for group in groups.values():
		group["outstanding"] = flt(group["outstanding"], 2)
		group["overdue"] = flt(group["overdue"], 2)
		group["buckets"] = {bucket: flt(value, 2) for bucket, value in group["buckets"].items()}
		result.append(group)
	return sorted(result, key=lambda group: -group["overdue"])


def get_invoice_in_scope(invoice: str) -> frappe._dict:
	"""The invoice with its deal, refused when the user may not see it."""
	owners = require_receivables()
	row = frappe.db.get_value(
		"Sales Invoice",
		{"name": invoice, "docstatus": 1},
		["name", "customer", "customer_name", "company", "currency", "outstanding_amount", "opportunity"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Invoice {0} not found.").format(invoice), frappe.DoesNotExistError)
	row.deal_owner = (
		frappe.db.get_value("Opportunity", row.opportunity, "opportunity_owner") if row.opportunity else None
	)
	if owners is not None and row.deal_owner not in owners:
		frappe.throw(_("You cannot see this invoice."), frappe.PermissionError)
	return row


@frappe.whitelist(methods=["POST"])
def add_collection_follow_up(
	invoice: str,
	due_date: str,
	remark: str | None = None,
	promised_date: str | None = None,
	promised_amount: float | None = None,
) -> dict:
	"""Record a collection follow-up on an invoice: the next date, a remark and an optional
	promise to pay. Updates the invoice's open collection task, or creates one assigned to me.
	Versions keep each change (ToDo has track_changes on)."""
	row = get_invoice_in_scope(invoice)
	if not due_date:
		frappe.throw(_("Choose the next follow-up date."))
	if promised_date and getdate(promised_date) < getdate(today()):
		frappe.throw(_("The promised date cannot be in the past."))
	if promised_amount is not None and flt(promised_amount) < 0:
		frappe.throw(_("The promised amount cannot be negative."))
	name = frappe.db.get_value(
		"ToDo",
		{
			"tbo_sales_invoice": invoice,
			"tbo_task_type": "Collection",
			"status": ("not in", CLOSED_TASK_STATUSES),
		},
	)
	task = frappe.get_doc("ToDo", name) if name else frappe.new_doc("ToDo")
	if not name:
		task.update(
			{
				"description": frappe.utils.escape_html(
					_("Collect {0} ({1})").format(invoice, row.customer_name)
				),
				"tbo_task_type": "Collection",
				"tbo_sales_invoice": invoice,
				"allocated_to": frappe.session.user,
				"status": OPEN,
				"priority": "Medium",
				"reference_type": "Opportunity" if row.opportunity else "Sales Invoice",
				"reference_name": row.opportunity or invoice,
			}
		)
	task.date = due_date
	task.tbo_promised_date = promised_date or None
	task.tbo_promised_amount = flt(promised_amount) if promised_amount not in (None, "") else None
	if remark:
		stamp = now_datetime().strftime("%Y-%m-%d %H:%M")
		entry = f"<p><b>{stamp}, {frappe.utils.escape_html(frappe.session.user)}:</b> {frappe.utils.escape_html(remark)}</p>"
		task.description = (task.description or "") + entry
	task.flags.ignore_permissions = True
	task.save()
	return {"task": task.name, "due_date": task.date}


@frappe.whitelist(methods=["POST"])
def set_accounts_owner(deal: str, user: str | None = None) -> dict:
	"""Set who in Accounts owns collection on a deal (Q18). Finance roles and managers only."""
	if not receivables_enabled():
		frappe.throw(_("Receivables are not switched on."), frappe.ValidationError)
	if not (sees_all_receivables() or is_finance_user()):
		frappe.throw(_("Only accounts and managers can set the accounts owner."), frappe.PermissionError)
	if not frappe.db.exists("Opportunity", deal):
		frappe.throw(_("Deal {0} not found.").format(deal), frappe.DoesNotExistError)
	if user and not frappe.db.get_value("User", {"name": user, "enabled": 1}):
		frappe.throw(_("User {0} is not an enabled user.").format(user))
	write_deal(frappe.get_doc("Opportunity", deal), {"tbo_accounts_owner": user or None})
	return {"accounts_owner": user or None}
