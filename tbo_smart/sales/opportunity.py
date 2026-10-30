"""The deal: ERPNext's Opportunity (docs/erpnext-lead-cutover.md §2.1, decision E5).

A deal's stage is its `sales_stage` (Sales Stage). Each Sales Stage carries the custom
`tbo_stage_type` (Open, Ongoing, On Hold, Won, Lost; blank reads as Ongoing), `tbo_probability`
and `tbo_position`, as Frappe CRM's CRM Deal Status did. A deal made from a lead is
`opportunity_from = "Lead"` with the lead as `party_name`, and keeps the lead in `tbo_lead` once
ERPNext moves it to the Customer made from the lead; its client is `tbo_customer`, else the
Customer ERPNext made from the lead (`Customer.lead_name`) or the deal's own Customer party.
"""

import frappe
from frappe.utils import flt, getdate, today

from tbo_smart import lead_source
from tbo_smart.sales import DEAL, LEAD_LINK
from tbo_smart.sales.stages import record_change

STAGE = "Sales Stage"
STAGE_TYPES = ("Open", "Ongoing", "On Hold", "Won", "Lost")
CLOSED_STAGE_TYPES = ("Won", "Lost")
# the stages a site gets when none of its Sales Stages is typed Won or Lost
DEFAULT_STAGES = (("Won", "Won", 100), ("Lost", "Lost", 0))


def stage_type(stage: str | None) -> str | None:
	if not stage:
		return None
	return frappe.get_cached_value(STAGE, stage, "tbo_stage_type") or "Ongoing"


def deal_stages() -> list[dict]:
	"""Every Sales Stage with its type and probability, in pipeline order."""
	rows = frappe.get_all(
		STAGE, fields=["name", "tbo_stage_type", "tbo_probability", "tbo_position"], limit_page_length=0
	)
	rows.sort(key=lambda row: (row.tbo_position or 10_000, row.name))
	return [
		{"name": row.name, "type": row.tbo_stage_type or "Ongoing", "probability": row.tbo_probability or 0}
		for row in rows
	]


def stage_types() -> dict[str, str]:
	return {row["name"]: row["type"] for row in deal_stages()}


def first_stage(kind: str) -> str | None:
	return next((row["name"] for row in deal_stages() if row["type"] == kind), None)


def stages_of_types(kinds) -> list[str]:
	return [row["name"] for row in deal_stages() if row["type"] in kinds]


def ensure_default_stages() -> None:
	"""A Won and a Lost stage, when no Sales Stage has that type yet."""
	for name, kind, probability in DEFAULT_STAGES:
		if frappe.db.exists(STAGE, {"tbo_stage_type": kind}):
			continue
		if frappe.db.exists(STAGE, name):
			frappe.db.set_value(STAGE, name, {"tbo_stage_type": kind, "tbo_probability": probability})
			continue
		frappe.get_doc(
			{"doctype": STAGE, "stage_name": name, "tbo_stage_type": kind, "tbo_probability": probability}
		).insert(ignore_permissions=True)


def lead_opportunity(lead: str) -> str | None:
	"""The lead's deal: the first Opportunity made from it."""
	return frappe.db.get_value(DEAL, {LEAD_LINK: lead}, "name", order_by="creation asc")


def leads_with_opportunity(leads) -> set[str]:
	leads = [lead for lead in set(leads or ()) if lead]
	if not leads:
		return set()
	return set(frappe.get_all(DEAL, filters={LEAD_LINK: ("in", leads)}, pluck=LEAD_LINK))


def converted_leads_query():
	"""Subquery of the leads that have a deal."""
	Deal = frappe.qb.DocType(DEAL)
	return frappe.qb.from_(Deal).select(Deal[LEAD_LINK]).where(Deal[LEAD_LINK].isnotnull())


def customer_of(deal) -> str | None:
	"""The deal's ERPNext Customer: `tbo_customer`, its Customer party, or the Customer made from
	its lead or prospect."""
	if deal.get("tbo_customer"):
		return deal.tbo_customer
	if deal.get("opportunity_from") == "Customer":
		return deal.party_name
	field = {"Lead": "lead_name", "Prospect": "prospect_name"}.get(deal.get("opportunity_from"))
	return frappe.db.get_value("Customer", {field: deal.party_name}, "name") if field else None


def customers_of(deals) -> dict[str, str]:
	"""{deal name: its Customer} for many deals (`customer_of`), in one query per party type."""
	made_from = {}
	for party_type, field in (("Lead", "lead_name"), ("Prospect", "prospect_name")):
		parties = {
			deal.party_name
			for deal in deals
			if deal.get("opportunity_from") == party_type and not deal.get("tbo_customer")
		}
		if parties:
			made_from[party_type] = dict(
				frappe.get_all(
					"Customer", filters={field: ("in", list(parties))}, fields=[field, "name"], as_list=True
				)
			)
	result = {}
	for deal in deals:
		customer = deal.get("tbo_customer") or (
			deal.party_name
			if deal.get("opportunity_from") == "Customer"
			else made_from.get(deal.get("opportunity_from"), {}).get(deal.party_name)
		)
		if customer:
			result[deal.name] = customer
	return result


def make_opportunity(lead, stage: str | None = None) -> str:
	"""The lead's deal through ERPNext's own Lead → Opportunity mapper (contact, address, owner,
	notes), in `stage`. ERPNext then moves the lead's open ToDos and Events to it."""
	from erpnext.crm.doctype.lead.lead import make_opportunity as erpnext_make_opportunity

	from tbo_smart.finance import get_default_company

	deal = erpnext_make_opportunity(lead.name)
	deal.update(
		{
			"sales_stage": stage,
			"opportunity_owner": lead.lead_owner or frappe.session.user,
			"company": deal.company or lead.get("company") or get_default_company(),
			lead_source.field(): lead.get(lead_source.field()),
			"territory": lead.get("territory"),
		}
	)
	deal.insert(ignore_permissions=True)
	return deal.name


class OpportunityStage:
	"""doc_events: Opportunity validate. The stage log, the day it was won or lost
	(`tbo_closed_on`), ERPNext's Lost status for a Lost-type stage, and the value in the company
	currency."""

	def __init__(self, doc):
		self.doc = doc

	def validate(self):
		record_change(self.doc)
		self.set_closed_on()
		self.mark_lost()
		self.set_base_amount()

	def stage_changed(self) -> bool:
		return self.doc.is_new() or self.doc.has_value_changed("sales_stage")

	def set_closed_on(self):
		if not self.stage_changed():
			return
		kind = stage_type(self.doc.sales_stage)
		self.doc.tbo_closed_on = getdate(today()) if kind in CLOSED_STAGE_TYPES else None

	def mark_lost(self):
		"""A Lost-type stage is ERPNext's Lost status; moving out of it reopens the deal."""
		if not self.stage_changed():
			return
		if stage_type(self.doc.sales_stage) == "Lost":
			self.doc.status = "Lost"
		elif self.doc.status == "Lost":
			self.doc.status = "Open"

	def set_base_amount(self):
		"""ERPNext fills `base_opportunity_amount` in the browser only; deals saved through the API
		need it for every figure in the company currency."""
		self.doc.base_opportunity_amount = flt(self.doc.opportunity_amount) * (
			flt(self.doc.conversion_rate) or 1
		)


def validate_opportunity(doc, method=None):
	OpportunityStage(doc).validate()
