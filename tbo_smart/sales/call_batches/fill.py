"""Filling a call batch (docs/call-batches.md §3): new leads nobody has called yet, until the
slot is full. 1. the person's own, hot first, then the newest, 2. unassigned ones of the
department (given to the person on the spot, rows locked so two people never get the same one).
Only leads at New Lead, with a phone, following the campaign filter, and not a newer duplicate of
another lead. A lead is never in two active batches nor twice in a day.
"""

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Coalesce, Lower
from frappe.utils import cint, get_datetime, getdate

from tbo_smart.lead_pipeline.statuses import NEW_LEAD
from tbo_smart.sales.activities import last_calls
from tbo_smart.sales.call_batches import (
	ACTIVE,
	BATCH,
	CAMPAIGN_COLUMNS,
	ITEM,
	NEVER_CALLED,
	PULLED,
	REASONS,
	assign_undated,
	daily_cap,
)
from tbo_smart.sales.duplicates import newer_duplicates

PHONE_FIELDS = ("mobile_no", "phone", "whatsapp_no")
PULLED_NOTE = "Pulled into your daily calls"
TEMPERATURE_RANK = {"Hot": 0, "Warm": 1}


def campaign_of(
	department, request: dict | None = None, focus: dict | None = None, user: str | None = None
) -> dict:
	"""{industry, state, district, product} to narrow new leads to. The department's own campaign
	is a limit set by a manager and always wins; then the product, industry and district of `user`'s
	call plan (also a manager's, §11); where they leave a key open the request's value is used, else the person's
	focus (when it is for this department)."""
	from tbo_smart.sales.call_batches.plans import plan_district, plan_industry, plan_product

	request = request or {}
	chosen = (focus or {}) if (focus or {}).get("department") == department.name else {}
	planned = (
		{
			"product": plan_product(department, user),
			"industry": plan_industry(department, user),
			"district": plan_district(department, user),
		}
		if user
		else {}
	)
	return {
		key: (
			department.get(f"campaign_{key}") or planned.get(key) or request.get(key) or chosen.get(key) or ""
		)
		.strip()
		.lower()
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
	def __init__(self, department, user: str, day, room: int, campaign: dict):
		self.department, self.user, self.day = department, user, getdate(day)
		self.room, self.campaign = room, campaign
		self.taken = taken_leads(self.day)
		self.items: list[dict] = []
		self.chosen: set[str] = set()

	def run(self) -> list[dict]:
		self.never_called(self.owned_pool())
		self.pull_unassigned()
		return self.items

	# ---------- helpers ----------

	@property
	def full(self) -> bool:
		return len(self.items) >= self.room

	def add(self, lead: str, priority: int) -> None:
		if self.full or lead in self.taken or lead in self.chosen:
			return
		self.chosen.add(lead)
		self.items.append(
			{
				"lead": lead,
				"priority": priority,
				"priority_reason": _(REASONS[priority]),
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
				"tbo_stage": NEW_LEAD,
			},
			fields=[
				"name",
				"tbo_temperature",
				"creation",
				*PHONE_FIELDS,
				*columns,
			],
			limit_page_length=0,
		)

	@staticmethod
	def rank(lead) -> tuple:
		"""Hot first, then the newest."""
		return (
			TEMPERATURE_RANK.get(lead.get("tbo_temperature"), 2),
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

	# ---------- the two steps ----------

	def never_called(self, pool: list) -> None:
		candidates = [
			lead for lead in pool if lead.name not in self.taken and self.callable_in_campaign(lead)
		]
		names = [lead.name for lead in candidates]
		skip = set(last_calls("Lead", names)) | newer_duplicates(names)
		for lead in sorted((lead for lead in candidates if lead.name not in skip), key=self.rank):
			self.add(lead.name, NEVER_CALLED)

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
				& (Lead.tbo_stage == NEW_LEAD)
				& Lead.tbo_last_call_on.isnull()
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
		skip = self.taken | self.chosen | newer_duplicates(query.run(pluck=True))
		if skip:
			query = query.where(Lead.name.notin(list(skip)))
		# SKIP LOCKED: a lead another person is being given right now is passed over
		rows = query.limit(self.room - len(self.items)).for_update(skip_locked=True).run()
		for (name,) in rows:
			self.give(name)
			self.add(name, PULLED)

	def give(self, lead: str) -> None:
		doc = frappe.get_doc("Lead", lead)
		doc.lead_owner = self.user
		doc.save(ignore_permissions=True)
		assign_undated(lead, self.user, PULLED_NOTE)


def lock_batches(user: str, day) -> bool:
	"""Hold `user`'s batches until this transaction ends, so two requests at once (a double tap on
	Next batch, the slot clock) cannot both hand out the same leads. False when another request
	made one of their batches for `day` after this one started reading: a plain read still sees
	the transaction's first snapshot, a locking read sees what is committed now."""
	frappe.db.get_value("User", user, "name", for_update=True)
	filters = {"user": user, "batch_date": getdate(day)}
	seen = frappe.db.count(BATCH, filters)
	return len(frappe.db.get_values(BATCH, filters, "name", for_update=True)) == seen


def build_batch(
	department, user: str, label: str, starts_at, ends_at, size: int, campaign: dict, allow_empty: bool = True
):
	"""Insert the batch for `user` and return it; None when the day's cap leaves no room, or
	(`allow_empty` False) when there is no lead to call."""
	day = getdate(starts_at)
	if not lock_batches(user, day):
		return None
	room = room_for(department, user, day, size)
	if room <= 0:
		return None
	items = BatchFiller(department, user, day, room, campaign).run()
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
