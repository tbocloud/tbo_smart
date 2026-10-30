"""Calls from MyOperator onto leads (docs/telephony-myoperator.md §3).

The webhook only stores the call (`receive`, a TBO Call keyed by MyOperator's call ID, so a
retried or repeated event changes nothing) and queues `process_call`, which finds the lead (the
click-to-call that started it, else the number), creates one for an unknown caller, writes the
call Event on the lead as the agent, adds a "Call back" task for a missed call, and queues the
recording's download and the AI summary.
"""

import json
from contextlib import contextmanager
from uuid import uuid4

import frappe
import requests
from frappe import _
from frappe.query_builder import CustomFunction
from frappe.query_builder.functions import Coalesce, Count, Sum
from frappe.utils import add_days, cint, getdate, now_datetime, today

from tbo_smart import lead_source
from tbo_smart.lead_pipeline.statuses import OPEN_STATUSES
from tbo_smart.sales.activities import CALL, CANCELLED, OPEN, add_event, add_follow_up_task
from tbo_smart.sales.stages import system_save
from tbo_smart.smart_leads import FOLLOW_UP_TYPE
from tbo_smart.smart_leads.utils import normalise_phone
from tbo_smart.telephony import (
	ANSWERED,
	INCOMING,
	LEAD_SOURCE,
	MANUAL_OUTCOMES,
	MISSED,
	OUTGOING,
	SETTINGS,
	min_summary_seconds,
	myoperator,
	recording_days,
	setting,
	summary_enabled,
	telephony_enabled,
)

DOCTYPE = "TBO Call"
AGENT_DOCTYPE = "TBO Telephony Agent"
REFERENCE_KEY = "tbo_smart:telephony:ref:{}"
REFERENCE_SECONDS = 6 * 60 * 60
DIAL_LOCK_KEY = "tbo_smart:telephony:dialing:{}"
DIAL_LOCK_SECONDS = 15
MAX_RECORDING_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT = 60
PHONE_FIELDS = ("mobile_no", "phone", "whatsapp_no")
CALL_BACK_PREFIX = "Call back"
Replace = CustomFunction("REPLACE", ["text", "old", "new"])


# ---------- the webhook ----------


def receive(event: dict) -> dict:
	"""Store a finished call's event once and queue its processing. A later call.summary
	completes a call first stored from call.end; anything else already seen is a duplicate."""
	call = myoperator.parse_event(event)
	if not call:
		return {"ok": True, "ignored": True}
	values = {
		"direction": call["direction"],
		"status": call["status"],
		"customer_number": call["customer_number"],
		"started_at": call["started_at"],
		"ended_at": call["ended_at"],
		"duration": call["duration"],
		"talk_seconds": call["talk_seconds"],
		"recording_filename": call["recording_filename"],
		"reference_id": call["reference_id"],
		"agent_ref": agent_ref(call["agent"]),
		"last_event": call["event_type"],
		"payload": json.dumps(event),
	}
	final = call["event_type"] == myoperator.FINAL_EVENT
	existing = stored_call(call["call_id"])
	if not existing:
		try:
			name = (
				frappe.get_doc({"doctype": DOCTYPE, "call_id": call["call_id"], **values})
				.insert(ignore_permissions=True)
				.name
			)
		except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
			# another request stored this call at the same moment; a call.summary still completes it
			if not final:
				return {"ok": True, "duplicate": True}
			existing = stored_call(call["call_id"])
	if existing:
		if existing.last_event == myoperator.FINAL_EVENT or not final:
			return {"ok": True, "duplicate": True}
		frappe.db.set_value(DOCTYPE, existing.name, {k: v for k, v in values.items() if v not in (None, "")})
		name = existing.name
	frappe.enqueue(
		"tbo_smart.telephony.calls.process_call",
		call=name,
		queue="short",
		enqueue_after_commit=True,
		now=frappe.flags.in_test,
	)
	return {"ok": True}


def log_manual_call(lead: str, outcome: str, note: str | None = None):
	"""A call the session user made outside MyOperator, logged by hand: an outgoing TBO Call by
	them now (answered, missed or voicemail by `outcome`) and its call Event on the lead, as a
	MyOperator call would leave. Needs write permission on the lead; no telephony setting."""
	if outcome not in MANUAL_OUTCOMES:
		frappe.throw(_("outcome must be one of {0}.").format(", ".join(MANUAL_OUTCOMES)))
	frappe.has_permission("Lead", "write", lead, throw=True)
	now = now_datetime()
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"call_id": f"manual-{uuid4().hex}",
			"direction": OUTGOING,
			"status": MANUAL_OUTCOMES[outcome],
			"lead": lead,
			"agent": frappe.session.user,
			"customer_number": frappe.db.get_value("Lead", lead, "mobile_no"),
			"started_at": now,
			"ended_at": now,
			"duration": 0,
			"talk_seconds": 0,
			"last_event": "manual",
		}
	)
	doc.event = add_event("Lead", lead, CALL, call_subject(doc), note, starts_on=now).name
	return doc.insert(ignore_permissions=True)


