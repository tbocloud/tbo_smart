"""One person's calls queue: what to call now, what is due later and what is overdue."""

import frappe
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime

from tbo_smart.sales.call_batches import ACTIVE, ANSWERED, BATCH, ITEM, OVERDUE_DAYS

ITEM_FIELDS = (
	"name",
	"lead",
	"priority",
	"priority_reason",
	"is_repeat",
	"outcome",
	"called_at",
	"retry_at",
	"attempts",
	"note",
)


def reached_leads(rows: list) -> set[tuple[str, str]]:
	"""(batch, lead) of the rows whose call was answered."""
	return {(row.batch, row.lead) for row in rows if row.outcome in ANSWERED}


def item_rows(batch_filters: dict) -> list[frappe._dict]:
	"""The items of the batches matching `batch_filters`, each with its batch's name, slot,
	department, user, status and window, in batch then list order."""
	Batch, Item = frappe.qb.DocType(BATCH), frappe.qb.DocType(ITEM)
	query = (
		frappe.qb.from_(Item)
		.join(Batch)
		.on(Batch.name == Item.parent)
		.select(
			*(Item[field] for field in ITEM_FIELDS),
			Batch.name.as_("batch"),
			Batch.slot,
			Batch.department,
			Batch.user,
			Batch.status,
			Batch.batch_date,
			Batch.starts_at,
			Batch.ends_at,
		)
		.orderby(Batch.starts_at)
		.orderby(Item.idx)
	)
	for field, value in batch_filters.items():
		many = isinstance(value, list | tuple | set)
		query = query.where(Batch[field].isin(list(value)) if many else Batch[field] == value)
	return query.run(as_dict=True)


class CallQueue:
	"""`pending` are due now, `scheduled` come back later in a running batch, `overdue` belong to a
	batch whose slot is over. Overdue lasts OVERDUE_DAYS days and lists a lead once (its latest
	missed call); a lead that is in the queue again, or was called since, is not listed overdue."""

	def __init__(self, user: str, now=None):
		self.user, self.now = user, get_datetime(now) if now else now_datetime()
		since = add_to_date(getdate(self.now), days=-OVERDUE_DAYS)
		self.rows = [row for row in item_rows({"user": user}) if getdate(row.batch_date) >= since]
		self.running = [row for row in self.rows if self.is_running(row)]
		self.pending = self.due_now()
		self.scheduled = self.coming_back()
		queued = {row.lead for row in self.pending + self.scheduled}
		self.overdue = self.missed(queued)

	def missed(self, queued: set) -> list:
		"""The calls of ended slots nobody made, the latest one per lead (a lead missed on five days
		is one call to make, not five)."""
		called = {}
		for row in self.rows:
			if row.outcome:
				called[row.lead] = max(
					getdate(row.batch_date), called.get(row.lead) or getdate(row.batch_date)
				)
		latest = {}
		for row in self.rows:  # by batch start, so the last row of a lead is its latest
			if row.outcome or self.is_running(row) or row.lead in queued:
				continue
			if row.lead in called and called[row.lead] >= getdate(row.batch_date):
				continue
			latest[row.lead] = row
		keep = {row.name for row in latest.values()}
		return [row for row in self.rows if row.name in keep]

	def due_now(self) -> list:
		rows = [row for row in self.running if not row.outcome and not self.waits(row)]
		return sorted(rows, key=self.queue_order)

	def coming_back(self) -> list:
		rows = [row for row in self.running if not row.outcome and self.waits(row)]
		return sorted(rows, key=self.retry_order)

	@staticmethod
	def queue_order(row) -> tuple:
		return (row.priority or 9, get_datetime(row.retry_at or row.starts_at))

	@staticmethod
	def retry_order(row):
		return get_datetime(row.retry_at)

	def is_running(self, row) -> bool:
		return row.status == ACTIVE and get_datetime(row.ends_at) > self.now

	def waits(self, row) -> bool:
		return bool(row.retry_at) and get_datetime(row.retry_at) > self.now

	@property
	def running_batches(self) -> list[str]:
		return list(dict.fromkeys(row.batch for row in self.running))

	def today(self) -> list[frappe._dict]:
		today = getdate(self.now)
		return [row for row in self.rows if getdate(row.batch_date) == today and not row.is_repeat]

	def totals(self, rows: list) -> dict:
		"""{size, called, connected} of the non-retry rows; a lead is connected when any call of it in
		the batch was answered (a retry, a call back, or the customer calling back)."""
		own = [row for row in rows if not row.is_repeat]
		reached = reached_leads(rows)
		return {
			"size": len(own),
			"called": sum(1 for row in own if row.outcome),
			"connected": sum(1 for row in own if (row.batch, row.lead) in reached),
		}
