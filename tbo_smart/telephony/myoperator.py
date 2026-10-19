"""MyOperator's APIs and webhook payloads (docs/telephony-myoperator.md §2).

Everything MyOperator-shaped lives here, so a field MyOperator names differently than its
documentation says is fixed in one place: the request bodies (`click_to_call_body`), the
webhook fields (`parse_event`) and the recording link answer (`find_url`).

Sources: MyOperator's API reference (Postman), https://documenter.getpostman.com/view/38426694/2sAXqy3evq
"""

from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_datetime, get_system_timezone

from tbo_smart.smart_leads.utils import normalise_phone
from tbo_smart.telephony import ANSWERED, INCOMING, MISSED, OUTGOING, VOICEMAIL, secret, setting

OBD_URL = "https://obd-api.myoperator.co/obd-api-v1"
DEVELOPERS_URL = "https://developers.myoperator.co"
USERS_PATH = "/search/user"
RECORDING_LINK_PATH = "/search/recordings/link"
TIMEOUT = 15
# the events that carry a finished call; call.summary is the full one, call.end may come alone
CALL_EVENTS = ("call.end", "call.summary")
FINAL_EVENT = "call.summary"
STATUSES = {"bridged": ANSWERED, "missed": MISSED, "voicemail": VOICEMAIL}
DIRECTIONS = {"incoming": INCOMING, "outgoing": OUTGOING}
# User dialer: MyOperator rings the agent's MyOperator user first, then the customer. With only
# a mobile number (no user ID) the anonymous dialer bridges the agent's mobile and the customer.
DIALER_TYPE = "1"
MAX_CALL_SECONDS = 5400


class MyOperatorError(frappe.ValidationError):
	"""MyOperator refused or couldn't be reached; the message is for people."""


# ---------- click-to-call (OBD API) ----------


def e164(number: str | None) -> str | None:
	"""+<country code><number>, as MyOperator wants every number."""
	digits = normalise_phone(number)
	return f"+{digits}" if digits else None


def click_to_call_body(customer: str, agent: dict, reference_id: str) -> dict:
	"""The OBD API body: the user dialer when the agent has a MyOperator user ID, else the
	anonymous dialer to their mobile. `agent`: {myoperator_user_id, mobile}."""
	body = {
		"company_id": (setting("myoperator_company_id") or "").strip(),
		"secret_token": secret("myoperator_secret_token") or "",
		"type": DIALER_TYPE,
		"number": customer,
		"public_ivr_id": (setting("myoperator_public_ivr_id") or "").strip(),
		"reference_id": reference_id,
		"call_hold": True,
	}
	if agent.get("myoperator_user_id"):
		body["user_id"] = agent["myoperator_user_id"]
	else:
		body["number_2"] = e164(agent.get("mobile"))
	caller_id = (setting("myoperator_caller_id") or "").strip()
	if caller_id:
		body["caller_id"] = caller_id
	return body


def missing_call_settings() -> list[str]:
	"""Labels of the settings click-to-call can't do without."""
	return [
		label
		for label, value in (
			(_("MyOperator company ID"), setting("myoperator_company_id")),
			(_("Public IVR ID"), setting("myoperator_public_ivr_id")),
			(_("Calling API key"), secret("myoperator_api_key")),
			(_("Calling secret token"), secret("myoperator_secret_token")),
		)
		if not (value or "").strip()
	]


def place_call(body: dict) -> dict:
	"""POST the OBD request; {unique_id, reference_id} when MyOperator accepted it."""
	try:
		response = requests.post(
			OBD_URL,
			json=body,
			headers={"x-api-key": secret("myoperator_api_key") or "", "Content-Type": "application/json"},
			timeout=TIMEOUT,
		)
	except requests.RequestException:
		raise MyOperatorError(_("Couldn't reach MyOperator. Try again in a minute.")) from None
	try:
		answer = response.json()
	except ValueError:
		answer = {}
	answer = answer if isinstance(answer, dict) else {}
	if response.ok and str(answer.get("status")).lower() == "success":
		return {"unique_id": answer.get("unique_id"), "reference_id": answer.get("reference_id")}
	raise MyOperatorError(
		call_refused(cint(answer.get("code")) or response.status_code, answer.get("details"))
	)


def call_refused(status: int, details) -> str:
	"""A refusal people can act on; MyOperator's own words only for a rejected request."""
	if status == 401:
		return _("MyOperator refused the calling API key. Ask an admin to check Settings → Telephony.")
	if status == 403:
		return _(
			"MyOperator refused the call: the site's IP isn't allowed, the usage limit or billing ran out, or the same call was asked twice. Ask an admin to check MyOperator."
		)
	if status == 400 and details:
		return _("MyOperator couldn't place the call: {0}").format(str(details)[:200])
	if status == 404:
		return _("MyOperator doesn't know this company or IVR. Ask an admin to check Settings → Telephony.")
	return _("MyOperator couldn't place the call ({0}). Try again in a minute.").format(status)


