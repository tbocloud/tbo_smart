"""Lead meetings and their minutes (MOM) (docs/lead-pipeline.md §5).

A TBO Lead Meeting belongs to a lead. Its number (1st, 2nd, 3rd, then Additional N) is taken
when it is scheduled; cancelled meetings don't count. Scheduling one moves the lead to Meeting
Scheduled. Once its time has passed without minutes it turns MOM Pending (a job every 15
minutes), the BDE is nudged, and it escalates like a follow-up: the BDM, then the sales head.
Submitting the MOM moves the lead to Nth Meeting Completed and creates the "Sales follow-up"
ToDo for the next follow-up date. Meetings are never deleted by sales users; a meeting
that doesn't happen is Cancelled.
"""

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, escape_html, format_datetime, get_datetime, getdate, now_datetime

from tbo_smart.lead_pipeline import pipeline_enabled, setting
from tbo_smart.lead_pipeline.statuses import MEETING_COMPLETED, MEETING_SCHEDULED, status_type
from tbo_smart.lead_pipeline.transitions import advance_lead
from tbo_smart.sales import lead_url
from tbo_smart.sales.activities import add_follow_up_task
from tbo_smart.smart_leads import FOLLOW_UP_TYPE, follow_ups_enabled
from tbo_smart.smart_leads.notify import deliver, notify
from tbo_smart.smart_leads.utils import on_leave

DOCTYPE = "TBO Lead Meeting"
SCHEDULED, IN_PROGRESS, COMPLETED = "Scheduled", "Meeting in Progress", "Completed"
MOM_PENDING, MOM_SUBMITTED = "MOM Pending", "MOM Submitted"
FOLLOW_UP_REQUIRED, RESCHEDULED, CANCELLED = "Follow-up Required", "Rescheduled", "Cancelled"
# not held yet: these turn Meeting in Progress, then MOM Pending, as the clock passes
UPCOMING = (SCHEDULED, RESCHEDULED)
# held (or should have been) without minutes yet
AWAITING_MOM = (SCHEDULED, RESCHEDULED, IN_PROGRESS, COMPLETED)
FINAL = (MOM_SUBMITTED, CANCELLED)
# what a person may set by hand; MOM Pending is the clock's, MOM Submitted comes with the MOM
MANUAL_STATUSES = (IN_PROGRESS, COMPLETED, FOLLOW_UP_REQUIRED, CANCELLED)
MEETING_TYPES = ("Online", "Physical visit")
DEFAULT_DURATION = 60
MAX_DURATION = 12 * 60
REMINDER_MINUTES = 60
# MOM escalation: BDE (0), the BDM (1), the sales head (2), on the follow-up ladder's days
MOM_TOP_LEVEL = 2
ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


def stage_label(stage: int) -> str:
	"""1st meeting, 2nd meeting, 3rd meeting, then Additional meeting 1, 2, …"""
	stage = cint(stage)
	if stage in ORDINALS:
		return _("{0} meeting").format(ORDINALS[stage])
	return _("Additional meeting {0}").format(max(stage - 3, 1))


def ends_at(doc):
	"""When the meeting ends: its time plus its duration."""
	return add_to_date(get_datetime(doc.scheduled_at), minutes=cint(doc.duration_minutes) or DEFAULT_DURATION)


