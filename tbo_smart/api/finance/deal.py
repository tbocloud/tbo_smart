"""A deal's Finance view (docs/crm-finance-workflow.md §7). The deal is an Opportunity.

Every method acts as the session user. Access to a deal's finance data follows access to the
deal itself (tbo_smart.finance.access); ERPNext document permissions are not required, so a
salesperson sees the invoices and payments of their own deals without ERPNext read access.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_url_to_form

from tbo_smart.api.finance.allocations import can_see_allocations, get_deal_allocation
from tbo_smart.finance import (
	allocation_enabled,
	finance_enabled,
	get_default_company,
	receivables_enabled,
	reconciliation_enabled,
)
from tbo_smart.finance.access import (
	can_close,
	can_see_amounts,
	can_write_deal,
	is_finance_user,
	require_deal,
	sees_all_receivables,
)
from tbo_smart.finance.deal_finance import DealFinance
from tbo_smart.finance.stage import closable_stage, compute_stage, update_stage, write_deal
from tbo_smart.sales.activities import CLOSED_TASK_STATUSES, task_title
from tbo_smart.sales.opportunity import customer_of
from tbo_smart.tbo_smart.doctype.tbo_finance_exception.tbo_finance_exception import is_approver

# Stages the tab shows in Phase 1, in order. Settling and Reconciled join with Phases 3 and 4.
PHASE_STAGES = ["Onboarding", "Quotation", "Invoiced", "Collecting", "Collected", "Closed"]
SETTLING_STAGES = [
	"Onboarding",
	"Quotation",
	"Invoiced",
	"Collecting",
	"Collected",
	"Settling",
	"Reconciled",
	"Closed",
]
AMOUNT_FIELDS = {
	"orders": ("grand_total", "advance_paid"),
	"invoices": ("grand_total", "rounded_total", "outstanding_amount", "overdue"),
	"terms": ("payment_amount", "paid_amount", "outstanding"),
	"payments": ("amount",),
	"follow_ups": ("tbo_promised_amount",),
}
TASK_LIMIT = 20


@frappe.whitelist()
def get_finance_access() -> dict:
	"""What the screens should show: the Finance view and report only when the workflow is on,
	the Receivables and Exceptions pages only when Phase 2 is on too."""
	enabled = finance_enabled()
	receivables = enabled and receivables_enabled()
	return {
		"enabled": enabled,
		"receivables": receivables,
		"is_finance_user": enabled and is_finance_user(),
		"can_close": enabled and can_close(),
		"can_see_amounts": enabled and can_see_amounts(),
		"sees_all_receivables": receivables and (sees_all_receivables() or is_finance_user()),
		"is_approver": receivables and is_approver(),
		"allocations": enabled and allocation_enabled() and can_see_allocations(),
		"reconciliation": enabled and reconciliation_enabled() and can_see_allocations(),
	}


@frappe.whitelist()
def get_deal_finance(deal: str) -> dict:
	"""Everything the Finance view shows for one deal. Amounts on orders, invoices and payments
	are None when the viewer may not see them (setting "BDE can see invoice/payment amounts")."""
	require_deal(deal)
	doc = frappe.get_doc("Opportunity", deal)
	finance = DealFinance(deal)
	show_amounts = can_see_amounts()
	overdue = finance.overdue_by_invoice()
	invoices = [
		{
			**row,
			"overdue": overdue.get(row.name, 0),
			"terms": finance.terms.get(row.name, []),
			"url": url("Sales Invoice", row.name),
		}
		for row in finance.invoices
	]
	data = {
		"deal": deal,
		"stage": doc.tbo_finance_stage or compute_stage(doc, finance),
		"stages": SETTLING_STAGES if reconciliation_enabled() else PHASE_STAGES,
		"closable_stage": closable_stage(),
		"company": doc.get("company") or get_default_company(),
		"customer": customer_of(doc),
		"onboarding_complete": cint(doc.get("tbo_onboarding_complete")),
		"closed": cint(doc.get("tbo_closed")),
		"can_set_onboarding": can_write_deal(deal) or is_finance_user(),
		"can_close": can_close(),
		"amounts_visible": show_amounts,
		"quotations": [{**row, "url": url("Quotation", row.name)} for row in finance.quotations],
		"orders": [{**row, "url": url("Sales Order", row.name)} for row in finance.orders],
		"invoices": invoices,
		"payments": [{**row, "url": url("Payment Entry", row.name)} for row in finance.payments],
		"totals": finance.totals() if show_amounts else [],
		"follow_ups": get_follow_ups(deal),
		"receivables": receivables_enabled(),
		"accounts_owner": doc.get("tbo_accounts_owner"),
		"can_set_accounts_owner": receivables_enabled() and (sees_all_receivables() or is_finance_user()),
		"exceptions": get_deal_exceptions(deal) if receivables_enabled() else [],
		"allocation": get_deal_allocation(deal),
	}
	if not show_amounts:
		hide_amounts(data)
	return data


def url(doctype: str, name: str) -> str:
	return get_url_to_form(doctype, name)


def hide_amounts(data: dict) -> None:
	for key, fields in AMOUNT_FIELDS.items():
		rows = (
			[term for invoice in data["invoices"] for term in invoice["terms"]]
			if key == "terms"
			else data[key]
		)
		for row in rows:
			for field in fields:
				row[field] = None


def get_follow_ups(deal: str) -> list[dict]:
	"""Open ToDos on the deal, earliest due first (the next follow-up is the first)."""
	ToDo = frappe.qb.DocType("ToDo")
	rows = (
		frappe.qb.from_(ToDo)
		.select(
			ToDo.name,
			ToDo.description,
			ToDo.status,
			ToDo.priority,
			ToDo.date.as_("due_date"),
			ToDo.allocated_to.as_("assigned_to"),
			ToDo.tbo_task_type,
			ToDo.tbo_sales_invoice,
			ToDo.tbo_promised_date,
			ToDo.tbo_promised_amount,
			ToDo.tbo_escalation_level,
		)
		.where(
			(ToDo.reference_type == "Opportunity")
			& (ToDo.reference_name == deal)
			& ToDo.status.notin(CLOSED_TASK_STATUSES)
		)
		.orderby(ToDo.date.isnull())
		.orderby(ToDo.date)
		.limit(TASK_LIMIT)
	).run(as_dict=True)
	for row in rows:
		row.title = task_title(row.pop("description"))
	return rows


def get_deal_exceptions(deal: str) -> list[dict]:
	"""Open, waiting and approved exceptions on the deal (Phase 2)."""
	return frappe.get_all(
		"TBO Finance Exception",
		filters={"opportunity": deal, "status": ("in", ["Open", "Pending Approval", "Approved"])},
		fields=[
			"name",
			"exception_type",
			"requested_action",
			"status",
			"reference_name",
			"new_due_date",
			"pause_until",
		],
		order_by="creation desc",
	)


@frappe.whitelist(methods=["POST"])
def set_onboarding_complete(deal: str, complete: int | bool = 1) -> dict:
	"""Tick or untick "Onboarding complete". Anyone who may edit the deal, or a finance user.
	Logged as a Version and a timeline comment; the stage is recomputed."""
	require_deal(deal)
	if not (can_write_deal(deal) or is_finance_user()):
		frappe.throw(_("You cannot change this deal."), frappe.PermissionError)
	doc = frappe.get_doc("Opportunity", deal)
	value = 1 if cint(complete) else 0
	if cint(doc.tbo_onboarding_complete) != value:
		write_deal(
			doc,
			{"tbo_onboarding_complete": value},
			_("Onboarding marked complete.") if value else _("Onboarding marked not complete."),
		)
	return {"onboarding_complete": value, "stage": update_stage(deal)}


@frappe.whitelist(methods=["POST"])
def set_finance_closed(deal: str, closed: int | bool = 1) -> dict:
	"""Close (or reopen) a deal's finance. Accounts Manager or System Manager, and only once
	everything invoiced is collected. Logged as a Version and a timeline comment."""
	require_deal(deal)
	if not can_close():
		frappe.throw(_("Only Accounts Managers can close a deal's finance."), frappe.PermissionError)
	doc = frappe.get_doc("Opportunity", deal)
	value = 1 if cint(closed) else 0
	if cint(doc.tbo_closed) == value:
		return {"closed": value, "stage": doc.tbo_finance_stage}
	if value and compute_stage(doc, DealFinance(deal)) != closable_stage():
		frappe.throw(
			_("A deal can be closed only when every invoice is collected.")
			if closable_stage() == "Collected"
			else _(
				"A deal can be closed only when it is Reconciled: collected, India's share transferred, every receipt verified, no open exceptions."
			)
		)
	write_deal(
		doc,
		{"tbo_closed": value},
		_("Finance closed.") if value else _("Finance reopened."),
	)
	return {"closed": value, "stage": update_stage(deal)}
