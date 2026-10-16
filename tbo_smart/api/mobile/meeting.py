"""Lead meetings and their minutes in the TBO Smart app (contract docs/tbo-smart-api-ess-crm.md
§6.4). Thin wrappers over tbo_smart.api.lead_pipeline: the same rules, permissions and lead
moves as the CRM. Every method needs the `crm` capability and TBO Smart Settings → Lead
pipeline."""

import frappe

from tbo_smart.api import lead_pipeline as pipeline
from tbo_smart.api.mobile.crm import require_crm


@frappe.whitelist()
def get_meetings(lead: str) -> dict:
	"""{lead, status, summary, meetings[]}: the lead's pipeline summary and meeting cards."""
	require_crm()
	return pipeline.get_lead_pipeline(lead)


@frappe.whitelist()
def get_meeting(name: str) -> dict:
	require_crm()
	return pipeline.get_meeting(name)


@frappe.whitelist(methods=["POST"])
def schedule(
	lead: str,
	scheduled_at: str,
	meeting_type: str = "Online",
	meeting_link: str | None = None,
	location: str | None = None,
	duration_minutes: int = 60,
	participants: str | None = None,
	agenda: str | None = None,
) -> dict:
	require_crm()
	return pipeline.schedule_meeting(
		lead,
		{
			"scheduled_at": scheduled_at,
			"meeting_type": meeting_type,
			"meeting_link": meeting_link,
			"location": location,
			"duration_minutes": duration_minutes,
			"participants": participants,
			"agenda": agenda,
		},
	)


@frappe.whitelist(methods=["POST"])
def set_status(name: str, status: str) -> dict:
	"""Meeting in Progress, Completed, Follow-up Required or Cancelled."""
	require_crm()
	return pipeline.set_meeting_status(name, status)


@frappe.whitelist(methods=["POST"])
def submit_mom(
	name: str,
	mom: dict | str,
	lead_status: str | None = None,
	lost_reason: str | None = None,
	next_meeting: dict | str | None = None,
) -> dict:
	"""Submit the minutes (`mom`: the MOM fields and action_items), as the CRM's Submit MOM."""
	require_crm()
	return pipeline.save_mom(name, mom, 1, lead_status, lost_reason, next_meeting)
