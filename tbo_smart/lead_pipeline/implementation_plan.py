"""The Project Implementation Plan (docs/client-documents.md §7).

Made from the approved SRS, entirely from data at no AI cost: the phases and their dates from the
proposal's timeline template and the agreement's start date, a milestone at the end of each
phase, the deliverables of each phase from the modules' scope lines, a RACI from the roles
library, the client's responsibilities with their dates, and the risks. The hub's *Create
project* makes its project from `project_plan` (phases, tasks and milestones), once: after that
the project is where the work is planned, and the plan stays as the client approved it.
"""

import math

import frappe
from frappe import _
from frappe.utils import add_days, formatdate, getdate, now_datetime

from tbo_smart.lead_pipeline import proposal_builder as builder

# a module's scope subsection (proposal_builder.SCOPE_GROUPS) and the timeline phase its work is in
PHASE_OF_GROUP = {
	"Business Analysis": "Analysis",
	"System Design": "Design",
	"Development & Configuration": "Development & Configuration",
	"Testing": "Testing & UAT",
	"Deployment": "Deployment",
	"Training & Handover": "Training & Go-live",
}
RACI_COLUMNS = ("R", "A", "C", "I")
CLIENT = "Client"
UAT_SIGNED = "Signed"


def start_date(doc):
	"""The plan's start: its own, else the agreement's."""
	from tbo_smart.lead_pipeline.document_types import lineage

	if doc.get("start_date"):
		return getdate(doc.start_date)
	order = next((row.name for row in lineage(doc) if row.document_type == "Work Order"), None)
	found = frappe.db.get_value("TBO Proposal", order, "start_date") if order else None
	return getdate(found) if found else None


def phases(doc) -> list[dict]:
	"""[{phase, days, start, end}] from the timeline template, each phase starting the day after
	the one before ends (calendar days); without a start date, no dates."""
	chosen = builder.chosen_timeline(doc)
	rows = builder.milestone_rows(chosen.body) if chosen else []
	begin, found = start_date(doc), []
	for name, days in rows:
		end = add_days(begin, max(math.ceil(days) - 1, 0)) if begin else None
		found.append({"phase": name, "days": days, "start": begin, "end": end})
		begin = add_days(end, 1) if end else None
	return found


def date(value) -> str:
	return formatdate(value, "d MMM yyyy") if value else _("To be agreed")


def milestone(phase: str) -> str:
	return _("{0} complete").format(phase)


def prepare(doc) -> None:
	"""A new plan starts on the agreement's start date."""
	doc.start_date = start_date(doc)


def plan_overview(doc) -> str:
	found = phases(doc)
	if not found:
		return _("The phases follow the agreed timeline; pick a timeline on the proposal to date them.")
	begin, end = found[0]["start"], found[-1]["end"]
	total = sum(row["days"] for row in found)
	lines = [
		_(
			"The project starts on {0} and goes live by {1}: {2} days in {3} phases, each ending in a milestone."
		).format(date(begin), date(end), f"{total:g}", len(found))
		if begin
		else _("The project runs {0} days in {1} phases; the dates follow from the start date.").format(
			f"{total:g}", len(found)
		),
		_("Dates move only by agreement: a change request, or a delay in what the client provides."),
	]
	return "\n\n".join(lines)


def phases_and_milestones(doc) -> str:
	"""The dated phases (drawn as bars on the page, the table kept) with their milestones."""
	found = phases(doc)
	if not found:
		return ""
	return builder.table(
		(_("Phase"), _("Days"), _("From"), _("To"), _("Milestone")),
		[
			(row["phase"], f"{row['days']:g}", date(row["start"]), date(row["end"]), milestone(row["phase"]))
			for row in found
		],
	)


def deliverables_by_phase(doc) -> dict[str, list[str]]:
	"""{phase: [what is done in it]}: the library's phase activities and the modules' scope lines,
	each in the phase its subsection belongs to."""
	found: dict[str, list[str]] = {row["phase"]: [] for row in phases(doc)}
	for row in builder.library("Phase", doc):
		if row.title in found and row.body:
			found[row.title].append(row.body.strip())
	for entry in builder.chosen_modules(doc):
		for line in builder.lines(entry.get("scope")):
			group, sep, text = line.partition("|")
			phase = PHASE_OF_GROUP.get(group.strip()) if sep else None
			if phase in found:
				found[phase].append(f"{entry.module_name}: {text.strip()}")
	return {phase: builder.unique(items) for phase, items in found.items()}


def phase_deliverables(doc) -> str:
	parts = [
		f"## {phase}\n" + builder.bullet_list(items)
		for phase, items in deliverables_by_phase(doc).items()
		if items
	]
	return "\n\n".join(parts)


def raci_letter(role: str, phase: str) -> str:
	"""Who does what in a phase, by the role's kind: Responsible, Accountable, Consulted, Informed."""
	role, phase = role.casefold(), phase.casefold()
	if "project manager" in role:
		return "A"
	if role == CLIENT.casefold():
		if "test" in phase or "uat" in phase or "training" in phase:
			return "R"
		return "C" if "analysis" in phase or "design" in phase or "deploy" in phase else "I"
	if "analyst" in role or "consultant" in role:
		return "R" if any(word in phase for word in ("analysis", "design", "training")) else "C"
	if "developer" in role:
		return "R" if "development" in phase else ("C" if "design" in phase or "test" in phase else "I")
	if "qa" in role or "test" in role:
		return "R" if "test" in phase else ("C" if "development" in phase else "I")
	if "devops" in role:
		return "R" if "deploy" in phase else "I"
	return "I"


