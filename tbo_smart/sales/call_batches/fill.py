"""Filling a call batch (docs/call-batches.md §3): in priority order until the slot is full.

1. the person's leads with a follow-up due today or earlier (a call back set for later waits for
   the slot it falls in), 2. yesterday's Not answered and Busy, 3. leads never called, hot or new
   first, 4. the leads quiet for longest, 5. unassigned open leads of the department (given to
   the person on the spot, rows locked so two people never get the same one).
Steps 3 to 5 follow the campaign filter. A lead is never in two active batches nor twice in a day.
"""

import frappe
from frappe import _
from frappe.desk.form.assign_to import _add as add_assignment
from frappe.query_builder import Case
from frappe.query_builder.functions import Coalesce, Lower, Max
from frappe.utils import cint, get_datetime, getdate, now_datetime

from tbo_smart.sales import stale
from tbo_smart.sales.activities import last_calls, next_follow_ups
from tbo_smart.sales.call_batches import (
	ACTIVE,
	BATCH,
	CAMPAIGN_COLUMNS,
	FOLLOW_UP_DUE,
	GONE_QUIET,
	ITEM,
	NEVER_CALLED,
	PULLED,
	REASONS,
	RETRY_YESTERDAY,
	UNREACHED,
	daily_cap,
)

PHONE_FIELDS = ("mobile_no", "phone", "whatsapp_no")
TEMPERATURE_RANK = {"Hot": 0, "Warm": 1}


def campaign_of(department, request: dict | None = None) -> dict:
	"""{industry, state, district} to narrow new leads to: the request's, else the department's."""
	request = request or {}
	return {
		key: (request.get(key) or department.get(f"campaign_{key}") or "").strip().lower()
		for key in CAMPAIGN_COLUMNS
	}


def used_today(user: str, department: str, day) -> int:
	"""Leads already handed to `user` for `department` on `day`, retries not counted."""
	Batch, Item = frappe.qb.DocType(BATCH), frappe.qb.DocType(ITEM)
	rows = (
		frappe.qb.from_(Item)
		.join(Batch)
		.on(Batch.name == Item.parent)
		.select(Item.name)
		.where(
			(Batch.user == user)
			& (Batch.department == department)
			& (Batch.batch_date == getdate(day))
			& (Item.is_repeat == 0)
		)
	).run()
	return len(rows)


def taken_leads(day) -> set[str]:
	"""Leads in any active batch, or in any batch of `day`: not to be handed out again."""
	Batch, Item = frappe.qb.DocType(BATCH), frappe.qb.DocType(ITEM)
	rows = (
		frappe.qb.from_(Item)
		.join(Batch)
		.on(Batch.name == Item.parent)
		.select(Item.lead)
		.where((Batch.status == ACTIVE) | (Batch.batch_date == getdate(day)))
	).run()
	return {lead for (lead,) in rows}


def room_for(department, user: str, day, size: int) -> int:
	return max(min(cint(size), daily_cap(department, user) - used_today(user, department.name, day)), 0)


