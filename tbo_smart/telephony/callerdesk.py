"""CallerDesk's click-to-call API and Call Report webhook (docs/telephony-callerdesk.md).

Everything CallerDesk-shaped lives here, as `myoperator.py` does for MyOperator: the request
(`click_to_call_params`), its answer (`place_call`) and the webhook fields (`parse_report`). The
call report becomes the same call shape `myoperator.parse_event` makes, so `calls.py` handles
both the same way.

Sources: https://docs.callerdesk.io/sample-payload (the Call Report fields),
https://docs.callerdesk.io/setup-live-webhook and CallerDesk's API reference at
https://api.callerdesk.io (the key goes in the `authcode` parameter, HTTPS only).
"""

from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_datetime, get_system_timezone

from tbo_smart.smart_leads.utils import normalise_phone
from tbo_smart.telephony import (
	AGENT_PICKED,
	ANSWERED,
	INCOMING,
	MISSED,
	ON_CALL,
	OUTGOING,
	RINGING,
	secret,
	setting,
)

CLICK_TO_CALL_URL = "https://app.callerdesk.io/api/click_to_call_v2"
TIMEOUT = 15
# CallerDesk's panel and every time in its call report are India time
PANEL_TIMEZONE = "Asia/Kolkata"
FINAL_EVENT = "call.summary"
ANSWER = "ANSWER"
OUTGOING_DIRECTIONS = ("WEBOBD", "OBD")
FAILED_STATUSES = ("error", "fail", "failed", "failure", "false", "0")


class CallerDeskError(frappe.ValidationError):
	"""CallerDesk refused or couldn't be reached; the message is for people."""


def has_call_sid(event) -> bool:
	return isinstance(event, dict) and bool(str(event.get("CallSid") or "").strip())


def is_live(event) -> bool:
	"""A Live Call event: a CallSid and none of the fields only a finished call has (EndTime,
	CallDuration). CallerDesk's sample for it lists only SourceNumber, DestinationNumber,
	DialWhomNumber, Status, StartTime, CallSid, Direction and campid."""
	return has_call_sid(event) and not (event.get("EndTime") or event.get("CallDuration"))


def is_report(event) -> bool:
	"""A Call Report: a CallSid and the end of the call."""
	return has_call_sid(event) and not is_live(event)


def live_status(event: dict, outgoing: bool, agent_numbers: set[str]) -> str:
	if str(event.get("Status") or "").strip().upper() != ANSWER:
		return RINGING
	if outgoing and tail(event.get("DialWhomNumber")) in agent_numbers:
		return AGENT_PICKED
	return ON_CALL


def parse_live(event: dict, agent_numbers: set[str]) -> dict | None:
	"""A Live Call event in this site's terms; None when it isn't one. `Status` ANSWER means
	someone picked up, anything else that the call is ringing. An outgoing call's first pick-up
	is the telecaller's own phone (DialWhomNumber is a telecaller's number: "Agent picked"),
	then the customer's ("On call"). Only an incoming call says who the customer and the
	telecaller are (SourceNumber, DialWhomNumber); for an outgoing one the box already knows
	both from the click-to-call's campid."""
	if not is_live(event):
		return None
	outgoing = str(event.get("Direction") or "").upper() in OUTGOING_DIRECTIONS
	source = normalise_phone(event.get("SourceNumber"))
	agent_number = str(event.get("DialWhomNumber") or "")
	return {
		"call_id": str(event["CallSid"]).strip()[:140],
		"direction": OUTGOING if outgoing else INCOMING,
		"live_status": live_status(event, outgoing, agent_numbers),
		"customer_number": f"+{source}" if source and not outgoing else None,
		"reference_id": str(event.get("campid") or "").strip() or None,
		"agent": {"contact": agent_number} if agent_number and not outgoing else None,
		"started_at": local_time(event.get("StartTime")),
	}


def tail(number: str | None) -> str | None:
	"""The last 10 digits of a number: how CallerDesk's numbers (no country code) and ours meet."""
	digits = normalise_phone(number)
	return digits[-10:] if digits else None


def local_time(value):
	"""A CallerDesk time ("yyyy-mm-dd hh:mm:ss", India time) in the site's time zone, without a
	zone (as Frappe stores Datetimes)."""
	if not value:
		return None
	try:
		moment = get_datetime(str(value)).replace(tzinfo=ZoneInfo(PANEL_TIMEZONE))
	except Exception:
		return None
	return moment.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)