def raci_matrix(doc) -> str:
	roles = [row.title for row in builder.library("Role", doc)] + [_(CLIENT)]
	found = phases(doc)
	if not found or len(roles) < 2:
		return ""
	key = _("R: responsible, A: accountable, C: consulted, I: informed.")
	return f"{key}\n\n" + builder.table(
		(_("Phase"), *roles),
		[
			(
				row["phase"],
				*(raci_letter(role if role != _(CLIENT) else CLIENT, row["phase"]) for role in roles),
			)
			for row in found
		],
	)


def client_responsibilities(doc) -> str:
	"""The client's part, each by its date (a phase's start or end, from the library's group)."""
	dated = {row["phase"]: row for row in phases(doc)}
	rows = []
	for item in builder.library("Client Responsibility", doc):
		phase, _sep, when = (item.group or "").partition("|")
		found = dated.get(phase.strip())
		due = (found["end"] if when.strip().casefold() == "end" else found["start"]) if found else None
		rows.append((item.title, date(due)))
	if not rows:
		return ""
	return builder.table((_("What the client does"), _("By")), rows)


BUILDERS = {
	"Plan Overview": plan_overview,
	"Project Timeline": phases_and_milestones,
	"Phase Deliverables": phase_deliverables,
	"RACI Matrix": raci_matrix,
	"Client Responsibilities": client_responsibilities,
	"Risks & Mitigation": builder.risks,
}


# ---------- the hub's project ----------


def project_plan(doc) -> dict:
	"""What the hub makes its project from: the name, the client, the dates, and each phase with
	its tasks (the phase's deliverables) and its milestone, dated."""
	from tbo_smart.lead_pipeline.proposals import ACCEPTED, client_name

	if doc.status != ACCEPTED:
		frappe.throw(_("Make the project once the client has approved the plan."))
	found = phases(doc)
	if not found or not found[0]["start"]:
		frappe.throw(_("The plan has no dated phases. Set its start date and timeline first."))
	work = deliverables_by_phase(doc)
	return {
		"plan": doc.name,
		"lead": doc.lead,
		"client": client_name(doc),
		"project_name": f"{client_name(doc)} - {doc.solution_type or _('Implementation')}"[:140],
		"solution_type": doc.solution_type,
		"start": str(found[0]["start"]),
		"end": str(found[-1]["end"]),
		"hub_project": doc.get("hub_project"),
		"phases": [
			{
				"phase": row["phase"],
				"start": str(row["start"]),
				"end": str(row["end"]),
				"tasks": [
					*(
						{
							"subject": item[:140],
							"start": str(row["start"]),
							"end": str(row["end"]),
							"milestone": 0,
						}
						for item in work.get(row["phase"], [])
					),
					{
						"subject": milestone(row["phase"])[:140],
						"start": str(row["end"]),
						"end": str(row["end"]),
						"milestone": 1,
					},
				],
			}
			for row in found
		],
	}


def link_project(doc, project: str, url: str | None) -> None:
	"""The hub made the plan's project: kept on the plan (once) and said on the lead."""
	from tbo_smart.lead_pipeline.proposals import DOCTYPE

	project = (project or "").strip()[:140]
	if not project:
		frappe.throw(_("Name the hub project."))
	current = frappe.db.get_value(DOCTYPE, doc.name, "hub_project", for_update=True)
	if current and current != project:
		frappe.throw(_("This plan's project is already {0}.").format(current))
	values = {"hub_project": project, "hub_project_url": (url or "").strip()[:500] or None}
	frappe.db.set_value(DOCTYPE, doc.name, values, update_modified=False)
	doc.update(values)
	if not current:
		frappe.get_doc("Lead", doc.lead).add_comment(
			"Info", _("Project {0} made from {1}").format(project, doc.title)
		)


def record_uat(doc, status: str, signed_by: str | None = None) -> None:
	"""The hub's UAT sign-off for the plan's project: its state, and when signed, who signed and
	the warranty's start (the day after)."""
	from tbo_smart.lead_pipeline.proposals import DOCTYPE

	if not doc.get("hub_project"):
		frappe.throw(_("This plan has no project yet."))
	status = (status or "").strip()[:60]
	if not status:
		frappe.throw(_("Give the UAT sign-off's state."))
	values = {"uat_status": status}
	if status == UAT_SIGNED:
		now = now_datetime()
		values.update(
			{
				"uat_signed_on": now,
				"uat_signed_by": (signed_by or "").strip()[:140] or None,
				"warranty_starts_on": add_days(getdate(now), 1),
			}
		)
	frappe.db.set_value(DOCTYPE, doc.name, values, update_modified=False)
	doc.update(values)
	if status == UAT_SIGNED:
		frappe.get_doc("Lead", doc.lead).add_comment(
			"Info",
			_("UAT signed off by {0}; the warranty starts on {1}").format(
				values["uat_signed_by"] or _("the client"),
				formatdate(values["warranty_starts_on"], "d MMMM yyyy"),
			),
		)
