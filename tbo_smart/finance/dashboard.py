"""Finance cards of the sales dashboard (docs/crm-finance-workflow.md §8, Phase 5).

`get_cards` returns the cards each user may see, by role, through tbo_smart.api.dashboard (the
CRM dashboard they were made for went with the Frappe CRM app).

| Card | Who | Definition |
| --- | --- | --- |
| Quotations | Sales users | Submitted quotations of deals in the date range: count, net value |
| Invoiced | Sales users (amounts) | Submitted invoices (not returns) posted in the range |
| Collected | Sales users (amounts) | Customer receipts in the range, refunds subtracted |
| Outstanding | Sales users (amounts) | Outstanding on submitted invoices now |
| Overdue | Sales users (amounts) | Outstanding on payment terms due before today |
| Lead → deal | Sales users | Deals created from a lead in the range ÷ leads created in the range |
| Payable to India | Accounts, finance, management (Phase 3) | Open share on allocations |
| Transfers waiting | Accounts, finance, management (Phase 3) | Transfers sent, not confirmed |
| Bank lines to verify | Accounts, finance, management (Phase 4) | Matched lines not yet verified |
| Open exceptions | Accounts, finance, management (Phases 2-4) | Exceptions Open or Pending Approval |

`user` is the dashboard's user filter (a plain Sales User always gets their own): when set,
sales figures count only that user's deals, so a BDE sees only their own deals' figures.
Amounts are in the invoicing company's currency (TBO Smart Settings), or the default company's
when that is blank; with no company filter they would mix currencies, so the company filter
always applies.
"""

import functools

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Count, Sum
from frappe.utils import add_days, date_diff, flt, getdate, today

from tbo_smart.api.mobile.capabilities import get_capabilities
from tbo_smart.api.mobile.utils import percent
from tbo_smart.finance import (
	allocation_enabled,
	dashboard_enabled,
	exceptions_enabled,
	get_default_company,
	get_setting,
	reconciliation_enabled,
)
from tbo_smart.finance.access import can_see_allocations, can_see_amounts, is_finance_user
from tbo_smart.finance.allocation import allocation_totals

SALES_CARDS = (
	"tbo_quotations",
	"tbo_invoiced",
	"tbo_collected",
	"tbo_outstanding",
	"tbo_overdue",
	"tbo_conversion",
)
AMOUNT_CARDS = {"tbo_invoiced", "tbo_collected", "tbo_outstanding", "tbo_overdue"}


def card(fn):
	"""A dashboard card: nothing while Phase 5 is off, and no amount for a user who may not
	see amounts (Q17)."""

	@functools.wraps(fn)
	def wrapper(from_date=None, to_date=None, user=None):
		if not dashboard_enabled():
			return None
		if fn.__name__.removeprefix("get_") in AMOUNT_CARDS and not can_see_amounts():
			return None
		return fn(from_date, to_date, user)

	return wrapper


def company() -> str | None:
	return (
		get_setting("invoicing_company")
		or get_default_company()
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)


def company_currency() -> str | None:
	return frappe.get_cached_value("Company", company(), "default_currency") if company() else None


def currency_symbol() -> str:
	currency = company_currency()
	return (frappe.db.get_value("Currency", currency, "symbol") or currency or "") if currency else ""


def previous_window(from_date, to_date) -> tuple:
	days = max(date_diff(to_date, from_date), 0) + 1
	return add_days(from_date, -days), add_days(from_date, -1)


def delta(current: float, previous: float) -> float:
	return round((current - previous) / previous * 100, 1) if previous else 0


def owned(Deal, user: str | None):
	return Deal.opportunity_owner == user if user else Deal.name.isnotnull()


# ---------- sales, collections, receivables ----------


def quotation_totals(from_date, to_date, user):
	Quotation = frappe.qb.DocType("Quotation")
	Deal = frappe.qb.DocType("Opportunity")
	return (
		frappe.qb.from_(Quotation)
		.join(Deal)
		.on(Quotation.opportunity == Deal.name)
		.select(Count(Quotation.name), Sum(Quotation.base_net_total))
		.where(
			(Quotation.docstatus == 1)
			& (Quotation.company == company())
			& (Quotation.transaction_date[getdate(from_date) : getdate(to_date)])
			& owned(Deal, user)
		)
	).run()[0]


