"""Custom fields of the smart lead features on ERPNext's Lead, created on install and every
migrate (docs/smart-leads.md §3.1). Nothing on a site without ERPNext."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from tbo_smart.finance.setup import create_finance_fields
from tbo_smart.sales import erpnext_installed


def get_lead_fields() -> dict:
	hidden = {"hidden": 1, "read_only": 1, "no_copy": 1}
	return {
		"Lead": [
			{
				"fieldname": "tbo_score",
				"fieldtype": "Int",
				"label": "Score",
				"insert_after": "tbo_lost_notes",
				"read_only": 1,
				"no_copy": 1,
				"in_list_view": 1,
				"description": "0-100, from TBO smart leads.",
			},
			{
				"fieldname": "tbo_temperature",
				"fieldtype": "Select",
				"options": "\nCold\nWarm\nHot",
				"label": "Temperature",
				"insert_after": "tbo_score",
				"read_only": 1,
				"no_copy": 1,
				"in_standard_filter": 1,
			},
			{
				"fieldname": "tbo_score_reason",
				"fieldtype": "Small Text",
				"label": "Score reason",
				"insert_after": "tbo_temperature",
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
			{
				"fieldname": "tbo_ai_insight",
				"fieldtype": "Long Text",
				"label": "AI insight",
				"insert_after": "tbo_ai_brief_at",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_insight_hash",
				"fieldtype": "Data",
				"label": "AI insight read of",
				"insert_after": "tbo_ai_insight",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_insight_at",
				"fieldtype": "Datetime",
				"label": "AI insight at",
				"insert_after": "tbo_ai_insight_hash",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_sentiment",
				"fieldtype": "Select",
				"options": "\nPositive\nNeutral\nNegative",
				"label": "AI sentiment",
				"insert_after": "tbo_ai_insight_at",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_intent",
				"fieldtype": "Select",
				"options": "\nHigh\nMedium\nLow\nNone",
				"label": "AI buying intent",
				"insert_after": "tbo_ai_sentiment",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_next_action",
				"fieldtype": "Small Text",
				"label": "AI next best action",
				"insert_after": "tbo_ai_intent",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_next_due",
				"fieldtype": "Date",
				"label": "AI next action by",
				"insert_after": "tbo_ai_next_action",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_stage",
				"fieldtype": "Data",
				"label": "AI suggested stage",
				"insert_after": "tbo_ai_next_due",
				**hidden,
			},
			{
				"fieldname": "tbo_ai_nudged_on",
				"fieldtype": "Date",
				"label": "AI nudged the owner on",
				"insert_after": "tbo_ai_stage",
				**hidden,
			},
		]
	}


def setup_smart_leads() -> None:
	"""The lead fields, plus the ToDo fields from the finance workflow (tbo_task_type
	"Sales follow-up", tbo_escalation_level), which the follow-up engine reuses."""
	if not erpnext_installed():
		return
	create_finance_fields()
	create_custom_fields(get_lead_fields(), ignore_validate=True)


def after_migrate() -> None:
	setup_smart_leads()
