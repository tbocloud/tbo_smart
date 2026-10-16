"""One-off move of the existing leads to the pipeline statuses (docs/lead-pipeline.md §3).

Run by the patch `tbo_smart.patches.v1.migrate_lead_pipeline_statuses` on deploy, whatever the
switches say. Each lead's old status is kept in `tbo_pre_pipeline_status`, which also makes the
run idempotent: a lead that has it is never mapped again. The retired statuses are deleted once
no lead uses them (CRM Lead Status has no "disabled" flag, and leaving them would keep them in
every status menu and kanban, and make CRM's conversion set "Qualified"), and their
definitions are kept in TBO Smart Settings so `revert` can put everything back.
"""

import json
from collections import Counter

import frappe
from frappe import _

from tbo_smart.lead_pipeline.statuses import (
	CLOSED_LOST,
	CLOSED_WON,
	CONTACTED,
	FIRST_MEETING,
	NEW_LEAD,
	POSITIVE,
	PROPOSAL_SENT,
	STATUSES,
	ensure_statuses,
)
from tbo_smart.smart_leads.migration import get_migration_notes

PREVIOUS_FIELD = "tbo_pre_pipeline_status"
RETIRED_FIELD = "pipeline_retired_statuses"
# Frappe CRM's default statuses and the migration's "Converted"; Contacted stays (same name)
OLD_STATUSES = ("New", "Contacted", "Nurture", "Qualified", "Unqualified", "Junk", "Converted")
RETIRED = ("New", "Nurture", "Qualified", "Unqualified", "Junk", "Converted")
LOST_REASON = "Other"


def new_status(status: str | None, original: str | None, converted: bool) -> str | None:
	"""The pipeline status for a lead's old status, the status its migration note records from
	the old ERPNext site, and whether it already became a deal. None: leave the lead alone.

	| Old status | New status |
	| --- | --- |
	| New | New Lead |
	| Contacted | Contacted; First Meeting Completed when the original was Meeting… / Demo… |
	| Nurture | Positive / Interested |
	| Qualified | Proposal Sent when the original was Quotation, else Positive / Interested |
	| Unqualified, Junk | Closed – Lost (the lost reason is kept) |
	| Converted, or any converted lead | Closed – Won (no deal is created for these) |
	"""
	if status not in OLD_STATUSES:
		return None
	original = (original or "").strip().lower()
	if converted or status == "Converted":
		return CLOSED_WON
	if status == "New":
		return NEW_LEAD
	if status == "Contacted":
		return FIRST_MEETING if original.startswith(("meeting", "demo")) else CONTACTED
	if status == "Nurture":
		return POSITIVE
	if status == "Qualified":
		return PROPOSAL_SENT if original == "quotation" else POSITIVE
	return CLOSED_LOST


def execute() -> Counter:
	"""Map every lead not mapped yet; retire the old statuses. Returns {(old, new): count}."""
	ensure_statuses()
	order_statuses()
	ensure_lost_reason()
	Lead = frappe.qb.DocType("CRM Lead")
	leads = (
		frappe.qb.from_(Lead)
		.select(Lead.name, Lead.status, Lead.converted, Lead.lost_reason, Lead.lost_notes)
		.where(
			Lead.status.isin(OLD_STATUSES) & ((Lead[PREVIOUS_FIELD].isnull()) | (Lead[PREVIOUS_FIELD] == ""))
		)
	).run(as_dict=True)
	notes = get_migration_notes([lead.name for lead in leads])
	counts: Counter = Counter()
	for lead in leads:
		target = new_status(lead.status, notes.get(lead.name, {}).get("status"), bool(lead.converted))
		values = {"status": target, PREVIOUS_FIELD: lead.status}
		if target == CLOSED_LOST and not lead.lost_reason:
			values["lost_reason"] = LOST_REASON
			values["lost_notes"] = lead.lost_notes or _("{0} before the pipeline statuses").format(
				lead.status
			)
		# no Version, no modified bump and no CRM on_update: nothing but the status moves
		frappe.db.set_value("CRM Lead", lead.name, values, update_modified=False)
		counts[(lead.status, target)] += 1
	retire_old_statuses()
	return counts


def order_statuses() -> None:
	"""Put the pipeline statuses in pipeline order, with their types (Contacted already exists)."""
	for position, (name, kind, _color) in enumerate(STATUSES, start=1):
		frappe.db.set_value("CRM Lead Status", name, {"position": position, "type": kind})
	frappe.clear_cache(doctype="CRM Lead Status")


def ensure_lost_reason() -> None:
	if not frappe.db.exists("CRM Lost Reason", LOST_REASON):
		frappe.get_doc({"doctype": "CRM Lost Reason", "lost_reason": LOST_REASON}).insert(
			ignore_permissions=True
		)


def retire_old_statuses() -> list[str]:
	"""Delete the old statuses no lead uses any more, keeping their definitions for `revert`."""
	kept = json.loads(frappe.db.get_single_value("TBO Smart Settings", RETIRED_FIELD) or "[]")
	known = {row["name"] for row in kept}
	retired = []
	for name in RETIRED:
		if not frappe.db.exists("CRM Lead Status", name) or frappe.db.exists("CRM Lead", {"status": name}):
			continue
		row = frappe.db.get_value(
			"CRM Lead Status", name, ["name", "type", "color", "position"], as_dict=True
		)
		if row.name not in known:
			kept.append(dict(row))
		frappe.delete_doc("CRM Lead Status", name, ignore_permissions=True, force=True)
		retired.append(name)
	frappe.db.set_single_value("TBO Smart Settings", RETIRED_FIELD, json.dumps(kept))
	return retired


def revert() -> int:
	"""Undo the migration by hand (`bench --site … execute
	tbo_smart.lead_pipeline.status_migration.revert`): recreate the retired statuses and put
	every mapped lead back on its old status. Lost reasons it added stay. Returns the leads moved."""
	for row in json.loads(frappe.db.get_single_value("TBO Smart Settings", RETIRED_FIELD) or "[]"):
		if not frappe.db.exists("CRM Lead Status", row["name"]):
			frappe.get_doc(
				{
					"doctype": "CRM Lead Status",
					"lead_status": row["name"],
					"type": row.get("type"),
					"color": row.get("color"),
					"position": row.get("position"),
				}
			).insert(ignore_permissions=True)
	leads = frappe.get_all(
		"CRM Lead",
		filters={PREVIOUS_FIELD: ("is", "set")},
		fields=["name", PREVIOUS_FIELD],
		limit_page_length=0,
	)
	for lead in leads:
		frappe.db.set_value(
			"CRM Lead",
			lead.name,
			{"status": lead[PREVIOUS_FIELD], PREVIOUS_FIELD: None},
			update_modified=False,
		)
	frappe.db.set_single_value("TBO Smart Settings", RETIRED_FIELD, None)
	return len(leads)