@card
def get_tbo_quotations(from_date, to_date, user=None) -> dict:
	count, value = quotation_totals(from_date, to_date, user)
	previous, _value = quotation_totals(*previous_window(from_date, to_date), user)
	return {
		"title": _("Quotations"),
		"tooltip": _("Submitted quotations of deals in the date range ({0} net)").format(
			frappe.utils.fmt_money(
				flt(value), currency=frappe.get_cached_value("Company", company(), "default_currency")
			)
		),
		"value": count or 0,
		"delta": delta(count or 0, previous or 0),
		"deltaSuffix": "%",
	}


def invoice_query(user):
	"""Submitted invoices of deals (the user's own deals when one is given)."""
	Invoice = frappe.qb.DocType("Sales Invoice")
	Deal = frappe.qb.DocType("Opportunity")
	query = (
		frappe.qb.from_(Invoice)
		.join(Deal)
		.on(Invoice.opportunity == Deal.name)
		.where(
			(Invoice.docstatus == 1)
			& (Invoice.is_return == 0)
			& (Invoice.company == company())
			& owned(Deal, user)
		)
	)
	return Invoice, query


def invoiced(from_date, to_date, user) -> float:
	Invoice, query = invoice_query(user)
	return flt(
		query.select(Sum(Invoice.base_grand_total))
		.where(Invoice.posting_date[getdate(from_date) : getdate(to_date)])
		.run()[0][0]
	)


@card
def get_tbo_invoiced(from_date, to_date, user=None) -> dict:
	value = invoiced(from_date, to_date, user)
	return {
		"title": _("Invoiced"),
		"tooltip": _("Submitted invoices posted in the date range, before returns"),
		"value": value,
		"prefix": currency_symbol(),
		"delta": delta(value, invoiced(*previous_window(from_date, to_date), user)),
		"deltaSuffix": "%",
	}


def collected(from_date, to_date, user) -> float:
	Entry = frappe.qb.DocType("Payment Entry")
	Deal = frappe.qb.DocType("Opportunity")
	signed = (
		Case().when(Entry.payment_type == "Pay", -Entry.base_paid_amount).else_(Entry.base_received_amount)
	)
	query = (
		frappe.qb.from_(Entry)
		.join(Deal)
		.on(Entry.opportunity == Deal.name)
		.select(Sum(signed))
		.where(
			(Entry.docstatus == 1)
			& (Entry.party_type == "Customer")
			& Entry.payment_type.isin(["Receive", "Pay"])
			& (Entry.company == company())
			& (Entry.posting_date[getdate(from_date) : getdate(to_date)])
			& owned(Deal, user)
		)
	)
	return flt(query.run()[0][0])


@card
def get_tbo_collected(from_date, to_date, user=None) -> dict:
	value = collected(from_date, to_date, user)
	return {
		"title": _("Collected"),
		"tooltip": _("Customer receipts in the date range, refunds subtracted"),
		"value": value,
		"prefix": currency_symbol(),
		"delta": delta(value, collected(*previous_window(from_date, to_date), user)),
		"deltaSuffix": "%",
	}


@card
def get_tbo_outstanding(from_date=None, to_date=None, user=None) -> dict:
	Invoice, query = invoice_query(user)
	# outstanding_amount is in the party account's currency: the company's, or the invoice's
	base = (
		Case()
		.when(Invoice.party_account_currency == company_currency(), Invoice.outstanding_amount)
		.else_(Invoice.outstanding_amount * Invoice.conversion_rate)
	)
	value = flt(query.select(Sum(base)).run()[0][0])
	return {
		"title": _("Outstanding"),
		"tooltip": _("Still to collect on submitted invoices, today"),
		"value": value,
		"prefix": currency_symbol(),
	}


@card
def get_tbo_overdue(from_date=None, to_date=None, user=None) -> dict:
	Invoice, query = invoice_query(user)
	Term = frappe.qb.DocType("Payment Schedule")
	value = flt(
		query.join(Term)
		.on((Term.parent == Invoice.name) & (Term.parenttype == "Sales Invoice"))
		.select(Sum(Term.base_outstanding))
		.where((Term.due_date < today()) & (Invoice.outstanding_amount > 0))
		.run()[0][0]
	)
	return {
		"title": _("Overdue"),
		"tooltip": _("Outstanding on payment terms due before today"),
		"value": value,
		"prefix": currency_symbol(),
		"negativeIsBetter": True,
	}


