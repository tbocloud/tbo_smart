"""The hub's Leads overview and the Sales scoreboard's figures (docs/hub-leads-api.md §2.4, §2.5).

Both build on the pipeline dashboard (`PipelineDashboard`, docs/lead-pipeline.md §11): the same
lead owners scope, date range and grouped queries, with what the hub's overview adds (the
pipeline's value by stage, the funnel, contacted and stale leads, segments and sources) and the
per-salesperson activity the hub scores. `metric_leads` names the leads behind each number, so
every figure on the overview opens the list of exactly those leads.
"""

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Count, Date, IfNull, Sum
from frappe.utils import (
	add_days,
	cint,
	flt,
	get_first_day,
	get_quarter_start,
	getdate,
	today,
)

from tbo_smart import lead_source
from tbo_smart.api.mobile.utils import get_full_names, percent
from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline import setting
from tbo_smart.lead_pipeline.dashboard import PipelineDashboard
from tbo_smart.lead_pipeline.meetings import CANCELLED
from tbo_smart.lead_pipeline.proposals import DOCTYPE as PROPOSAL
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_WON,
	CONTACTED,
	FIRST_MEETING,
	NOT_RESPONDING,
	OPEN_STATUSES,
	POSITIVE,
	PROGRESS,
	PROPOSAL_SENT,
	STATUSES,
)
from tbo_smart.sales import DEAL, LEAD_LINK
from tbo_smart.sales.activities import CANCELLED as TASK_CANCELLED
from tbo_smart.sales.activities import DONE, OPEN
from tbo_smart.smart_leads import ai_leads_enabled
from tbo_smart.smart_leads.migration import NOTE_TITLE_PREFIX
from tbo_smart.telephony.calls import calls_by_agent

PERIODS = ("week", "month", "quarter", "custom")
METRICS = (
	"new",
	"intake",
	"contacted",
	"meetings",
	"quotations",
	"won",
	"lost",
	"stale",
	"overdue",
	"hot",
	"open",
)
# what AI runs leads read in the open leads (docs/smart-leads.md §11): {metric: criterion builder}
AI_METRICS = {
	"high_intent": lambda Lead: Lead.tbo_ai_intent == "High",
	"unhappy": lambda Lead: Lead.tbo_ai_sentiment == "Negative",
	"stage_suggested": lambda Lead: Lead.tbo_ai_stage.isnotnull() & (Lead.tbo_ai_stage != ""),
}
DEFAULT_STALE_DAYS = 21
# the funnel's steps: how far along the pipeline a lead created in the range got; "meeting" is
# a meeting held (First Meeting Completed or later), as the "meetings" figure counts
FUNNEL = (
	("created", 0),
	("contacted", PROGRESS[CONTACTED]),
	("meeting", PROGRESS[FIRST_MEETING]),
	("proposal", PROGRESS[PROPOSAL_SENT]),
	("won", PROGRESS[CLOSED_WON]),
)
NO_VALUE = ""


def period_range(period: str, from_date=None, to_date=None) -> tuple:
	"""(from, to) of this week (Monday on), this month, this quarter, or a custom range."""
	if period not in PERIODS:
		frappe.throw(_("period must be one of {0}.").format(", ".join(PERIODS)))
	now = getdate(today())
	if period == "custom":
		if not (from_date and to_date):
			frappe.throw(_("A custom period needs a from and a to date."))
		return getdate(from_date), getdate(to_date)
	if period == "week":
		return add_days(now, -now.weekday()), now
	if period == "month":
		return getdate(get_first_day(now)), now
	return getdate(get_quarter_start(now)), now


def stale_days() -> int:
	return cint(setting("lead_stale_days")) or DEFAULT_STALE_DAYS


