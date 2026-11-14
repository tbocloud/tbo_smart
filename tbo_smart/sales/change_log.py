"""The lead change feed the hub's local copy reads (docs/hub-leads-api.md §2.32).

Every change that alters what the hub shows of a lead (the lead, its notes, calls, events,
follow-ups, files, call batches, quotations, proposals, meetings and deals) writes one
`TBO Lead Change` row naming the lead. The row is written **after the transaction commits**, so
a save that is rolled back leaves none, and it never breaks the save it describes: a failure
is logged and the save goes on (the hub's nightly comparison finds what the feed missed).
Rows older than 30 days are deleted daily; the feed is only for catching up.
"""

import frappe
from frappe.query_builder.functions import Max
from frappe.utils import add_days, now_datetime

from tbo_smart.sales import LEAD

DOCTYPE = "TBO Lead Change"
KEEP_DAYS = 30
EPOCH = "2000-01-01 00:00:00"
FIELDS = (
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"lead",
	"change_type",
	"at",
)


def record(changes) -> None:
	"""Queue `(lead, change_type)` rows to be written once the transaction commits."""
	changes = {(lead, kind) for lead, kind in changes if lead}
	if not changes or frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
		return
	frappe.db.after_commit.add(lambda: write(changes))


def write(changes: set) -> None:
	"""One insert for all the rows; runs after the commit, so it can only log, never raise."""
	try:
		at = now_datetime()
		user = frappe.session.user
		rows = [
			(frappe.generate_hash(length=10), at, at, user, user, 0, 0, lead, kind, at)
			for lead, kind in sorted(changes)
		]
		frappe.db.bulk_insert(DOCTYPE, list(FIELDS), rows)
	except Exception:
		frappe.log_error(title="Lead change feed: could not write", reference_doctype=LEAD)


# ---------- doc_events ----------


def lead_saved(doc, method=None) -> None:
	record([(doc.name, "Lead")])


def lead_deleted(doc, method=None) -> None:
	record([(doc.name, "Deleted")])


def lead_renamed(doc, method=None, old=None, new=None, merge=False) -> None:
	"""A rename gives the lead a new name; a merge folds it into the lead kept. Either way the
	old name is gone and the new one has changed."""
	record([(old, "Deleted"), (new or doc.name, "Lead")])


def activity_changed(doc, method=None) -> None:
	"""A note, event, follow-up, call, file, batch, quotation, proposal, meeting or deal on a lead."""
	reader = READERS.get(doc.doctype)
	if reader:
		record(reader(doc))


def comment_leads(doc):
	if doc.get("reference_doctype") == LEAD and doc.get("comment_type") == "Comment":
		yield doc.reference_name, "Note"


def event_leads(doc):
	kind = "Call" if doc.get("event_category") == "Call" else "Event"
	for row in doc.get("event_participants") or []:
		if row.get("reference_doctype") == LEAD:
			yield row.reference_docname, kind


def todo_leads(doc):
	if doc.get("reference_type") == LEAD:
		yield doc.reference_name, "Follow-up"


def file_leads(doc):
	if doc.get("attached_to_doctype") == LEAD:
		yield doc.attached_to_name, "File"


def batch_leads(doc):
	for row in doc.get("items") or []:
		yield row.get("lead"), "Batch"


def linked_lead(field: str, kind: str):
	def reader(doc):
		yield doc.get(field), kind

	return reader


READERS = {
	"Comment": comment_leads,
	"Event": event_leads,
	"ToDo": todo_leads,
	"File": file_leads,
	"TBO Call": linked_lead("lead", "Call"),
	"TBO Call Batch": batch_leads,
	"TBO Proposal": linked_lead("lead", "Proposal"),
	# a document step skipped, not used or brought back: the lead's documents changed
	"TBO Lead Document Step": linked_lead("lead", "Proposal"),
	"TBO Lead Meeting": linked_lead("lead", "Meeting"),
	"Quotation": linked_lead("tbo_lead", "Quotation"),
	"Opportunity": linked_lead("tbo_lead", "Deal"),
}


# ---------- the feed ----------


def changes_since(since: str | None, limit: int) -> dict:
	"""{changes: [{lead, at, deleted}], next_since, more, now}: the latest change of each lead
	after `since`, oldest first. `next_since` is the last change's time: ask again from it.
	`deleted` is a hint (the lead's own delete was its last change); the bulk read is what
	says a lead is gone."""
	Change = frappe.qb.DocType(DOCTYPE)
	latest = Max(Change.at)
	rows = (
		frappe.qb.from_(Change)
		.select(Change.lead, latest.as_("at"))
		.where(Change.at > (since or EPOCH))
		.groupby(Change.lead)
		.orderby(latest)
		.limit(limit + 1)
	).run(as_dict=True)
	more = len(rows) > limit
	rows = rows[:limit]
	removed = {}
	if rows:
		removed = dict(
			(
				frappe.qb.from_(Change)
				.select(Change.lead, Max(Change.at))
				.where((Change.change_type == "Deleted") & Change.lead.isin([row.lead for row in rows]))
				.groupby(Change.lead)
			).run()
		)
	return {
		"changes": [
			{"lead": row.lead, "at": row.at, "deleted": removed.get(row.lead) == row.at} for row in rows
		],
		"next_since": rows[-1].at if rows else since,
		"more": more,
		"now": now_datetime(),
	}


def prune() -> None:
	"""Scheduler, daily."""
	Change = frappe.qb.DocType(DOCTYPE)
	frappe.qb.from_(Change).delete().where(Change.at < add_days(now_datetime(), -KEEP_DAYS)).run()