def stored_call(call_id: str):
	return frappe.db.get_value(DOCTYPE, {"call_id": call_id}, ["name", "last_event"], as_dict=True)


def agent_ref(agent: dict | None) -> str | None:
	if not agent:
		return None
	return agent.get("uuid") or agent.get("extension") or agent.get("contact") or agent.get("email")


# ---------- processing (background) ----------


def process_call(call: str) -> None:
	"""Background job: the call onto its lead, once. Safe to run again for the same call."""
	if not telephony_enabled():
		return
	doc = frappe.get_doc(DOCTYPE, call, for_update=True)
	parsed = myoperator.parse_event(json.loads(doc.payload or "{}")) or {}
	started = pending_call(doc.reference_id)
	doc.agent = doc.agent or started.get("user") or map_agent(parsed.get("agent"))
	if not doc.lead:
		doc.lead = started.get("lead") or find_lead(doc.customer_number)
	if not doc.lead and doc.direction == INCOMING and cint(setting("telephony_create_leads")):
		doc.lead = new_caller_lead(doc.customer_number, doc.agent or fallback_owner())
	if doc.lead:
		owner = frappe.db.get_value("Lead", doc.lead, "lead_owner")
		with run_as(doc.agent or owner or "Administrator"):
			if doc.event:
				# a call.summary after call.end: the one Event and task follow the final call
				update_call_event(doc)
			else:
				doc.event = log_call_event(doc).name
			settle_call_back(doc, owner or doc.agent or fallback_owner())
	if wants_summary(doc) and not doc.ai_status:
		doc.ai_status = "Pending"
	doc.save(ignore_permissions=True)
	if doc.recording_filename and not doc.recording and doc.status == ANSWERED:
		frappe.enqueue(
			"tbo_smart.telephony.calls.fetch_recording",
			call=doc.name,
			queue="long",
			enqueue_after_commit=True,
			now=frappe.flags.in_test,
		)


@contextmanager
def run_as(user: str):
	"""Write the call's records as the person who took it (owner, timeline), then give the
	job's session back."""
	caller = frappe.session.user
	frappe.set_user(user)  # a background job writing as the call's agent - nosemgrep
	try:
		yield
	finally:
		frappe.set_user(caller)  # back to the job's own user - nosemgrep


def pending_call(reference_id: str | None) -> dict:
	"""{lead, user} of the click-to-call that started this call, while it is remembered."""
	if not reference_id:
		return {}
	return frappe.cache.get_value(REFERENCE_KEY.format(reference_id)) or {}


def call_subject(doc) -> str:
	""" "Call from …" / "Call to …" / "Missed call from …" / "Unanswered call to …"."""
	number = doc.customer_number or _("an unknown number")
	if doc.status != ANSWERED:
		return (
			_("Missed call from {0}").format(number)
			if doc.direction == INCOMING
			else _("Unanswered call to {0}").format(number)
		)
	length = format_duration(doc.talk_seconds or doc.duration)
	if doc.direction == INCOMING:
		return _("Call from {0} ({1})").format(number, length)
	return _("Call to {0} ({1})").format(number, length)


def log_call_event(doc):
	"""The call Event on the lead, as the session user (the agent)."""
	# the AI summary feeds the lead's autopilot itself: no second read for this call
	return add_event(
		"Lead",
		doc.lead,
		CALL,
		call_subject(doc),
		None,
		starts_on=doc.started_at,
		ai_read=not wants_summary(doc),
	)


def update_call_event(doc) -> None:
	"""The call's Event as the final payload has it: subject, start and the agent who took it."""
	values = {"subject": call_subject(doc)}
	if doc.started_at:
		values["starts_on"] = doc.started_at
	if doc.agent:
		values["owner"] = doc.agent
	frappe.db.set_value("Event", doc.event, values, update_modified=False)


