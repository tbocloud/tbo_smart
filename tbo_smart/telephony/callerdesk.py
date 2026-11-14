"""CallerDesk's click-to-call API and Call Report webhook (docs/telephony-callerdesk.md).

Everything CallerDesk-shaped lives here, as `myoperator.py` does for MyOperator: the request
(`click_to_call_params`), its answer (`place_call`) and the webhook fields (`parse_report`). The
call report becomes the same call shape `myoperator.parse_event` makes, so `calls.py` handles
both the same way.

Sources: CallerDesk's API reference (https://api.callerdesk.io, its Postman collection: webhook
payloads, click-to-call, call report) and CallerDesk support's answers of 2026-11-14; the key
goes in the `authcode` parameter, HTTPS only.
"""

from zoneinfo import ZoneInfo

import frappe
import requests
from frappe import _
from frappe.utils import cint, get_datetime, get_system_timezone

from tbo_smart.smart_leads.utils import normalise_phone
from tbo_smart.telephony import (
	AGENT_PICKED,
	AGENT_SIDE_STATUSES,
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
CALL_LIST_URL = "https://app.callerdesk.io/api/call_list_v2"
# calls the telecaller dialled from CallerDesk's own mobile app ("Single Leg Call Report")
APP_CALL_LIST_URL = "https://app.callerdesk.io/api/app_call_list_v2"
# call_list_v2 pages hold 25 calls ("set at 25 from backend"); a day never needs more than this
MAX_LIST_PAGES = 20
TIMEOUT = 15
# CallerDesk's panel and every time in its call report are India time
PANEL_TIMEZONE = "Asia/Kolkata"
FINAL_EVENT = "call.summary"
ANSWER = "ANSWER"
OUTGOING_DIRECTIONS = ("WEBOBD", "OBD")
REPORT_TYPE = "call_report"
# every `Status` CallerDesk support listed (2026-11-14), compared in lower case
KNOWN_STATUSES = {
	ANSWER.lower(),
	"cancel-agent",
	"cancel-customer",
	"cancel",
	"congestion",
	"agentengaged",
	"abandonedcall",
	"agent-unregistered",
	"unavailable",
	"noanswer",
	"busy",
}
# `CallStatus` only ever says "Answered" or "No Answer"
CALL_STATUS_AS_STATUS = {"answered": ANSWER, "no answer": "NOANSWER"}
AGENT_ON_BREAK = "agent on break"
AGENT_ON_BREAK_HINT = "Ask an admin to add your number as an active member in CallerDesk."


class CallerDeskError(frappe.ValidationError):
	"""CallerDesk refused or couldn't be reached; the message is for people."""


def has_call_sid(event) -> bool:
	return isinstance(event, dict) and bool(str(event.get("CallSid") or "").strip())


def is_live(event) -> bool:
	"""A Live Call event: a CallSid and not a Call Report. CallerDesk's Call Report carries
	`type` "call_report"; its Live Call sample documents no `type`, so any other `type` is live,
	and with no `type` at all the call is live when it has none of the fields only a finished
	call has (EndTime, CallDuration)."""
	if not has_call_sid(event):
		return False
	kind = str(event.get("type") or "").strip().lower()
	if kind:
		return kind != REPORT_TYPE
	return not (event.get("EndTime") or event.get("CallDuration"))


def is_report(event) -> bool:
	"""A Call Report: a CallSid and the end of the call."""
	return has_call_sid(event) and not is_live(event)


def provider_status(event: dict) -> str:
	"""The call's end result as CallerDesk words it: `Status` when it is one of the documented
	values, else `CallStatus` ("Answered" / "No Answer") in `Status`'s words, else whatever
	`Status` said ("" when nothing)."""
	status = str(event.get("Status") or "").strip()
	if status.lower() in KNOWN_STATUSES:
		return status
	return CALL_STATUS_AS_STATUS.get(str(event.get("CallStatus") or "").strip().lower(), status)


def live_status(event: dict, outgoing: bool, agent_numbers: set[str]) -> str:
	if provider_status(event).upper() != ANSWER:
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
	The caller is SourceNumber and DialWhomNumber ("the agent number on which the call has been
	answered") the telecaller, on an incoming call (Direction "IVR") and, as in CallerDesk's
	WEBOBD sample, on an outgoing one; if SourceNumber is a telecaller's mapped number and
	DialWhomNumber isn't, the two are the other way round. The call ID is the CallSid, the
	click-to-call's campid is the reference."""
	if not is_report(event):
		return None
	source, dialled = str(event.get("SourceNumber") or ""), str(event.get("DialWhomNumber") or "")
	outgoing = str(event.get("Direction") or "").upper() in OUTGOING_DIRECTIONS
	if outgoing and tail(source) in agent_numbers and tail(dialled) not in agent_numbers:
		customer, agent_number = dialled, source
	else:
		customer, agent_number = source, dialled
	result = provider_status(event)
	answered = result.upper() == ANSWER
	duration = max(cint(event.get("CallDuration")), 0)
	# TalkDuration is "if the call was answered": a 0 is a real 0, only a missing one is unknown
	talk = max(cint(event.get("TalkDuration")), 0) if str(event.get("TalkDuration") or "") else duration
	recording = (event.get("CallRecordingUrl") or "").strip()
	return {
		"call_id": str(event["CallSid"]).strip()[:140],
		"event_type": FINAL_EVENT,
		"direction": OUTGOING if outgoing else INCOMING,
		"status": ANSWERED if answered else MISSED,
		"provider_status": result[:140],
		"customer_number": f"+{normalise_phone(customer)}" if normalise_phone(customer) else None,
		"started_at": local_time(event.get("StartTime")),
		"ended_at": local_time(event.get("EndTime")),
		"duration": duration,
		"talk_seconds": talk if answered else 0,
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
	"""GET the click-to-call request; {campid} when CallerDesk accepted it. CallerDesk answers
	{"type": "success", "message", "campid", "callerid"} and refuses with HTTP 200
	{"type": "error", "message"}, so only `type` "success" is an accepted call."""
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
	if response.ok and str(answer.get("type") or "").strip().lower() == "success":
		return {"campid": str(answer["campid"]).strip() if answer.get("campid") else None}
	raise CallerDeskError(call_refused(response.status_code, str(answer.get("message") or "")[:200]))


def call_refused(status: int, reason: str | None) -> str:
	"""A refusal people can act on."""
	if status in (401, 403):
		return _(
			"CallerDesk didn't accept the call: it refused the API key. Ask an admin to check Settings → Telephony."
		)
	if reason and AGENT_ON_BREAK in reason.lower():
		return _("CallerDesk didn't accept the call: {0} {1}").format(reason, _(AGENT_ON_BREAK_HINT))
	if reason:
		return _("CallerDesk didn't accept the call: {0}").format(reason)
	return _("CallerDesk didn't accept the call ({0}). Try again in a minute.").format(status)


# ---------- call report (reconcile) ----------


def fetch_call_list(start_date: str, end_date: str, url: str = CALL_LIST_URL) -> list[dict]:
	"""The finished calls between two days (yyyy-mm-dd) from `call_list_v2`, or from
	`app_call_list_v2` (`url`; the same rows) (POST form: authcode, start_date, end_date,
	current_page), page after page until one adds nothing new. [] when
	CallerDesk answers something else than calls; CallerDeskError when it can't be reached."""
	rows: dict[str, dict] = {}
	for page in range(1, MAX_LIST_PAGES + 1):
		data = {
			"authcode": secret("callerdesk_api_key") or "",
			"start_date": start_date,
			"end_date": end_date,
			"current_page": page,
		}
		try:
			response = requests.post(url, data=data, timeout=TIMEOUT)
			answer = response.json() if response.ok else {}
		except (requests.RequestException, ValueError):
			# the request carries the key, so nothing of the exception is shown or logged
			raise CallerDeskError(_("Couldn't read CallerDesk's call list.")) from None
		found = answer.get("result") if isinstance(answer, dict) else answer
		fresh = {
			str(row["sid_id"]).strip(): row
			for row in (found if isinstance(found, list) else [])
			if isinstance(row, dict) and str(row.get("sid_id") or "").strip()
		}
		fresh = {sid: row for sid, row in fresh.items() if sid not in rows}
		if not fresh:
			break
		rows.update(fresh)
	return list(rows.values())


def report_from_row(row: dict, campid: str | None = None, app: bool = False) -> dict | None:
	"""A `call_list_v2` row as the Call Report the webhook would have sent (so `parse_report`
	and the normal receive path take it); None while the call has no end (still running) or no
	`sid_id`. The row's `caller_num` is the customer and `member_num` the telecaller (as the
	live call's `msisdn` and `member_num`); `callresult` "Click-to-call" and a `channel` of
	WEBOBD / OBD mean an outgoing call. A call dialled from the CallerDesk app (`app`) is outgoing
	unless the row says IVR."""
	sid = str(row.get("sid_id") or "").strip()
	if not sid or not row.get("enddatetime"):
		return None
	flow = str(row.get("channel") or row.get("Flow_type") or "").upper()
	outgoing = (
		flow in OUTGOING_DIRECTIONS
		or "click" in str(row.get("callresult") or "").lower()
		or (app and flow != "IVR")
	)
	return {
		"type": REPORT_TYPE,
		"CallSid": sid,
		"Direction": "WEBOBD" if outgoing else "IVR",
		"SourceNumber": row.get("caller_num") or "",
		"DialWhomNumber": row.get("member_num") or "",
		"Status": row.get("callstatus") or "",
		"StartTime": row.get("startdatetime"),
		"EndTime": row.get("enddatetime"),
		"CallDuration": row.get("total_duration"),
		"TalkDuration": row.get("talk_duration"),
		"CallRecordingUrl": row.get("file") or "",
		"campid": campid or str(row.get("campid") or ""),
		"receiver_name": row.get("member_name"),
		"LegA_Picked_time": row.get("LegA_Picked_time"),
		"LegB_Picked_time": row.get("LegB_Picked_time"),
	}
