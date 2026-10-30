"""The management-only Business overview (docs/hub-leads-api.md §2.12).

Company-wide figures for a meeting: the clients in the pipeline, the proposals given, the
onboardings (won clients and projects started) and the revenue that came in. Only System Manager
and the "TBO Management" role may ask; the figures are never narrowed to the caller's sales scope.
They build on the Leads overview (`LeadOverview`): the same stage-log based counts, so the two
screens agree. `department` narrows the lead side by the lead's department and the money side by
the lead behind each Sales Order (invoices and receipts that cannot be traced to a lead are
company-wide only).
"""

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Count, Date, IfNull, Sum
from frappe.utils import add_months, cint, flt, get_first_day, getdate

from tbo_smart.api.mobile.utils import percent
from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline.dashboard import company_currency
from tbo_smart.lead_pipeline.overview import LeadOverview
from tbo_smart.lead_pipeline.proposals import ACCEPTED, DECLINED
from tbo_smart.lead_pipeline.proposals import DOCTYPE as PROPOSAL
from tbo_smart.lead_pipeline.setup import MANAGEMENT_ROLE
from tbo_smart.lead_pipeline.statuses import CLOSED_WON, OPEN_STATUSES, STATUSES
from tbo_smart.sales import DEAL, LEAD_LINK

TREND_MONTHS = 6
TOP_CLIENTS = 5
NO_DEPARTMENT = "No department"
NOT_FROM_LEAD = "Not linked to a lead"


def can_view_business(user: str | None = None) -> bool:
	"""System Manager or the TBO Management role: the one place who sees the Business overview
	is decided."""
	roles = frappe.get_roles(user or frappe.session.user)
	return "System Manager" in roles or MANAGEMENT_ROLE in roles


def require_business() -> None:
	if not can_view_business():
		frappe.throw(_("Only management can see the Business overview."), frappe.PermissionError)


def month_start(date):
	return getdate(get_first_day(date))


def months_between(start, end) -> list:
	"""The first day of every month from `start`'s to `end`'s."""
	months, month = [], month_start(start)
	while month <= end:
		months.append(month)
		month = getdate(add_months(month, 1))
	return months


def by_month(rows, months: list) -> list[float]:
	"""Totals of (date, amount) rows per month in `months`."""
	totals = dict.fromkeys(months, 0.0)
	for date, amount in rows:
		key = month_start(getdate(date))
		if key in totals:
			totals[key] += flt(amount)
	return [flt(totals[month], 2) for month in months]


