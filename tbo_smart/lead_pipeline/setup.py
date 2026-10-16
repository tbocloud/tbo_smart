"""Custom fields, masters and the print format of the lead pipeline on ERPNext's Lead and
Opportunity, created on install and every migrate (docs/lead-pipeline.md §4,
docs/erpnext-lead-cutover.md §2.1). Nothing on a site without ERPNext."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from tbo_smart.lead_pipeline.statuses import NEW_LEAD, STATUS_NAMES
from tbo_smart.sales import DEAL, LEAD, LEAD_LINK, erpnext_installed
from tbo_smart.sales.opportunity import STAGE_TYPES, ensure_default_stages
from tbo_smart.sales.stages import OTHER_REASON

QUOTE_STATUSES = (
	"Draft",
	"Pending Approval",
	"Approved",
	"Sent to Client",
	"Viewed / Acknowledged",
	"Under Negotiation",
	"Revision Required",
	"Accepted",
	"Rejected",
	"Expired",
)
PRINT_FORMAT = "TBO Quotation"
SEGMENTS = ("Retail", "Small", "Medium", "Large", "Premium")
HUB_ROLE = "Hub Integration"


def get_pipeline_fields() -> dict:
	tracked = {"read_only": 1, "no_copy": 1, "allow_on_submit": 1}
	hidden = {"hidden": 1, "read_only": 1, "no_copy": 1}
	stage_log = {
		"fieldname": "tbo_stage_log",
		"fieldtype": "Table",
		"options": "TBO Stage Change",
		"label": "Stage history",
		"read_only": 1,
		"no_copy": 1,
	}
	return {
		"Lead": [
			{
				"fieldname": "tbo_stage",
				"fieldtype": "Select",
				"options": "\n".join(STATUS_NAMES),
				"default": NEW_LEAD,
				"label": "Stage",
				"insert_after": "status",
				"in_list_view": 1,
				"in_standard_filter": 1,
				"search_index": 1,
				"description": "TBO's pipeline stage. ERPNext sets Status itself.",
			},
			{
				"fieldname": "tbo_segment",
				"fieldtype": "Select",
				"options": "\n" + "\n".join(SEGMENTS),
				"label": "Segment",
				"insert_after": "tbo_stage",
				"in_standard_filter": 1,
			},
			{
				"fieldname": "tbo_lost_reason",
				"fieldtype": "Link",
				"options": "Opportunity Lost Reason",
				"label": "Lost reason",
				"insert_after": "tbo_segment",
				"depends_on": "eval:doc.tbo_stage == 'Closed – Lost'",
			},
			{
				"fieldname": "tbo_lost_notes",
				"fieldtype": "Small Text",
				"label": "Lost notes",
				"insert_after": "tbo_lost_reason",
				"depends_on": "eval:doc.tbo_stage == 'Closed – Lost'",
			},
			{
				"fieldname": "tbo_stage_section",
				"fieldtype": "Section Break",
				"label": "Stage history",
				"insert_after": "notes",
				"collapsible": 1,
			},
			{**stage_log, "insert_after": "tbo_stage_section"},
			{
				"fieldname": "crm_lead_ref",
				"fieldtype": "Data",
				"label": "Frappe CRM lead",
				"insert_after": "tbo_stage_log",
				"search_index": 1,
				**hidden,
			},
		],
		"Opportunity": [
			{
				"fieldname": "tbo_lead",
				"fieldtype": "Link",
				"options": "Lead",
				"label": "Lead",
				"insert_after": "party_name",
				"read_only": 1,
				"search_index": 1,
				"description": "The lead it was made from, also once it is made out to the lead's Customer.",
			},
			{
				"fieldname": "tbo_customer",
				"fieldtype": "Link",
				"options": "Customer",
				"label": "ERPNext customer",
				"insert_after": "tbo_lead",
				"read_only": 1,
				"no_copy": 1,
				"description": "The client's Customer, once there is one.",
			},
			{
				"fieldname": "tbo_next_step",
				"fieldtype": "Data",
				"label": "Next step",
				"insert_after": "sales_stage",
			},
			{
				"fieldname": "tbo_closed_on",
				"fieldtype": "Date",
				"label": "Won or lost on",
				"insert_after": "tbo_next_step",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "tbo_stage_section",
				"fieldtype": "Section Break",
				"label": "Stage history",
				"insert_after": "notes",
				"collapsible": 1,
			},
			{**stage_log, "insert_after": "tbo_stage_section"},
			{
				"fieldname": "crm_deal_ref",
				"fieldtype": "Data",
				"label": "Frappe CRM deal",
				"insert_after": "tbo_stage_log",
				"search_index": 1,
				**hidden,
			},
		],
		"Sales Stage": [
			{
				"fieldname": "tbo_stage_type",
				"fieldtype": "Select",
				"options": "\n" + "\n".join(STAGE_TYPES),
				"label": "Type",
				"insert_after": "stage_name",
				"in_list_view": 1,
				"description": "Won and Lost close the deal; blank reads as Ongoing.",
			},
			{
				"fieldname": "tbo_probability",
				"fieldtype": "Percent",
				"label": "Probability",
				"insert_after": "tbo_stage_type",
			},
			{
				"fieldname": "tbo_position",
				"fieldtype": "Int",
				"label": "Position",
				"insert_after": "tbo_probability",
				"description": "The order of the stages in the pipeline.",
			},
		],
		"Quotation": [
			{
				"fieldname": "tbo_lead_section",
				"fieldtype": "Section Break",
				"label": "TBO lead pipeline",
				"insert_after": "party_name",
				"collapsible": 1,
			},
			{
				"fieldname": "tbo_lead",
				"fieldtype": "Link",
				"options": "Lead",
				"label": "Lead",
				"insert_after": "tbo_lead_section",
				"read_only": 1,
				"search_index": 1,
				"description": "The lead it was made for, also once it is made out to the lead's Customer.",
			},
			{
				"fieldname": "tbo_lead_meeting",
				"fieldtype": "Link",
				"options": "TBO Lead Meeting",
				"label": "From meeting",
				"insert_after": "tbo_lead",
				"read_only": 1,
			},
			{
				"fieldname": "tbo_package",
				"fieldtype": "Link",
				"options": "TBO Quotation Package",
				"label": "Package",
				"insert_after": "tbo_lead_meeting",
				"read_only": 1,
			},
			{
				"fieldname": "tbo_quote_status",
				"fieldtype": "Select",
				"options": "\n".join(QUOTE_STATUSES),
				"label": "Quote status",
				"insert_after": "tbo_package",
				"in_list_view": 1,
				"in_standard_filter": 1,
				**tracked,
			},
			{"fieldname": "tbo_lead_column", "fieldtype": "Column Break", "insert_after": "tbo_quote_status"},
			{
				"fieldname": "tbo_quote_family",
				"fieldtype": "Data",
				"label": "Quotation family",
				"insert_after": "tbo_lead_column",
				"search_index": 1,
				"read_only": 1,
				"description": "The first quotation of the chain; every revision carries it.",
			},
			{
				"fieldname": "tbo_revision_no",
				"fieldtype": "Int",
				"label": "Revision",
				"insert_after": "tbo_quote_family",
				"read_only": 1,
			},
			{
				"fieldname": "tbo_is_latest",
				"fieldtype": "Check",
				"label": "Latest revision",
				"insert_after": "tbo_revision_no",
				**tracked,
			},
			{
				"fieldname": "tbo_sent_via",
				"fieldtype": "Select",
				"options": "\nEmail\nWhatsApp\nManual",
				"label": "Sent via",
				"insert_after": "tbo_is_latest",
				**tracked,
			},
			{
				"fieldname": "tbo_sent_on",
				"fieldtype": "Datetime",
				"label": "Sent on",
				"insert_after": "tbo_sent_via",
				**tracked,
			},
		],
	}


def setup_lead_pipeline() -> None:
	if not erpnext_installed():
		return
	create_custom_fields(get_pipeline_fields(), ignore_validate=True)
	link_lead_records()
	ensure_default_stages()
	ensure_other_reason()
	ensure_hub_role()
	sync_print_format()


def link_lead_records() -> None:
	"""Deals and quotations still made out to a lead that predate `tbo_lead` get it."""
	for doctype, party_type in ((DEAL, "opportunity_from"), ("Quotation", "quotation_to")):
		table = frappe.qb.DocType(doctype)
		(
			frappe.qb.update(table)
			.set(table[LEAD_LINK], table.party_name)
			.where((table[party_type] == LEAD) & table[LEAD_LINK].isnull())
		).run()


def ensure_hub_role() -> None:
	"""The role of the TBO Support hub's API user (docs/hub-leads-api.md)."""
	if not frappe.db.exists("Role", HUB_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": HUB_ROLE, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def ensure_other_reason() -> None:
	"""Free-text lost reasons are saved as "Other" with the text as the notes."""
	if not frappe.db.exists("Opportunity Lost Reason", OTHER_REASON):
		frappe.get_doc({"doctype": "Opportunity Lost Reason", "lost_reason": OTHER_REASON}).insert(
			ignore_permissions=True
		)


def sync_print_format() -> None:
	"""The TBO Quotation print format (Jinja, from quotation_print.html), kept in step with the
	code on every migrate. Copy it under another name to customise it."""
	html = frappe.read_file(frappe.get_app_path("tbo_smart", "lead_pipeline", "quotation_print.html"))
	if frappe.db.exists("Print Format", PRINT_FORMAT):
		if frappe.db.get_value("Print Format", PRINT_FORMAT, "html") != html:
			frappe.db.set_value("Print Format", PRINT_FORMAT, "html", html)
		return
	frappe.get_doc(
		{
			"doctype": "Print Format",
			"name": PRINT_FORMAT,
			"doc_type": "Quotation",
			"module": "Tbo Smart",
			"standard": "No",
			"custom_format": 1,
			"print_format_type": "Jinja",
			"html": html,
		}
	).insert(ignore_permissions=True)


def after_migrate() -> None:
	setup_lead_pipeline()