def settle_call_back(doc, owner: str | None) -> None:
	"""A missed incoming call has its Call back task; one that turned out answered has it
	cancelled, unless another missed call still waits on the same task."""
	# calls on one lead settle their shared task one at a time: the lead's row is locked until
	# this job commits, with the call's own link saved
	frappe.db.get_value("Lead", doc.lead, "name", for_update=True)
	missed = doc.status == MISSED and doc.direction == INCOMING
	if missed and not doc.call_back:
		doc.call_back = call_back_task(doc.lead, owner, doc.customer_number)
	elif not missed and doc.call_back and not other_missed_call(doc):
		# the task stays linked on the call, for the record; another missed call may still need it
		if frappe.db.get_value("ToDo", doc.call_back, "status") == OPEN:
			frappe.db.set_value("ToDo", doc.call_back, "status", CANCELLED)


def other_missed_call(doc) -> bool:
	"""Another missed call shares this one's Call back task (one task a person a day)."""
	return bool(
		frappe.db.exists(DOCTYPE, {"call_back": doc.call_back, "name": ("!=", doc.name), "status": MISSED})
	)


def format_duration(seconds) -> str:
	seconds = cint(seconds)
	return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def call_back_task(lead: str, owner: str | None, number: str | None) -> str | None:
	"""A high-priority "Call back" for today, unless one is already open for the person today:
	the task's name."""
	if not owner:
		return None
	ToDo = frappe.qb.DocType("ToDo")
	open_today = (
		frappe.qb.from_(ToDo)
		.select(ToDo.name)
		.where(
			(ToDo.reference_type == "Lead")
			& (ToDo.reference_name == lead)
			& (ToDo.allocated_to == owner)
			& (ToDo.status == OPEN)
			& (ToDo.date == getdate())
			& ToDo.description.like(f"{CALL_BACK_PREFIX}%")
		)
		.limit(1)
	).run()
	if open_today:
		return open_today[0][0]
	return add_follow_up_task(
		"Lead",
		lead,
		today(),
		title=f"{CALL_BACK_PREFIX}: {number or ''}".strip(": "),
		assigned_to=owner,
		task_type=FOLLOW_UP_TYPE,
		priority="High",
	).name


def fallback_owner() -> str | None:
	return setting("telephony_fallback_owner") or setting("lead_fallback_owner")


def new_caller_lead(number: str | None, owner: str | None) -> str | None:
	"""A new lead for an unknown caller: named by their number, source Phone call."""
	if not number:
		return None
	lead = frappe.new_doc("Lead")
	lead.update(
		{
			"first_name": number,
			"mobile_no": number,
			lead_source.field(): LEAD_SOURCE if lead_source.exists(LEAD_SOURCE) else None,
			"lead_owner": owner,
		}
	)
	with run_as(owner or "Administrator"):
		# a caller's lead has only their number: Source may be required on the site, but the
		# call can't wait for it
		system_save(lead, ignore_permissions=True)
	return lead.name


# ---------- finding the lead and the agent ----------


def find_lead(number: str | None) -> str | None:
	"""The lead with this mobile, phone or WhatsApp number (spaces, dashes, +91 and a trunk 0
	don't matter): an open one first, then the newest."""
	digits = normalise_phone(number)
	if not digits:
		return None
	tail = digits[-10:]
	Lead = frappe.qb.DocType("Lead")

	def bare(field):
		value = field
		for char in (" ", "-", "(", ")", "."):
			value = Replace(value, char, "")
		return value

	criterion = None
	for fieldname in PHONE_FIELDS:
		match = bare(Lead[fieldname]).like(f"%{tail}")
		criterion = match if criterion is None else criterion | match
	rows = (
		frappe.qb.from_(Lead)
		.select(Lead.name, Lead.tbo_stage, Lead.mobile_no, Lead.phone, Lead.whatsapp_no)
		.where(criterion)
		.orderby(Lead.modified, order=frappe.qb.desc)
		.limit(20)
	).run(as_dict=True)
	same = [row for row in rows if any(same_number(row[field], digits) for field in PHONE_FIELDS)]
	same.sort(key=lambda row: row.tbo_stage not in OPEN_STATUSES)
	return same[0].name if same else None


def same_number(stored: str | None, digits: str) -> bool:
	other = normalise_phone(stored)
	return bool(other) and (other == digits or (len(other) >= 10 and other[-10:] == digits[-10:]))


def agent_rows() -> list:
	return frappe.get_all(
		AGENT_DOCTYPE,
		filters={"parenttype": SETTINGS, "parentfield": "telephony_agents"},
		fields=["user", "myoperator_user_id", "extension", "mobile"],
		order_by="idx asc",
	)


