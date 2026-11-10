"""Merging two leads that are the same enquiry (docs/hub-leads-api.md §2.31).

The lead to KEEP takes the other's numbers, its empty fields and everything linked to it; the
other lead is then deleted. Frappe's own `rename_doc(..., merge=True)` re-points every Link and
Dynamic Link field (child tables included: call batch items, event participants, deal and
quotation parties), comments, communications, files, ToDos and versions. What it does not carry
is done here: the number rows (the merged lead's child rows go with it), its empty-field values,
its last call and reply, and the copied `lead_name` of meetings, proposals, outreach messages and
triage rows. Nothing of the merged lead's own stage log is kept.

The whole merge is one transaction: any failure leaves both leads as they were.
"""

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart import lead_source
from tbo_smart.sales import LEAD, departments, numbers
from tbo_smart.sales.activities import add_note

# filled from the merged lead only where the kept lead has nothing
FILL_FIELDS = (
	"company_name",
	"email_id",
	"phone",
	"whatsapp_no",
	"job_title",
	"website",
	"territory",
	"industry",
	"city",
	"state",
	"country",
	"customer",
	"no_of_employees",
	"annual_revenue",
	"tbo_district",
	"tbo_segment",
	"tbo_google_profile_url",
	"tbo_maps_url",
	"tbo_latitude",
	"tbo_longitude",
	"tbo_location_note",
)
# (when it happened, what it was): the later of the two leads' wins
LATEST_PAIRS = (
	("tbo_last_call_on", "tbo_last_call_result"),
	("tbo_last_reply_on", "tbo_last_reply_class"),
)
# doctypes holding a copy of the lead's name next to their `lead` link
NAME_COPIES = ("TBO Proposal", "TBO Lead Meeting", "TBO Outreach Message", "TBO Lead Triage Row")


def is_manager() -> bool:
	return departments.can_manage()


def can_merge(department: str | None) -> bool:
	"""A manager, or a head of the lead's department."""
	return is_manager() or departments.is_head(department)


def label_of(lead) -> str:
	""" "Ashraf, Amana UAE": the person and the company, as the timeline names a lead."""
	return ", ".join(part for part in (lead.lead_name, lead.company_name) if part) or lead.name


# ---------- what a lead holds, for the dialog ----------


def counts_of(lead: str) -> dict:
	"""How many of each thing sit on the lead (the ones a merge moves): {notes, calls,
	follow_ups, meetings, proposals, quotations, deals, files}."""
	from tbo_smart.lead_pipeline.quotations import lead_quotations
	from tbo_smart.sales.activities import calls_of

	on_lead = {"reference_doctype": LEAD, "reference_name": lead}
	return {
		"notes": frappe.db.count("Comment", {**on_lead, "comment_type": "Comment"}),
		"calls": len(calls_of(LEAD, [lead]).get(lead, [])) + frappe.db.count("TBO Call", {"lead": lead}),
		"follow_ups": frappe.db.count("ToDo", {"reference_type": LEAD, "reference_name": lead}),
		"meetings": frappe.db.count("TBO Lead Meeting", {"lead": lead}),
		"proposals": frappe.db.count("TBO Proposal", {"lead": lead}),
		"quotations": len(lead_quotations(lead, ["name"])),
		"deals": frappe.db.count("Opportunity", {"tbo_lead": lead}),
		"files": frappe.db.count("File", {"attached_to_doctype": LEAD, "attached_to_name": lead}),
	}


def summary_of(lead: str, other: str) -> dict:
	"""One side of the merge dialog: who the lead is, its counts, and how many of its numbers
	`other` does not have yet."""
	row = frappe.db.get_value(
		LEAD,
		lead,
		["name", "lead_name", "company_name", "email_id", "mobile_no", "tbo_stage", "lead_owner", "creation"],
		as_dict=True,
	)
	row.owner_name = frappe.utils.get_fullname(row.lead_owner) if row.lead_owner else None
	row.numbers = numbers.numbers_of(lead)
	row.new_numbers = len(new_number_rows(lead, other))
	row.counts = counts_of(lead)
	return row


def new_number_rows(source: str, target: str) -> list:
	"""The rows of `source` whose number `target` does not have (in any status)."""
	have = [row.digits for row in numbers.rows_of(target)]
	return [
		row
		for row in numbers.rows_of(source)
		if not any(numbers.same_digits(row.digits, digits) for digits in have)
	]