class LeadMeeting:
	"""Controller logic of TBO Lead Meeting (the DocType class mixes this in)."""

	def before_insert(self):
		self.set_stage()

	def validate(self):
		self.set_defaults()
		self.validate_lead()
		self.validate_place()
		self.validate_duration()
		self.validate_status_change()

	def after_insert(self):
		self.advance_lead_on_schedule()

	def on_update(self):
		self.complete_mom()
		self.publish_update()

	# ---------- methods ----------

	def set_stage(self):
		"""The next number for this lead, under a lock on the lead so two meetings scheduled
		at once can't take the same number."""
		frappe.db.get_value("Lead", self.lead, "name", for_update=True)
		held = frappe.db.count(DOCTYPE, {"lead": self.lead, "status": ("!=", CANCELLED)})
		self.stage = held + 1
		self.stage_label = stage_label(self.stage)

	def set_defaults(self):
		if not self.bde:
			self.bde = frappe.db.get_value("Lead", self.lead, "lead_owner") or frappe.session.user
		if not self.duration_minutes:
			self.duration_minutes = DEFAULT_DURATION
		if self.meeting_type == "Online":
			self.location = None
		else:
			self.meeting_link = None

	def validate_lead(self):
		stage = frappe.db.get_value("Lead", self.lead, "tbo_stage")
		if self.is_new() and status_type(stage) == "Lost":
			frappe.throw(_("This lead is {0}. Reopen it before scheduling a meeting.").format(stage))

	def validate_place(self):
		if self.meeting_type not in MEETING_TYPES:
			frappe.throw(_("Choose Online or Physical visit."))
		if self.meeting_type == "Online" and self.meeting_link:
			self.meeting_link = self.meeting_link.strip()
			parsed = urlparse(self.meeting_link)
			if parsed.scheme != "https" or not parsed.hostname:
				frappe.throw(_("Paste the full https:// link of the Google Meet or Teams meeting."))

	def validate_duration(self):
		if not 5 <= cint(self.duration_minutes) <= MAX_DURATION:
			frappe.throw(_("A meeting lasts between 5 minutes and 12 hours."))

	def validate_status_change(self):
		if self.is_new() or not self.has_value_changed("status"):
			return
		before = self.get_doc_before_save().status
		if before in FINAL:
			frappe.throw(_("This meeting is {0}; its status can't change.").format(_(before)))

	def advance_lead_on_schedule(self):
		if self.status in UPCOMING:
			advance_lead(self.lead, MEETING_SCHEDULED, _("{0} scheduled").format(self.stage_label))

	def complete_mom(self):
		"""When the MOM is submitted: the lead moves to Nth Meeting Completed, and the next
		follow-up date becomes a "Sales follow-up" task for the BDE."""
		if self.status != MOM_SUBMITTED or not self.has_value_changed("status"):
			return
		target = MEETING_COMPLETED.get(cint(self.stage))
		if target:
			advance_lead(self.lead, target, _("MOM of the {0} submitted").format(self.stage_label))
		if self.next_follow_up_on and not self.follow_up_task:
			self.db_set("follow_up_task", self.create_follow_up())

	def create_follow_up(self) -> str:
		task = add_follow_up_task(
			"Lead",
			self.lead,
			getdate(self.next_follow_up_on),
			title=_("Follow up after the {0}").format(self.stage_label),
			assigned_to=self.bde,
			description=self.next_meeting_details or None,
			task_type=FOLLOW_UP_TYPE,
		)
		return task.name

	def publish_update(self):
		frappe.publish_realtime(
			"tbo_lead_pipeline_update",
			{"lead": self.lead, "meeting": self.name},
			doctype="Lead",
			docname=self.lead,
			after_commit=True,
		)


# ---------- permissions (hooks) ----------


def has_permission(doc, ptype=None, user=None) -> bool:
	"""A meeting is open to whoever may read its lead (tbo_smart.sales.permissions); changing it
	needs write on the lead. Deleting stays with the doctype's roles (System Manager)."""
	user = user or frappe.session.user
	if ptype == "delete":
		return "System Manager" in frappe.get_roles(user)
	lead_ptype = (
		"read" if ptype in ("read", "select", "print", "export", "report", "email", "share") else "write"
	)
	return bool(frappe.has_permission("Lead", lead_ptype, doc=doc.lead, user=user))


def get_permission_query_conditions(user=None) -> str:
	"""List queries: only meetings of the leads the user may read (the Lead list query)."""
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return ""
	leads = frappe.get_list("Lead", fields=["name"], user=user, limit_page_length=0, run=0)
	return f"`tab{DOCTYPE}`.`lead` in ({leads})"


# ---------- the clock: in progress, MOM pending, reminders (every 15 minutes) ----------


def run_meeting_clock(now=None) -> dict | None:
	"""Scheduler, every 15 minutes: Scheduled → Meeting in Progress at its time; any meeting
	without minutes → MOM Pending once it has ended (the BDE is told); the reminder before an
	upcoming meeting."""
	if not pipeline_enabled():
		return None
	now = get_datetime(now or now_datetime())
	# reminders first: a meeting booked less than a tick before it starts still gets one
	reminded = send_reminders(now)
	return {
		"in_progress": start_meetings(now),
		"mom_pending": mark_mom_pending(now),
		"reminded": reminded,
	}


