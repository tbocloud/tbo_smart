"""BDE conversion report: leads → deals → quotations → won, per salesperson
(docs/crm-finance-workflow.md §8, "Performance / BDE conversion").

Each column counts its own records in the date range, as the plan's metric table defines them:

- leads: CRM Leads created in the range, by lead owner;
- deals: CRM Deals created from a lead in the range, by deal owner;
- quotations: submitted Quotations of a deal, by quotation date, by the deal's owner;
- won: deals in a Won stage closed in the range, with their value in the CRM base currency
  (deal_value x exchange_rate, as Frappe CRM's dashboard counts it).

Who appears follows the caller's CRM scope (tbo_smart.api.mobile.capabilities): a salesperson
sees their own row, a manager in the sales hierarchy sees their team, a Sales Manager outside
it, a System Manager and the finance roles see everyone.
"""

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Count, IfNull, Sum
from frappe.utils import add_days, flt, get_first_day, getdate, today
from pypika.terms import EmptyCriterion

from tbo_smart.api.mobile.capabilities import get_capabilities, scope_users
from tbo_smart.api.mobile.crm import deal_value, get_crm_currency, owned_by
from tbo_smart.api.mobile.utils import get_full_names, percent
from tbo_smart.finance import finance_enabled, get_default_company
from tbo_smart.finance.access import is_finance_user

MAX_RANGE_DAYS = 731


@frappe.whitelist()
def get_bde_conversion(
	from_date: str | None = None,
	to_date: str | None = None,
	organization: str | None = None,
	company: str | None = None,
) -> dict:
	"""Rows per salesperson and a total row. `organization` is a CRM Organization (the client);
	`company` the invoicing company (a deal's `tbo_company`, or the CRM default company when
	blank; a quotation's company). Leads have no company, so `company` does not filter them."""
	if not finance_enabled():
		frappe.throw(_("The finance workflow is not switched on."), frappe.ValidationError)
	owners = get_report_owners()
	from_date = getdate(from_date or get_first_day(today()))
	to_date = getdate(to_date or today())
	if to_date < from_date:
		frappe.throw(_("To date must be on or after from date."))
	if (to_date - from_date).days > MAX_RANGE_DAYS:
		frappe.throw(_("Choose a range of at most two years."))
	window = (from_date, add_days(to_date, 1))
	counts = {}
	merge(counts, "leads", count_leads(window, owners, organization))
	merge(counts, "deals", count_deals(window, owners, organization, company))
	merge(counts, "quotations", count_quotations(window, owners, organization, company))
	for owner, won, value in count_won(window, owners, organization, company):
		row = counts.setdefault(owner, empty_row())
		row["won"], row["won_value"] = won, flt(value, 2)
	names = get_full_names(set(counts))
	rows = sorted(
		(
			{"user": owner, "full_name": names.get(owner, owner), **finish(row)}
			for owner, row in counts.items()
			if owner
		),
		key=lambda row: (-row["won_value"], -row["deals"], row["full_name"]),
	)
	total = empty_row()
	for row in rows:
		for key in total:
			total[key] += row[key]
	return {
		"from_date": from_date,
		"to_date": to_date,
		"currency": get_crm_currency(),
		"rows": rows,
		"total": finish(total),
	}


def get_report_owners() -> list[str] | None:
	"""Owners the caller may see; None means everyone."""
	if is_finance_user():
		return None
	capabilities = get_capabilities()
	if not capabilities.crm:
		frappe.throw(_("Only CRM users can see the BDE conversion report."), frappe.PermissionError)
	scope = capabilities.crm_scopes[-1]
	return scope_users(scope)


def empty_row() -> dict:
	return {"leads": 0, "deals": 0, "quotations": 0, "won": 0, "won_value": 0.0}


def merge(counts: dict, key: str, rows) -> None:
	for owner, count in rows:
		counts.setdefault(owner, empty_row())[key] = count


def finish(row: dict) -> dict:
	return {
		**row,
		"won_value": flt(row["won_value"], 2),
		"conversion_rate": percent(row["deals"], row["leads"]),
	}


def in_window(field, window) -> object:
	return (field >= window[0]) & (field < window[1])


def deal_company(Deal):
	"""A deal's invoicing company, the CRM default when the deal has none."""
	return (
		Case().when(IfNull(Deal.tbo_company, "") == "", get_default_company() or "").else_(Deal.tbo_company)
	)


def count_leads(window, owners, organization):
	Lead = frappe.qb.DocType("CRM Lead")
	return (
		frappe.qb.from_(Lead)
		.select(Lead.lead_owner, Count(Lead.name))
		.where(
			in_window(Lead.creation, window)
			& owned_by(Lead.lead_owner, owners)
			& ((Lead.organization == organization) if organization else EmptyCriterion())
		)
		.groupby(Lead.lead_owner)
	).run()


def deal_filters(Deal, owners, organization, company):
	return (
		owned_by(Deal.deal_owner, owners)
		& ((Deal.organization == organization) if organization else EmptyCriterion())
		& ((deal_company(Deal) == company) if company else EmptyCriterion())
	)


def count_deals(window, owners, organization, company):
	Deal = frappe.qb.DocType("CRM Deal")
	return (
		frappe.qb.from_(Deal)
		.select(Deal.deal_owner, Count(Deal.name))
		.where(
			in_window(Deal.creation, window)
			& Deal.lead.isnotnull()
			& (Deal.lead != "")
			& deal_filters(Deal, owners, organization, company)
		)
		.groupby(Deal.deal_owner)
	).run()


def count_quotations(window, owners, organization, company):
	Deal = frappe.qb.DocType("CRM Deal")
	Quotation = frappe.qb.DocType("Quotation")
	return (
		frappe.qb.from_(Quotation)
		.join(Deal)
		.on(Quotation.crm_deal == Deal.name)
		.select(Deal.deal_owner, Count(Quotation.name))
		.where(
			(Quotation.docstatus == 1)
			& in_window(Quotation.transaction_date, window)
			& owned_by(Deal.deal_owner, owners)
			& ((Deal.organization == organization) if organization else EmptyCriterion())
			& ((Quotation.company == company) if company else EmptyCriterion())
		)
		.groupby(Deal.deal_owner)
	).run()


def count_won(window, owners, organization, company):
	Deal = frappe.qb.DocType("CRM Deal")
	Status = frappe.qb.DocType("CRM Deal Status")
	return (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(Deal.deal_owner, Count(Deal.name), Sum(deal_value(Deal)))
		.where(
			(Status.type == "Won")
			& in_window(Deal.closed_date, window)
			& deal_filters(Deal, owners, organization, company)
		)
		.groupby(Deal.deal_owner)
	).run()