def map_agent(agent: dict | None) -> str | None:
	"""The user behind MyOperator's agent: by user ID, extension or mobile in the mapping, else
	an enabled user with the agent's email."""
	if not agent:
		return None
	contact = normalise_phone(agent.get("contact"))
	for row in agent_rows():
		if (
			(agent.get("uuid") and row.myoperator_user_id == agent["uuid"])
			or (agent.get("extension") and row.extension == str(agent["extension"]))
			or (contact and normalise_phone(row.mobile) == contact)
		):
			return row.user
	email = (agent.get("email") or "").strip()
	if email and frappe.db.get_value("User", {"name": email, "enabled": 1, "user_type": "System User"}):
		return email
	return None


def agent_of(user: str):
	"""The person's mapping row, or None."""
	return next((row for row in agent_rows() if row.user == user), None)


def can_call(user: str | None = None) -> bool:
	"""Telephony is on and the person has a sales role and a MyOperator user ID or a mobile to
	ring: the app's click_to_call capability and the hub's can_call."""
	from tbo_smart.api.mobile.capabilities import CRM_ROLES  # it imports this module

	user = user or frappe.session.user
	if not telephony_enabled() or not CRM_ROLES & set(frappe.get_roles(user)):
		return False
	agent = agent_of(user)
	return bool(agent and ((agent.myoperator_user_id or "").strip() or normalise_phone(agent.mobile)))


# ---------- the recording ----------


def fetch_recording(call: str) -> None:
	"""Background job: download the recording (MyOperator's links last 24 hours) into a
	private File on the call, then queue the AI summary when it is wanted."""
	doc = frappe.get_doc(DOCTYPE, call, for_update=True)
	if doc.recording or not doc.recording_filename:
		return
	try:
		content = download(myoperator.recording_link(doc.recording_filename))
	except (myoperator.MyOperatorError, requests.RequestException, ValueError) as e:
		frappe.log_error(title=f"MyOperator recording not fetched: {doc.name}", message=str(e)[:300])
		return
	recording = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"call-{doc.name}.mp3",
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": doc.name,
			"attached_to_field": "recording",
			"is_private": 1,
			"content": content,
		}
	).insert(ignore_permissions=True)
	doc.db_set("recording", recording.file_url)
	if doc.ai_status == "Pending":
		frappe.enqueue(
			"tbo_smart.telephony.summary.summarise_call",
			call=doc.name,
			queue="long",
			enqueue_after_commit=True,
			now=frappe.flags.in_test,
		)


def download(url: str | None) -> bytes:
	if not url or not url.startswith("https://"):
		raise ValueError("MyOperator gave no https recording link")
	with requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True) as response:
		response.raise_for_status()
		chunks, size = [], 0
		for chunk in response.iter_content(64 * 1024):
			size += len(chunk)
			if size > MAX_RECORDING_BYTES:
				raise ValueError("the recording is larger than 50 MB")
			chunks.append(chunk)
	return b"".join(chunks)


def wants_summary(doc) -> bool:
	"""An answered call on a lead, long enough, with a recording, while the summary is on."""
	return bool(
		summary_enabled()
		and doc.lead
		and doc.status == ANSWERED
		and doc.recording_filename
		and cint(doc.talk_seconds or doc.duration) >= min_summary_seconds()
	)


def recording_file(call: str):
	"""The call's recording File for a person who may read its lead; "not found" otherwise."""
	row = frappe.db.get_value(DOCTYPE, call, ["lead", "recording"], as_dict=True)
	if not row or not row.lead or not row.recording:
		frappe.throw(_("This recording isn't available."), frappe.DoesNotExistError)
	frappe.has_permission("Lead", "read", row.lead, throw=True)
	name = frappe.db.get_value("File", {"file_url": row.recording, "attached_to_doctype": DOCTYPE})
	if not name:
		frappe.throw(_("This recording isn't available."), frappe.DoesNotExistError)
	return frappe.get_doc("File", name)


def purge_old_recordings() -> None:
	"""Daily, with telephony on or off: recordings and transcripts older than the retention days
	go (by the call's start, else when it was stored); the call and its summary stay."""
	Call = frappe.qb.DocType(DOCTYPE)
	cutoff = add_days(getdate(), -recording_days())
	old = (
		frappe.qb.from_(Call)
		.select(Call.name)
		.where(
			(Coalesce(Call.started_at, Call.creation) < cutoff)
			& (Call.recording.isnotnull() | Call.transcript.isnotnull())
		)
	).run(pluck=True)
	for name in old:
		for file in frappe.get_all(
			"File", filters={"attached_to_doctype": DOCTYPE, "attached_to_name": name}, pluck="name"
		):
			frappe.delete_doc("File", file, ignore_permissions=True, force=True)
		frappe.db.set_value(DOCTYPE, name, {"recording": None, "transcript": None})