def open_meetings(statuses, until) -> list:
	"""Meetings in `statuses` scheduled at or before `until` (the duration is applied in Python)."""
	return frappe.get_all(
		DOCTYPE,
		filters={"status": ("in", statuses), "scheduled_at": ("<=", until)},
		fields=[
			"name",
			"lead",
			"lead_name",
			"bde",
			"stage_label",
			"scheduled_at",
			"duration_minutes",
			"status",
		],
		limit_page_length=0,
	)


def start_meetings(now) -> int:
	started = 0
	for row in open_meetings(UPCOMING, now):
		if ends_at(row) > now:
			frappe.db.set_value(DOCTYPE, row.name, "status", IN_PROGRESS)
			started += 1
	return started


def mark_mom_pending(now) -> int:
	pending = 0
	for row in open_meetings(AWAITING_MOM, now):
		if ends_at(row) > now:
			continue
		frappe.db.set_value(
			DOCTYPE, row.name, {"status": MOM_PENDING, "mom_pending_since": now, "escalation_level": 0}
		)
		notify_people(
			[row.bde],
			_("MOM pending: {0} with {1}").format(row.stage_label, row.lead_name or row.lead),
			row.lead,
		)
		pending += 1
	return pending


def send_reminders(now) -> int:
	minutes = cint(setting("meeting_reminder_minutes")) or REMINDER_MINUTES
	rows = frappe.get_all(
		DOCTYPE,
		filters={
			"status": ("in", UPCOMING),
			"scheduled_at": ("between", (now, add_to_date(now, minutes=minutes))),
			"reminded_at": ("is", "not set"),
		},
		fields=[
			"name",
			"lead",
			"lead_name",
			"bde",
			"stage_label",
			"scheduled_at",
			"meeting_type",
			"meeting_link",
			"location",
		],
		limit_page_length=0,
	)
	for row in rows:
		# marked first: a failed Teams post must not send the reminder twice
		frappe.db.set_value(DOCTYPE, row.name, "reminded_at", now)
		send_reminder(row)
	return len(rows)


def send_reminder(row) -> None:
	when = format_datetime(row.scheduled_at, "HH:mm")
	where = row.meeting_link if row.meeting_type == "Online" else (row.location or _("the client's place"))
	subject = _("{0} with {1} at {2}").format(row.stage_label, row.lead_name or row.lead, when)
	text = (
		f"**{subject}**\n{_('Join')}: {where}" if row.meeting_type == "Online" else f"**{subject}**\n{where}"
	)
	html = f"<p><b>{escape_html(subject)}</b></p><p>{escape_html(where or '')}</p>"
	deliver(row.bde, subject, text, html, lead_url(row.lead), _("Open the lead"))
	notify_people([row.bde], subject, row.lead)


def notify_people(users, subject: str, lead: str) -> None:
	notify(users, subject, "Lead", lead)


# ---------- the morning: MOM escalation and today's meetings (09:30, working days) ----------


def run_meeting_morning(on_date=None) -> dict | None:
	"""Scheduler, 09:30 site time: MOM Pending escalation (BDE → BDM → sales head, on the
	follow-up ladder's working days, once per level), and, when the follow-up digest is off,
	each BDE's meetings today and pending minutes (with it on, they are in that digest)."""
	if not pipeline_enabled():
		return None
	from tbo_smart.smart_leads.follow_ups import FollowUpRun

	run = FollowUpRun(on_date)
	if not run.calendar.is_working_day(run.today):
		return None
	escalated = escalate_mom_pending(run)
	sent = {} if follow_ups_enabled() else send_meeting_digests(run.today)
	return {"escalated": escalated, "sent": sent}


