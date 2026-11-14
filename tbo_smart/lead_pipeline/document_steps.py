"""The steps of a lead's document chain that no client document carries (docs/client-documents.md
§9): a step skipped for this client, earlier steps not used when the chain starts in the middle,
and the project made straight from the lead with its UAT sign-off.

Each is a TBO Lead Document Step (one per lead and step), so the mobile app and the reports read
the same state the hub shows. A blank status is a step that is wanted again (brought back) or that
carries the lead's own project.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline import proposals as p
from tbo_smart.lead_pipeline.statuses import status_type

STEP = "TBO Lead Document Step"
SKIPPED, NOT_USED = "Skipped", "Not used"
SETTLED = (SKIPPED, NOT_USED)
PROJECT, UAT = "Project", "UAT"
STEP_TYPES = (*kinds.TYPES, PROJECT, UAT)
# with the client or decided: only a revision changes it, it can't be skipped
DECIDED = (*p.WITH_CLIENT, p.ACCEPTED)
MAX_REASON = 500
FIELDS = (
	"name",
	"document_type",
	"status",
	"reason",
	"set_by",
	"set_on",
	"hub_project",
	"hub_project_url",
	"uat_status",
	"uat_signed_on",
	"uat_signed_by",
	"warranty_starts_on",
)


# ---------- who may steer the chain ----------


def can_steer(lead: str) -> bool:
	"""Skip a step, bring one back, or start out of order: the lead's owner, a head of its
	department, or a manager (Sales Manager, System Manager). A BD only on their own leads."""
	from tbo_smart.sales.departments import can_manage, is_head

	if not frappe.has_permission("Lead", "write", lead):
		return False
	row = frappe.db.get_value("Lead", lead, ["lead_owner", "tbo_department"], as_dict=True) or {}
	return bool(
		can_manage() or row.get("lead_owner") == frappe.session.user or is_head(row.get("tbo_department"))
	)


def require_steer(lead: str) -> None:
	if not can_steer(lead):
		frappe.throw(
			_(
				"Only the lead's owner, a head of its department or a manager can skip steps or work out of order."
			),
			frappe.PermissionError,
		)


def closed_reason(lead: str) -> str | None:
	"""Why the lead's documents can't move: it is lost (Closed – Lost, Not Interested, Wrong
	Number, Small Business). A won lead carries on: accepting the proposal is what wins it."""
	stage = frappe.db.get_value("Lead", lead, "tbo_stage") if lead else None
	if status_type(stage) == "Lost":
		return _("This lead is {0}. Move it back to an open stage to work on its documents.").format(_(stage))
	return None


def require_open(lead: str) -> None:
	reason = closed_reason(lead)
	if reason:
		frappe.throw(reason)


def get_permission_query_conditions(user=None) -> str:
	"""List queries and reports: only the steps of the leads the user may read (the row itself
	follows proposals.has_permission: its lead decides)."""
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return ""
	leads = frappe.get_list("Lead", fields=["name"], user=user, limit_page_length=0, run=0)
	return f"`tab{STEP}`.`lead` in ({leads})"


# ---------- reading ----------


def rows(lead: str) -> dict[str, frappe._dict]:
	"""{step: its row} for the lead."""
	found = frappe.get_all(STEP, filters={"lead": lead}, fields=list(FIELDS))
	return {row.document_type: row for row in found}


def payload(row) -> dict | None:
	"""A skipped or not-used step's who, when and why, for the chain."""
	if not row or row.status not in SETTLED:
		return None
	return {
		"status": row.status,
		"reason": row.reason,
		"by": row.set_by,
		"by_name": frappe.utils.get_fullname(row.set_by) if row.set_by else None,
		"on": row.set_on,
	}


def project_row(lead: str, create: bool = False):
	"""The lead's own Project step (a project made straight from the lead), locked; made when
	`create` and missing."""
	name = frappe.db.get_value(STEP, {"lead": lead, "document_type": PROJECT}, "name", for_update=True)
	if name:
		return frappe.get_doc(STEP, name)
	if not create:
		return None
	return new_row(lead, PROJECT)