def parse_report(event: dict, agent_numbers: set[str]) -> dict | None:
	"""The call in a Call Report, in this site's terms (as `myoperator.parse_event`); None when
	it has no CallSid. `agent_numbers`: the last 10 digits of every mapped telecaller's number.
	Incoming (Direction "IVR"): the caller is SourceNumber and DialWhomNumber answered. Outgoing
	(Direction "WEBOBD"): CallerDesk doesn't say which number is the customer, so it is the one
	that isn't a telecaller's. The call ID is the CallSid, the click-to-call's campid is the
	reference."""
	if not is_report(event):
		return None
	source, dialled = str(event.get("SourceNumber") or ""), str(event.get("DialWhomNumber") or "")
	outgoing = str(event.get("Direction") or "").upper() in OUTGOING_DIRECTIONS
	if not outgoing:
		customer, agent_number = source, dialled
	elif tail(source) in agent_numbers:
		customer, agent_number = dialled, source
	elif tail(dialled) in agent_numbers:
		customer, agent_number = source, dialled
	else:
		customer, agent_number = dialled, None
	answered = str(event.get("Status") or "").strip().upper() == ANSWER
	duration = max(cint(event.get("CallDuration")), 0)
	recording = (event.get("CallRecordingUrl") or "").strip()
	return {
		"call_id": str(event["CallSid"]).strip()[:140],
		"event_type": FINAL_EVENT,
		"direction": OUTGOING if outgoing else INCOMING,
		"status": ANSWERED if answered else MISSED,
		"customer_number": f"+{normalise_phone(customer)}" if normalise_phone(customer) else None,
		"started_at": local_time(event.get("StartTime")),
		"ended_at": local_time(event.get("EndTime")),
		"duration": duration,
		"talk_seconds": (max(cint(event.get("TalkDuration")), 0) or duration) if answered else 0,
		"recording_filename": recording if answered and recording else None,
		"reference_id": str(event.get("campid") or "").strip() or None,
		"agent": {"contact": agent_number, "name": event.get("receiver_name")} if agent_number else None,
		"leg_a_picked_at": local_time(event.get("LegA_Picked_time")),
		"leg_b_picked_at": local_time(event.get("LegB_Picked_time")),
	}


# ---------- click-to-call ----------


def missing_call_settings() -> list[str]:
	"""Labels of the settings click-to-call can't do without."""
	return [
		label
		for label, value in (
			(_("CallerDesk API key"), secret("callerdesk_api_key")),
			(_("Virtual number (deskphone)"), setting("callerdesk_deskphone")),
		)
		if not (value or "").strip()
	]


def dial_number(number: str | None) -> str:
	"""The 10-digit Indian number as CallerDesk takes it (no country code); any other country's
	number with its code."""
	digits = normalise_phone(number) or ""
	return digits[2:] if digits.startswith("91") and len(digits) == 12 else digits


def click_to_call_params(customer: str, agent_number: str) -> dict:
	"""The request: the agent's phone (calling_party_a) rings first, then the customer
	(calling_party_b), from the virtual number; `group_name` when a call group is set."""
	params = {
		"calling_party_a": dial_number(agent_number),
		"calling_party_b": dial_number(customer),
		"deskphone": (setting("callerdesk_deskphone") or "").strip(),
		"call_from_did": "1",
		"authcode": secret("callerdesk_api_key") or "",
	}
	group = (setting("callerdesk_group") or "").strip()
	if group:
		params["group_name"] = group
	return params


def place_call(params: dict) -> dict:
	"""GET the click-to-call request; {campid} when CallerDesk accepted it. CallerDesk doesn't
	document the answer, so a 2xx whose JSON doesn't say it failed is taken as accepted."""
	try:
		response = requests.get(CLICK_TO_CALL_URL, params=params, timeout=TIMEOUT)
	except requests.RequestException:
		# the request's URL carries the key, so nothing of the exception is shown or logged
		raise CallerDeskError(_("Couldn't reach CallerDesk. Try again in a minute.")) from None
	try:
		answer = response.json()
	except ValueError:
		answer = {}
	answer = answer if isinstance(answer, dict) else {}
	reason = refusal_reason(answer)
	if response.ok and not reason:
		return {"campid": find_campid(answer)}
	raise CallerDeskError(call_refused(response.status_code, reason))


def refusal_reason(answer: dict) -> str | None:
	"""CallerDesk's own words when its answer says the call failed; "" when it fails without any."""
	status = str(answer.get("status", answer.get("success", ""))).strip().lower()
	if status in FAILED_STATUSES or answer.get("error"):
		return str(answer.get("message") or answer.get("msg") or answer.get("error") or "")[:200]
	return None


def find_campid(answer: dict) -> str | None:
	"""The call's campid, wherever the answer keeps it."""
	for holder in (answer, answer.get("data")):
		if isinstance(holder, dict) and holder.get("campid"):
			return str(holder["campid"])
	return None


def call_refused(status: int, reason: str | None) -> str:
	"""A refusal people can act on."""
	if status in (401, 403):
		return _(
			"CallerDesk didn't accept the call: it refused the API key. Ask an admin to check Settings → Telephony."
		)
	if reason:
		return _("CallerDesk didn't accept the call: {0}").format(reason)
	return _("CallerDesk didn't accept the call ({0}). Try again in a minute.").format(status)
