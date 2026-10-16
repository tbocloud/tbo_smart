"""What happens on a lead or a deal, in ERPNext's own activity records
(docs/erpnext-lead-cutover.md §2.1):

| Activity | Record |
| --- | --- |
| Note | Comment (`comment_type` "Comment") on the record |
| Call | Event, category Call, Completed, the record as participant; the note is its description |
| Logged meeting, email, WhatsApp | Event (Meeting / Sent/Received Email / Other), subject "Meeting: …" |
| Follow-up, task | ToDo on the record (`date` the due day, `allocated_to` the person) |

Open ToDos and Events are what ERPNext's Lead and Opportunity forms list under Activities. A
ToDo also assigns the record to its person (`_assign`), so they can open it.
"""

import re
from html import unescape

import frappe
from frappe import _
from frappe.query_builder import CustomFunction
from frappe.query_builder.functions import Coalesce, Max, Min
from frappe.utils import escape_html, get_datetime, getdate, now_datetime, strip_html, today

from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.sales import record_label

OPEN, DONE, CANCELLED = "Open", "Closed", "Cancelled"
CLOSED_TASK_STATUSES = (DONE, CANCELLED)
CALL = "Call"
LOG_CATEGORIES = {"meeting": "Meeting", "email": "Sent/Received Email", "whatsapp": "Other"}
LOG_LABELS = {"meeting": "Meeting", "email": "Email", "whatsapp": "WhatsApp"}
TITLE_CHARS = 140
LINE_BREAK = re.compile(r"(?i)<br\s*/?>|</p>|</div>")
# MariaDB's TIMESTAMPDIFF(unit, from, to), as Frappe CRM's dashboard used it
TimestampDiff = CustomFunction("TIMESTAMPDIFF", ["unit", "from", "to"])


def clean_text(text: str | None) -> str:
	return (text or "").strip()


def plain_text(html: str | None) -> str:
	"""What a note, call or log says, as text (tags stripped, entities like &#x27; decoded)."""
	return unescape(strip_html(html or "")).strip()


def task_title(description: str | None) -> str:
	"""A ToDo's title: the first line of its description."""
	text = LINE_BREAK.sub("\n", description or "")
	lines = [line.strip() for line in plain_text(text).splitlines() if line.strip()]
	return lines[0][:TITLE_CHARS] if lines else ""


# ---------- writing ----------


def add_note(doctype: str, name: str, text: str):
	"""A Comment on the record by the session user."""
	return frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": doctype,
			"reference_name": name,
			"content": escape_html(clean_text(text)),
			"comment_email": frappe.session.user,
			"comment_by": frappe.utils.get_fullname(frappe.session.user),
		}
	).insert(ignore_permissions=True)


def add_event(doctype: str, name: str, category: str, subject: str, text: str | None):
	now = now_datetime()
	event = frappe.get_doc(
		{
			"doctype": "Event",
			"subject": subject,
			"event_category": category,
			"event_type": "Private",
			"status": "Completed",
			"send_reminder": 0,
			"starts_on": now,
			"description": escape_html(clean_text(text)) if text else None,
			"event_participants": [{"reference_doctype": doctype, "reference_docname": name}],
		}
	)
	event.insert(ignore_permissions=True)
	return event


def add_call(doctype: str, name: str, text: str, phone: str | None = None):
	"""A completed call Event on the record, with what was said as its description."""
	called = phone or frappe.db.get_value(
		doctype, name, "mobile_no" if doctype == "Lead" else "contact_mobile"
	)
	who = called or record_label(doctype, name) or name
	return add_event(doctype, name, CALL, _("Call to {0}").format(who), text)


def add_log(doctype: str, name: str, kind: str, text: str):
	"""A meeting, email or WhatsApp logged by hand, shown as "Meeting: …"."""
	return add_event(
		doctype, name, LOG_CATEGORIES[kind], f"{_(LOG_LABELS[kind])}: {clean_text(text)[:TITLE_CHARS]}", text
	)


def add_follow_up_task(
	reference_doctype: str,
	name: str,
	due,
	title: str | None = None,
	assigned_to: str | None = None,
	description: str | None = None,
	task_type: str | None = None,
	priority: str = "Medium",
):
	"""An open ToDo on the record due on `due`'s day, for `assigned_to` (default: me).
	`task_type` sets `tbo_task_type` (e.g. "Sales follow-up")."""
	label = record_label(reference_doctype, name)
	title = clean_text(title) or _("Follow up with {0}").format(label or name)
	body = escape_html(title[:TITLE_CHARS])
	if clean_text(description):
		body += "<br><br>" + escape_html(clean_text(description))
	return frappe.get_doc(
		{
			"doctype": "ToDo",
			"status": OPEN,
			"priority": priority,
			"date": getdate(due),
			"allocated_to": assigned_to or frappe.session.user,
			"assigned_by": frappe.session.user,
			"description": body,
			"reference_type": reference_doctype,
			"reference_name": name,
			"tbo_task_type": task_type,
		}
	).insert(ignore_permissions=True)


def close_due_follow_ups(reference_doctype: str, name: str) -> None:
	"""My open tasks on the record due by today are done: the follow-up was answered."""
	for task in frappe.get_all(
		"ToDo",
		filters={
			"reference_type": reference_doctype,
			"reference_name": name,
			"allocated_to": frappe.session.user,
			"status": OPEN,
			"date": ("<=", today()),
		},
		pluck="name",
	):
		doc = frappe.get_doc("ToDo", task)
		doc.status = DONE
		doc.save(ignore_permissions=True)


