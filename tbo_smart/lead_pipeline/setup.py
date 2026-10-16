"""Custom fields, statuses and the print format of the lead pipeline, created on install and
every migrate (docs/lead-pipeline.md §4). Nothing on a site without the CRM app."""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from tbo_smart.finance import crm_installed
from tbo_smart.lead_pipeline.statuses import ensure_statuses

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
# Quotation To: ERPNext's own parties, Frappe CRM's CRM Deal, and the lead (§9.1)
QUOTATION_PARTIES = ["Customer", "Lead", "Prospect", "CRM Deal", "CRM Lead"]


def get_pipeline_fields() -> dict:
	tracked = {"read_only": 1, "no_copy": 1, "allow_on_submit": 1}
	return {
		"CRM Lead": [
			{
				"fieldname": "tbo_pre_pipeline_status",
				"fieldtype": "Data",
				"label": "Status before the pipeline",
				"insert_after": "tbo_ai_brief_at",
				"hidden": 1,
				"read_only": 1,
				"no_copy": 1,
				"description": "The status the lead had before the 2026 pipeline statuses; kept to undo the move.",
			}
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
				"fieldname": "tbo_crm_lead",
				"fieldtype": "Link",
				"options": "CRM Lead",
				"label": "CRM Lead",
				"insert_after": "tbo_lead_section",
				"search_index": 1,
				"read_only": 1,
			},
			{
				"fieldname": "tbo_lead_meeting",
				"fieldtype": "Link",
				"options": "TBO Lead Meeting",
				"label": "From meeting",
				"insert_after": "tbo_crm_lead",
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
	if not crm_installed():
		return
	create_custom_fields(get_pipeline_fields(), ignore_validate=True)
	ensure_statuses()
	allow_lead_as_quotation_party()
	sync_print_format()


def allow_lead_as_quotation_party() -> None:
	"""Quotation To may be a CRM Lead. Frappe CRM's own setting adds CRM Deal to the same
	property setter; this keeps whatever is listed there and adds CRM Lead."""
	current = frappe.db.get_value("Property Setter", "Quotation-quotation_to-link_filters", "value")
	parties = list(QUOTATION_PARTIES)
	if current:
		try:
			parties = list(dict.fromkeys([*json.loads(current)[0][3], "CRM Lead"]))
		except (ValueError, IndexError, TypeError):
			pass
	value = json.dumps([["DocType", "name", "in", parties]])
	if value != current:
		make_property_setter(
			"Quotation", "quotation_to", "link_filters", value, "JSON", validate_fields_for_doctype=False
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
