"""The deal at Closed – Won (docs/lead-pipeline.md §6).

Everything before the sale happens on the lead. When a lead reaches a Won-type status (Closed –
Won), Frappe CRM's own `convert_to_deal` makes the CRM Deal, in the first Won-type deal stage,
so the deal-based finance workflow (Finance tab, Sales Order, invoices) carries on from there.
A lead that already has a deal keeps it. People don't convert leads by hand while the pipeline
is on: `convert_to_deal` (the CRM's Convert button and the mobile app) refuses until the lead is
Closed – Won.
"""

import frappe
from crm.fcrm.doctype.crm_lead.crm_lead import convert_to_deal as crm_convert_to_deal
from frappe import _

from tbo_smart.lead_pipeline import pipeline_enabled
from tbo_smart.lead_pipeline.quotations import relink_to_deal
from tbo_smart.lead_pipeline.statuses import CLOSED_WON, status_type


def lead_updated(doc, method=None) -> None:
	"""doc_events: CRM Lead on_update. A lead that has just turned Won gets its deal."""
	if pipeline_enabled() and doc.has_value_changed("status") and status_type(doc.status) == "Won":
		create_deal(doc)


def create_deal(lead) -> str:
	"""The lead's deal, made with CRM's conversion (contact, organization, products, owner and
	assignees carried over) as a system action. Returns the deal's name."""
	existing = frappe.db.get_value("CRM Deal", {"lead": lead.name}, "name")
	if existing:
		return existing
	stage = won_deal_stage()
	lead.flags.ignore_permissions = True
	deal = crm_convert_to_deal(
		lead=lead.name, doc=lead, deal={"status": stage} if stage else None, if_converted="Return Existing"
	)
	# CRM's conversion sets the lead to "Qualified" when that status exists; it stays Won
	frappe.db.set_value("CRM Lead", lead.name, "status", lead.status, update_modified=False)
	lead.add_comment("Info", _("Deal {0} created: the lead is {1}").format(deal, lead.status))
	relink_to_deal(lead.name, deal)
	return deal


def won_deal_stage() -> str | None:
	return frappe.db.get_value("CRM Deal Status", {"type": "Won"}, "name", order_by="position asc")


@frappe.whitelist(methods=["POST"])
def convert_to_deal(
	lead: str,
	deal: str | dict | None = None,
	existing_contact: str | None = None,
	existing_organization: str | None = None,
	if_converted: str = "Create",
):
	"""Override of Frappe CRM's whitelisted convert_to_deal (the lead page's Convert to Deal).
	While the pipeline is on, a deal comes only from Closed – Won; off, CRM's own conversion."""
	if pipeline_enabled():
		status = frappe.db.get_value("CRM Lead", lead, "status")
		if status_type(status) != "Won":
			frappe.throw(
				_("Set the lead to {0}: its deal is created then.").format(CLOSED_WON),
				frappe.ValidationError,
			)
		frappe.has_permission("CRM Lead", "write", lead, throw=True)
		return create_deal(frappe.get_doc("CRM Lead", lead))
	return crm_convert_to_deal(
		lead=lead,
		deal=deal,
		existing_contact=existing_contact,
		existing_organization=existing_organization,
		if_converted=if_converted,
	)
