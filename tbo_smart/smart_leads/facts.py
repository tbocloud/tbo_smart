"""What is known about a lead, gathered in bulk (docs/smart-leads.md §3.3).

One `LeadFacts` loads, for a list of leads and in a fixed number of queries: the lead fields, the
migration note, the notes and calls since, the open and done follow-ups, the status changes,
emails and comments. Scoring, Call today, the follow-up rules, the brief and the triage all read
the same facts, so a score, a list and a summary never disagree.
"""

import hashlib
import json

import frappe
from frappe.query_builder.functions import Max, Min
from frappe.utils import cstr, date_diff, get_datetime, getdate, now_datetime, strip_html

from tbo_smart import ai
from tbo_smart.smart_leads import CLOSED_TASK_STATUSES, OPEN_STATUSES
from tbo_smart.smart_leads.migration import NOTE_TITLE_PREFIX, get_migration_notes

LEAD_FIELDS = [
	"name",
	"lead_name",
	"organization",
	"status",
	"source",
	"lead_owner",
	"mobile_no",
	"phone",
	"email",
	"creation",
	"converted",
	"lead_score",
	"lead_temperature",
	"tbo_score_reason",
	"tbo_ai_adjust",
	"tbo_ai_reason",
	"tbo_ai_hash",
	"tbo_ai_brief",
	"tbo_ai_brief_hash",
	"tbo_ai_brief_at",
]
NOTES_PER_LEAD = 8
NOTE_CHARS = 300
REMARK_CHARS = 600


def parse_date(value):
	"""A date from a migration note value (ISO, or the formats Frappe's getdate reads)."""
	try:
		return getdate(cstr(value).strip()[:10]) if value else None
	except Exception:
		return None


def clip(text: str | None, limit: int) -> str:
	text = " ".join(cstr(text).split())
	return text if len(text) <= limit else text[: limit - 1] + "…"


