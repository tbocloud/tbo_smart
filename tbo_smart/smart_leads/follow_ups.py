"""The lead follow-up engine (docs/smart-leads.md §5).

A follow-up is an open **CRM Task** on the lead (the finance workflow's `tbo_task_type` =
"Sales follow-up" for the ones this feature creates). The earliest open task with a due date is
the lead's next follow-up. Rules, for open leads (New, Contacted, Nurture, Qualified):

| Rule | When | Who |
| --- | --- | --- |
| No follow-up | no open task with a due date | the owner |
| Re-engage | no follow-up and no touch for `lead_stale_days` | the owner |
| Due today | the next follow-up is due today | the task's assignee (else the owner) |
| Overdue | the next follow-up's day has passed | the escalation ladder |

Ladder (working days overdue, each level adds people): L0 the assignee; L1 (`lead_ladder_l1_days`)
plus the owner's BDM (their manager in the CRM sales hierarchy); L2 (`lead_ladder_l2_days`) plus
the Sales & Marketing Head (TBO Smart Settings → Head - Sales & Marketing, else the role's
users); L3 (`lead_ladder_l3_days`) plus the users with `lead_manager_role`.

Nobody on approved HRMS leave is told; an owner on leave is covered by the BDM, else the sales
head. A disabled owner is never told: their leads go to the fallback owner, else they are listed
under Needs owner for the sales head. Nothing is sent on a non-working day.
"""

from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import cint, escape_html, formatdate, getdate, today

from tbo_smart.smart_leads import OPEN_STATUSES, SALES_HEAD_ROLE, follow_ups_enabled, setting
from tbo_smart.smart_leads.facts import LeadFacts
from tbo_smart.smart_leads.notify import deliver, notify
from tbo_smart.smart_leads.utils import WorkCalendar, enabled_users, on_leave, users_with_role

NO_FOLLOW_UP, RE_ENGAGE, DUE_TODAY, OVERDUE = "no_follow_up", "re_engage", "due_today", "overdue"
RULE_LABELS = {
	OVERDUE: "Overdue",
	DUE_TODAY: "Due today",
	RE_ENGAGE: "Re-engage",
	NO_FOLLOW_UP: "No follow-up",
}
DIGEST_ORDER = (OVERDUE, DUE_TODAY, RE_ENGAGE, NO_FOLLOW_UP)
DIGEST_LINES = 8
LEVEL_LABELS = {1: "Escalated to the BDM", 2: "Escalated to the sales head", 3: "Escalated to the managers"}
LEVEL_NOTES = {1: "escalated to the BDM", 2: "escalated to the sales head", 3: "escalated to the managers"}


@dataclass
class FollowUp:
	lead: str
	lead_name: str
	organization: str | None
	rule: str
	owner: str | None
	score: int = 0
	level: int = 0
	task: str | None = None
	due: str | None = None
	days_overdue: int = 0
	recipients: set = field(default_factory=set)
	cover_note: str | None = None


