"""The SRS: the functional specification, module by module (docs/client-documents.md §6).

Made from the approved BRD. Each module's section comes from the module library's SRS text
(numbered functional requirements with their acceptance criteria, roles, screens and fields,
validations, notifications) and its workflow, at no AI cost; the customisations get their own
section. With the AI on, at most one small call a module group expands the requirements from what
the BRD says about that module (its current and desired process, the business rules, the client's
answers), never from anything else. The FR numbers (FR-ACC-01 …) are what the UAT tests.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart import ai
from tbo_smart.lead_pipeline import proposal_builder as builder

MODULE_SECTION = "Module Specification"
CUSTOMISATIONS = "Customisations"
TO_CONFIRM = "To confirm with client"
MAX_EXTRA = 8
BRD_CHARS = 2500
FR_HEADER = ("ID", "Requirement", "Acceptance criteria")
FR_ID = re.compile(r"^FR-[A-Z]{2,4}-\d{2}$")


def code(name: str) -> str:
	"""A module's short code for its FR numbers: ACC for Accounts, HRP for HR & Payroll."""
	words = [word for word in re.split(r"[^A-Za-z]+", name or "") if word]
	if len(words) == 1:
		return words[0][:3].upper()
	return "".join(word[0] for word in words)[:4].upper() or "MOD"


def module_entry(name: str):
	"""A catalogue module's SRS text."""
	fields = [
		"module_name",
		"workflow",
		"features",
		"srs_requirements",
		"srs_roles",
		"srs_screens",
		"srs_validations",
		"srs_notifications",
	]
	return frappe.db.get_value(builder.MODULE, {"module_name": name}, fields, as_dict=True) or frappe._dict(
		module_name=name
	)


def pairs(text: str | None) -> list[tuple[str, str]]:
	""" "a | b" lines as (a, b); a line without " | " is (line, "")."""
	found = []
	for line in builder.lines(text):
		first, _sep, second = line.partition("|")
		found.append((first.strip(), second.strip()))
	return found


def requirements_table(prefix: str, rows: list[tuple[str, str]]) -> str:
	numbered = [
		(f"FR-{prefix}-{index:02d}", requirement, acceptance or f"{_(TO_CONFIRM)}")
		for index, (requirement, acceptance) in enumerate(rows, start=1)
	]
	return builder.table(tuple(_(cell) for cell in FR_HEADER), numbered)


def bullets(items) -> str:
	return builder.bullet_list(items) if items else f"- {_('None')}"


# ---------- the sections ----------


def introduction(doc) -> str:
	from tbo_smart.lead_pipeline.document_types import lineage
	from tbo_smart.lead_pipeline.proposals import client_name

	refs = {row.document_type: row.name for row in lineage(doc)}
	labels = (
		("BRD", _("Business requirements")),
		("Work Order", _("Project agreement")),
		("Proposal", _("Proposal")),
	)
	parts = [
		_(
			"This specification describes what the system does for {0}, module by module: the numbered functional requirements and how each is accepted, the roles, the screens, the rules and the notifications. It follows the approved business requirements; a change after its approval is a change request."
		).format(client_name(doc)),
		f"## {_('Scope')}\n" + bullets([row.module_name for row in doc.modules]),
	]
	references = [f"{label}: {refs[kind]}" for kind, label in labels if refs.get(kind)]
	if references:
		parts.append(f"## {_('References')}\n" + bullets(references))
	return "\n\n".join(parts)


def user_roles(doc) -> str:
	rows = []
	for module in doc.modules:
		for role, may in pairs(module_entry(module.module_name).srs_roles):
			rows.append((role, module.module_name, may))
	if not rows:
		return _("{0}: who uses each module.").format(_(TO_CONFIRM))
	return builder.table((_("Role"), _("Module"), _("What they may do")), rows)


def brd_text(doc, module: str | None = None) -> dict:
	"""What the approved BRD says that a module's FRs can come from (its own subsections of the
	current and desired process, the rules and the client's answers)."""
	from tbo_smart.lead_pipeline.document_types import lineage

	brd = next((row.name for row in lineage(doc) if row.document_type == "BRD"), None)
	if not brd:
		return {}
	sections = {
		row.section_type: row.body or ""
		for row in frappe.get_all(
			"TBO Proposal Section",
			filters={"parent": brd, "parenttype": "TBO Proposal"},
			fields=["section_type", "body"],
		)
	}

	def own(kind: str) -> str:
		"""The section's text (a module's own subsection when it has them)."""
		text = sections.get(kind, "")
		if module:
			chunk = re.search(rf"(?ms)^## {re.escape(module)}\s*$(.*?)(?=^## |\Z)", text)
			text = chunk.group(1) if chunk else ""
		return confirmed(text)

	answers = [
		f"{row.question} {row.answer}"
		for row in frappe.get_all(
			"TBO Document Question",
			filters={"parent": brd},
			fields=["question", "answer"],
			order_by="idx asc",
		)
		if row.answer and (not module or module.casefold() in row.question.casefold())
	]
	return {
		"current_process": own("Current Process"),
		"desired_process": own("Desired Process"),
		"business_rules": confirmed(sections.get("Business Rules", "")),
		"client_answers": answers,
	}