class BatchFiller:
	def __init__(self, department, user: str, day, starts_at, ends_at, room: int, campaign: dict):
		self.department, self.user, self.day = department, user, getdate(day)
		self.starts_at, self.ends_at = get_datetime(starts_at), get_datetime(ends_at)
		self.room, self.campaign = room, campaign
		self.taken = taken_leads(self.day)
		self.items: list[dict] = []
		self.chosen: set[str] = set()

	def run(self) -> list[dict]:
		pool = self.owned_pool()
		self.follow_ups_due(pool)
		self.unreached_yesterday(pool)
		self.never_called(pool)
		self.gone_quiet(pool)
		self.pull_unassigned()
		return self.items

	# ---------- helpers ----------

	@property
	def full(self) -> bool:
		return len(self.items) >= self.room

	def add(self, lead: str, priority: int, retry_at=None) -> None:
		if self.full or lead in self.taken or lead in self.chosen:
			return
		self.chosen.add(lead)
		self.items.append(
			{
				"lead": lead,
				"priority": priority,
				"priority_reason": _(REASONS[priority]),
				"retry_at": retry_at,
				"attempts": 0,
			}
		)

	def owned_pool(self) -> list:
		columns = [column for column in CAMPAIGN_COLUMNS.values() if frappe.db.has_column("Lead", column)]
		return frappe.get_all(
			"Lead",
			filters={
				"lead_owner": self.user,
				"tbo_department": self.department.name,
				"tbo_stage": ("in", stale.STALE_STAGES),
			},
			fields=[
				"name",
				"tbo_temperature",
				"tbo_stage",
				"creation",
				"tbo_callback_at",
				*PHONE_FIELDS,
				*columns,
			],
			limit_page_length=0,
		)

	@staticmethod
	def rank(lead) -> tuple:
		"""Hot first, then a lead still at New Lead, then the newest."""
		return (
			TEMPERATURE_RANK.get(lead.get("tbo_temperature"), 2),
			0 if lead.get("tbo_stage") == "New Lead" else 1,
			-get_datetime(lead.creation).timestamp(),
		)

	def callable_in_campaign(self, lead) -> bool:
		if not any(lead.get(field) for field in PHONE_FIELDS):
			return False
		for key, column in CAMPAIGN_COLUMNS.items():
			wanted = self.campaign.get(key)
			if wanted and column in lead and (lead.get(column) or "").strip().lower() != wanted:
				return False
		return True

	# ---------- the five steps ----------

	def follow_ups_due(self, pool: list) -> None:
		due = next_follow_ups("Lead", [lead.name for lead in pool])
		now = now_datetime()
		rows = []
		for lead in pool:
			callback = get_datetime(lead.tbo_callback_at) if lead.tbo_callback_at else None
			if lead.name not in due or getdate(due[lead.name]) > self.day:
				continue
			if callback and callback > self.ends_at:
				continue
			rows.append((getdate(due[lead.name]), self.rank(lead), lead.name, callback))
		for _due, _rank, name, callback in sorted(rows, key=lambda row: row[:3]):
			self.add(
				name, FOLLOW_UP_DUE, callback if callback and callback > max(now, self.starts_at) else None
			)

	def unreached_yesterday(self, pool: list) -> None:
		if self.full:
			return
		Batch, Item = frappe.qb.DocType(BATCH), frappe.qb.DocType(ITEM)
		previous = (
			frappe.qb.from_(Batch)
			.select(Max(Batch.batch_date))
			.where(
				(Batch.user == self.user)
				& (Batch.department == self.department.name)
				& (Batch.batch_date < self.day)
			)
		).run()[0][0]
		if not previous:
			return
		rows = (
			frappe.qb.from_(Item)
			.join(Batch)
			.on(Batch.name == Item.parent)
			.select(Item.lead, Item.outcome)
			.where(
				(Batch.user == self.user)
				& (Batch.department == self.department.name)
				& (Batch.batch_date == previous)
				& Item.outcome.isnotnull()
				& (Item.outcome != "")
			)
			.orderby(Item.called_at)
		).run()
		final = dict(rows)  # the last outcome of the day wins
		owned = {lead.name for lead in pool}
		for lead, outcome in final.items():
			if outcome in UNREACHED and lead in owned:
				self.add(lead, RETRY_YESTERDAY)

	def never_called(self, pool: list) -> None:
		if self.full:
			return
		candidates = [
			lead for lead in pool if lead.name not in self.taken and self.callable_in_campaign(lead)
		]
		called = last_calls("Lead", [lead.name for lead in candidates])
		for lead in sorted((lead for lead in candidates if lead.name not in called), key=self.rank):
			self.add(lead.name, NEVER_CALLED)

	def gone_quiet(self, pool: list) -> None:
		if self.full:
			return
		candidates = [
			lead for lead in pool if lead.name not in self.taken and self.callable_in_campaign(lead)
		]
		if not candidates:
			return
		newest = stale.latest_activity([lead.name for lead in candidates])
		longest_quiet = sorted(
			candidates,
			key=lambda lead: (
				get_datetime(newest.get(lead.name) or lead.creation),
				lead.name,
			),
		)
		for lead in longest_quiet:
			self.add(lead.name, GONE_QUIET)

	def pull_unassigned(self) -> None:
		if self.full or not cint(self.department.pull_unassigned):
			return
		Lead = frappe.qb.DocType("Lead")
		query = (
			frappe.qb.from_(Lead)
			.select(Lead.name)
			.where(
				(Lead.tbo_department == self.department.name)
				& (Lead.lead_owner.isnull() | (Lead.lead_owner == ""))
				& Lead.tbo_stage.isin(list(stale.STALE_STAGES))
			)
			.where(
				(Coalesce(Lead.mobile_no, "") != "")
				| (Coalesce(Lead.phone, "") != "")
				| (Coalesce(Lead.whatsapp_no, "") != "")
			)
			.orderby(Case().when(Lead.tbo_temperature == "Hot", 0).else_(1))
			.orderby(Lead.creation, order=frappe.qb.desc)
		)
		for key, column in CAMPAIGN_COLUMNS.items():
			if self.campaign.get(key) and frappe.db.has_column("Lead", column):
				query = query.where(Lower(Lead[column]) == self.campaign[key])
		if self.taken or self.chosen:
			query = query.where(Lead.name.notin(list(self.taken | self.chosen)))
		# SKIP LOCKED: a lead another person is being given right now is passed over
		rows = query.limit(self.room - len(self.items)).for_update(skip_locked=True).run()
		for (name,) in rows:
			self.give(name)
			self.add(name, PULLED)

	def give(self, lead: str) -> None:
		doc = frappe.get_doc("Lead", lead)
		doc.lead_owner = self.user
		doc.save(ignore_permissions=True)
		add_assignment(
			{
				"assign_to": [self.user],
				"doctype": "Lead",
				"name": lead,
				"assigned_by": frappe.session.user,
				"description": _("Pulled into your daily calls"),
			},
			ignore_permissions=True,
		)


def build_batch(
	department, user: str, label: str, starts_at, ends_at, size: int, campaign: dict, allow_empty: bool = True
):
	"""Insert the batch for `user` and return it; None when the day's cap leaves no room, or
	(`allow_empty` False) when there is no lead to call."""
	day = getdate(starts_at)
	room = room_for(department, user, day, size)
	if room <= 0:
		return None
	items = BatchFiller(department, user, day, starts_at, ends_at, room, campaign).run()
	if not items and not allow_empty:
		return None
	return frappe.get_doc(
		{
			"doctype": BATCH,
			"user": user,
			"department": department.name,
			"batch_date": day,
			"slot": label,
			"starts_at": starts_at,
			"ends_at": ends_at,
			"status": ACTIVE,
			"items": items,
		}
	).insert(ignore_permissions=True)
