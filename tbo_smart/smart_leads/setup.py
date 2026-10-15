"""Custom fields of the smart lead features, created on install and every migrate
(docs/smart-leads.md §3.1). Nothing on a site without the CRM app."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from tbo_smart.finance import crm_installed
from tbo_smart.finance.setup import create_finance_fields


def get_lead_fields() -> dict:
	hidden = {"hidden": 1, "read_only": 1, "no_copy": 1}
	return {
		"CRM Lead": [
			{
				"fieldname": "tbo_score_reason",
				"fieldtype": "Small Text",
				"label": "Score reason",
				"insert_after": "lead_temperature",
				"read_only": 1,
				"no_copy": 1,
				"description": "Why the lead has its score, from TBO smart leads.",
			},
			{
				"fieldname": "tbo_scored_at",
				"fieldtype": "Datetime",
				"label": "Scored at",
				"insert_after": "tbo_score_reason",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_adjust",
				"fieldtype": "Int",
				"label": "AI score adjustment",
				"insert_after": "tbo_scored_at",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_reason",
				"fieldtype": "Small Text",
				"label": "AI score reason",
				"insert_after": "tbo_ai_adjust",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_hash",
				"fieldtype": "Data",
				"label": "AI score read of",
				"insert_after": "tbo_ai_reason",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_brief",
				"fieldtype": "Small Text",
				"label": "Lead brief",
				"insert_after": "tbo_ai_hash",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_brief_hash",
				"fieldtype": "Data",
				"label": "Lead brief read of",
				"insert_after": "tbo_ai_brief",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_brief_at",
				"fieldtype": "Datetime",
				"label": "Lead brief at",
				"insert_after": "tbo_ai_brief_hash",
				**hidden,
			},
		]
	}


def setup_smart_leads() -> None:
	"""The lead fields, plus the CRM Task fields from the finance workflow (tbo_task_type
	"Sales follow-up", tbo_escalation_level), which the follow-up engine reuses."""
	if not crm_installed():
		return
	create_finance_fields()
	create_custom_fields(get_lead_fields(), ignore_validate=True)


def after_migrate() -> None:
	setup_smart_leads()