class FollowUpRun:
	"""Evaluate every open lead once; nothing is sent here, so the digest, the summary on the
	Call today page and the tests read the same result."""

	def __init__(self, on_date=None, leads: list[str] | None = None):
		self.today = getdate(on_date or today())
		# only these leads (the summary on Call today); None: every open lead
		self.leads = leads
		self.calendar = WorkCalendar()
		self.stale_days = cint(setting("lead_stale_days")) or 21
		self.ladder = (
			cint(setting("lead_ladder_l1_days")) or 2,
			cint(setting("lead_ladder_l2_days")) or 4,
			cint(setting("lead_ladder_l3_days")) or 7,
		)
		self.fallback = setting("lead_fallback_owner")
		self.items: list[FollowUp] = []
		self.needs_owner: list[str] = []

	def evaluate(self) -> list[FollowUp]:
		filters = {"status": ("in", OPEN_STATUSES), "converted": 0}
		if self.leads is not None:
			filters["name"] = ("in", self.leads or [""])
		names = frappe.get_all("CRM Lead", filters=filters, pluck="name", limit_page_length=0)
		self.facts = LeadFacts(names, self.today)
		owners = {self.facts.lead(name).lead_owner for name in self.facts.names}
		assignees = {task.assigned_to for tasks in self.facts.follow_ups.values() for task in tasks}
		self.active = enabled_users(owners | assignees | {self.fallback})
		self.managers = self.load_managers(owners)
		self.sales_heads = self.load_sales_heads()
		self.top_managers = users_with_role(setting("lead_manager_role"))
		for name in self.facts.names:
			item = self.rule_for(name)
			if item:
				self.items.append(item)
		# everyone told, and everyone who may cover for them
		self.away = on_leave(
			{user for item in self.items for user in item.recipients}
			| set(self.managers.values())
			| set(self.sales_heads),
			self.today,
		)
		for item in self.items:
			self.cover_leave(item)
		return self.items

	# ---------- one lead ----------

	def owner_of(self, name: str) -> str | None:
		"""The lead owner while enabled, else the fallback owner, else None (needs owner)."""
		owner = self.facts.lead(name).lead_owner
		if owner in self.active:
			return owner
		if self.fallback in self.active:
			return self.fallback
		self.needs_owner.append(name)
		return None

	def rule_for(self, name: str) -> FollowUp | None:
		lead = self.facts.lead(name)
		owner = self.owner_of(name)
		item = FollowUp(
			name, lead.lead_name, lead.organization, NO_FOLLOW_UP, owner, int(lead.lead_score or 0)
		)
		task = self.facts.next_follow_up(name)
		if task:
			due = getdate(task.due_date)
			if due > self.today:
				return None
			item.task, item.due = task.name, str(due)
			assignee = task.assigned_to if task.assigned_to in self.active else owner
			item.owner = assignee
			if due == self.today:
				item.rule = DUE_TODAY
			else:
				item.rule = OVERDUE
				item.days_overdue = self.calendar.working_days_between(due, self.today)
				item.level = self.level_for(item.days_overdue)
		elif self.is_stale(name):
			item.rule = RE_ENGAGE
		item.recipients = self.recipients(item, lead.lead_owner)
		return item

	def is_stale(self, name: str) -> bool:
		days = self.facts.days_since_touch(name)
		if days is None:
			days = (self.today - self.facts.created_on(name)).days
		return days >= self.stale_days

	def level_for(self, days_overdue: int) -> int:
		return sum(days_overdue >= threshold for threshold in self.ladder)

	def recipients(self, item: FollowUp, lead_owner: str | None) -> set:
		people = {item.owner} if item.owner else set()
		if item.rule == OVERDUE:
			if item.level >= 1:
				people.add(self.managers.get(lead_owner))
			if item.level >= 2:
				people |= set(self.sales_heads)
			if item.level >= 3:
				people |= set(self.top_managers)
		return {person for person in people if person}

	def cover_leave(self, item: FollowUp) -> None:
		"""Take people on leave off the item; an owner on leave is covered by the BDM, else the
		sales head."""
		away = {person for person in item.recipients if person in self.away}
		if not away:
			return
		item.recipients -= away
		if item.owner in away:
			lead_owner = self.facts.lead(item.lead).lead_owner
			cover = {self.managers.get(lead_owner)} - set(self.away) - {None}
			cover = cover or (set(self.sales_heads) - set(self.away))
			item.recipients |= cover
			item.cover_note = _("{0} is on leave until {1}").format(
				frappe.utils.get_fullname(item.owner), formatdate(self.away[item.owner], "d MMM")
			)

	# ---------- who ----------

	def load_managers(self, owners: set) -> dict:
		"""{owner: their manager in the CRM sales hierarchy} (the BDM), enabled only."""
		Node = frappe.qb.DocType("CRM Sales Hierarchy")
		Parent = frappe.qb.DocType("CRM Sales Hierarchy").as_("parent_node")
		owners = [owner for owner in owners if owner]
		if not owners:
			return {}
		rows = (
			frappe.qb.from_(Node)
			.join(Parent)
			.on(Parent.name == Node.reports_to)
			.select(Node.user, Parent.user.as_("manager"))
			.where(Node.user.isin(owners))
		).run(as_dict=True)
		active = enabled_users({row.manager for row in rows})
		return {row.user: row.manager for row in rows if row.manager in active}

	def load_sales_heads(self) -> list[str]:
		head = setting("escalation_sales_head")
		if head and enabled_users([head]):
			return [head]
		return users_with_role(SALES_HEAD_ROLE)

	# ---------- results ----------

	def for_person(self, user: str) -> dict[str, list[FollowUp]]:
		"""{rule: items} the person is told about, best score first within a rule."""
		groups: dict[str, list[FollowUp]] = {rule: [] for rule in DIGEST_ORDER}
		for item in self.items:
			if user in item.recipients:
				groups[item.rule].append(item)
		for rule, items in groups.items():
			if rule == OVERDUE:
				items.sort(key=lambda item: (-item.level, -item.days_overdue, -item.score))
			else:
				items.sort(key=lambda item: -item.score)
		return groups

	def people(self) -> set[str]:
		return {person for item in self.items for person in item.recipients}


