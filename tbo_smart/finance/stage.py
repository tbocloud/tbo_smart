"""The CRM Deal's post-sale stage, `tbo_stage` (docs/crm-finance-workflow.md §2).

Nobody types the stage. It is recomputed when a linked Quotation, Sales Order, Sales Invoice or
Payment Entry is saved, submitted or cancelled, and nightly as a safety net. Only "Onboarding
complete" and "Closed" are set by a person, through tbo_smart.api.finance.deal, and both are
logged. Every change writes a Version row on the deal, so the stage keeps its history.

Phase 1 computes Onboarding → Quotation → Invoiced → Collecting → Collected, and Closed when a
person closes a collected deal. Settling, Reconciled and On Exception arrive with Phases 3 and 4.
"""

import copy

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart.finance import finance_enabled, get_default_company, publish_deal_update
from tbo_smart.finance.deal_finance import AMOUNT_TOLERANCE, DealFinance
from tbo_smart.finance.deal_links import deals_of, deals_of_quotations


def compute_stage(deal, finance: DealFinance) -> str:
	"""The stage a deal is in, from its own flags and its ERPNext documents. Blank while the
	deal has nothing post-sale yet (no customer, onboarding, quotation or invoice)."""
	if cint(deal.get("tbo_closed")):
		return "Closed"
	if finance.sales_invoices():
		if finance.outstanding() <= AMOUNT_TOLERANCE:
			return "Collected"
		if finance.received() > AMOUNT_TOLERANCE or finance.overdue_by_invoice():
			return "Collecting"
		return "Invoiced"
	if cint(deal.get("tbo_onboarding_complete")) or finance.has_quotation_or_order():
		return "Quotation"
	if deal.get("erpnext_customer"):
		return "Onboarding"
	return ""


def write_deal(deal, values: dict, comment: str | None = None) -> None:
	"""Write fields on a CRM Deal without running its save hooks, and keep the history.

	A full save would re-run Frappe CRM's on_update (customer creation, automations) from an
	accounting document's submit. So the values go straight to the row, and a Version row and
	an optional timeline comment record who changed what."""
	before = copy.deepcopy(deal)
	deal.update(values)
	frappe.db.set_value("CRM Deal", deal.name, values)
	version = frappe.new_doc("Version")
	if version.update_version_info(before, deal):
		version.insert(ignore_permissions=True)
	if comment:
		deal.add_comment("Info", comment)
	publish_deal_update([deal.name])


def update_stage(deal_name: str) -> str | None:
	"""Recompute and store one deal's stage; returns it (None when the deal is gone)."""
	if not frappe.db.exists("CRM Deal", deal_name):
		return None
	deal = frappe.get_doc("CRM Deal", deal_name)
	stage = compute_stage(deal, DealFinance(deal_name))
	if stage != (deal.tbo_stage or ""):
		write_deal(deal, {"tbo_stage": stage})
	return stage


def deals_of_document(doc) -> set[str]:
	"""Deals a document touches: its own crm_deal, plus for a payment its references' deals."""
	deals = {doc.get("crm_deal")}
	if doc.doctype == "Quotation":
		deals |= set(deals_of_quotations([doc.name]).values())
	if doc.doctype == "Payment Entry":
		for doctype in ("Sales Invoice", "Sales Order"):
			names = {
				row.reference_name for row in doc.get("references") or [] if row.reference_doctype == doctype
			}
			deals |= set(deals_of(doctype, names).values())
	return {deal for deal in deals if deal}


class StageSync:
	"""doc_events entry point for Quotation, Sales Order, Sales Invoice and Payment Entry."""

	def __init__(self, doc):
		self.doc = doc

	def run(self):
		if not finance_enabled():
			return
		deals = deals_of_document(self.doc)
		for deal in deals:
			update_stage(deal)
		publish_deal_update(deals)


def recompute_all() -> None:
	"""Nightly safety net: every deal that has a stage, a customer or a linked document. A
	failure on one deal is logged and does not stop the rest."""
	if not finance_enabled():
		return
	for deal in get_post_sale_deals():
		try:
			update_stage(deal)
		except Exception:
			frappe.log_error(title=_("TBO finance: stage update failed for {0}").format(deal))


def get_post_sale_deals() -> list[str]:
	Deal = frappe.qb.DocType("CRM Deal")
	deals = set(
		(
			frappe.qb.from_(Deal)
			.select(Deal.name)
			.where(
				((Deal.tbo_stage.isnotnull()) & (Deal.tbo_stage != ""))
				| ((Deal.erpnext_customer.isnotnull()) & (Deal.erpnext_customer != ""))
				| (Deal.tbo_onboarding_complete == 1)
			)
		).run(pluck=True)
	)
	for doctype in ("Quotation", "Sales Order", "Sales Invoice"):
		Table = frappe.qb.DocType(doctype)
		deals |= set(
			(
				frappe.qb.from_(Table)
				.select(Table.crm_deal)
				.distinct()
				.where(Table.crm_deal.isnotnull() & (Table.crm_deal != "") & (Table.docstatus < 2))
			).run(pluck=True)
		)
	return sorted(deals)


def stage_changed(doc, method=None):
	StageSync(doc).run()


def default_company(doc, method=None):
	"""CRM Deal before_insert: the invoicing company defaults to ERPNext CRM Settings' company."""
	DealDefaults(doc).set_company()


class DealDefaults:
	def __init__(self, doc):
		self.doc = doc

	def set_company(self):
		if self.doc.get("tbo_company") or not finance_enabled():
			return
		self.doc.tbo_company = get_default_company()