# ---------- reading ----------


def is_task(row) -> bool:
	"""A follow-up or task, not a plain assignment: it has a due day or a TBO task type."""
	return bool(row.get("date") or row.get("tbo_task_type"))


def events_of(doctype: str, names: list[str], categories=None):
	"""Events with these records as participants: (query, Event, participant table)."""
	Event = frappe.qb.DocType("Event")
	Participant = frappe.qb.DocType("Event Participants")
	query = (
		frappe.qb.from_(Event)
		.join(Participant)
		.on((Participant.parent == Event.name) & (Participant.parenttype == "Event"))
		.where((Participant.reference_doctype == doctype) & Participant.reference_docname.isin(names or [""]))
	)
	if categories:
		query = query.where(Event.event_category.isin(list(categories)))
	return query, Event, Participant


def calls_of(doctype: str, names: list[str]) -> dict[str, list]:
	"""{record: [call]} newest first, each {name, note, at, owner}."""
	query, Event, Participant = events_of(doctype, names, [CALL])
	rows = (
		query.select(
			Event.name,
			Participant.reference_docname.as_("record"),
			Event.description.as_("note"),
			Event.starts_on.as_("at"),
			Event.owner,
		)
		.orderby(Event.starts_on, order=frappe.qb.desc)
		.run(as_dict=True)
	)
	calls: dict[str, list] = {}
	for row in rows:
		calls.setdefault(row.record, []).append(row)
	return calls


def notes_of(doctype: str, names: list[str]) -> dict[str, list]:
	"""{record: [note]} newest first, each {name, content, creation, owner}."""
	Comment = frappe.qb.DocType("Comment")
	rows = (
		frappe.qb.from_(Comment)
		.select(
			Comment.name,
			Comment.reference_name.as_("record"),
			Comment.content,
			Comment.creation,
			Comment.owner,
			Comment.comment_email,
		)
		.where(
			(Comment.reference_doctype == doctype)
			& Comment.reference_name.isin(names or [""])
			& (Comment.comment_type == "Comment")
		)
		.orderby(Comment.creation, order=frappe.qb.desc)
	).run(as_dict=True)
	notes: dict[str, list] = {}
	for row in rows:
		notes.setdefault(row.record, []).append(row)
	return notes


def next_follow_ups(reference_doctype: str, names: list[str]) -> dict:
	"""{record: the earliest open task's due day} (one grouped query)."""
	if not names:
		return {}
	ToDo = frappe.qb.DocType("ToDo")
	return dict(
		(
			frappe.qb.from_(ToDo)
			.select(ToDo.reference_name, Min(ToDo.date))
			.where(
				(ToDo.reference_type == reference_doctype)
				& ToDo.reference_name.isin(names)
				& (ToDo.status == OPEN)
				& ToDo.date.isnotnull()
			)
			.groupby(ToDo.reference_name)
		).run()
	)


def last_calls(doctype: str, names: list[str]) -> dict:
	"""{record: when the newest call was}."""
	if not names:
		return {}
	query, Event, Participant = events_of(doctype, names, [CALL])
	return dict(
		query.select(Participant.reference_docname, Max(Coalesce(Event.starts_on, Event.creation)))
		.groupby(Participant.reference_docname)
		.run()
	)


def get_tasks(reference_doctype: str, name: str) -> list:
	rows = frappe.get_all(
		"ToDo",
		filters={"reference_type": reference_doctype, "reference_name": name, "status": ("!=", CANCELLED)},
		fields=["name", "description", "date", "status", "allocated_to", "tbo_task_type"],
		order_by="date asc",
		limit_page_length=0,
	)
	return [row for row in rows if is_task(row)]


def get_timeline(reference_doctype: str, name: str) -> dict:
	"""{notes, tasks, activities}: the record's notes, tasks, and calls, notes and logged
	meetings / emails / WhatsApp merged newest first."""
	notes = notes_of(reference_doctype, [name]).get(name, [])
	tasks = get_tasks(reference_doctype, name)
	query, Event, _participant = events_of(reference_doctype, [name])
	events = query.select(
		Event.name,
		Event.event_category,
		Event.subject,
		Event.description,
		Event.starts_on,
		Event.owner,
	).run(as_dict=True)
	people = get_full_names({note.owner for note in notes} | {event.owner for event in events})
	note_text = {note.name: plain_text(note.content) for note in notes}
	activities = [
		{
			"id": event.name,
			"at": event.starts_on,
			"who": people.get(event.owner),
			"kind": "call" if event.event_category == CALL else "comment",
			"text": (
				plain_text(event.description) or plain_text(event.subject)
				if event.event_category == CALL
				else plain_text(event.subject)
			),
		}
		for event in events
	]
	activities += [
		{
			"id": note.name,
			"at": note.creation,
			"who": people.get(note.owner),
			"kind": "note",
			"text": note_text[note.name],
		}
		for note in notes
	]
	activities.sort(key=lambda row: get_datetime(row["at"]), reverse=True)
	return {
		"notes": [
			{
				"id": note.name,
				"at": note.creation,
				"who": people.get(note.owner),
				"text": note_text[note.name],
			}
			for note in notes
		],
		"tasks": [
			{
				"id": task.name,
				"title": task_title(task.description),
				"due": task.date,
				"status": task.status,
				"done": task.status == DONE,
				"assigned_to": task.allocated_to,
			}
			for task in tasks
		],
		"activities": activities,
	}