class LeadOverview(PipelineDashboard):
	"""The overview's figures for `owners` (None: everyone) from `from_date` to `to_date`."""

	def __init__(self, from_date=None, to_date=None, owners=None):
		super().__init__(from_date, to_date, owners)
		self.stale_cutoff = add_days(getdate(), -stale_days())
		self.Quotation = frappe.qb.DocType("Quotation")

	# ---------- the whole page ----------

	def build_overview(self) -> dict:
		new = self.created_leads()
		contacted = self.contacted_leads()
		won = self.closed_rows("Won")
		lost = self.closed_rows("Lost")
		quotations = self.quotations()
		pipeline = self.pipeline()
		won_values = self.lead_values([row.name for row in won])
		open_stages = [row for row in pipeline if row["stage"] in OPEN_STATUSES]
		return {
			"from_date": self.from_date,
			"to_date": self.to_date,
			"stale_days": stale_days(),
			"currency": quotations["currency"],
			"totals": {
				"new": len(new),
				"contacted": len(contacted),
				"meetings": self.meetings_completed()["total"],
				"mom_pending": self.mom_pending()["count"],
				"quotations_sent": self.quotations_sent(),
				"won": len(won),
				"won_value": flt(sum(won_values.values()), 2),
				"won_unpriced": sum(1 for row in won if not won_values.get(row.name)),
				"lost": len(lost),
				"stale": len(self.stale_leads()),
				"overdue": self.overdue_follow_ups(),
				"hot": self.high_potential()["count"],
				"open": sum(row["count"] for row in open_stages),
				"open_value": flt(sum(row["value"] for row in open_stages), 2),
				"win_rate": percent(len(won), len(won) + len(lost)),
				"conversion_rate": percent(self.cohort_won(), len(new)),
			},
			"pipeline": pipeline,
			"funnel": self.funnel(new),
			"lost_reasons": self.lost_reasons(),
			"hot": self.high_potential(),
			"by_owner": self.by_owner(new, contacted),
			"by_segment": self.by_field("tbo_segment", new, won),
			"by_source": self.by_field(lead_source.field(), new, won),
		}

	# ---------- leads behind each number ----------

	def metric_leads(self, metric: str) -> list[str]:
		"""The leads a figure counts, for the list it opens."""
		if metric not in METRICS and metric not in AI_METRICS:
			frappe.throw(_("metric must be one of {0}.").format(", ".join((*METRICS, *AI_METRICS))))
		if metric in AI_METRICS:
			# what the AI read stays hidden while AI runs leads is off
			return self.ai_leads(AI_METRICS[metric]) if ai_leads_enabled() else []
		if metric == "new":
			return list(self.created_leads())
		if metric == "intake":
			return list(self.taken_leads())
		if metric == "contacted":
			return list(self.contacted_leads())
		if metric == "meetings":
			return self.meeting_leads()
		if metric == "quotations":
			return self.quotation_leads()
		if metric in ("won", "lost"):
			return [row.name for row in self.closed_rows(metric.title())]
		if metric == "stale":
			return list(self.stale_leads())
		if metric == "overdue":
			return self.overdue_leads()
		if metric == "open":
			return self.open_leads()
		return self.hot_leads()

	# ---------- leads ----------

	def created_leads(self) -> dict:
		"""{lead: owner} of the leads created in the range."""
		Lead = self.Lead
		return dict(
			(
				frappe.qb.from_(Lead)
				.select(Lead.name, Lead.lead_owner)
				.where(self.lead_criterion() & self.in_range(Lead.creation))
			).run()
		)

	def not_imported(self):
		"""A lead someone took, not one copied in from the old CRM (`crm_lead_ref` set)."""
		return IfNull(self.Lead.crm_lead_ref, "") == ""

	def taken_leads(self) -> dict:
		"""{lead: owner} of the leads salespeople took in the range: created, imports left out."""
		Lead = self.Lead
		return dict(
			(
				frappe.qb.from_(Lead)
				.select(Lead.name, Lead.lead_owner)
				.where(self.lead_criterion() & self.in_range(Lead.creation) & self.not_imported())
			).run()
		)

	def build_intake(self) -> dict:
		"""New leads taken each day of the range and by whom (imports left out), for the daily
		new-lead target: {from_date, to_date, days: [{date, total, by_owner: [{user, full_name,
		count}]}]}, every day of the range, newest first."""
		Lead = self.Lead
		day = Date(Lead.creation)
		counts: dict = {}
		for date, owner, count in (
			frappe.qb.from_(Lead)
			.select(day, IfNull(Lead.lead_owner, NO_VALUE), Count(Lead.name))
			.where(self.lead_criterion() & self.in_range(Lead.creation) & self.not_imported())
			.groupby(day, IfNull(Lead.lead_owner, NO_VALUE))
		).run():
			counts.setdefault(getdate(date), {})[owner or None] = cint(count)
		names = get_full_names({owner for by_owner in counts.values() for owner in by_owner if owner})
		days = []
		date = self.to_date
		while date >= self.from_date:
			by_owner = counts.get(date, {})
			days.append(
				{
					"date": date,
					"total": sum(by_owner.values()),
					"by_owner": sorted(
						(
							{
								"user": user,
								"full_name": names.get(user, user) if user else None,
								"count": count,
							}
							for user, count in by_owner.items()
						),
						key=lambda row: (-row["count"], row["full_name"] or "~"),
					),
				}
			)
			date = add_days(date, -1)
		return {"from_date": self.from_date, "to_date": self.to_date, "days": days}

	def touched_query(self, since, until=None):
		"""Subqueries of the leads touched from `since` (to `until`): a note, a call or logged
		activity, an email, a stage change or a follow-up done. The migration note isn't a
		touch."""
		Comment = frappe.qb.DocType("Comment")
		Event = frappe.qb.DocType("Event")
		Participant = frappe.qb.DocType("Event Participants")
		Communication = frappe.qb.DocType("Communication")
		Log = frappe.qb.DocType("TBO Stage Change")
		ToDo = frappe.qb.DocType("ToDo")

		def between(field):
			criterion = field >= since
			return criterion & (field < until) if until else criterion

		return (
			frappe.qb.from_(Comment)
			.select(Comment.reference_name)
			.where(
				(Comment.reference_doctype == "Lead")
				& (Comment.comment_type == "Comment")
				& Comment.content.not_like(f"%{NOTE_TITLE_PREFIX}%")
				& between(Comment.creation)
			),
			frappe.qb.from_(Event)
			.join(Participant)
			.on((Participant.parent == Event.name) & (Participant.parenttype == "Event"))
			.select(Participant.reference_docname)
			.where((Participant.reference_doctype == "Lead") & between(Event.starts_on)),
			frappe.qb.from_(Communication)
			.select(Communication.reference_name)
			.where(
				(Communication.reference_doctype == "Lead")
				& (Communication.communication_medium == "Email")
				& between(Communication.communication_date)
			),
			# the first row of a stage log is the lead coming in, not a touch
			frappe.qb.from_(Log)
			.select(Log.parent)
			.where((Log.parenttype == "Lead") & (Log.idx > 1) & between(Log.changed_on)),
			frappe.qb.from_(ToDo)
			.select(ToDo.reference_name)
			.where((ToDo.reference_type == "Lead") & (ToDo.status == DONE) & between(ToDo.modified)),
		)

	def touched(self, since, until=None) -> dict:
		"""{lead: owner} of the leads in scope touched from `since` (to `until`)."""
		Lead = self.Lead
		names = set()
		for query in self.touched_query(since, until):
			names.update(query.run(pluck=True))
		if not names:
			return {}
		return dict(
			(
				frappe.qb.from_(Lead)
				.select(Lead.name, Lead.lead_owner)
				.where(self.lead_criterion() & Lead.name.isin(list(names)))
			).run()
		)

	def contacted_leads(self) -> dict:
		return self.touched(self.from_date, self.end)

	def stale_leads(self) -> set:
		"""Open leads nobody touched for `lead_stale_days`, counted from their creation when never
		touched."""
		Lead = self.Lead
		old_open = set(
			(
				frappe.qb.from_(Lead)
				.select(Lead.name)
				.where(
					self.lead_criterion()
					& Lead.tbo_stage.isin(OPEN_STATUSES)
					& (Lead.creation < self.stale_cutoff)
				)
			).run(pluck=True)
		)
		return old_open - set(self.touched(self.stale_cutoff)) if old_open else set()

	def open_leads(self) -> list[str]:
		Lead = self.Lead
		return (
			frappe.qb.from_(Lead)
			.select(Lead.name)
			.where(self.lead_criterion() & Lead.tbo_stage.isin(OPEN_STATUSES))
		).run(pluck=True)

	def ai_leads(self, criterion) -> list[str]:
		"""Open leads in scope the AI read this way, now."""
		Lead = self.Lead
		return (
			frappe.qb.from_(Lead)
			.select(Lead.name)
			.where(self.lead_criterion() & Lead.tbo_stage.isin(OPEN_STATUSES) & criterion(Lead))
		).run(pluck=True)

	def hot_leads(self) -> list[str]:
		Lead = self.Lead
		return (
			frappe.qb.from_(Lead)
			.select(Lead.name)
			.where(
				self.lead_criterion()
				& Lead.tbo_stage.isin(OPEN_STATUSES)
				& ((Lead.tbo_temperature == "Hot") | (Lead.tbo_stage == POSITIVE))
			)
		).run(pluck=True)

	def overdue_leads(self) -> list[str]:
		ToDo = frappe.qb.DocType("ToDo")
		Lead = self.Lead
		return (
			frappe.qb.from_(ToDo)
			.join(Lead)
			.on((ToDo.reference_type == "Lead") & (ToDo.reference_name == Lead.name))
			.select(Lead.name)
			.distinct()
			.where(
				(ToDo.status == OPEN)
				& (ToDo.date < getdate())
				& Lead.tbo_stage.isin(OPEN_STATUSES)
				& self.lead_criterion()
			)
		).run(pluck=True)

	def meeting_leads(self) -> list[str]:
		return list({lead for lead, _number in self.held_meetings()})

	def quotation_query(self):
		return frappe.qb.from_(self.Quotation).join(self.Lead).on(self.Lead.name == self.Quotation.tbo_lead)

	def sent_leads(self) -> set:
		"""The leads a proposal went to in the range: a quotation sent (`tbo_sent_on`), a proposal
		document sent (`sent_on`), or the lead entering Proposal Sent (its stage log). Leads
		moved there by hand or imported have no sent document, so the documents alone read 0
		while the pipeline showed Proposal Sent."""
		Quotation, Proposal, Log, Lead = (
			self.Quotation,
			frappe.qb.DocType(PROPOSAL),
			frappe.qb.DocType("TBO Stage Change"),
			self.Lead,
		)
		sent = set(
			self.quotation_query()
			.select(Quotation.tbo_lead)
			.where((Quotation.docstatus != 2) & self.in_range(Quotation.tbo_sent_on) & self.lead_criterion())
			.run(pluck=True)
		)
		sent.update(
			frappe.qb.from_(Proposal)
			.join(Lead)
			.on(Lead.name == Proposal.lead)
			.select(Proposal.lead)
			.where(
				(Proposal.document_type == kinds.PROPOSAL)
				& self.in_range(Proposal.sent_on)
				& self.lead_criterion()
			)
			.run(pluck=True)
		)
		sent.update(
			frappe.qb.from_(Log)
			.join(Lead)
			.on(Lead.name == Log.parent)
			.select(Log.parent)
			.where(
				(Log.parenttype == "Lead")
				& (Log.stage == PROPOSAL_SENT)
				& self.in_range(Log.changed_on)
				& self.lead_criterion()
			)
			.run(pluck=True)
		)
		return sent

	def quotation_leads(self) -> list[str]:
		return list(self.sent_leads())

	def quotations_sent(self) -> int:
		"""Leads a proposal was sent to in the range (`sent_leads`), each once."""
		return len(self.sent_leads())

	def lead_values(self, leads: list[str]) -> dict:
		"""{lead: what it is worth}: its latest quotations' total; else its deal's amount; else
		its Sales Orders' total. Leads won without any of them are 0."""
		values = dict.fromkeys(leads, 0.0)
		if not leads:
			return values
		Quotation, Deal = frappe.qb.DocType("Quotation"), frappe.qb.DocType(DEAL)
		Order = frappe.qb.DocType("Sales Order")
		for lead, value in (
			frappe.qb.from_(Quotation)
			.select(Quotation.tbo_lead, Sum(Quotation.base_grand_total))
			.where(
				Quotation.tbo_lead.isin(leads) & (Quotation.docstatus != 2) & (Quotation.tbo_is_latest == 1)
			)
			.groupby(Quotation.tbo_lead)
		).run():
			values[lead] = flt(value)
		for lead, value in (
			frappe.qb.from_(Deal)
			.select(
				Deal[LEAD_LINK],
				Sum(
					Deal.opportunity_amount
					* Case().when(IfNull(Deal.conversion_rate, 0) > 0, Deal.conversion_rate).else_(1)
				),
			)
			.where(Deal[LEAD_LINK].isin(leads) & (Deal.status != "Lost"))
			.groupby(Deal[LEAD_LINK])
		).run():
			if not values[lead]:
				values[lead] = flt(value)
		if any(not value for value in values.values()):
			for lead, value in (
				frappe.qb.from_(Order)
				.join(Deal)
				.on(Deal.name == Order.opportunity)
				.select(Deal[LEAD_LINK], Sum(Order.base_grand_total))
				.where(Deal[LEAD_LINK].isin(leads) & (Order.docstatus == 1))
				.groupby(Deal[LEAD_LINK])
			).run():
				if not values[lead]:
					values[lead] = flt(value)
		return values

	def lead_value(self, leads: list[str]) -> float:
		return flt(sum(self.lead_values(leads).values()), 2)

	def pipeline(self) -> list[dict]:
		"""Every stage now, in pipeline order: its leads and their latest quotations' value."""
		Lead, Quotation = self.Lead, self.Quotation
		latest = (
			frappe.qb.from_(Quotation)
			.select(Quotation.tbo_lead, Sum(Quotation.base_grand_total).as_("value"))
			.where(
				(Quotation.docstatus != 2) & (Quotation.tbo_is_latest == 1) & Quotation.tbo_lead.isnotnull()
			)
			.groupby(Quotation.tbo_lead)
		)
		rows = (
			frappe.qb.from_(Lead)
			.left_join(latest)
			.on(latest.tbo_lead == Lead.name)
			.select(Lead.tbo_stage, Count(Lead.name), Sum(IfNull(latest.value, 0)))
			.where(self.lead_criterion())
			.groupby(Lead.tbo_stage)
		).run()
		found = {stage: (cint(count), flt(value, 2)) for stage, count, value in rows}
		return [
			{
				"stage": name,
				"type": kind,
				"color": color,
				"count": found.get(name, (0, 0))[0],
				"value": found.get(name, (0, 0.0))[1],
			}
			for name, kind, color in STATUSES
		]

	def funnel(self, created: dict) -> list[dict]:
		"""Of the leads created in the range, how many got as far as each step, from their stage
		log (a lead that skipped a stage still passed it)."""
		furthest = dict.fromkeys(created, 0)
		if created:
			Log = frappe.qb.DocType("TBO Stage Change")
			for lead, stage in (
				frappe.qb.from_(Log)
				.select(Log.parent, Log.stage)
				.where((Log.parenttype == "Lead") & Log.parent.isin(list(created)))
			).run():
				# a call nobody answered is not a lead contacted
				reached = 0 if stage == NOT_RESPONDING else PROGRESS.get(stage, 0)
				furthest[lead] = max(furthest[lead], reached)
		total = len(created)
		return [
			{
				"step": step,
				"count": sum(1 for reached in furthest.values() if reached >= progress),
				"rate": percent(sum(1 for reached in furthest.values() if reached >= progress), total),
			}
			for step, progress in FUNNEL
		]

	# ---------- by owner, segment and source ----------

	def open_by(self, field: str) -> dict:
		Lead = self.Lead
		return dict(
			(
				frappe.qb.from_(Lead)
				.select(IfNull(Lead[field], NO_VALUE), Count(Lead.name))
				.where(self.lead_criterion() & Lead.tbo_stage.isin(OPEN_STATUSES))
				.groupby(IfNull(Lead[field], NO_VALUE))
			).run()
		)

	def by_owner(self, created: dict, contacted: dict) -> list[dict]:
		"""The pipeline dashboard's per-BDE figures, with each owner's new, contacted and open
		leads."""
		rows = {row["user"]: row for row in self.by_bde()}
		open_leads = self.open_by("lead_owner")
		owners = {owner for owner in (*created.values(), *contacted.values(), *open_leads) if owner}
		names = get_full_names(owners | set(rows))
		result = []
		for owner in owners | set(rows):
			row = rows.get(owner) or {
				"user": owner,
				"full_name": names.get(owner, owner),
				"meetings_held": 0,
				"mom_pending": 0,
				"mom_rate": 0.0,
				"quotations": 0,
				"quotations_sent": 0,
				"won": 0,
			}
			row.update(
				{
					"new": sum(1 for who in created.values() if who == owner),
					"contacted": sum(1 for who in contacted.values() if who == owner),
					"open": cint(open_leads.get(owner)),
				}
			)
			result.append(row)
		return sorted(result, key=lambda row: (-row["won"], -row["contacted"], row["full_name"]))

	def by_field(self, field: str, created: dict, won: list) -> list[dict]:
		"""Per segment or source: leads new in the range, open now and won in the range."""
		Lead = self.Lead
		values = {}
		names = list(created) + [row.name for row in won]
		if names:
			values = dict(
				(
					frappe.qb.from_(Lead)
					.select(Lead.name, IfNull(Lead[field], NO_VALUE))
					.where(Lead.name.isin(names))
				).run()
			)
		open_leads = self.open_by(field)
		keys = set(open_leads) | {values[name] for name in names if name in values}
		rows = [
			{
				"value": key or None,
				"new": sum(1 for name in created if values.get(name) == key),
				"open": cint(open_leads.get(key)),
				"won": sum(1 for row in won if values.get(row.name) == key),
			}
			for key in keys
		]
		return sorted(rows, key=lambda row: (-row["new"], -row["open"], row["value"] or "~"))