def new_row(lead: str, document_type: str):
	doc = frappe.get_doc({"doctype": STEP, "lead": lead, "document_type": document_type})
	doc.insert(ignore_permissions=True)  # written only here, after the checks - nosemgrep
	return doc


# ---------- skipping and bringing back ----------


def skip(lead: str, document_types: list, reason: str, status: str = SKIPPED) -> list[str]:
	"""Mark steps Skipped (this client doesn't need them) or Not used (the chain started after
	them), with the reason, as the session user. Refused for a step already with the client or
	decided (revise it instead), a done project or a signed UAT. Returns the steps marked."""
	from tbo_smart.lead_pipeline import client_documents as documents

	require_open(lead)
	require_steer(lead)
	if status not in SETTLED:
		frappe.throw(_("A step is skipped or not used."))
	reason = p.clean(reason, MAX_REASON)
	if not reason:
		frappe.throw(_("Say why in a few words."))
	wanted = list(dict.fromkeys(document_types or []))
	if not wanted:
		frappe.throw(_("Pick a step to skip."))
	frappe.db.get_value("Lead", lead, "name", for_update=True)
	steps = {step["document_type"]: step for step in documents.chain(lead)["steps"]}
	for name in wanted:
		step = steps.get(name)
		if not step:
			frappe.throw(_("{0} isn't a step of this lead's documents.").format(_(name)))
		why = skip_refused(step)
		if why:
			frappe.throw(why)
	now = now_datetime()
	for name in wanted:
		set_status(lead, name, status, reason, now)
	labels = ", ".join(steps[name]["label"] for name in wanted)
	p.tell_lead_owner(
		lead,
		(_("Skipped {0}: {1}") if status == SKIPPED else _("Marked not used: {0}. {1}")).format(
			labels, reason
		),
	)
	return wanted


def skip_refused(step: dict) -> str | None:
	"""Why this step can't be skipped, or None."""
	if step.get("skipped"):
		return _("{0} is already {1}.").format(step["label"], _(step["skipped"]["status"]).lower())
	if step["document_type"] == PROJECT and step.get("name"):
		return _("The project is made. It can't be skipped.")
	if step["document_type"] == UAT and step.get("done"):
		return _("The UAT is signed off. It can't be skipped.")
	if step["document_type"] in kinds.TYPES and step.get("document_status") in DECIDED:
		return _("{0} is {1}. It can't be skipped, only revised.").format(
			step["label"], (step.get("status_label") or "").lower()
		)
	return None


def set_status(lead: str, document_type: str, status: str, reason: str | None, on=None) -> None:
	name = frappe.db.get_value(STEP, {"lead": lead, "document_type": document_type}, "name")
	doc = frappe.get_doc(STEP, name) if name else new_row(lead, document_type)
	doc.update(
		{"status": status, "reason": reason, "set_by": frappe.session.user, "set_on": on or now_datetime()}
	)
	doc.save(ignore_permissions=True)  # written only here, after the checks - nosemgrep


def bring_back(lead: str, document_type: str) -> None:
	"""A skipped or not-used step wanted again: it shows as a step to do (its draft, if it had
	one, comes back with it)."""
	require_open(lead)
	require_steer(lead)
	row = frappe.db.get_value(
		STEP,
		{"lead": lead, "document_type": document_type},
		["name", "status"],
		as_dict=True,
		for_update=True,
	)
	if not row or row.status not in SETTLED:
		frappe.throw(_("{0} isn't skipped.").format(_(document_type)))
	set_status(lead, document_type, "", None)
	p.tell_lead_owner(lead, _("Brought back {0}").format(label(document_type)))


def clear(lead: str, document_type: str) -> None:
	"""The step is being done after all (its document made or sent, the project made): it is no
	longer skipped or not used. Nothing to do when it never was."""
	row = frappe.db.get_value(
		STEP, {"lead": lead, "document_type": document_type}, ["name", "status"], as_dict=True
	)
	if row and row.status in SETTLED:
		set_status(lead, document_type, "", None)


def label(document_type: str) -> str:
	if document_type == PROJECT:
		return _("Project")
	if document_type == UAT:
		return _("UAT sign-off")
	return kinds.label(document_type)
