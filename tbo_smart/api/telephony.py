"""Telephony through MyOperator (docs/telephony-myoperator.md §4).

- `myoperator_webhook`: MyOperator's Webhooks v2 (guest, rate limited, the shared secret in the
  X-Webhook-Token header is the only key).
- `click_to_call`, `call_recording`: for a salesperson, or for the hub's integration user acting
  for one (`acting_user`, as in tbo_smart.api.hub_leads).
- The settings (`get_settings`, `save_settings`, `test_connection`, `regenerate_webhook_secret`):
  System Managers, here or through the hub. Secrets never come back, except a new webhook
  secret once, right after it is made.
"""

import hmac
import json

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from tbo_smart.api.hub_leads import acting_as
from tbo_smart.api.mobile.crm import get_crm_users
from tbo_smart.telephony import SETTINGS, calls, myoperator, secret, telephony_enabled

TOKEN_HEADER = "X-Webhook-Token"
WEBHOOK_METHOD = "tbo_smart.api.telephony.myoperator_webhook"
SECRET_FIELDS = ("myoperator_api_key", "myoperator_secret_token", "myoperator_api_token")
FIELDS = (
	"enable_telephony",
	"myoperator_company_id",
	"myoperator_public_ivr_id",
	"myoperator_caller_id",
	"telephony_create_leads",
	"telephony_fallback_owner",
	"telephony_recording_days",
	"telephony_recording_notice",
	"enable_call_summary",
	"call_summary_min_seconds",
	"transcription_model",
	"transcription_price_per_minute",
)
AGENT_FIELDS = ("user", "myoperator_user_id", "extension", "mobile")


# ---------- MyOperator's webhook ----------


@frappe.whitelist(allow_guest=True, methods=["POST"])  # the shared secret is the key - nosemgrep
@rate_limit(limit=600, seconds=60)
def myoperator_webhook() -> dict:
	"""A Webhooks v2 event. Only a request whose X-Webhook-Token header is the webhook secret is
	read; nothing in the payload decides who may call this. A finished call is stored once by
	its call ID and processed in the background."""
	if not telephony_enabled():
		frappe.throw(_("Telephony is switched off."), frappe.PermissionError)
	expected = secret("telephony_webhook_secret") or ""
	given = frappe.get_request_header(TOKEN_HEADER) or ""
	if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
		frappe.throw(_("Not allowed."), frappe.PermissionError)
	try:
		event = json.loads(frappe.request.get_data(as_text=True) or "{}")
	except ValueError:
		frappe.throw(_("The body must be JSON."))
	return calls.receive(event)


# ---------- calling and listening ----------


@frappe.whitelist(methods=["POST"])
def click_to_call(lead: str, number: str | None = None, acting_user: str | None = None) -> dict:
	"""MyOperator rings the person's phone, then the lead (`number`: one of the lead's numbers,
	default its mobile). {ok, message, number, reference_id}."""
	with acting_as(acting_user):
		return calls.click_to_call(lead, number)


@frappe.whitelist()
def call_recording(call: str, acting_user: str | None = None) -> None:
	"""The call's recording, to a person who may read its lead."""
	with acting_as(acting_user):
		recording = calls.recording_file(call)
		content = recording.get_content()
	frappe.local.response.filename = recording.file_name
	frappe.local.response.filecontent = content
	frappe.local.response.content_type = "audio/mpeg"
	frappe.local.response.display_content_as = "inline"
	frappe.local.response.type = "download"


# ---------- settings ----------


def require_manager() -> None:
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can change the telephony settings."), frappe.PermissionError)


def webhook_url() -> str:
	return frappe.utils.get_url(f"/api/method/{WEBHOOK_METHOD}")


def masked(value: str | None) -> dict:
	"""{set, masked}: enough of a secret to tell which one is saved, never the secret."""
	if not value:
		return {"set": False, "masked": None}
	return {"set": True, "masked": f"…{value[-4:]}" if len(value) >= 16 else "…"}


def settings_payload() -> dict:
	doc = frappe.get_single(SETTINGS)
	return {
		"settings": {field: doc.get(field) for field in FIELDS},
		"agents": [{field: row.get(field) for field in AGENT_FIELDS} for row in doc.telephony_agents],
		"secrets": {field: masked(secret(field)) for field in (*SECRET_FIELDS, "telephony_webhook_secret")},
		"webhook": {"url": webhook_url(), "header": TOKEN_HEADER, "events": list(myoperator.CALL_EVENTS)},
		"users": [{"email": row.user, "full_name": row.full_name} for row in get_crm_users(None)],
	}


@frappe.whitelist()
def get_settings(acting_user: str | None = None) -> dict:
	"""{settings, agents, secrets: {field: {set, masked}}, webhook: {url, header, events}, users}."""
	with acting_as(acting_user):
		require_manager()
		return settings_payload()


@frappe.whitelist(methods=["POST"])
def save_settings(values: dict | str, acting_user: str | None = None) -> dict:
	"""Save the fields sent; `agents` replaces the mapping. A secret that isn't sent is kept;
	"" removes it."""
	with acting_as(acting_user):
		require_manager()
		values = frappe.parse_json(values) if isinstance(values, str) else values
		if not isinstance(values, dict):
			frappe.throw(_("values must be an object."))
		doc = frappe.get_single(SETTINGS)
		for field in FIELDS:
			if field in values:
				doc.set(field, values[field])
		for field in SECRET_FIELDS:
			if field in values:
				doc.set(field, (values[field] or "").strip())
		if "agents" in values:
			doc.set("telephony_agents", [])
			for row in values["agents"] or []:
				doc.append("telephony_agents", {field: (row or {}).get(field) for field in AGENT_FIELDS})
		doc.save(ignore_permissions=True)
		return settings_payload()


@frappe.whitelist(methods=["POST"])
def regenerate_webhook_secret(acting_user: str | None = None) -> dict:
	"""A new webhook secret: {secret, header, url}. Shown this once; the old one stops working."""
	with acting_as(acting_user):
		require_manager()
		value = frappe.generate_hash(length=40)
		doc = frappe.get_single(SETTINGS)
		doc.telephony_webhook_secret = value
		doc.save(ignore_permissions=True)
		return {"secret": value, "header": TOKEN_HEADER, "url": webhook_url()}


@frappe.whitelist(methods=["POST"])
def test_connection(acting_user: str | None = None) -> dict:
	"""Checks the saved settings without placing a call: the calling fields are there and the API
	token lists MyOperator's users (returned, to fill the mapping). {ok, message, missing, users}."""
	with acting_as(acting_user):
		require_manager()
		missing = myoperator.missing_call_settings()
		try:
			users = myoperator.list_users()
		except myoperator.MyOperatorError as e:
			return {"ok": False, "message": str(e), "missing": missing, "users": []}
		message = (
			_("MyOperator answered with {0} users. Click-to-call still needs: {1}.").format(
				len(users), ", ".join(missing)
			)
			if missing
			else _("MyOperator answered with {0} users. Click-to-call is set up.").format(len(users))
		)
		return {"ok": not missing, "message": message, "missing": missing, "users": users}
