"""CRM Settings → TBO smart leads: AI providers and the smart lead switches
(docs/smart-leads.md §2.4). System Managers only; keys never leave the server."""

import json

import frappe
from frappe import _

from tbo_smart import ai
from tbo_smart.smart_leads.follow_ups import FollowUpRun, send_digests

SETTINGS = "TBO Smart Settings"
SECRET_FIELDS = ("openai_api_key", "anthropic_api_key", "teams_dm_workflow_url")
FIELDS = (
	"ai_primary_provider",
	"ai_failover",
	"openai_base_url",
	"openai_model",
	"openai_input_price",
	"openai_output_price",
	"anthropic_base_url",
	"anthropic_model",
	"anthropic_input_price",
	"anthropic_output_price",
	"enable_lead_scoring",
	"lead_scoring_use_ai",
	"call_today_size",
	"ai_daily_lead_limit",
	"enable_lead_follow_ups",
	"lead_stale_days",
	"lead_fallback_owner",
	"lead_ladder_l1_days",
	"lead_ladder_l2_days",
	"lead_ladder_l3_days",
	"lead_manager_role",
	"escalation_sales_head",
	"enable_lead_notes",
	"enable_lead_triage",
	"triage_max_leads",
	"triage_daily_capacity",
)


def require_manager() -> None:
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(
			_("Only a System Manager can change the TBO smart lead settings."), frappe.PermissionError
		)


def masked(fieldname: str) -> dict:
	value = ai.password(fieldname)
	if not value:
		return {"set": False, "masked": None}
	if fieldname == "teams_dm_workflow_url":
		return {"set": True, "masked": value.split("?")[0][:48] + "…"}
	return {"set": True, "masked": f"{value[:7]}…{value[-4:]}" if len(value) > 14 else "…"}


@frappe.whitelist()
def get_settings() -> dict:
	"""{settings, secrets: {field: {set, masked}}, status}; never a key."""
	require_manager()
	doc = frappe.get_single(SETTINGS)
	return {
		"settings": {field: doc.get(field) for field in FIELDS},
		"secrets": {field: masked(field) for field in SECRET_FIELDS},
		"status": ai.provider_status(),
	}


@frappe.whitelist(methods=["POST"])
def save_settings(values: str | dict) -> dict:
	"""Save the fields sent. A secret that isn't sent is kept; "" removes it."""
	require_manager()
	values = json.loads(values) if isinstance(values, str) else values
	doc = frappe.get_single(SETTINGS)
	for field in FIELDS:
		if field in values:
			doc.set(field, values[field])
	for field in SECRET_FIELDS:
		if field in values:
			doc.set(field, (values[field] or "").strip())
	doc.save(ignore_permissions=True)
	ai.reset_provider_state()
	return get_settings()


@frappe.whitelist(methods=["POST"])
def test_connection(provider: str) -> dict:
	"""One tiny call with the saved settings of a provider section."""
	require_manager()
	if provider not in ai.SLOTS:
		frappe.throw(_("Unknown provider {0}.").format(provider))
	return ai.test_provider(provider)


@frappe.whitelist(methods=["POST"])
def send_test_digest() -> dict:
	"""Today's lead follow-up digest for me, sent only to me (Teams, else email)."""
	require_manager()
	run = FollowUpRun()
	run.evaluate()
	sent = send_digests(run, only=frappe.session.user)
	if not sent:
		return {"ok": False, "message": _("You have nothing in today's digest, so nothing was sent.")}
	channel = sent[frappe.session.user]
	return {
		"ok": channel != "none",
		"message": {
			"teams": _("Sent to you in Teams."),
			"email": _("Sent to you by email (no Teams DM workflow URL, or Teams refused it)."),
			"none": _("Neither Teams nor email could send it. See the Error Log."),
		}[channel],
	}
