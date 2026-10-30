"""Telephony through MyOperator (docs/telephony-myoperator.md).

Behind TBO Smart Settings → Telephony (MyOperator) (`enable_telephony`), off by default.
MyOperator's Webhooks v2 tell this site about every call; the call lands on the lead as a call
Event with its recording, a missed call becomes a "Call back" task, and with the AI call
summary on, the recording is transcribed and summed up on the lead. Click-to-call asks
MyOperator to ring the salesperson's phone first, then the lead.
"""

import frappe
from frappe.utils import cint

from tbo_smart.sales import erpnext_installed

SETTINGS = "TBO Smart Settings"
LEAD_SOURCE = "Phone call"
INCOMING, OUTGOING = "Incoming", "Outgoing"
ANSWERED, MISSED, VOICEMAIL = "Answered", "Missed", "Voicemail"
# what a salesperson can say about a call they logged by hand, and the status it is stored as
MANUAL_OUTCOMES = {
	"Connected": ANSWERED,
	"No answer": MISSED,
	"Not answered": MISSED,
	"Busy": MISSED,
	"Voicemail": VOICEMAIL,
	"Call back": ANSWERED,
	"Not interested": ANSWERED,
	"Wrong number": MISSED,
}
DEFAULT_MIN_SUMMARY_SECONDS = 30
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-transcribe"
DEFAULT_RECORDING_DAYS = 180


def setting(fieldname: str):
	return frappe.db.get_single_value(SETTINGS, fieldname)


def telephony_enabled() -> bool:
	return erpnext_installed() and bool(cint(setting("enable_telephony")))


def summary_enabled() -> bool:
	return telephony_enabled() and bool(cint(setting("enable_call_summary")))


def min_summary_seconds() -> int:
	return cint(setting("call_summary_min_seconds")) or DEFAULT_MIN_SUMMARY_SECONDS


def recording_days() -> int:
	return cint(setting("telephony_recording_days")) or DEFAULT_RECORDING_DAYS


def secret(fieldname: str) -> str | None:
	from frappe.utils.password import get_decrypted_password

	return get_decrypted_password(SETTINGS, SETTINGS, fieldname, raise_exception=False)