# ---------- click-to-call ----------


def click_to_call(lead: str, number: str | None = None) -> dict:
	"""Ask MyOperator to ring the session user's phone, then the lead's number (one of the
	lead's own numbers; default its mobile)."""
	if not telephony_enabled():
		frappe.throw(_("Calling through MyOperator is switched off."))
	frappe.has_permission("Lead", "read", lead, throw=True)
	user = frappe.session.user
	agent = agent_of(user)
	if not can_call(user):
		frappe.throw(
			_("You aren't set up for calling yet. Ask an admin to add you under Settings → Telephony.")
		)
	missing = myoperator.missing_call_settings()
	if missing:
		frappe.throw(_("Telephony isn't set up yet: add {0}.").format(", ".join(missing)))
	customer = lead_number(lead, number)
	key = frappe.cache.make_key(DIAL_LOCK_KEY.format(user))
	if not frappe.cache.set(key, 1, nx=True, ex=DIAL_LOCK_SECONDS):  # site-prefixed via make_key - nosemgrep
		frappe.throw(_("A call is already being placed. Wait a few seconds."))
	reference_id = str(uuid4())
	frappe.cache.set_value(
		REFERENCE_KEY.format(reference_id), {"lead": lead, "user": user}, expires_in_sec=REFERENCE_SECONDS
	)
	answer = myoperator.place_call(myoperator.click_to_call_body(customer, agent, reference_id))
	return {
		"ok": True,
		"message": _("MyOperator is calling your phone…"),
		"number": customer,
		"reference_id": reference_id,
		"request_id": answer.get("unique_id"),
	}


def lead_number(lead: str, number: str | None) -> str:
	numbers = frappe.db.get_value("Lead", lead, list(PHONE_FIELDS), as_dict=True) or {}
	own = [myoperator.e164(numbers.get(field)) for field in PHONE_FIELDS]
	own = [value for value in own if value]
	if number:
		wanted = myoperator.e164(number)
		if wanted not in own:
			frappe.throw(_("Call one of the lead's own numbers."))
		return wanted
	if not own:
		frappe.throw(_("This lead has no phone number."))
	return own[0]


# ---------- reading ----------


def call_details(events: list[str]) -> dict:
	"""{event: {call, duration, direction, status, recording, summary}} for the call Events that
	came through MyOperator. `recording` is the call's name when it has one (the player asks a
	permission-checked endpoint for it); the transcript never leaves the server."""
	if not events:
		return {}
	Call = frappe.qb.DocType(DOCTYPE)
	rows = (
		frappe.qb.from_(Call)
		.select(
			Call.name,
			Call.event,
			Call.duration,
			Call.talk_seconds,
			Call.direction,
			Call.status,
			Call.recording,
			Call.summary,
		)
		.where(Call.event.isin(events))
	).run(as_dict=True)
	return {
		row.event: {
			"call": row.name,
			"duration": cint(row.talk_seconds or row.duration),
			"direction": row.direction.lower() if row.direction else None,
			"status": row.status.lower() if row.status else None,
			"recording": row.name if row.recording else None,
			"summary": parse_summary(row.summary),
		}
		for row in rows
	}


def parse_summary(value) -> dict | None:
	if not value:
		return None
	if isinstance(value, dict):
		return value
	try:
		parsed = json.loads(value)
	except ValueError:
		return None
	return parsed if isinstance(parsed, dict) else None


def calls_by_agent(start, end, owners: list | None) -> dict:
	"""{agent: {calls, talk_seconds}}: calls answered from `start` to before `end`, by the
	person who took them (only `owners` when given)."""
	Call = frappe.qb.DocType(DOCTYPE)
	query = (
		frappe.qb.from_(Call)
		.select(Call.agent, Count(Call.name), Sum(Call.talk_seconds))
		.where(
			Call.agent.isnotnull()
			& (Call.status == ANSWERED)
			& (Call.started_at >= start)
			& (Call.started_at < end)
		)
		.groupby(Call.agent)
	)
	if owners is not None:
		query = query.where(Call.agent.isin(owners or [""]))
	return {agent: {"calls": cint(count), "talk_seconds": cint(talk)} for agent, count, talk in query.run()}