def escalate_mom_pending(run) -> int:
	rows = frappe.get_all(
		DOCTYPE,
		filters={"status": MOM_PENDING},
		fields=["name", "lead", "lead_name", "bde", "stage_label", "mom_pending_since", "escalation_level"],
		limit_page_length=0,
	)
	managers = run.load_managers({row.bde for row in rows})
	heads = run.load_sales_heads()
	climbed = 0
	for row in rows:
		days = run.calendar.working_days_between(getdate(row.mom_pending_since or run.today), run.today)
		level = min(run.level_for(days), MOM_TOP_LEVEL)
		if level <= cint(row.escalation_level):
			continue
		people = {row.bde, managers.get(row.bde) if level >= 1 else None}
		if level >= 2:
			people |= set(heads)
		people = {person for person in people if person}
		people -= set(on_leave(people, run.today))
		frappe.db.set_value(DOCTYPE, row.name, "escalation_level", level)
		notify_people(
			people,
			_("MOM pending for {0} working days: {1} with {2}").format(
				days, row.stage_label, row.lead_name or row.lead
			),
			row.lead,
		)
		climbed += 1
	return climbed


def meetings_for(user: str, day) -> dict:
	"""{today: [...], mom_pending: [...]} for a BDE: meetings scheduled today and minutes owed."""
	day = getdate(day)
	fields = ["name", "lead", "lead_name", "stage_label", "scheduled_at", "meeting_type"]
	return {
		"today": frappe.get_all(
			DOCTYPE,
			filters={
				"bde": user,
				"status": ("in", (*UPCOMING, IN_PROGRESS)),
				"scheduled_at": ("between", (f"{day} 00:00:00", f"{day} 23:59:59")),
			},
			fields=fields,
			order_by="scheduled_at asc",
		),
		"mom_pending": frappe.get_all(
			DOCTYPE,
			filters={"bde": user, "status": MOM_PENDING},
			fields=fields,
			order_by="scheduled_at asc",
		),
	}


def meeting_people(day) -> set[str]:
	"""BDEs with a meeting today or minutes owed; nobody while the pipeline is off."""
	if not pipeline_enabled():
		return set()
	day = getdate(day)
	today = frappe.get_all(
		DOCTYPE,
		filters={
			"status": ("in", (*UPCOMING, IN_PROGRESS)),
			"scheduled_at": ("between", (f"{day} 00:00:00", f"{day} 23:59:59")),
		},
		pluck="bde",
	)
	return set(today) | set(frappe.get_all(DOCTYPE, filters={"status": MOM_PENDING}, pluck="bde"))


def meeting_digest_lines(user: str, day) -> list[tuple[str, list[str]]]:
	"""[(section title, lines)] for the morning digest; empty when the pipeline is off."""
	if not pipeline_enabled():
		return []
	found = meetings_for(user, day)
	sections = []
	if found["today"]:
		sections.append(
			(
				_("Meetings today"),
				[
					f"{format_datetime(row.scheduled_at, 'HH:mm')} {row.stage_label}: {row.lead_name or row.lead}"
					for row in found["today"]
				],
			)
		)
	if found["mom_pending"]:
		sections.append(
			(
				_("MOM pending"),
				[f"{row.stage_label}: {row.lead_name or row.lead}" for row in found["mom_pending"]],
			)
		)
	return sections


def send_meeting_digests(day) -> dict:
	"""Meetings-only digest (Teams, else email, plus in-app) when the follow-up digest is off."""
	people = meeting_people(day)
	people -= set(on_leave(people, getdate(day)))
	sent = {}
	for user in sorted(people):
		sections = meeting_digest_lines(user, day)
		if not sections:
			continue
		counts = " · ".join(f"{len(lines)} {title.lower()}" for title, lines in sections)
		subject = _("Your meetings: {0}").format(counts)
		text = "\n".join(
			[f"**{subject}**"]
			+ [f"\n**{title}**\n" + "\n".join(f"- {line}" for line in lines) for title, lines in sections]
		)
		html = "".join(
			f"<p><b>{escape_html(title)}</b></p><ul>"
			+ "".join(f"<li>{escape_html(line)}</li>" for line in lines)
			+ "</ul>"
			for title, lines in sections
		)
		sent[user] = deliver(user, subject, text, html, lead_url(), _("Open the leads"))
		notify([user], subject, "TBO Smart Settings", "TBO Smart Settings")
	return sent