@card
def get_tbo_conversion(from_date, to_date, user=None) -> dict:
	Lead = frappe.qb.DocType("Lead")
	Deal = frappe.qb.DocType("Opportunity")
	window = (getdate(from_date), add_days(getdate(to_date), 1))
	leads = (
		frappe.qb.from_(Lead)
		.select(Count(Lead.name))
		.where(
			(Lead.creation >= window[0])
			& (Lead.creation < window[1])
			& ((Lead.lead_owner == user) if user else Lead.name.isnotnull())
		)
	).run()[0][0]
	deals = (
		frappe.qb.from_(Deal)
		.select(Count(Deal.name))
		.where(
			(Deal.creation >= window[0])
			& (Deal.creation < window[1])
			& (Deal.opportunity_from == "Lead")
			& owned(Deal, user)
		)
	).run()[0][0]
	return {
		"title": _("Lead → deal"),
		"tooltip": _(
			"Deals created from a lead in the date range, per lead created in it ({0} of {1})"
		).format(deals, leads),
		"value": percent(deals, leads),
		"suffix": "%",
	}


# ---------- allocation, transfers, reconciliation (accounts, finance, management) ----------


@card
def get_tbo_payable(from_date=None, to_date=None, user=None) -> dict | None:
	if not (allocation_enabled() and can_see_allocations()):
		return None
	# the Allocations page's "pending" in the company currency, the one allocations are kept in
	value = sum(
		row["pending"] for row in allocation_totals(company()) if row["currency"] == company_currency()
	)
	return {
		"title": _("Payable to partner"),
		"tooltip": _("India's share not yet transferred"),
		"value": flt(value, 2),
		"prefix": currency_symbol(),
	}


@card
def get_tbo_transfers_waiting(from_date=None, to_date=None, user=None) -> dict | None:
	if not (allocation_enabled() and can_see_allocations()):
		return None
	return {
		"title": _("Transfers waiting"),
		"tooltip": _("Transfers sent to India and not yet confirmed"),
		"value": frappe.db.count(
			"TBO Fund Transfer",
			{"docstatus": 1, "status": ("in", ["Sent", "Difference"]), "from_company": company()},
		),
	}


@card
def get_tbo_lines_to_verify(from_date=None, to_date=None, user=None) -> dict | None:
	if not (reconciliation_enabled() and can_see_allocations()):
		return None
	return {
		"title": _("Bank lines to verify"),
		"tooltip": _("Matched bank lines a second person has not verified yet"),
		"value": frappe.db.count(
			"Bank Transaction",
			{
				"docstatus": 1,
				"status": "Reconciled",
				"tbo_verification": ("!=", "Verified"),
				"company": company(),
			},
		),
	}


@card
def get_tbo_open_exceptions(from_date=None, to_date=None, user=None) -> dict | None:
	if not (exceptions_enabled() and can_see_allocations()):
		return None
	return {
		"title": _("Open exceptions"),
		"tooltip": _("Finance exceptions open or waiting for approval"),
		"value": frappe.db.count(
			"TBO Finance Exception",
			{"status": ("in", ["Open", "Pending Approval"]), "company": company()},
		),
	}


FINANCE_CARDS = ("tbo_payable", "tbo_transfers_waiting", "tbo_lines_to_verify", "tbo_open_exceptions")


def get_cards(from_date, to_date, user=None) -> list[dict]:
	"""The finance cards this user may see, with their data (tbo_smart.api.dashboard)."""
	if not dashboard_enabled():
		return []
	names = []
	if get_capabilities().crm or is_finance_user():
		show_amounts = can_see_amounts()
		names += [name for name in SALES_CARDS if show_amounts or name not in AMOUNT_CARDS]
	names += FINANCE_CARDS
	cards = []
	for name in names:
		data = globals()[f"get_{name}"](from_date, to_date, user)
		if data is not None:
			cards.append({"name": name, "type": "number_chart", "w": 4, "h": 3, "data": data})
	return cards