def preview(first: str, second: str) -> dict:
	"""{leads: [the two leads' summaries]}: what the dialog shows before anything moves (the
	pair is checked as for the merge)."""
	check_pair(first, second)
	return {"leads": [summary_of(first, second), summary_of(second, first)]}


# ---------- the merge ----------


def check_pair(keep: str, merge: str) -> tuple:
	"""Both leads, loaded and checked: two different leads the acting user may write, and may merge."""
	if keep == merge:
		frappe.throw(_("Choose two different leads."))
	kept, merged = frappe.get_doc(LEAD, keep), frappe.get_doc(LEAD, merge)
	for lead in (kept, merged):
		lead.check_permission("write")
	if not is_manager():
		if merged.tbo_department and merged.tbo_department != kept.tbo_department:
			frappe.throw(
				_("These leads are in different departments. Only a manager may merge them."),
				frappe.PermissionError,
			)
		if not departments.is_head(kept.tbo_department):
			frappe.throw(
				_("Only a manager or the head of the lead's department may merge leads."),
				frappe.PermissionError,
			)
	return kept, merged


def merge_leads(keep: str, merge: str) -> str:
	"""Merge lead `merge` into `keep` and return the kept lead's name."""
	kept, merged = check_pair(keep, merge)
	moved = counts_of(merge)
	moved["numbers"] = len(new_number_rows(merge, keep))
	label = label_of(merged)
	copy_numbers(kept, merged)
	fill_empty_fields(kept, merged)
	kept.save()
	frappe.rename_doc(LEAD, merge, keep, force=True, merge=True, show_alert=False, rebuild_search=False)
	refresh_name_copies(kept)
	add_note(LEAD, keep, merge_note(merge, label, moved))
	return keep


def copy_numbers(kept, merged) -> None:
	"""The merged lead's numbers the kept lead lacks, as non-primary rows with their status,
	label, person and who added them (the rows would go with the merged lead)."""
	for row in numbers.table(merged):
		if numbers.row_with(kept, row.digits):
			continue
		copy = numbers.add_row(
			kept,
			row.number,
			row.label,
			row.person,
			row.designation,
			note=row.note,
			source=row.source,
			quiet=True,
		)
		copy.update({"status": row.status, "added_by": row.added_by, "added_on": row.added_on})


def fill_empty_fields(kept, merged) -> None:
	"""Values only the merged lead has; what the kept lead holds is never overwritten."""
	for field in (*FILL_FIELDS, lead_source.field()):
		if not kept.get(field) and merged.get(field):
			kept.set(field, merged.get(field))
	if kept.email_id == merged.email_id:
		# ERPNext refuses an email another lead has; the merged lead is deleted anyway
		frappe.db.set_value(LEAD, merged.name, "email_id", None, update_modified=False)
	if cint(merged.unsubscribed):
		kept.unsubscribed = 1
	for when, what in LATEST_PAIRS:
		if merged.get(when) and (not kept.get(when) or merged.get(when) > kept.get(when)):
			kept.set(when, merged.get(when))
			kept.set(what, merged.get(what))


def refresh_name_copies(kept) -> None:
	"""The lead's name copied onto its meetings, proposals, messages and triage rows."""
	for doctype in NAME_COPIES:
		frappe.db.set_value(
			doctype,
			{"lead": kept.name, "lead_name": ("!=", kept.lead_name)},
			"lead_name",
			kept.lead_name,
			update_modified=False,
		)


def merge_note(merge: str, label: str, moved: dict) -> str:
	"""The kept lead's timeline entry: who was merged in, by whom, and what came with it."""
	nouns = {
		"notes": (_("note"), _("notes")),
		"calls": (_("call"), _("calls")),
		"follow_ups": (_("follow-up"), _("follow-ups")),
		"meetings": (_("meeting"), _("meetings")),
		"proposals": (_("proposal"), _("proposals")),
		"quotations": (_("quotation"), _("quotations")),
		"deals": (_("deal"), _("deals")),
		"files": (_("file"), _("files")),
		"numbers": (_("number"), _("numbers")),
	}
	brought = ", ".join(f"{count} {nouns[key][int(count != 1)]}" for key, count in moved.items() if count)
	text = _("Merged {0} ({1}) into this lead by {2}").format(
		merge, label, frappe.utils.get_fullname(frappe.session.user)
	)
	return f"{text}. {brought}" if brought else text