# ---------- escalation notices ----------


def notify_escalations(run: FollowUpRun) -> int:
	"""One in-app notification to everyone newly told when an overdue follow-up climbs a level,
	and the task's `tbo_escalation_level` recorded. Returns how many tasks climbed."""
	climbed = 0
	stored = (
		dict(
			frappe.get_all(
				"CRM Task",
				filters={"name": ("in", [item.task for item in run.items if item.task])},
				fields=["name", "tbo_escalation_level"],
				as_list=True,
			)
		)
		if any(item.task for item in run.items)
		else {}
	)
	for item in run.items:
		if not item.task:
			continue
		before = cint(stored.get(item.task))
		if item.level == before:
			continue
		frappe.db.set_value("CRM Task", item.task, "tbo_escalation_level", item.level, update_modified=False)
		if item.level < before:
			continue
		climbed += 1
		subject = _("{0}: follow-up on {1} is {2} working days overdue").format(
			_(LEVEL_LABELS.get(item.level, "Overdue")), item.lead_name or item.lead, item.days_overdue
		)
		if item.cover_note:
			subject += f" ({item.cover_note})"
		notify(item.recipients, subject, "CRM Lead", item.lead)
	return climbed


# ---------- digest ----------


def digest_counts(groups: dict) -> str:
	parts = [f"{len(groups[rule])} {_(RULE_LABELS[rule]).lower()}" for rule in DIGEST_ORDER if groups[rule]]
	return " · ".join(parts)


def item_line(item: FollowUp) -> str:
	who = item.lead_name or item.lead
	if item.organization:
		who += f" ({item.organization})"
	if item.rule == OVERDUE:
		detail = (
			_("1 working day overdue")
			if item.days_overdue == 1
			else _("{0} working days overdue").format(item.days_overdue)
		)
		if item.level:
			detail += f", {_(LEVEL_NOTES[item.level])}"
	elif item.rule == DUE_TODAY:
		detail = _("due today")
	elif item.rule == RE_ENGAGE:
		detail = _("no touch for a while")
	else:
		detail = _("no next follow-up")
	if item.cover_note:
		detail += f"; {item.cover_note}"
	return f"{who}: {detail}"


def build_digest(run: FollowUpRun, user: str) -> dict | None:
	"""{subject, text, html, counts} of the person's digest, or None when there is nothing."""
	groups = run.for_person(user)
	needs_owner = len(run.needs_owner) if user in run.sales_heads or user in run.top_managers else 0
	if not any(groups.values()) and not needs_owner:
		return None
	counts = digest_counts(groups)
	if needs_owner:
		owners = _("{0} need an owner").format(needs_owner)
		counts = f"{counts} · {owners}" if counts else owners
	subject = _("Lead follow-ups for {0}: {1}").format(formatdate(run.today, "d MMM"), counts)
	text_lines = [f"**{subject}**"]
	sections = []
	for rule in DIGEST_ORDER:
		items = groups[rule]
		if not items:
			continue
		shown = items[:DIGEST_LINES]
		more = len(items) - len(shown)
		text_lines.append(f"\n**{_(RULE_LABELS[rule])} ({len(items)})**")
		text_lines += [f"- {item_line(item)}" for item in shown]
		if more:
			text_lines.append(_("…and {0} more on Call today").format(more))
		sections.append((rule, items, shown, more))
	if needs_owner:
		text_lines.append(
			"\n"
			+ _("{0} open leads belong to disabled users. Assign them on Call today → Needs owner.").format(
				needs_owner
			)
		)
	return {
		"subject": subject,
		"counts": counts,
		"text": "\n".join(text_lines),
		"html": digest_html(subject, sections, needs_owner),
	}