class SalesActivity(LeadOverview):
	"""Each salesperson's CRM activity in the range, for the hub's Sales scoreboard. Everything
	counts for the lead's owner; follow-ups for whom they are allocated to. Counts and totals only:
	no lead is named."""

	def build_activity(self) -> dict:
		bde = {row["user"]: row for row in self.by_bde()}
		created = self.created_leads()
		contacted = self.contacted_leads()
		won = self.closed_rows("Won")
		won_value = self.won_value_by_owner(won)
		follow_ups = self.follow_ups_by_person()
		calls = calls_by_agent(self.from_date, self.end, self.owners)
		people = (
			set(bde)
			| set(created.values())
			| set(contacted.values())
			| set(follow_ups)
			| set(won_value)
			| set(calls)
		) - {None, ""}
		names = get_full_names(people)
		rows = []
		for user in people:
			row = bde.get(user, {})
			done = follow_ups.get(user, {})
			new = sum(1 for who in created.values() if who == user)
			won_count = sum(1 for lead in won if lead.lead_owner == user)
			rows.append(
				{
					"user": user,
					"full_name": names.get(user, user),
					"contacted": sum(1 for who in contacted.values() if who == user),
					"meetings_held": cint(row.get("meetings_ended")),
					"moms_submitted": cint(row.get("meetings_held")),
					"mom_pending": cint(row.get("mom_pending")),
					"quotations_sent": cint(row.get("quotations_sent")),
					"follow_ups_done": cint(done.get("done")),
					"follow_ups_on_time": cint(done.get("on_time")),
					"follow_ups_overdue": cint(done.get("overdue")),
					"new": new,
					"won": won_count,
					"won_value": flt(won_value.get(user), 2),
					"conversion": percent(won_count, new),
					# answered MyOperator calls the person took, and their talk time
					"calls": calls.get(user, {}).get("calls", 0),
					"talk_seconds": calls.get(user, {}).get("talk_seconds", 0),
				}
			)
		return {
			"from_date": self.from_date,
			"to_date": self.to_date,
			"currency": self.quotations()["currency"],
			"people": sorted(rows, key=lambda row: row["user"]),
		}

	def by_bde(self) -> list[dict]:
		"""The pipeline dashboard's per-owner rows, with the meetings that ended in the range."""
		rows = super().by_bde()
		Meeting, Lead = self.Meeting, self.Lead
		ended = dict(
			(
				self.meetings_query()
				.select(Lead.lead_owner, Count(Meeting.name))
				.where(
					(Meeting.scheduled_at < self.now)
					& self.in_range(Meeting.scheduled_at)
					& (Meeting.status != CANCELLED)
				)
				.where(self.lead_criterion())
				.groupby(Lead.lead_owner)
			).run()
		)
		for row in rows:
			row["meetings_ended"] = cint(ended.pop(row["user"], 0))
		for user, count in ended.items():
			if user:
				rows.append({"user": user, "meetings_ended": cint(count)})
		return rows

	def won_value_by_owner(self, won: list) -> dict:
		"""{owner: the worth (`lead_values`) of their leads won in the range}."""
		values = self.lead_values([row.name for row in won])
		result = dict.fromkeys((row.lead_owner for row in won), 0.0)
		for row in won:
			result[row.lead_owner] += values.get(row.name, 0.0)
		return result

	def follow_ups_by_person(self) -> dict:
		"""{user: {done, on_time, overdue}}: follow-ups on leads closed in the range (on time when
		closed by their day) and open ones past their day now."""
		ToDo = frappe.qb.DocType("ToDo")
		Lead = self.Lead
		base = (
			frappe.qb.from_(ToDo)
			.join(Lead)
			.on((ToDo.reference_type == "Lead") & (ToDo.reference_name == Lead.name))
			.where(ToDo.date.isnotnull() & (ToDo.status != TASK_CANCELLED) & self.lead_criterion())
		)
		result: dict[str, dict] = {}
		for user, done, on_time in (
			base.select(
				ToDo.allocated_to,
				Count(ToDo.name),
				Sum(Case().when(Date(ToDo.modified) <= ToDo.date, 1).else_(0)),
			)
			.where((ToDo.status == DONE) & self.in_range(ToDo.modified))
			.groupby(ToDo.allocated_to)
		).run():
			result.setdefault(user, {}).update({"done": cint(done), "on_time": cint(on_time)})
		for user, overdue in (
			base.select(ToDo.allocated_to, Count(ToDo.name))
			.where((ToDo.status == OPEN) & (ToDo.date < getdate()) & Lead.tbo_stage.isin(OPEN_STATUSES))
			.groupby(ToDo.allocated_to)
		).run():
			result.setdefault(user, {})["overdue"] = cint(overdue)
		return result
