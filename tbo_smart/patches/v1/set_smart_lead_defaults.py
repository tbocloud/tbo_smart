"""Store the defaults of the new AI and smart lead settings on existing sites (a Single's
defaults only reach a new site). Never touches a key or a switch that was already set."""

import frappe

FIELDS = (
	"ai_primary_provider",
	"ai_failover",
	"openai_base_url",
	"openai_model",
	"call_today_size",
	"ai_daily_lead_limit",
	"lead_stale_days",
	"lead_ladder_l1_days",
	"lead_ladder_l2_days",
	"lead_ladder_l3_days",
	"lead_manager_role",
	"triage_max_leads",
	"triage_daily_capacity",
)


def execute():
	meta = frappe.get_meta("TBO Smart Settings")
	for fieldname in FIELDS:
		field = meta.get_field(fieldname)
		if not field or field.default in (None, ""):
			continue
		if frappe.db.get_single_value("TBO Smart Settings", fieldname) in (None, "", 0):
			if fieldname == "lead_manager_role" and not frappe.db.exists("Role", field.default):
				continue
			frappe.db.set_single_value("TBO Smart Settings", fieldname, field.default)
