"""Custom fields and statuses of the lead pipeline, created on install and every migrate
(docs/lead-pipeline.md §4). Nothing on a site without the CRM app."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from tbo_smart.finance import crm_installed
from tbo_smart.lead_pipeline.statuses import ensure_statuses


def get_pipeline_fields() -> dict:
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
		]
	}


def setup_lead_pipeline() -> None:
	if not crm_installed():
		return
	create_custom_fields(get_pipeline_fields(), ignore_validate=True)
	ensure_statuses()


def after_migrate() -> None:
	setup_lead_pipeline()
