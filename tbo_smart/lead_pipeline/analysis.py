"""The hub's Leads overview: Calling performance and Lead quality (docs/hub-leads-api.md §2.25).

Calling performance reads the call batches (what each person was handed, called and reached) and
the call log (every call they took or missed, and their talk time). Lead quality takes the leads
created in the range and shows, per source, industry, district, department or product, how many
were called, picked up, turned out wrong or not interested, and moved on to a meeting or beyond.
All of it is grouped queries on the overview's owners scope and range.
"""

import frappe
from frappe.query_builder import Case
from frappe.query_builder.functions import Count, IfNull, Max, Sum
from frappe.utils import cint

from tbo_smart import lead_source
from tbo_smart.api.mobile.utils import get_full_names, percent
from tbo_smart.lead_pipeline.overview import NO_VALUE, LeadOverview
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_WON,
	FIRST_MEETING,
	NOT_INTERESTED,
	PROGRESS,
	WRONG_NUMBER,
)
from tbo_smart.sales.call_batches import ANSWERED, BATCH, CLOSED, ITEM
from tbo_smart.telephony import MISSED
from tbo_smart.telephony.calls import DOCTYPE as CALL

# the switch on Lead quality: {key: Lead field}; a field this site does not have is left out
GROUPS = {
	"source": lead_source.field(),
	"industry": "industry",
	"district": "tbo_district",
	"department": "tbo_department",
	"product": "tbo_product",
}
# a group is only called best or worst once this many of its leads were called
MIN_CALLED = 10
MAX_GROUPS = 50
# a lead that reached a meeting or later (the overview funnel's "meeting" step on)
MOVED_ON = tuple(stage for stage, progress in PROGRESS.items() if progress >= PROGRESS[FIRST_MEETING])