class BusinessOverview(LeadOverview):
	"""The Business figures for the company from `from_date` to `to_date`, optionally for one
	lead department."""

	def __init__(self, from_date=None, to_date=None, department=None):
		super().__init__(from_date, to_date, None)
		self.department = department or None
		self.company = frappe.defaults.get_global_default("company")
		self._orders = None

	def lead_criterion(self, Lead=None):
		Lead = Lead or self.Lead
		criterion = super().lead_criterion(Lead)
		if self.department:
			criterion &= Lead.tbo_department == self.department
		return criterion

	# ---------- the whole page ----------

	def build_business(self, include_profit: bool = False) -> dict:
		created = self.created_leads()
		months = months_between(self.from_date, self.to_date)
		invoices = self.invoices(self.from_date, self.end)
		money = self.collected_rows(self.from_date, self.end)
		revenue = {
			"months": [
				{"month": month, "invoiced": invoiced, "collected": collected}
				for month, invoiced, collected in zip(
					months,
					by_month([(row.posting_date, row.amount) for row in invoices], months),
					by_month(money, months),
					strict=True,
				)
			],
			"invoiced": flt(sum(row.amount for row in invoices), 2),
			"collected": flt(sum(flt(amount) for _date, amount in money), 2),
			"outstanding": self.outstanding(),
		}
		onboarding = self.onboarding()
		return {
			"from_date": self.from_date,
			"to_date": self.to_date,
			"department": self.department,
			"currency": company_currency(),
			"pipeline": self.open_pipeline(),
			"proposals": self.proposals(),
			"onboarding": onboarding,
			"revenue": revenue,
			"funnel": self.funnel(created),
			"new_leads": len(created),
			"meetings_held": self.meetings_completed()["total"],
			"trend": self.trend(),
			"top_clients": self.top_clients(invoices),
			"by_department": self.by_department(invoices),
			"profit": self.profit() if include_profit else self.no_profit(_("Not requested.")),
		}

	# ---------- the pipeline ----------

	def open_pipeline(self) -> dict:
		"""Open leads by stage now, with the value of their latest quotations. No lead stage has
		a probability, so the value is not weighted (`weighted`: false)."""
		Lead, Quotation = self.Lead, self.Quotation
		latest = (
			frappe.qb.from_(Quotation)
			.select(Quotation.tbo_lead, Sum(Quotation.base_grand_total).as_("value"))
			.where(
				(Quotation.docstatus != 2) & (Quotation.tbo_is_latest == 1) & Quotation.tbo_lead.isnotnull()
			)
			.groupby(Quotation.tbo_lead)
		)
		priced = Case().when(IfNull(latest.value, 0) > 0, 1).else_(0)
		rows = {
			stage: (cint(count), flt(value, 2), cint(with_value))
			for stage, count, value, with_value in (
				frappe.qb.from_(Lead)
				.left_join(latest)
				.on(latest.tbo_lead == Lead.name)
				.select(Lead.tbo_stage, Count(Lead.name), Sum(IfNull(latest.value, 0)), Sum(priced))
				.where(self.lead_criterion() & Lead.tbo_stage.isin(OPEN_STATUSES))
				.groupby(Lead.tbo_stage)
			).run()
		}
		stages = [
			{
				"stage": name,
				"color": color,
				"count": rows.get(name, (0, 0.0, 0))[0],
				"value": rows.get(name, (0, 0.0, 0))[1],
			}
			for name, kind, color in STATUSES
			if name in OPEN_STATUSES
		]
		return {
			"count": sum(row["count"] for row in stages),
			"value": flt(sum(row["value"] for row in stages), 2),
			"unpriced": sum(count - with_value for count, _value, with_value in rows.values()),
			"weighted": False,
			"stages": stages,
		}

	# ---------- proposals and onboarding ----------

	def decided_leads(self, status: str) -> set:
		"""The leads whose proposal or quotation document was accepted / declined in the range."""
		Proposal, Lead = frappe.qb.DocType(PROPOSAL), self.Lead
		return set(
			frappe.qb.from_(Proposal)
			.join(Lead)
			.on(Lead.name == Proposal.lead)
			.select(Proposal.lead)
			.where(
				Proposal.document_type.isin((kinds.PROPOSAL, kinds.QUOTATION))
				& (Proposal.status == status)
				& self.in_range(Proposal.decided_on)
				& self.lead_criterion()
			)
			.run(pluck=True)
		)

	def proposals(self) -> dict:
		"""Proposals given in the range (leads a proposal went to, each once), those accepted in
		the range, and accepted of given (a period ratio, not a cohort)."""
		sent, accepted = self.sent_leads(), self.decided_leads(ACCEPTED)
		sent_values, accepted_values = self.lead_values(list(sent)), self.lead_values(list(accepted))
		return {
			"sent": {
				"count": len(sent),
				"value": flt(sum(sent_values.values()), 2),
				"unpriced": sum(1 for value in sent_values.values() if not value),
			},
			"accepted": {
				"count": len(accepted),
				"value": flt(sum(accepted_values.values()), 2),
			},
			"declined": len(self.decided_leads(DECLINED)),
			"acceptance_rate": percent(len(accepted), len(sent)),
		}

	def onboarding(self) -> dict:
		"""Clients won in the range (entered Closed – Won) with their value, and projects started:
		the hub projects linked on a client document changed in the range (the link has no date of
		its own, so the document's last change stands for it)."""
		won = self.closed_rows("Won")
		values = self.lead_values([row.name for row in won])
		Proposal, Lead = frappe.qb.DocType(PROPOSAL), self.Lead
		projects = set(
			frappe.qb.from_(Proposal)
			.join(Lead)
			.on(Lead.name == Proposal.lead)
			.select(Proposal.hub_project)
			.where(
				(IfNull(Proposal.hub_project, "") != "")
				& self.in_range(Proposal.modified)
				& self.lead_criterion()
			)
			.run(pluck=True)
		)
		return {
			"won": {
				"count": len(won),
				"value": flt(sum(values.values()), 2),
				"unpriced": sum(1 for value in values.values() if not value),
			},
			"projects_started": len(projects),
		}

	# ---------- revenue ----------

	def order_departments(self) -> dict:
		"""{Sales Order: department of the lead behind it ("" when the lead has none)}, through the
		deal made from the lead or a line from the lead's quotation."""
		if self._orders is None:
			Order, Item = frappe.qb.DocType("Sales Order"), frappe.qb.DocType("Sales Order Item")
			Deal, Quotation, Lead = frappe.qb.DocType(DEAL), self.Quotation, self.Lead
			found = {}
			for order, department in (
				frappe.qb.from_(Item)
				.join(Quotation)
				.on(Quotation.name == Item.prevdoc_docname)
				.join(Lead)
				.on(Lead.name == Quotation[LEAD_LINK])
				.select(Item.parent, IfNull(Lead.tbo_department, ""))
				.distinct()
				.where(Item.parenttype == "Sales Order")
			).run():
				found[order] = department
			for order, department in (
				frappe.qb.from_(Order)
				.join(Deal)
				.on(Deal.name == Order.opportunity)
				.join(Lead)
				.on(Lead.name == Deal[LEAD_LINK])
				.select(Order.name, IfNull(Lead.tbo_department, ""))
				.where(Order.docstatus == 1)
			).run():
				found[order] = department
			self._orders = found
		return self._orders

	def invoice_departments(self, invoices: list[str]) -> dict:
		"""{Sales Invoice: department ("" none) of the lead behind it}; invoices that cannot be
		traced to a lead are left out."""
		if not invoices:
			return {}
		Item = frappe.qb.DocType("Sales Invoice Item")
		orders, found = self.order_departments(), {}
		for invoice, order in (
			frappe.qb.from_(Item)
			.select(Item.parent, Item.sales_order)
			.where(Item.parent.isin(invoices) & (IfNull(Item.sales_order, "") != ""))
		).run():
			if order in orders:
				found.setdefault(invoice, orders[order])
		return found

	def invoices(self, start, end) -> list:
		"""The submitted Sales Invoices posted from `start` to before `end`, returns included
		(negative): [{name, customer, posting_date, amount, department}], in the company
		currency. With a department set, only the invoices traced to it."""
		Invoice = frappe.qb.DocType("Sales Invoice")
		rows = (
			frappe.qb.from_(Invoice)
			.select(
				Invoice.name,
				Invoice.customer,
				Invoice.customer_name,
				Invoice.posting_date,
				Invoice.base_grand_total.as_("amount"),
			)
			.where(
				(Invoice.docstatus == 1)
				& (Invoice.company == self.company)
				& (Invoice.posting_date >= start)
				& (Invoice.posting_date < end)
			)
		).run(as_dict=True)
		departments = self.invoice_departments([row.name for row in rows])
		result = []
		for row in rows:
			row.department = departments.get(row.name)
			if self.department and row.department != self.department:
				continue
			result.append(row)
		return result

	def collected_rows(self, start, end) -> list:
		"""[(posting date, amount)] of customer receipts (Payment Entry, Receive, submitted) from
		`start` to before `end`, in the company currency. With a department set, what was
		allocated to its Sales Orders and Invoices."""
		Entry = frappe.qb.DocType("Payment Entry")
		base = (
			(Entry.docstatus == 1)
			& (Entry.company == self.company)
			& (Entry.payment_type == "Receive")
			& (Entry.party_type == "Customer")
			& (Entry.posting_date >= start)
			& (Entry.posting_date < end)
		)
		if not self.department:
			return (
				frappe.qb.from_(Entry)
				.select(Entry.posting_date, Sum(Entry.base_received_amount))
				.where(base)
				.groupby(Entry.posting_date)
			).run()
		return [
			(date, amount)
			for date, amount, department in self.allocations(base)
			if department == self.department
		]

	def allocations(self, criterion) -> list:
		"""[(posting date, amount, department or None)] of the receipts matching `criterion`,
		allocated to Sales Orders and Invoices (None: not traced to a lead)."""
		Entry = frappe.qb.DocType("Payment Entry")
		Reference = frappe.qb.DocType("Payment Entry Reference")
		rows = (
			frappe.qb.from_(Reference)
			.join(Entry)
			.on(Entry.name == Reference.parent)
			.select(
				Entry.posting_date,
				Reference.reference_doctype,
				Reference.reference_name,
				Reference.allocated_amount,
				Entry.source_exchange_rate,
			)
			.where(
				criterion
				& (Reference.parenttype == "Payment Entry")
				& Reference.reference_doctype.isin(("Sales Order", "Sales Invoice"))
			)
		).run()
		orders = self.order_departments()
		invoice_departments = self.invoice_departments(
			[name for _d, kind, name, _a, _r in rows if kind == "Sales Invoice"]
		)
		return [
			(
				date,
				flt(amount) * (flt(rate) or 1),
				orders.get(name) if kind == "Sales Order" else invoice_departments.get(name),
			)
			for date, kind, name, amount, rate in rows
		]

	def outstanding(self) -> float:
		"""What customers still owe now: submitted Sales Invoices' outstanding amounts (credit
		notes net off), in the company currency."""
		Invoice = frappe.qb.DocType("Sales Invoice")
		rows = (
			frappe.qb.from_(Invoice)
			.select(Invoice.name, Invoice.outstanding_amount * IfNull(Invoice.conversion_rate, 1))
			.where(
				(Invoice.docstatus == 1)
				& (Invoice.company == self.company)
				& (Invoice.outstanding_amount != 0)
			)
		).run()
		if self.department:
			departments = self.invoice_departments([name for name, _amount in rows])
			rows = [(name, amount) for name, amount in rows if departments.get(name) == self.department]
		return flt(sum(flt(amount) for _name, amount in rows), 2)

	# ---------- trend, clients, departments ----------

	def trend(self) -> list[dict]:
		"""The last six months up to the range's end: new leads, won, invoiced, collected."""
		months = months_between(add_months(month_start(self.to_date), 1 - TREND_MONTHS), self.to_date)
		start = months[0]
		Lead, Log = self.Lead, frappe.qb.DocType("TBO Stage Change")
		created = (
			frappe.qb.from_(Lead)
			.select(Date(Lead.creation), Count(Lead.name))
			.where(self.lead_criterion() & (Lead.creation >= start) & (Lead.creation < self.end))
			.groupby(Date(Lead.creation))
		).run()
		won, seen = [], set()
		for lead, date in (
			frappe.qb.from_(Log)
			.join(Lead)
			.on(Lead.name == Log.parent)
			.select(Log.parent, Log.changed_on)
			.where(
				(Log.parenttype == "Lead")
				& (Log.stage == CLOSED_WON)
				& (Log.changed_on >= start)
				& (Log.changed_on < self.end)
				& self.lead_criterion()
			)
			.orderby(Log.changed_on)
		).run():
			if lead not in seen:
				seen.add(lead)
				won.append((date, 1))
		invoiced = by_month(
			[(row.posting_date, row.amount) for row in self.invoices(start, self.end)], months
		)
		collected = by_month(self.collected_rows(start, self.end), months)
		leads = by_month(created, months)
		wins = by_month(won, months)
		return [
			{
				"month": month,
				"new": cint(leads[index]),
				"won": cint(wins[index]),
				"invoiced": invoiced[index],
				"collected": collected[index],
			}
			for index, month in enumerate(months)
		]

	@staticmethod
	def top_clients(invoices: list) -> list[dict]:
		"""The five clients invoiced the most in the range."""
		totals: dict[str, dict] = {}
		for row in invoices:
			entry = totals.setdefault(
				row.customer,
				{"customer": row.customer, "name": row.customer_name or row.customer, "invoiced": 0.0},
			)
			entry["invoiced"] += flt(row.amount)
		ranked = sorted(totals.values(), key=lambda row: -row["invoiced"])
		return [
			{**row, "invoiced": flt(row["invoiced"], 2)}
			for row in ranked[:TOP_CLIENTS]
			if row["invoiced"] > 0
		]

	def by_department(self, invoices: list) -> list[dict]:
		"""Per lead department: open leads and their value now, won in the range and its value,
		invoiced and collected in the range. What cannot be traced to a lead is "Not linked to a
		lead" (department None, label that). The department filter, when set, leaves one row."""
		rows: dict[str, dict] = {}

		def row(key):
			return rows.setdefault(
				key,
				{
					"department": key or None,
					"label": NOT_FROM_LEAD if key is None else (key or NO_DEPARTMENT),
					"open": 0,
					"open_value": 0.0,
					"won": 0,
					"won_value": 0.0,
					"invoiced": 0.0,
					"collected": 0.0,
				},
			)

		Lead, Quotation = self.Lead, self.Quotation
		latest = (
			frappe.qb.from_(Quotation)
			.select(Quotation.tbo_lead, Sum(Quotation.base_grand_total).as_("value"))
			.where(
				(Quotation.docstatus != 2) & (Quotation.tbo_is_latest == 1) & Quotation.tbo_lead.isnotnull()
			)
			.groupby(Quotation.tbo_lead)
		)
		for department, count, value in (
			frappe.qb.from_(Lead)
			.left_join(latest)
			.on(latest.tbo_lead == Lead.name)
			.select(IfNull(Lead.tbo_department, ""), Count(Lead.name), Sum(IfNull(latest.value, 0)))
			.where(self.lead_criterion() & Lead.tbo_stage.isin(OPEN_STATUSES))
			.groupby(IfNull(Lead.tbo_department, ""))
		).run():
			entry = row(department)
			entry["open"], entry["open_value"] = cint(count), flt(value, 2)
		won = self.closed_rows("Won")
		if won:
			values = self.lead_values([lead.name for lead in won])
			departments = dict(
				frappe.qb.from_(Lead)
				.select(Lead.name, IfNull(Lead.tbo_department, ""))
				.where(Lead.name.isin([lead.name for lead in won]))
				.run()
			)
			for lead in won:
				entry = row(departments.get(lead.name, ""))
				entry["won"] += 1
				entry["won_value"] = flt(entry["won_value"] + values.get(lead.name, 0), 2)
		for invoice in invoices:
			row(invoice.department)["invoiced"] += flt(invoice.amount)
		Entry = frappe.qb.DocType("Payment Entry")
		for _date, amount, department in self.allocations(
			(Entry.docstatus == 1)
			& (Entry.company == self.company)
			& (Entry.payment_type == "Receive")
			& (Entry.party_type == "Customer")
			& self.in_range(Entry.posting_date)
		):
			row(department)["collected"] += amount
		result = [
			{**entry, "invoiced": flt(entry["invoiced"], 2), "collected": flt(entry["collected"], 2)}
			for entry in rows.values()
		]
		if self.department:
			result = [entry for entry in result if entry["department"] == self.department]
		return sorted(result, key=lambda entry: (-entry["invoiced"], -entry["open_value"], entry["label"]))

	# ---------- profit ----------

	@staticmethod
	def no_profit(reason: str) -> dict:
		return {"available": False, "reason": reason, "income": None, "expense": None, "profit": None}

	def profit(self) -> dict:
		"""Income minus expense from the general ledger for the range (cancelled entries and period
		closing vouchers left out). Company-wide only."""
		if self.department:
			return self.no_profit(_("Profit is company-wide; it is not split by department."))
		GL, Account = frappe.qb.DocType("GL Entry"), frappe.qb.DocType("Account")
		totals = {"Income": 0.0, "Expense": 0.0}
		for root, debit, credit in (
			frappe.qb.from_(GL)
			.join(Account)
			.on(Account.name == GL.account)
			.select(Account.root_type, Sum(GL.debit), Sum(GL.credit))
			.where(
				(GL.is_cancelled == 0)
				& (GL.company == self.company)
				& (GL.voucher_type != "Period Closing Voucher")
				& self.in_range(GL.posting_date)
				& Account.root_type.isin(tuple(totals))
			)
			.groupby(Account.root_type)
		).run():
			totals[root] = flt(credit) - flt(debit) if root == "Income" else flt(debit) - flt(credit)
		return {
			"available": True,
			"reason": None,
			"income": flt(totals["Income"], 2),
			"expense": flt(totals["Expense"], 2),
			"profit": flt(totals["Income"] - totals["Expense"], 2),
		}


def build_business(from_date, to_date, department=None, include_profit=False) -> dict:
	"""The Business overview for the session user, who must be management."""
	require_business()
	if department and not frappe.db.exists("TBO Lead Department", department):
		frappe.throw(_("{0} is not a lead department.").format(department), frappe.ValidationError)
	return BusinessOverview(from_date, to_date, department).build_business(include_profit)