def confirmed(text: str) -> str:
	"""Only what the client confirmed: lines still "to confirm" are no evidence."""
	return "\n".join(line for line in (text or "").splitlines() if TO_CONFIRM not in line).strip()


def module_specification(doc, row) -> str:
	"""One module's specification (the row's heading names the module), or the customisations'."""
	name = (row.heading or "").strip()
	if name in (CUSTOMISATIONS, _(CUSTOMISATIONS)):
		return customisations_specification(doc)
	entry = module_entry(name)
	requirements = pairs(entry.srs_requirements) or [
		(_("{0} as in the business requirements").format(name), "")
	]
	reports = [feature for feature in builder.lines(entry.features) if "report" in feature.casefold()]
	parts = [
		f"## {_('Functional requirements')}\n" + requirements_table(code(name), requirements),
	]
	roles = pairs(entry.srs_roles)
	if roles:
		parts.append(
			f"## {_('User roles and permissions')}\n"
			+ builder.table((_("Role"), _("What they may do")), roles)
		)
	flows = [line.partition(":")[2].strip() or line for line in builder.lines(entry.workflow)]
	if flows:
		parts.append(f"## {_('Workflow and states')}\n" + "\n\n".join(flows))
	sections = (
		(_("Screens and fields"), builder.lines(entry.srs_screens)),
		(_("Validations"), builder.lines(entry.srs_validations)),
		(_("Reports"), reports),
		(_("Notifications"), builder.lines(entry.srs_notifications)),
	)
	for heading, items in sections:
		if items:
			parts.append(f"## {heading}\n" + builder.bullet_list(items))
	return "\n\n".join(parts)


def customisation_lines(doc) -> list[str]:
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	return [
		f"{row['item']}: {row['detail']}" if row.get("detail") else row["item"]
		for row in answers["customisations"]
	]


def customisations_specification(doc, acceptances: list[str] | None = None) -> str:
	"""A requirement a customisation, with the acceptance criteria the AI wrote (else to confirm)."""
	items = customisation_lines(doc)
	if not items:
		return ""
	given = list(acceptances or [])
	rows = [(item, given[index] if index < len(given) else "") for index, item in enumerate(items)]
	return f"## {_('Functional requirements')}\n" + requirements_table("CUS", rows)


def non_functional(doc) -> str:
	return builder.titled(builder.library("Non-functional Requirement", doc))


def prepare(doc) -> None:
	"""A new SRS gets a section a module (and one for the customisations), before the approval."""
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	names = [row.module_name for row in doc.modules]
	if answers["customisations"]:
		names.append(_(CUSTOMISATIONS))
	# plain rows (no idx), so the new order is the saved order
	rows = [
		{field: row.get(field) for field in ("section_type", "enabled", "heading", "body")}
		for row in doc.sections
	]
	at = next(
		(i for i, row in enumerate(rows) if row.get("section_type") == "Non-functional Requirements"),
		len(rows),
	)
	modules = [{"section_type": MODULE_SECTION, "heading": name[:140], "enabled": 1} for name in names]
	doc.set("sections", [*rows[:at], *modules, *rows[at:]])


BUILDERS = {
	"Introduction": introduction,
	"User Roles": user_roles,
	"Non-functional Requirements": non_functional,
}
ROW_BUILDERS = {MODULE_SECTION: module_specification}


# ---------- the AI: at most one call a module group ----------


def write_sections(
	doc, kinds: list[str] | None = None, feature: str = "SRS draft", whole: bool | None = None
) -> list[str]:
	"""Every unedited module section's requirements expanded from the BRD, one call a module that
	the BRD says something about (none for a module it is silent on)."""
	if kinds is not None and MODULE_SECTION not in kinds:
		return []
	warnings = []
	for row in doc.sections:
		if row.section_type == MODULE_SECTION and not cint(row.edited):
			warnings += write_module(doc, row, feature)
	return warnings