# ---------- users and recordings (developers.myoperator.co, token) ----------


def developers_get(path: str, params: dict) -> dict:
	token = secret("myoperator_api_token")
	if not token:
		raise MyOperatorError(_("Add the MyOperator API token in Settings → Telephony."))
	try:
		response = requests.get(f"{DEVELOPERS_URL}{path}", params={"token": token, **params}, timeout=TIMEOUT)
	except requests.RequestException:
		raise MyOperatorError(_("Couldn't reach MyOperator. Try again in a minute.")) from None
	try:
		answer = response.json()
	except ValueError:
		answer = None
	if not response.ok or not isinstance(answer, dict) or str(answer.get("status")).lower() == "error":
		if response.status_code in (401, 403) or (
			isinstance(answer, dict) and cint(answer.get("code")) in (401, 403)
		):
			raise MyOperatorError(_("MyOperator refused the API token."))
		raise MyOperatorError(_("MyOperator answered {0}.").format(response.status_code))
	return answer


def list_users() -> list[dict]:
	"""MyOperator's users (first 100): {uuid, name, email, extension, contact}."""
	answer = developers_get(USERS_PATH, {"page": 1, "page-size": 100, "_all": 1})
	return [
		{
			"uuid": row.get("uuid"),
			"name": row.get("name"),
			"email": row.get("email"),
			"extension": row.get("extension"),
			"contact": row.get("contact_number"),
		}
		for row in answer.get("data") or []
		if isinstance(row, dict)
	]


def recording_link(filename: str) -> str | None:
	"""A download link for a call's recording (valid 24 hours). MyOperator doesn't document
	the answer's shape, so the first http(s) URL in it is taken."""
	return find_url(developers_get(RECORDING_LINK_PATH, {"file": filename}))


def find_url(value) -> str | None:
	if isinstance(value, str):
		return value if value.startswith(("https://", "http://")) else None
	if isinstance(value, dict):
		value = list(value.values())
	if isinstance(value, list):
		for item in value:
			found = find_url(item)
			if found:
				return found
	return None


# ---------- Webhooks v2 ----------


def parse_event(event: dict) -> dict | None:
	"""The call in a Webhooks v2 call.end / call.summary event, in this site's terms; None for
	any other event. MyOperator: envelope {event_id, event_type, session_id, direction,
	customer_identifier, payload} and payload {id, direction, customer_number, status, started_at,
	ended_at, duration, recording_filename, client_ref_id, legs[{type, result, talk_duration,
	agent{uuid, email, extension, contact}}]}."""
	if not isinstance(event, dict) or event.get("event_type") not in CALL_EVENTS:
		return None
	payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
	call_id = str(payload.get("id") or event.get("session_id") or "").strip()
	if not call_id:
		return None
	direction = DIRECTIONS.get(
		str(payload.get("direction") or event.get("direction") or "").lower(), INCOMING
	)
	agent_leg = pick_agent_leg(payload.get("legs"))
	agent = (agent_leg or {}).get("agent") or {}
	return {
		"call_id": call_id[:140],
		"event_type": event["event_type"],
		"direction": direction,
		"status": STATUSES.get(str(payload.get("status") or "").lower(), MISSED),
		"customer_number": e164(payload.get("customer_number") or event.get("customer_identifier")),
		"started_at": local_time(payload.get("started_at")),
		"ended_at": local_time(payload.get("ended_at")),
		"duration": max(cint(payload.get("duration")), 0),
		"talk_seconds": max(cint((agent_leg or {}).get("talk_duration")), 0),
		"recording_filename": (payload.get("recording_filename") or "").strip() or None,
		"reference_id": (payload.get("client_ref_id") or "").strip() or None,
		"agent": {
			"uuid": agent.get("uuid"),
			"email": agent.get("email"),
			"extension": str(agent.get("extension") or "") or None,
			"contact": agent.get("contact") or (agent_leg or {}).get("phone_number"),
		}
		if agent_leg
		else None,
	}


def pick_agent_leg(legs) -> dict | None:
	"""The agent who talked: the answered agent leg (the last one when the call was
	transferred), else the last agent leg that rang."""
	agent_legs = [leg for leg in legs or [] if isinstance(leg, dict) and leg.get("type") == "agent"]
	answered = [leg for leg in agent_legs if leg.get("result") in ("answered", "connected")]
	return (answered or agent_legs or [None])[-1]


def local_time(value):
	"""An ISO 8601 time from MyOperator in the site's time zone, without a zone (as Frappe
	stores Datetimes)."""
	if not value:
		return None
	try:
		moment = get_datetime(str(value).replace("Z", "+00:00"))
	except Exception:
		return None
	if moment and moment.tzinfo:
		moment = moment.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	return moment