class LeadFacts:
	def __init__(self, names: list[str], today=None):
		self.today = getdate(today)
		self.names = list(dict.fromkeys(name for name in names if name))
		self.leads = {row.name: row for row in self.load_leads()}
		self.names = [name for name in self.names if name in self.leads]
		self.migration = get_migration_notes(self.names)
		self.notes = self.load_notes()
		self.calls = self.load_calls()
		self.follow_ups = self.load_follow_ups()
		self.done_tasks = self.max_by("CRM Task", "modified", {"status": "Done"})
		self.status_changes = self.load_status_changes()
		self.emails = self.load_emails()
		self.comments = self.max_by_comment()

	# ---------- loading ----------

	def load_leads(self) -> list:
		if not self.names:
			return []
		return frappe.get_all(
			"CRM Lead", filters={"name": ("in", self.names)}, fields=LEAD_FIELDS, limit_page_length=0
		)

	def load_notes(self) -> dict[str, list]:
		"""Notes other than the migration note, newest first (call notes included)."""
		if not self.names:
			return {}
		Note = frappe.qb.DocType("FCRM Note")
		rows = (
			frappe.qb.from_(Note)
			.select(Note.name, Note.reference_docname, Note.title, Note.content, Note.creation, Note.owner)
			.where(
				(Note.reference_doctype == "CRM Lead")
				& (Note.reference_docname.isin(self.names))
				& (Note.title.not_like(f"{NOTE_TITLE_PREFIX}%"))
			)
			.orderby(Note.creation, order=frappe.qb.desc)
		).run(as_dict=True)
		notes: dict[str, list] = {}
		for row in rows:
			notes.setdefault(row.reference_docname, []).append(row)
		return notes

	def load_calls(self) -> dict[str, list]:
		if not self.names:
			return {}
		Call = frappe.qb.DocType("CRM Call Log")
		rows = (
			frappe.qb.from_(Call)
			.select(
				Call.name,
				Call.reference_docname,
				Call.note,
				Call.type,
				Call.status,
				Call.creation,
				Call.start_time,
			)
			.where((Call.reference_doctype == "CRM Lead") & (Call.reference_docname.isin(self.names)))
			.orderby(Call.creation, order=frappe.qb.desc)
		).run(as_dict=True)
		calls: dict[str, list] = {}
		for row in rows:
			calls.setdefault(row.reference_docname, []).append(row)
		return calls

	def load_follow_ups(self) -> dict[str, list]:
		"""Open tasks on each lead with a due date, earliest first."""
		if not self.names:
			return {}
		Task = frappe.qb.DocType("CRM Task")
		rows = (
			frappe.qb.from_(Task)
			.select(
				Task.name,
				Task.reference_docname,
				Task.title,
				Task.due_date,
				Task.assigned_to,
				Task.tbo_escalation_level,
				Task.creation,
			)
			.where(
				(Task.reference_doctype == "CRM Lead")
				& (Task.reference_docname.isin(self.names))
				& (Task.status.notin(CLOSED_TASK_STATUSES))
			)
			.orderby(Task.due_date)
		).run(as_dict=True)
		tasks: dict[str, list] = {}
		for row in rows:
			tasks.setdefault(row.reference_docname, []).append(row)
		return tasks

	def max_by(self, doctype: str, field: str, filters: dict) -> dict:
		if not self.names:
			return {}
		Table = frappe.qb.DocType(doctype)
		query = (
			frappe.qb.from_(Table)
			.select(Table.reference_docname, Max(Table[field]))
			.where((Table.reference_doctype == "CRM Lead") & (Table.reference_docname.isin(self.names)))
			.groupby(Table.reference_docname)
		)
		for key, value in filters.items():
			query = query.where(Table[key] == value)
		return dict(query.run())

	def load_status_changes(self) -> dict[str, list]:
		"""Status changes after the lead was created (its first log row is the insert itself,
		which for the migrated leads is the import of 2026-10-14)."""
		if not self.names:
			return {}
		Log = frappe.qb.DocType("CRM Status Change Log")
		rows = (
			frappe.qb.from_(Log)
			.select(Log.parent, Log["from"].as_("status"), Log.from_date, Log.log_owner, Log.idx)
			.where((Log.parenttype == "CRM Lead") & (Log.parent.isin(self.names)) & (Log.idx > 1))
			.orderby(Log.idx)
		).run(as_dict=True)
		changes: dict[str, list] = {}
		for row in rows:
			changes.setdefault(row.parent, []).append(row)
		return changes

	def load_emails(self) -> dict:
		if not self.names:
			return {}
		Communication = frappe.qb.DocType("Communication")
		return dict(
			(
				frappe.qb.from_(Communication)
				.select(Communication.reference_name, Max(Communication.communication_date))
				.where(
					(Communication.reference_doctype == "CRM Lead")
					& (Communication.reference_name.isin(self.names))
					& (Communication.communication_type == "Communication")
				)
				.groupby(Communication.reference_name)
			).run()
		)

	def max_by_comment(self) -> dict:
		if not self.names:
			return {}
		Comment = frappe.qb.DocType("Comment")
		return dict(
			(
				frappe.qb.from_(Comment)
				.select(Comment.reference_name, Max(Comment.creation))
				.where(
					(Comment.reference_doctype == "CRM Lead")
					& (Comment.reference_name.isin(self.names))
					& (Comment.comment_type == "Comment")
				)
				.groupby(Comment.reference_name)
			).run()
		)

	# ---------- per lead ----------

	def lead(self, name: str) -> frappe._dict:
		return self.leads[name]

	def is_open(self, name: str) -> bool:
		lead = self.leads[name]
		return lead.status in OPEN_STATUSES and not lead.converted

	def created_on(self, name: str):
		"""The original created date from the migration note, else the lead's creation."""
		return parse_date(self.migration.get(name, {}).get("created")) or getdate(self.leads[name].creation)

	def last_touch(self, name: str) -> tuple:
		"""(datetime, kind) of the newest note, call, done task, status change, email or comment
		since the lead came in, or (None, None)."""
		candidates = []
		notes = self.notes.get(name) or []
		calls = self.calls.get(name) or []
		if calls:
			candidates.append((get_datetime(calls[0].start_time or calls[0].creation), "call"))
		call_notes = {call.note for call in calls}
		other_notes = [note for note in notes if note.name not in call_notes]
		if other_notes:
			candidates.append((get_datetime(other_notes[0].creation), "note"))
		if self.done_tasks.get(name):
			candidates.append((get_datetime(self.done_tasks[name]), "follow-up done"))
		if self.status_changes.get(name):
			candidates.append((get_datetime(self.status_changes[name][-1].from_date), "status change"))
		if self.emails.get(name):
			candidates.append((get_datetime(self.emails[name]), "email"))
		if self.comments.get(name):
			candidates.append((get_datetime(self.comments[name]), "comment"))
		return max(candidates, key=lambda item: item[0]) if candidates else (None, None)

	def days_since_touch(self, name: str) -> int | None:
		at, _kind = self.last_touch(name)
		return date_diff(self.today, at) if at else None

	def next_follow_up(self, name: str) -> frappe._dict | None:
		"""The earliest open task with a due date, else None."""
		return next((task for task in self.follow_ups.get(name) or [] if task.due_date), None)

	def note_lines(self, name: str) -> list[dict]:
		"""Newest notes and call notes as {on, kind, text}, for the AI."""
		calls = self.calls.get(name) or []
		call_notes = {call.note for call in calls}
		lines = [
			{
				"on": str(getdate(note.creation)),
				"kind": "call" if note.name in call_notes else "note",
				"text": clip(strip_html(note.content or note.title), NOTE_CHARS),
			}
			for note in (self.notes.get(name) or [])[:NOTES_PER_LEAD]
		]
		return [line for line in lines if line["text"]]

	def facts(self, name: str) -> dict:
		"""Everything the AI may use about a lead. Numbers in AI text are checked against it."""
		lead = self.leads[name]
		original = self.migration.get(name, {})
		at, kind = self.last_touch(name)
		follow_up = self.next_follow_up(name)
		return {
			"lead": name,
			"name": lead.lead_name,
			"organization": lead.organization,
			"status": lead.status,
			"source": lead.source,
			"created_on": str(self.created_on(name)),
			"days_since_created": date_diff(self.today, self.created_on(name)),
			"last_touch": {"on": str(getdate(at)), "kind": kind} if at else None,
			"days_since_last_touch": date_diff(self.today, at) if at else None,
			"status_changes": [
				{"to": change.status, "on": str(getdate(change.from_date))}
				for change in (self.status_changes.get(name) or [])[-5:]
			],
			"next_follow_up": str(getdate(follow_up.due_date)) if follow_up else None,
			"before_migration": {
				key: clip(value, REMARK_CHARS)
				for key, value in original.items()
				if key in ("status", "source", "priority", "project_type", "remarks", "created")
			},
			"notes": self.note_lines(name),
			"has_phone": bool(lead.mobile_no or lead.phone),
			"has_email": bool(lead.email),
		}

	def known_numbers(self, name: str, *more) -> set[str]:
		"""The numbers the AI may write about a lead: those in its facts and `more`, not the
		digits of its ID (CRM-LEAD-2026-00050 must not make "50 licences" look like a fact)."""
		facts = {key: value for key, value in self.facts(name).items() if key != "lead"}
		return ai.fact_numbers(facts, *more)

	def content_hash(self, name: str) -> str:
		"""Changes when what a person wrote or decided changes (status, notes, remarks), not
		when a day passes: the AI is asked again only then."""
		lead = self.leads[name]
		payload = {
			"status": lead.status,
			"notes": self.note_lines(name),
			"remarks": self.migration.get(name, {}).get("remarks"),
			"changes": [change.status for change in self.status_changes.get(name) or []],
		}
		return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:20]


def first_due(rows: list) -> dict:
	"""{lead: earliest open follow-up due date} for lead rows (one query); used by lists."""
	names = [row.name for row in rows]
	if not names:
		return {}
	Task = frappe.qb.DocType("CRM Task")
	return dict(
		(
			frappe.qb.from_(Task)
			.select(Task.reference_docname, Min(Task.due_date))
			.where(
				(Task.reference_doctype == "CRM Lead")
				& (Task.reference_docname.isin(names))
				& (Task.status.notin(CLOSED_TASK_STATUSES))
				& Task.due_date.isnotnull()
			)
			.groupby(Task.reference_docname)
		).run()
	)


def now():
	return now_datetime()