def write_module(doc, row, feature: str = "SRS module") -> list[str]:
	"""One module group's call: extra requirements (with acceptance criteria) the BRD shows for
	this module and its library doesn't already have. A requirement with a number that isn't in
	the BRD is left out. Returns the warnings."""
	name = (row.heading or "").strip()
	custom = name in (CUSTOMISATIONS, _(CUSTOMISATIONS))
	evidence = brd_text(doc, None if custom else name)
	base = module_specification(doc, row)
	if custom:
		evidence["customisations"] = customisation_lines(doc)
	if not any(
		evidence.get(key)
		for key in ("current_process", "desired_process", "client_answers", "customisations")
	):
		row.body = base or None
		return []
	builder.require_ai_budget()
	context = {
		"module": name,
		"library_requirements": [
			requirement for requirement, _acceptance in pairs(module_entry(name).srs_requirements)
		],
		**{
			key: builder.clip(value, BRD_CHARS) if isinstance(value, str) else value
			for key, value in evidence.items()
		},
	}
	answer = ai.call_ai(PROMPT, json.dumps(context, default=str), feature, ("TBO Proposal", doc.name))[
		"response"
	]
	answer = answer if isinstance(answer, dict) else {}
	known = ai.fact_numbers(context)
	extra, dropped = [], 0
	for item in (answer.get("requirements") if isinstance(answer.get("requirements"), list) else [])[
		:MAX_EXTRA
	]:
		if not isinstance(item, dict):
			continue
		requirement = " ".join(str(item.get("requirement") or "").split())[:300]
		acceptance = " ".join(str(item.get("acceptance") or "").split())[:300]
		# a customisation's answer is its acceptance criterion; a module's needs its requirement
		if not (acceptance if custom else requirement):
			continue
		if ai.invents_numbers(f"{requirement} {acceptance}", known):
			dropped += 1
			continue
		extra.append((requirement, acceptance))
	row.body = with_extra(doc, row, extra) or None
	if not dropped:
		return []
	return [
		_("{0}: {1} requirements had numbers the BRD doesn't, so they were left out.").format(name, dropped)
	]


def with_extra(doc, row, extra: list[tuple[str, str]]) -> str:
	"""The module's specification with the AI's requirements numbered after the library's."""
	name = (row.heading or "").strip()
	if name in (CUSTOMISATIONS, _(CUSTOMISATIONS)):
		return customisations_specification(doc, [acceptance for _requirement, acceptance in extra])
	entry = module_entry(name)
	text = module_specification(doc, row)
	if not extra:
		return text
	requirements = (
		pairs(entry.srs_requirements) or [(_("{0} as in the business requirements").format(name), "")]
	) + extra
	_first, sep, rest = text.partition("\n\n## ")
	table = f"## {_('Functional requirements')}\n" + requirements_table(code(name), requirements)
	return table + (f"\n\n## {rest}" if sep else "")


def functional_requirements(doc) -> list[dict]:
	"""Every FR of an SRS (from its module sections' tables), for the UAT: {id, module, requirement,
	acceptance}."""
	from tbo_smart.lead_pipeline.proposals import text_parts

	found = []
	for row in doc.sections:
		if row.section_type != MODULE_SECTION or not cint(row.enabled):
			continue
		for part in text_parts(row.body):
			if part["kind"] != "table" or len(part["header"]) < 3:
				continue
			for cells in part["rows"]:
				if len(cells) >= 3 and FR_ID.match(cells[0]):
					found.append(
						{
							"id": cells[0],
							"module": row.heading,
							"requirement": cells[1],
							"acceptance": cells[2],
						}
					)
	return found


PROMPT = """You write functional requirements for one module of an IT project's specification (SRS), for
TBO (Team Back Office), which implements ERPNext and Frappe and builds web and Flutter apps in India.
You get the module, the requirements its library already has, and only what the approved business
requirements (BRD) say about it: the current and desired process, the business rules and the
client's answers (or, for "Customisations", the customisations agreed).

Add only requirements the BRD text shows and the library doesn't already have. Never invent a
feature, rule, number or role. Each requirement is one testable sentence of what the system does;
its acceptance criterion is how the client checks it in testing ("A … shows …"). For the
customisations, give one acceptance criterion per customisation, in their order.

Answer only JSON: {"requirements": [{"requirement": "<sentence>", "acceptance": "<sentence>"}, ...]}
with at most 8 items (none when the BRD shows nothing new)."""