def digest_html(subject: str, sections: list, needs_owner: int) -> str:
	"""A plain, readable email: neutral grays, the one accent only on the button."""
	link = frappe.utils.get_url("/crm/call-today")
	body = [
		f'<p style="font:600 16px/1.4 sans-serif;color:#1f2329;margin:0 0 16px">{escape_html(subject)}</p>'
	]
	for rule, items, shown, more in sections:
		body.append(
			f'<p style="font:600 13px/1.4 sans-serif;color:#1f2329;margin:16px 0 6px">'
			f"{escape_html(_(RULE_LABELS[rule]))} ({len(items)})</p>"
		)
		body.append('<ul style="margin:0;padding-left:18px;font:13px/1.6 sans-serif;color:#3d434b">')
		body += [digest_line(item) for item in shown]
		body.append("</ul>")
		if more:
			body.append(
				f'<p style="font:12px/1.4 sans-serif;color:#6b717a;margin:4px 0 0">'
				f"{escape_html(_('…and {0} more on Call today').format(more))}</p>"
			)
	if needs_owner:
		body.append(
			f'<p style="font:13px/1.5 sans-serif;color:#3d434b;margin:16px 0 0">'
			f"{escape_html(_('{0} open leads belong to disabled users. Assign them on Call today → Needs owner.').format(needs_owner))}</p>"
		)
	body.append(
		f'<p style="margin:20px 0 0"><a href="{link}" style="display:inline-block;background:#3a748d;color:#fff;'
		f'font:600 13px/1 sans-serif;padding:10px 14px;border-radius:8px;text-decoration:none">'
		f"{escape_html(_('Open Call today'))}</a></p>"
	)
	return '<div style="max-width:560px">' + "".join(body) + "</div>"


def digest_line(item: FollowUp) -> str:
	link = frappe.utils.get_url("/crm/leads/" + item.lead)
	return f'<li><a href="{link}" style="color:#1f2329">{escape_html(item_line(item))}</a></li>'


def send_digests(run: FollowUpRun, only: str | None = None) -> dict:
	"""Each person's digest by Teams (else email), plus one in-app summary. {user: channel}."""
	people = {only} if only else run.people() | (set(run.sales_heads) if run.needs_owner else set())
	people -= set(run.away)
	sent = {}
	for user in sorted(people):
		digest = build_digest(run, user)
		if not digest:
			continue
		sent[user] = deliver(
			user, digest["subject"], digest["text"], digest["html"], "/call-today", _("Open Call today")
		)
		notify(
			[user],
			_("Lead follow-ups: {0}").format(digest["counts"]),
			"TBO Smart Settings",
			"TBO Smart Settings",
		)
	return sent


# ---------- jobs ----------


def run_follow_ups(on_date=None) -> dict | None:
	"""Scheduler, 09:30 site time: escalation notices and the digests, once a working day."""
	if not follow_ups_enabled():
		return None
	run = FollowUpRun(on_date)
	if not run.calendar.is_working_day(run.today):
		return None
	if getdate(setting("lead_digest_sent_on") or "1900-01-01") >= run.today:
		return None
	# recorded and committed before anything goes out: Teams posts can't be rolled back
	frappe.db.set_single_value("TBO Smart Settings", "lead_digest_sent_on", run.today)
	# the marker must survive a failure halfway, or people get two digests
	frappe.db.commit()  # nosemgrep
	run.evaluate()
	climbed = notify_escalations(run)
	return {"escalated": climbed, "sent": send_digests(run)}
