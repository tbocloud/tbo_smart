"""CRM Settings → TBO → Lead pipeline (docs/lead-pipeline.md §8). System Managers only."""

import json

import frappe

from tbo_smart.api.smart_leads_settings import require_manager
from tbo_smart.finance import crm_installed

SETTINGS = "TBO Smart Settings"
FIELDS = ("enable_lead_pipeline", "meeting_reminder_minutes")


@frappe.whitelist()
def get_settings() -> dict:
	"""{settings, crm}: the pipeline switches and numbers."""
	require_manager()
	doc = frappe.get_single(SETTINGS)
	return {"settings": {field: doc.get(field) for field in FIELDS}, "crm": crm_installed()}


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