class LeadAnalysis(LeadOverview):
	"""Calling performance and Lead quality for `owners` (None: everyone) in the range."""

	def build_analysis(self) -> dict:
		meta = frappe.get_meta("Lead")
		return {
			"from_date": self.from_date,
			"to_date": self.to_date,
			"min_called": MIN_CALLED,
			"calling": self.calling(),
			"quality": {
				key: self.quality_by(field) for key, field in GROUPS.items() if meta.has_field(field)
			},
		}

	# ---------- calling performance ----------

	def calling(self) -> list[dict]:
		"""One row per person: the batch calls handed in (repeats not counted), called, reached
		and left overdue, the calls logged (all, missed) and the talk time. Most called first."""
		batches = self.batch_figures()
		calls = self.call_figures()
		people = set(batches) | set(calls)
		names = get_full_names(people)
		rows = []
		for user in people:
			batch = batches.get(user, {})
			call = calls.get(user, {})
			given, called, reached = (cint(batch.get(key)) for key in ("given", "called", "reached"))
			rows.append(
				{
					"user": user,
					"full_name": names.get(user, user),
					"given": given,
					"called": called,
					"completion": percent(called, given),
					"reached": reached,
					"reach_rate": percent(reached, called),
					"overdue": cint(batch.get("overdue")),
					"calls": cint(call.get("calls")),
					"missed": cint(call.get("missed")),
					"talk_seconds": cint(call.get("talk_seconds")),
				}
			)
		return sorted(rows, key=lambda row: (-row["called"], -row["calls"], row["full_name"]))

	def batch_figures(self) -> dict:
		Batch, Item = frappe.qb.DocType(BATCH), frappe.qb.DocType(ITEM)
		original = Item.is_repeat == 0
		has_outcome = IfNull(Item.outcome, NO_VALUE) != NO_VALUE
		# a batch whose slot ended: a call still without an answer is overdue
		ended = (Batch.status == CLOSED) | (Batch.ends_at <= self.now)
		query = (
			frappe.qb.from_(Batch)
			.join(Item)
			.on((Item.parent == Batch.name) & (Item.parenttype == BATCH))
			.select(
				Batch.user,
				Sum(Case().when(original, 1).else_(0)),
				Sum(Case().when(original & has_outcome, 1).else_(0)),
				Sum(Case().when(original & Item.outcome.isin(ANSWERED), 1).else_(0)),
				Sum(Case().when(~has_outcome & ended, 1).else_(0)),
			)
			.where((Batch.batch_date >= self.from_date) & (Batch.batch_date <= self.to_date))
			.groupby(Batch.user)
		)
		if self.owners is not None:
			query = query.where(Batch.user.isin(self.owners or [""]))
		return {
			user: {"given": given, "called": called, "reached": reached, "overdue": overdue}
			for user, given, called, reached, overdue in query.run()
		}

	def call_figures(self) -> dict:
		Call = frappe.qb.DocType(CALL)
		query = (
			frappe.qb.from_(Call)
			.select(
				Call.agent,
				Count(Call.name),
				Sum(Case().when(Call.status == MISSED, 1).else_(0)),
				Sum(IfNull(Call.talk_seconds, 0)),
			)
			.where(
				Call.agent.isnotnull() & (Call.started_at >= self.from_date) & (Call.started_at < self.end)
			)
			.groupby(Call.agent)
		)
		if self.owners is not None:
			query = query.where(Call.agent.isin(self.owners or [""]))
		return {
			agent: {"calls": calls, "missed": missed, "talk_seconds": talk}
			for agent, calls, missed, talk in query.run()
		}

	# ---------- lead quality ----------

	def quality_by(self, field: str) -> dict:
		"""{rows, best, worst}: the leads created in the range, per value of `field`. Rates are
		of the leads, except the reach rate, which is of the leads called."""
		Lead = self.Lead
		Inner, Item, Log = (
			frappe.qb.DocType("Lead"),
			frappe.qb.DocType(ITEM),
			frappe.qb.DocType("TBO Stage Change"),
		)
		cohort = self.lead_criterion(Inner) & self.in_range(Inner.creation)
		called = (
			frappe.qb.from_(Item)
			.join(Inner)
			.on(Item.lead == Inner.name)
			.select(Item.lead, Max(Case().when(Item.outcome.isin(ANSWERED), 1).else_(0)).as_("reached"))
			.where((Item.parenttype == BATCH) & (IfNull(Item.outcome, NO_VALUE) != NO_VALUE) & cohort)
			.groupby(Item.lead)
		)
		moved = (
			frappe.qb.from_(Log)
			.join(Inner)
			.on(Log.parent == Inner.name)
			.select(Log.parent)
			.distinct()
			.where((Log.parenttype == "Lead") & Log.stage.isin(MOVED_ON) & cohort)
		)
		wrong = Lead.tbo_stage == WRONG_NUMBER
		if frappe.get_meta("Lead").has_field("tbo_wrong_number"):
			wrong |= Lead.tbo_wrong_number == 1
		group = IfNull(Lead[field], NO_VALUE)
		rows = [
			self.quality_row(*figures)
			for figures in (
				frappe.qb.from_(Lead)
				.left_join(called)
				.on(called.lead == Lead.name)
				.left_join(moved)
				.on(moved.parent == Lead.name)
				.select(
					group,
					Count(Lead.name),
					Count(called.lead),
					Sum(IfNull(called.reached, 0)),
					Sum(Case().when(wrong, 1).else_(0)),
					Sum(Case().when(Lead.tbo_stage == NOT_INTERESTED, 1).else_(0)),
					Count(moved.parent),
					Sum(Case().when(Lead.tbo_stage == CLOSED_WON, 1).else_(0)),
				)
				.where(self.lead_criterion() & self.in_range(Lead.creation))
				.groupby(group)
			).run()
		]
		rows.sort(key=lambda row: (-row["leads"], row["value"] or "~"))
		ranked = [row for row in rows if row["value"] and row["called"] >= MIN_CALLED]
		best = worst = None
		if len(ranked) > 1:
			best = max(ranked, key=lambda row: (row["reach_rate"], row["called"]))["value"]
			worst = min(ranked, key=lambda row: (row["reach_rate"], -row["called"]))["value"]
		return {"rows": rows[:MAX_GROUPS], "best": best, "worst": worst}

	@staticmethod
	def quality_row(value, leads, called, reached, wrong, not_interested, moved_on, won) -> dict:
		leads, called, reached, wrong, not_interested, moved_on, won = (
			cint(count) for count in (leads, called, reached, wrong, not_interested, moved_on, won)
		)
		return {
			"value": value or None,
			"leads": leads,
			"called": called,
			"called_rate": percent(called, leads),
			"reached": reached,
			"reach_rate": percent(reached, called),
			"wrong_rate": percent(wrong, leads),
			"not_interested_rate": percent(not_interested, leads),
			"moved_on": moved_on,
			"moved_rate": percent(moved_on, leads),
			"won": won,
		}
