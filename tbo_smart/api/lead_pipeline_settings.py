"""CRM Settings → TBO → Lead pipeline (docs/lead-pipeline.md §8). System Managers only."""

import json

import frappe

from tbo_smart.api.smart_leads_settings import require_manager
from tbo_smart.finance import crm_installed

SETTINGS = "TBO Smart Settings"
FIELDS = (
	"enable_lead_pipeline",
	"meeting_reminder_minutes",
	"enable_lead_quotations",
	"quotation_validity_days",
	"quotation_follow_up_days",
	"quotation_terms",
	"quotation_payment_terms",
	"quotation_print_format",
	"quotation_preset_roles",
)


@frappe.whitelist()
def get_settings() -> dict:
	"""{settings, crm, choices}: the pipeline and quotation settings, and the pick lists."""
	require_manager()
	doc = frappe.get_single(SETTINGS)
	return {
		"settings": {field: doc.get(field) for field in FIELDS},
		"crm": crm_installed(),
		"choices": {
			"terms": frappe.get_all("Terms and Conditions", filters={"disabled": 0}, pluck="name"),
			"payment_terms": frappe.get_all("Payment Terms Template", pluck="name"),
			"print_formats": frappe.get_all(
				"Print Format", filters={"doc_type": "Quotation", "disabled": 0}, pluck="name"
			),
		},
	}


@frappe.whitelist(methods=["POST"])
def save_settings(values: str | dict) -> dict:
	"""Save the fields sent; the settings' own validation applies."""
	require_manager()
	values = json.loads(values) if isinstance(values, str) else values
	doc = frappe.get_single(SETTINGS)
	for field in FIELDS:
		if field in values:
			doc.set(field, values[field])
	doc.save(ignore_permissions=True)
	return get_settings()
