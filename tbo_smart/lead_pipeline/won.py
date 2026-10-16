"""The deal at Closed – Won (docs/lead-pipeline.md §6).

Everything before the sale happens on the lead. When a lead reaches a Won-type stage (Closed –
Won), its deal is made with ERPNext's own Lead → Opportunity mapper, in the first Won-type Sales
Stage, so the deal-based finance workflow (Finance, Sales Order, invoices) carries on from
there. A lead that already has a deal keeps it. People don't convert leads by hand while the
pipeline is on: `convert_lead` (the app's Convert) refuses until the lead is Closed – Won; with
it off, Convert makes the deal and closes the lead as Closed – Won.
"""

import frappe
from frappe import _

from tbo_smart.lead_pipeline import pipeline_enabled
from tbo_smart.lead_pipeline.quotations import relink_to_deal
from tbo_smart.lead_pipeline.statuses import CLOSED_WON, status_type
from tbo_smart.lead_pipeline.transitions import set_lead_status
from tbo_smart.sales.opportunity import first_stage, lead_opportunity, make_opportunity


def lead_updated(doc, method=None) -> None:
	"""doc_events: Lead on_update. A lead that has just turned Won gets its deal (not during an
	import: the CRM cutover copies the deals themselves)."""
	if frappe.flags.in_import or not pipeline_enabled():
		return
	if doc.has_value_changed("tbo_stage") and status_type(doc.tbo_stage) == "Won":
		create_deal(doc)


def create_deal(lead) -> str:
	"""The lead's deal (contact, address, owner and notes carried over by ERPNext's mapper), made
	as a system action. Returns the deal's name."""
	existing = lead_opportunity(lead.name)
	if existing:
		relink_to_deal(lead.name, existing)
		return existing
	deal = make_opportunity(lead, first_stage("Won"))
	lead.add_comment("Info", _("Deal {0} created: the lead is {1}").format(deal, lead.tbo_stage))
	relink_to_deal(lead.name, deal)
	return deal


def convert_lead(lead: str) -> str:
	"""The lead's deal, made by hand (the app's Convert). While the pipeline is on, a deal comes
	only from Closed – Won; off, any lead may become a deal. Needs write on the lead."""
	frappe.has_permission("Lead", "write", lead, throw=True)
	existing = lead_opportunity(lead)
	if existing:
		return existing
	doc = frappe.get_doc("Lead", lead)
	if pipeline_enabled():
		if status_type(doc.tbo_stage) != "Won":
			frappe.throw(
				_("Set the lead to {0}: its deal is created then.").format(CLOSED_WON),
				frappe.ValidationError,
			)
		return create_deal(doc)
	deal = make_opportunity(doc, first_stage("Open") or first_stage("Ongoing"))
	# a converted lead is closed, as Frappe CRM's conversion closed it
	set_lead_status(lead, CLOSED_WON, _("converted to deal {0}").format(deal))
	return deal
