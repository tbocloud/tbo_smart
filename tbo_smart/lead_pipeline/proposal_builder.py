"""Building a proposal from its template, its scoping answers, the module catalogue and the
section libraries (docs/proposals.md §3, §4).

Everything factual is data: the modules and their prices, the scope, the phases and weeks, the
team, the commercials, the terms. The AI writes only the narrative sections (AI_SECTIONS) in
one call, from a compact brief, and its numbers are checked against that brief. A section built
from the libraries is rebuilt from them on request at no AI cost.
"""

import json
import math

import frappe
from frappe import _
from frappe.query_builder.functions import Count, Sum
from frappe.utils import cint, flt, fmt_money, getdate, today

from tbo_smart import ai
from tbo_smart.lead_pipeline import proposal_media as media
from tbo_smart.lead_pipeline import rate_card, setting

MODULE = "TBO Proposal Module"
LIBRARY = "TBO Proposal Library Item"
IMAGE = "TBO Proposal Image"
AI_SECTIONS = ("Executive Summary", "Client Background", "Business Challenges", "Proposed Solution")
HOSTING = ("Frappe Cloud", "On-premise", "Client's cloud", "TBO hosting")
ARCHITECTURE = ("Simple", "Detailed")
FITS = ("Standard", "Configured", "Customised")
# what the AI may read: enough to be specific, little enough to stay cheap
NOTES_CHARS = 1500
MINUTES_CHARS = 1500
MAX_MODULES = 40
MAX_CUSTOMISATIONS = 30
SMALL_RISKS = 3
# a project this big (person-days of module and customisation effort) gets the full team
LARGE_EFFORT_DAYS = 40
# the section kinds a library image may suit, and where generation puts one
IMAGE_SECTIONS = ("Cover", "Company Overview", "Proposed Solution", "Modules & Features", "Project Team")
# the Scope of Work's subsections, in order; a module's scope line names one ("Testing | …")
SCOPE_GROUPS = (
	"Business Analysis",
	"System Design",
	"Development & Configuration",
	"Testing",
	"Deployment",
	"Training & Handover",
)
# a scoping answer that brings its catalogue module with it
IMPLIED_MODULES = {
	"data_migration": "Data Migration",
	"integrations": "Integrations",
	"mobile_app": "Flutter Mobile App",
}
MODULE_TEXT = ("description", "features", "scope", "deliverables", "assumptions", "out_of_scope", "workflow")
DEFAULT_DAILY_LIMIT = 50
# every proposal AI call, counted against the daily limit
AI_FEATURES = (
	"Proposal draft",
	"Proposal section",
	"Proposal requirements",
	"Proposal client profile",
	"Proposal edit",
	"Proposal module draft",
)


# ---------- the scoping answers ----------


def scoping_answers(raw, solution_type: str | None = None) -> dict:
	"""The scoping form's answers, cleaned: unknown keys dropped, each in its type."""
	raw = frappe.parse_json(raw) if isinstance(raw, str) else (raw or {})
	if not isinstance(raw, dict):
		frappe.throw(_("Send the scoping answers as a set of fields."))
	answers = {
		"modules": [str(name).strip() for name in (raw.get("modules") or []) if str(name).strip()][
			:MAX_MODULES
		],
		"customisation": bool(cint(raw.get("customisation"))),
		"customisations": customisation_rows(raw.get("customisations")),
		"mobile_app": bool(cint(raw.get("mobile_app"))) or "mobile" in (solution_type or "").lower(),
		"web_app": bool(cint(raw.get("web_app"))),
		"integrations": str(raw.get("integrations") or "").strip()[:500],
		"data_migration": bool(cint(raw.get("data_migration"))),
		"users": headcount(raw.get("users")),
		"companies": headcount(raw.get("companies")),
		"hosting": raw.get("hosting") if raw.get("hosting") in HOSTING else None,
		"amc": bool(cint(raw.get("amc"))),
		"timeline_weeks": max(cint(raw.get("timeline_weeks")), 0),
		"source_code": bool(cint(raw.get("source_code"))),
		"architecture": raw.get("architecture") if raw.get("architecture") in ARCHITECTURE else "Simple",
		# the templates picked at the start (titles in the Timeline, Payment Terms and AMC Plan
		# libraries; blank: the suggested timeline, the first payment terms, no plan marked)
		"timeline_template": str(raw.get("timeline_template") or "").strip()[:140] or None,
		"payment_terms": str(raw.get("payment_terms") or "").strip()[:140] or None,
		"amc_plan": str(raw.get("amc_plan") or "").strip()[:140] or None,
	}
	if not answers["customisation"]:
		answers["customisations"] = []
	return answers


UNLIMITED = "Unlimited"


def headcount(value) -> int | str:
	"""Users or companies: a number (0: not said) or "Unlimited"."""
	if str(value or "").strip().casefold() in ("unlimited", "-1"):
		return UNLIMITED
	return max(cint(value), 0)


def scale_text(answers: dict) -> str:
	"""What the set-up is sized for, in words: "Unlimited users, Up to 3 companies"."""
	users, companies = answers.get("users"), answers.get("companies")
	parts = []
	if users == UNLIMITED:
		parts.append(_("Unlimited users"))
	elif users:
		parts.append((_("{0} user") if users == 1 else _("{0} users")).format(users))
	if companies == UNLIMITED:
		parts.append(_("Unlimited companies"))
	elif companies:
		parts.append(
			(_("Up to {0} company") if companies == 1 else _("Up to {0} companies")).format(companies)
		)
	return ", ".join(parts)


def customisation_rows(value) -> list[dict]:
	"""Customisations as [{item, days}], from a list or from "item | days" lines."""
	if isinstance(value, str):
		value = [line for line in value.splitlines() if line.strip()]
	rows = []
	for row in value or []:
		if isinstance(row, str):
			name, _sep, days = row.partition("|")
			row = {"item": name, "days": days}
		if not isinstance(row, dict) or not str(row.get("item") or "").strip():
			continue
		rows.append(
			{
				"item": str(row["item"]).strip()[:140],
				"days": max(flt(row.get("days")), 0),
				# what the AI wrote about it (the per-proposal call), editable after
				"detail": str(row.get("detail") or "").strip()[:600] or None,
			}
		)
	return rows[:MAX_CUSTOMISATIONS]


def conditions(doc) -> set[str]:
	"""The library conditions this proposal meets (the `condition` of a library item)."""
	answers = scoping_answers(doc.scoping, doc.solution_type)
	names = {row.module_name for row in doc.modules}
	met = set()
	if answers["data_migration"] or IMPLIED_MODULES["data_migration"] in names:
		met.add("Data migration")
	if answers["mobile_app"] or IMPLIED_MODULES["mobile_app"] in names:
		met.add("Mobile app")
	if answers["web_app"]:
		met.add("Web app")
	if answers["customisations"]:
		met.add("Customisation")
	if answers["integrations"] or IMPLIED_MODULES["integrations"] in names:
		met.add("Integrations")
	if answers["source_code"]:
		met.add("Source code")
	if answers["hosting"]:
		met.add("Hosting")
	if answers["architecture"] == "Detailed":
		met.add("Detailed architecture")
	if effort_days(doc) > LARGE_EFFORT_DAYS:
		met.add("Large project")
	return met


def effort_days(doc) -> float:
	answers = scoping_answers(doc.scoping, doc.solution_type)
	return sum(flt(row.effort_days) for row in doc.modules) + sum(
		row["days"] for row in answers["customisations"]
	)


def lines(value: str | None) -> list[str]:
	return [line.strip() for line in (value or "").splitlines() if line.strip()]


def suits(row, solution_type: str | None) -> bool:
	"""A library row or module for every solution type (blank) or for this one."""
	types = lines(row.get("solution_types"))
	return not types or (solution_type or "") in types


# ---------- the libraries ----------


def library(kind: str, doc=None) -> list:
	"""The enabled items of one library, in order; with `doc`, only those that suit its solution
	type and whose condition it meets."""
	item = frappe.qb.DocType(LIBRARY)
	rows = (
		frappe.qb.from_(item)
		.select(
			item.name,
			item.title,
			item.body,
			item.group,
			item.condition,
			item.weeks,
			item.amount,
			item.solution_types,
			item.sort_order,
		)
		.where(item.library == kind)
		.where(item.enabled == 1)
		.orderby(item.sort_order)
		.orderby(item.creation)
		.run(as_dict=True)
	)
	if doc is None:
		return rows
	met = conditions(doc)
	return [
		row for row in rows if suits(row, doc.solution_type) and (not row.condition or row.condition in met)
	]


def catalogue(solution_type: str | None = None) -> list[dict]:
	"""The enabled modules (for a solution type: those that suit it), in order."""
	module = frappe.qb.DocType(MODULE)
	rows = (
		frappe.qb.from_(module)
		.select(
			module.name,
			module.module_name,
			module.solution_types,
			module.description,
			module.features,
			module.effort_days,
			module.default_price,
			module.item,
			module.fit,
			module.scope,
			module.deliverables,
			module.assumptions,
			module.out_of_scope,
			module.workflow,
			module.image_tag,
		)
		.where(module.enabled == 1)
		.orderby(module.sort_order)
		.orderby(module.module_name)
		.run(as_dict=True)
	)
	return [row for row in rows if solution_type is None or suits(row, solution_type)]


def chosen_modules(doc) -> list:
	"""The catalogue entries of the proposal's modules, in the proposal's order (a module typed
	by hand, with no catalogue entry, adds only its own line)."""
	found = {row.name: row for row in catalogue()}
	by_name = {row.module_name: row for row in found.values()}
	chosen = []
	for row in doc.modules:
		entry = found.get(row.module) or by_name.get(row.module_name)
		if entry:
			chosen.append(entry)
	return chosen


def module_lines(doc, field: str) -> list[str]:
	return [line for entry in chosen_modules(doc) for line in lines(entry.get(field))]


def unique(items) -> list:
	seen, kept = set(), []
	for item in items:
		key = item.casefold()
		if key not in seen:
			seen.add(key)
			kept.append(item)
	return kept


def table(header: tuple, rows: list) -> str:
	"""A table in a block's text: one "a | b" line a row, the first the header."""
	cells = [header, *rows]
	return "\n".join(" | ".join(clean_cell(cell) for cell in row) for row in cells)


def clean_cell(value) -> str:
	return str(value if value is not None else "").replace("|", "/").replace("\n", " ").strip() or "–"


def bullet_list(items) -> str:
	return "\n".join(f"- {item}" for item in items)


def titled(rows) -> str:
	"""Each library item as a subsection: its title, then its text."""
	return "\n\n".join(f"## {row.title}\n{(row.body or '').strip()}".strip() for row in rows)


# ---------- the sections built from data ----------


def build_section(doc, kind: str) -> str | None:
	"""A section's text from the libraries, the catalogue and the scoping answers, or None for
	one that isn't built from data (the cover, the acceptance, the AI's, a custom block)."""
	builder = BUILDERS.get(kind)
	return builder(doc) if builder else None


def company_overview(doc) -> str:
	return titled(library("Company Overview", doc))


def proposed_solution_data(doc) -> str:
	rows = [(row.title, row.body) for row in library("Solution Component", doc)]
	scale = scale_text(scoping_answers(doc.scoping, doc.solution_type))
	if scale:
		rows.append((_("Users and companies"), scale))
	return table((_("Component"), _("What it does")), rows) if rows else ""


def scope_of_work(doc) -> str:
	"""The library's general lines and each module's own, by subsection (SCOPE_GROUPS order),
	then the customisations with what each means."""
	groups: dict[str, list[str]] = {group: [] for group in SCOPE_GROUPS}
	for row in library("Scope of Work", doc):
		groups.setdefault(row.group or SCOPE_GROUPS[2], []).append(row.title)
	for line in module_lines(doc, "scope"):
		group, _sep, text = line.partition("|")
		if not _sep:
			group, text = SCOPE_GROUPS[2], line
		groups.setdefault(group.strip() or SCOPE_GROUPS[2], []).append(text.strip())
	parts = [f"## {_(group)}\n{bullet_list(unique(items))}" for group, items in groups.items() if items]
	customisations = scoping_answers(doc.scoping, doc.solution_type)["customisations"]
	if customisations:
		parts.append(
			f"## {_('Customisations')}\n"
			+ table(
				(_("Customisation"), _("What it means"), _("Effort (days)")),
				[(row["item"], row["detail"] or "", format_days(row["days"])) for row in customisations],
			)
		)
	return "\n\n".join(parts)


def modules_and_features(doc) -> str:
	if not doc.modules:
		return ""
	return table(
		(_("Module"), _("Fit"), _("What it covers"), _("Key features")),
		[
			(
				row.module_name,
				_(row.fit or "Configured"),
				row.description or "",
				"; ".join(lines(row.features)),
			)
			for row in doc.modules
		],
	)


def proposed_workflow(doc) -> str:
	"""The library's chain for the solution type and each module's ("Title: A → B → C")."""
	chains = [(row.title, (row.body or "").strip()) for row in library("Workflow", doc)]
	for line in module_lines(doc, "workflow"):
		title, _sep, chain = line.partition(":")
		chains.append((title.strip(), chain.strip()) if _sep else ("", line))
	seen, parts = set(), []
	for title, chain in chains:
		if chain and chain not in seen:
			seen.add(chain)
			parts.append(f"## {title}\n{chain}" if title else chain)
	return "\n\n".join(parts)


def technical_architecture(doc) -> str:
	rows = library("Architecture", doc)
	return table((_("Layer"), _("What we use")), [(row.title, row.body) for row in rows]) if rows else ""


def deliverables(doc) -> str:
	return bullet_list(
		unique([*module_lines(doc, "deliverables"), *(row.title for row in library("Deliverable", doc))])
	)


def implementation_plan(doc) -> str:
	rows = library("Phase", doc)
	return table((_("Phase"), _("Activities")), [(row.title, row.body) for row in rows]) if rows else ""


def timeline_templates() -> list:
	"""The timeline templates (45, 60, 90, 120 days…), shortest first."""
	return sorted(library("Timeline"), key=lambda row: flt(row.weeks))


def suggest_timeline(doc) -> str | None:
	"""The shortest timeline template that fits the effort: about 1.6 calendar days for each
	person-day of module and customisation effort (testing, reviews and waiting included), or the
	client's target when it is longer. A suggestion: presales picks."""
	options = timeline_templates()
	if not options:
		return None
	answers = scoping_answers(doc.scoping, doc.solution_type)
	needed = max(effort_days(doc) * 1.6, answers["timeline_weeks"] * 7)
	return next((row.title for row in options if flt(row.weeks) >= needed), options[-1].title)


def project_timeline(doc) -> str:
	"""The picked timeline template's phases ("phase | days"), drawn as a bar on the page."""
	answers = scoping_answers(doc.scoping, doc.solution_type)
	options = {row.title: row for row in timeline_templates()}
	chosen = options.get(answers["timeline_template"] or "") or options.get(suggest_timeline(doc) or "")
	if not chosen:
		return ""
	rows = milestone_rows(chosen.body)
	return f"{_('Planned over {0} days.').format(f'{flt(chosen.weeks):g}')}\n\n" + table(
		(_("Phase"), _("Days")), [(name, f"{days:g}") for name, days in rows]
	)


def project_team(doc) -> str:
	rows = library("Role", doc)
	return table((_("Role"), _("Responsibilities")), [(row.title, row.body) for row in rows]) if rows else ""


def investment(doc) -> str:
	notes = [row.title for row in library("Investment Note", doc)]
	scale = scale_text(scoping_answers(doc.scoping, doc.solution_type))
	if scale:
		notes.insert(0, _("The price covers {0}.").format(scale.lower()))
	return bullet_list(notes)


def payment_terms(doc) -> str:
	"""The picked payment terms template (the first by default); the page adds each amount."""
	presets = library("Payment Terms", doc)
	if not presets:
		return ""
	picked = scoping_answers(doc.scoping, doc.solution_type)["payment_terms"]
	chosen = next((row for row in presets if row.title == picked), presets[0])
	return table(
		(_("Milestone"), _("Share")),
		[(label, f"{flt(share):g}%") for label, share in milestone_rows(chosen.body)],
	)


def milestone_rows(text: str | None) -> list[tuple[str, float]]:
	rows = []
	for line in lines(text):
		label, _sep, share = line.partition("|")
		if label.strip() and _sep:
			rows.append((label.strip(), flt(share.replace("%", ""))))
	return rows


def warranty(doc) -> str:
	return "\n\n".join((row.body or "").strip() for row in library("Warranty", doc))


def amc(doc) -> str:
	plans = library("AMC Plan", doc)
	if not plans:
		return ""
	features: list[str] = []
	values = []
	for plan in plans:
		own = {}
		for line in lines(plan.body):
			label, _sep, value = line.partition("|")
			if label.strip() not in features:
				features.append(label.strip())
			own[label.strip()] = value.strip()
		values.append(own)
	currency = default_currency()
	rows = [(feature, *(own.get(feature, "–") for own in values)) for feature in features]
	rows.append((_("Yearly fee"), *(money(plan.amount, currency) for plan in plans)))
	note = _("Optional: billed yearly from go-live, separate from the project price above.")
	recommended = scoping_answers(doc.scoping, doc.solution_type)["amc_plan"]
	if recommended in {plan.title for plan in plans}:
		note += " " + _("We recommend {0}.").format(recommended)
	return f"{note}\n\n" + table((_("What you get"), *(plan.title for plan in plans)), rows)


def assumptions(doc) -> str:
	scale = scale_text(scoping_answers(doc.scoping, doc.solution_type))
	sized = [_("The set-up is sized for {0}.").format(scale.lower())] if scale else []
	return bullet_list(
		unique(
			[*(row.title for row in library("Assumption", doc)), *module_lines(doc, "assumptions"), *sized]
		)
	)


def out_of_scope(doc) -> str:
	items = unique([*(row.title for row in library("Out of Scope", doc)), *module_lines(doc, "out_of_scope")])
	day_rate = flt(setting("proposal_day_rate"))
	if day_rate:
		items.append(
			_("Work after sign-off is a change request at {0} a person-day.").format(
				money(day_rate, default_currency())
			)
		)
	else:
		items.append(_("Work after sign-off is a change request, estimated before it starts."))
	return bullet_list(items)


def risks(doc) -> str:
	rows = library("Risk", doc)
	if "Large project" not in conditions(doc):
		rows = rows[:SMALL_RISKS]
	return table((_("Risk"), _("How we handle it")), [(row.title, row.body) for row in rows]) if rows else ""


def terms(doc) -> str:
	return titled(library("Terms", doc))


BUILDERS = {
	"Company Overview": company_overview,
	"Proposed Solution": proposed_solution_data,
	"Scope of Work": scope_of_work,
	"Modules & Features": modules_and_features,
	"Proposed Workflow": proposed_workflow,
	"Technical Architecture": technical_architecture,
	"Deliverables": deliverables,
	"Implementation Plan": implementation_plan,
	"Project Timeline": project_timeline,
	"Project Team": project_team,
	"Investment": investment,
	"Payment Terms": payment_terms,
	"Warranty & Support": warranty,
	"AMC": amc,
	"Assumptions": assumptions,
	"Out of Scope": out_of_scope,
	"Risks & Mitigation": risks,
	"Terms & Conditions": terms,
}


def format_days(value: float) -> str:
	return f"{flt(value):g}"


def default_currency() -> str:
	company = frappe.defaults.get_global_default("company")
	return (company and frappe.get_cached_value("Company", company, "default_currency")) or "INR"


def money(value, currency: str) -> str:
	return fmt_money(flt(value), currency=currency)


def build_all(doc) -> None:
	"""Every data section's text, for a new proposal (the AI's are written after)."""
	for row in doc.sections:
		text = build_section(doc, row.section_type)
		if text is not None:
			row.body = text or None


# ---------- the modules ----------


def module_rows(doc, names: list[str], keep: list | None = None) -> list[dict]:
	"""Proposal module lines for these catalogue modules, priced for the lead's segment; a line
	in `keep` (the proposal's own, maybe set by hand) is kept as it is."""
	kept = {row.get("module_name"): row for row in keep or []}
	found = {row.module_name: row for row in catalogue()}
	price_list = rate_card.price_list_for(rate_card.lead_segment(doc.lead))
	prices = rate_card.item_prices(
		[price_list] if price_list else [], [row.item for row in found.values() if row.item]
	)
	rows = []
	for name in names:
		if name in kept:
			rows.append(kept[name])
			continue
		module = found.get(name)
		if not module:
			continue
		rows.append(
			{
				"module": module.name,
				"module_name": module.module_name,
				"fit": module.fit or "Configured",
				"features": module.features,
				"description": module.description,
				"effort_days": flt(module.effort_days),
				"item": module.item,
				"rate": prices.get((price_list, module.item), flt(module.default_price)),
				"rate_overridden": 0,
			}
		)
	return rows


def list_prices(doc, rows: list[dict]) -> list[dict]:
	"""Module lines whose price wasn't set by hand take the list price: the Item's rate on the
	lead segment's price list, else the catalogue's default price."""
	priced = {row["module_name"]: row["rate"] for row in module_rows(doc, [r["module_name"] for r in rows])}
	for row in rows:
		if not row["rate_overridden"] and row["module_name"] in priced:
			row["rate"] = priced[row["module_name"]]
	return rows


def module_line(row) -> dict:
	"""One module line from the hub: the price set by hand is kept as such."""
	if not isinstance(row, dict) or not str(row.get("module_name") or "").strip():
		frappe.throw(_("Each module needs a name."))
	module = row.get("module") if row.get("module") and frappe.db.exists(MODULE, row.get("module")) else None
	return {
		"module": module,
		"module_name": str(row["module_name"]).strip()[:140],
		"fit": row.get("fit") if row.get("fit") in FITS else "Configured",
		"features": str(row.get("features") or "").strip()[:2000] or None,
		"description": str(row.get("description") or "").strip()[:2000] or None,
		"effort_days": max(flt(row.get("effort_days")), 0),
		"item": row.get("item") or (frappe.db.get_value(MODULE, module, "item") if module else None),
		"rate": max(flt(row.get("rate")), 0),
		"rate_overridden": cint(row.get("rate_overridden")),
	}


def pricing_values(doc) -> dict:
	"""The quotation's values from the modules and the customisation days: one line each, at
	the module's price (the segment's, or the one set by hand). The discount band and the
	approvals apply as for any quotation."""
	service = setting("proposal_service_item")
	items = []
	for row in doc.modules:
		code = row.item or service
		if not code:
			frappe.throw(
				_(
					"{0} has no Item. Link it to one in the module catalogue, or set the proposal service item in TBO Smart Settings."
				).format(row.module_name)
			)
		items.append(
			{
				"item_code": code,
				"qty": 1,
				"price_list_rate": flt(row.rate),
				"discount_percentage": 0,
				"description": row.module_name if code == service else None,
			}
		)
	days = sum(row["days"] for row in scoping_answers(doc.scoping, doc.solution_type)["customisations"])
	if days:
		if not service:
			frappe.throw(_("Set the proposal service item in TBO Smart Settings to price the customisation."))
		items.append(
			{
				"item_code": service,
				"qty": days,
				"price_list_rate": flt(setting("proposal_day_rate")),
				"discount_percentage": 0,
				"description": _("Customisation ({0} person-days)").format(format_days(days)),
			}
		)
	if not items:
		frappe.throw(_("Add a module to price the proposal from."))
	values = {"items": items}
	if doc.quotation and frappe.db.exists("Quotation", doc.quotation):
		current = frappe.db.get_value(
			"Quotation",
			doc.quotation,
			["taxes_and_charges", "additional_discount_percentage", "valid_till"],
			as_dict=True,
		)
		values.update(
			{
				"taxes_and_charges": current.taxes_and_charges,
				"additional_discount_percentage": current.additional_discount_percentage,
				"valid_till": current.valid_till
				if current.valid_till and getdate(current.valid_till) >= getdate(today())
				else None,
			}
		)
	return values


# ---------- images from the library ----------


def library_images(solution_type: str | None = None, section: str | None = None) -> list[dict]:
	"""The enabled library images (for a solution type and a section: those tagged for them)."""
	image = frappe.qb.DocType(IMAGE)
	rows = (
		frappe.qb.from_(image)
		.select(
			image.name,
			image.title,
			image.image,
			image.alt_text,
			image.sections,
			image.solution_types,
			image.modules,
			image.enabled,
		)
		.orderby(image.title)
		.run(as_dict=True)
	)
	return [
		row
		for row in rows
		if row.enabled
		and (solution_type is None or suits(row, solution_type))
		and (section is None or section in lines(row.sections))
	]


def module_tags(doc) -> list[str]:
	"""The proposal's module names and their catalogue image tags."""
	return [row.module_name for row in doc.modules] + [
		entry.image_tag for entry in chosen_modules(doc) if entry.get("image_tag")
	]


def use_library_image(doc, name: str) -> dict:
	"""A library image on a draft proposal: its own File (the same file on disk), like an upload.
	Returns {file, url, alt}."""
	from tbo_smart.lead_pipeline.proposals import require_draft

	require_draft(doc)
	row = frappe.db.get_value(IMAGE, {"name": name, "enabled": 1}, ["image", "alt_text"], as_dict=True)
	if not row or not row.image:
		frappe.throw(_("Image {0} not found.").format(name), frappe.DoesNotExistError)
	original = frappe.db.get_value("File", {"file_url": row.image}, ["file_name", "is_private"], as_dict=True)
	if not original or not media.file_mime(frappe._dict(file_name=original.file_name, file_url=row.image)):
		frappe.throw(_("That library image can't be used: it isn't a PNG, JPEG or WebP file."))
	copy = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": original.file_name,
			"file_url": row.image,
			"attached_to_doctype": media.DOCTYPE,
			"attached_to_name": doc.name,
			"is_private": original.is_private,
		}
	)
	copy.insert(ignore_permissions=True)
	return {"file": copy.name, "url": media.image_url(doc, copy.name), "alt": row.alt_text}


def pick_images(doc) -> None:
	"""A fitting library image (by its tags, no AI) for the cover and the sections that suit one,
	where the section has none yet."""
	tags = {name.casefold() for name in module_tags(doc)}
	for row in doc.sections:
		if row.image or not cint(row.enabled) or row.section_type not in IMAGE_SECTIONS:
			continue
		found = library_images(doc.solution_type, row.section_type)
		if row.section_type == "Modules & Features":
			# an image of one of its modules first
			found = sorted(found, key=lambda image: not tags & {t.casefold() for t in lines(image.modules)})
		if not found:
			continue
		picked = use_library_image(doc, found[0].name)
		row.image, row.image_alt = picked["file"], picked["alt"]


# ---------- the AI's sections ----------


def daily_limit() -> int:
	return cint(setting("proposal_ai_daily_limit")) or DEFAULT_DAILY_LIMIT


def require_ai_budget() -> None:
	log = frappe.qb.DocType("TBO AI Usage Log")
	used = (
		frappe.qb.from_(log)
		.select(Count(log.name))
		.where(log.feature.isin(AI_FEATURES))
		.where(log.creation >= getdate(today()))
		.run()[0][0]
	)
	if cint(used) >= daily_limit():
		frappe.throw(_("Today's AI proposal runs are used up. Try again tomorrow, or write it by hand."))


def brief(doc, kinds: list[str]) -> dict:
	"""What the AI reads: compact on purpose (the newest notes and minutes, cut short; module
	names, not their catalogue text; no boilerplate)."""
	from tbo_smart.lead_pipeline.proposals import client_name, meeting_minutes

	lead = frappe.db.get_value(
		"Lead", doc.lead, ["lead_name", "company_name", "industry", "territory"], as_dict=True
	)
	answers = scoping_answers(doc.scoping, doc.solution_type)
	guidance = {row.section_type: row for row in doc.sections}
	from tbo_smart.lead_pipeline.proposal_profile import clean_profile, profile_text

	return {
		"client": client_name(doc),
		"client_profile": profile_text(clean_profile(doc.get("client_profile"))),
		"requirements": clip(doc.get("requirements") or "", REQUIREMENTS_CHARS),
		"contact": lead.lead_name,
		"industry": lead.industry,
		"location": lead.territory,
		"solution": doc.solution_type,
		"modules": [row.module_name for row in doc.modules],
		"scoping": {
			key: value
			for key, value in answers.items()
			if value not in (None, "", [], False, 0) and key not in ("modules", "architecture")
		},
		"notes": clip(lead_notes(doc.lead), NOTES_CHARS),
		"meetings": clip(
			"\n".join(
				"; ".join(f"{key}: {value}" for key, value in row.items() if value)
				for row in reversed(meeting_minutes(doc.lead))
			),
			MINUTES_CHARS,
		),
		"sections": [
			{"key": kind, "brief": (guidance.get(kind) and guidance[kind].get("guidance")) or BRIEFS[kind]}
			for kind in kinds
		],
	}


BRIEFS = {
	"Executive Summary": "One page: the client's business, their main problems, what we propose, the benefits.",
	"Client Background": "From the client profile: who they are, current software and processes, difficulties, limits of the current system, what to automate.",
	"Business Challenges": "Only the client's real challenges, from the profile, the requirements, the notes and meetings, as a list.",
	"Proposed Solution": "The business benefits of the proposed solution, as a list.",
}


def lead_notes(lead: str) -> str:
	"""The lead's notes and call notes, newest first (smart_leads.facts.LeadFacts)."""
	from tbo_smart.smart_leads.facts import LeadFacts

	return "\n".join(f"{line['on']}: {line['text']}" for line in LeadFacts([lead]).note_lines(lead))


def clip(text: str, limit: int) -> str:
	text = (text or "").strip()
	return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def estimate_tokens(doc, kinds: list[str] | None = None) -> int:
	"""About how many tokens writing these sections costs (prompt and brief at four characters a
	token, plus about 250 a section of answer)."""
	kinds = kinds or [row.section_type for row in doc.sections if row.section_type in AI_SECTIONS]
	if not kinds:
		return 0
	size = len(PROMPT) + len(json.dumps(brief(doc, kinds), default=str))
	return size // 4 + 250 * len(kinds)


def write_sections(
	doc, kinds: list[str] | None = None, feature: str = "Proposal draft", whole: bool | None = None
) -> list[str]:
	"""The proposal's one AI call: the narrative sections (all of them by default) and, for a whole
	draft (`whole`, by default when no `kinds` are named), each customisation bullet as a proper
	description. Text with a number that isn't in the brief is left out, with a warning. Returns
	the warnings."""
	whole = kinds is None if whole is None else whole
	kinds = (
		kinds
		if kinds is not None
		else [row.section_type for row in doc.sections if row.section_type in AI_SECTIONS]
	)
	answers = scoping_answers(doc.scoping, doc.solution_type)
	bullets = [row["item"] for row in answers["customisations"]] if whole else []
	if not kinds and not bullets:
		return []
	require_ai_budget()
	context = brief(doc, kinds)
	if bullets:
		context["customisations"] = bullets
	answer = ai.call_ai(PROMPT, json.dumps(context, default=str), feature, (media.DOCTYPE, doc.name))[
		"response"
	]
	answer = answer if isinstance(answer, dict) else {}
	known = ai.fact_numbers(context)
	dropped = []
	written = answer.get("sections") if isinstance(answer.get("sections"), dict) else {}
	for row in doc.sections:
		if row.section_type not in kinds:
			continue
		text = str(written.get(row.section_type) or "").strip()[:6000]
		if text and ai.invents_numbers(text, known):
			dropped.append(row.heading or _(row.section_type))
			continue
		row.body = with_data(doc, row.section_type, text) or None
	if bullets:
		dropped += expand_customisations(doc, answers, answer.get("customisations"), known)
	if not dropped:
		return []
	return [
		_("Some text had numbers that aren't in the lead's notes, so it was left out: {0}.").format(
			", ".join(dropped)
		)
	]


def expand_customisations(doc, answers: dict, written, known: set) -> list[str]:
	"""The AI's description of each customisation bullet, kept on the scoping answers; the Scope
	of Work shows them (rebuilt, unless someone already edited it). Returns what was left out."""
	written = written if isinstance(written, dict) else {}
	scope = next((row for row in doc.sections if row.section_type == "Scope of Work"), None)
	untouched = scope is not None and (scope.body or "") == (scope_of_work(doc) or "")
	dropped = []
	for row in answers["customisations"]:
		text = str(written.get(row["item"]) or "").strip()[:600]
		if text and ai.invents_numbers(text, known):
			dropped.append(row["item"])
		elif text:
			row["detail"] = text
	doc.scoping = frappe.as_json(answers)
	if untouched:
		scope.body = scope_of_work(doc) or None
	return dropped


def draft_module(name: str) -> dict:
	"""Library upkeep, once per module (editors): the AI drafts a catalogue module's proposal text
	from its name and what is already there. Returns the draft for the editor to review and save;
	nothing is saved here."""
	require_ai_budget()
	module = frappe.get_doc(MODULE, name)
	context = {
		"module": module.module_name,
		"solution_types": lines(module.solution_types),
		"current": {field: module.get(field) for field in MODULE_TEXT if module.get(field)},
		"scope_subsections": list(SCOPE_GROUPS),
	}
	answer = ai.call_ai(
		MODULE_PROMPT, json.dumps(context, default=str), "Proposal module draft", (MODULE, name)
	)["response"]
	answer = answer if isinstance(answer, dict) else {}
	draft = {}
	for field in MODULE_TEXT:
		value = answer.get(field)
		if isinstance(value, list):
			value = "\n".join(str(line).strip() for line in value if str(line).strip())
		if value:
			draft[field] = str(value).strip()[:4000]
	return draft


MODULE_PROMPT = """You write reusable text for one module of TBO's proposal library. TBO (Team Back Office)
implements ERPNext and Frappe and builds web and Flutter apps for companies in India. The text is
reused in many proposals, so it is general (no client names), concrete and in plain business
English, with Indian context where it fits (GST, PF, ESI, INR). Never give prices, efforts or dates.

Answer only JSON with these keys: "description" (two sentences), "features" (a list of 4-7 short
bullets), "scope" (a list of lines "<subsection> | <what we do>", subsections from
scope_subsections), "deliverables", "assumptions", "out_of_scope" (short lists), "workflow" (a list
of lines "Title: Step → Step → Step"). Improve on "current" when it is given."""


def with_data(doc, kind: str, text: str) -> str:
	"""The Proposed Solution is its component table (data) and the AI's benefits."""
	if kind != "Proposed Solution":
		return text
	data = proposed_solution_data(doc)
	benefits = f"## {_('Business benefits')}\n{text}" if text else ""
	return "\n\n".join(part for part in (data, benefits) if part)


PROMPT = """You write the narrative sections of a business proposal from TBO (Team Back Office), an
IT services company in Kerala, India (ERPNext, Frappe, web and Flutter mobile apps). You get a
compact brief: the client, the solution, the modules, the scoping answers, the newest lead notes
and meeting minutes, and the sections to write, each with a short brief.

Rules: clear, warm, specific English for a business reader. Use only what the brief says; never
invent a fact, number, price, date, percentage, name or challenge. Business Challenges lists only
challenges the notes or minutes show; if there are none, return an empty string for it. Keep the
Executive Summary within one page (about 250 words). Paragraphs are separated by a blank line;
a list item starts with "- ". No headings, no markdown other than that.

When the brief has "customisations" (short bullets a salesperson typed), describe each in one or
two sentences: what it does for the client. No effort, price or dates.

Answer only JSON: {"sections": {"<section key>": "<text>", ...}, "customisations": {"<bullet as
given>": "<description>", ...}} with one entry per section and per customisation."""


# ---------- the requirements box ----------

REQUIREMENTS_CHARS = 3000


def requirements_from_lead(lead: str) -> str:
	"""The lead's notes and its meetings' minutes as one text, newest first, to start the
	requirements box from (no AI)."""
	from tbo_smart.lead_pipeline.proposals import meeting_minutes

	minutes = [
		"; ".join(f"{key.replace('_', ' ')}: {value}" for key, value in row.items() if value)
		for row in reversed(meeting_minutes(lead))
	]
	return clip("\n".join(part for part in (lead_notes(lead), *minutes) if part), REQUIREMENTS_CHARS)


def extract_requirements(lead: str, text: str) -> dict:
	"""The requirements box read into the scoping form by one small AI call (structured output
	only): {template, scoping}. The answer is checked against the templates, the catalogue and
	the form's choices; presales confirms it before anything is generated."""
	from tbo_smart.lead_pipeline.proposals import templates

	text = clip(text, REQUIREMENTS_CHARS)
	if not text:
		frappe.throw(_("Write or paste the client's requirements first."))
	require_ai_budget()
	options = templates()
	names = [row.module_name for row in catalogue()]
	context = {
		"requirements": text,
		"templates": [
			{"name": row["name"], "solution": row["solution_type"], "about": row["description"]}
			for row in options
		],
		"modules": names,
		"hosting": list(HOSTING),
	}
	answer = ai.call_ai(
		EXTRACT_PROMPT, json.dumps(context, default=str), "Proposal requirements", ("Lead", lead)
	)["response"]
	answer = answer if isinstance(answer, dict) else {}
	template = answer.get("template") if answer.get("template") in {row["name"] for row in options} else None
	solution = next((row["solution_type"] for row in options if row["name"] == template), None)
	answer["modules"] = [name for name in answer.get("modules") or [] if name in names]
	bullets = [str(item).strip() for item in answer.get("customisations") or [] if str(item).strip()]
	answer["customisation"] = 1 if bullets else 0
	answer["customisations"] = [{"item": item, "days": 0} for item in bullets]
	return {"template": template, "scoping": scoping_answers(answer, solution)}


EXTRACT_PROMPT = """You read a salesperson's notes on what a client needs and fill a scoping form for an IT
services proposal (ERPNext, Frappe, web and Flutter apps). Use only what the text says; leave
anything it doesn't mention empty, false or 0.

Answer only JSON: {"template": "<one of templates[].name, the best fit>", "modules": ["<only
names from modules>"], "customisations": ["<each special request not covered by a module, as a
short line in the client's words>"], "mobile_app": true/false, "web_app": true/false,
"integrations": "<systems to connect, or empty>", "data_migration": true/false, "users": <number,
"Unlimited" for unlimited users, or 0>, "companies": <number of companies, "Unlimited", 2 when only
"multi-company" is said, or 0>, "hosting": "<one of hosting, or null>", "amc": true/false,
"timeline_weeks": <number or 0>, "source_code": true/false}"""


def usage(doc) -> dict:
	"""The AI runs logged against the proposal: count, tokens and the estimated cost."""
	log = frappe.qb.DocType("TBO AI Usage Log")
	row = (
		frappe.qb.from_(log)
		.select(
			Count(log.name).as_("runs"),
			Sum(log.input_tokens).as_("input_tokens"),
			Sum(log.output_tokens).as_("output_tokens"),
			Sum(log.estimated_cost_usd).as_("cost_usd"),
		)
		.where(log.reference_doctype == media.DOCTYPE)
		.where(log.reference_name == doc.name)
		.run(as_dict=True)[0]
	)
	return {
		"runs": cint(row.runs),
		"input_tokens": cint(row.input_tokens),
		"output_tokens": cint(row.output_tokens),
		"cost_usd": flt(row.cost_usd, 4),
	}


# ---------- the assistant: edits in words ----------

MAX_CHAT = 200
MODULE_LINE_FIELDS = (
	"module",
	"module_name",
	"fit",
	"features",
	"description",
	"effort_days",
	"rate",
	"rate_overridden",
	"item",
)
MAX_MESSAGE = 1000


def chat_log(raw) -> list[dict]:
	"""The transcript, cleaned: [{role: presales | assistant, text, at}], the newest MAX_CHAT."""
	raw = frappe.parse_json(raw) if isinstance(raw, str) else (raw or [])
	rows = []
	for row in raw if isinstance(raw, list) else []:
		if isinstance(row, dict) and row.get("role") in ("presales", "assistant") and row.get("text"):
			rows.append(
				{"role": row["role"], "text": str(row["text"]).strip()[:MAX_MESSAGE], "at": row.get("at")}
			)
	return rows[-MAX_CHAT:]


def edit_state(doc) -> dict:
	"""What the edit call reads: the proposal's structured state and the choices, never the
	transcript or the text."""
	from tbo_smart.lead_pipeline.proposals import MASTER_SECTIONS

	answers = scoping_answers(doc.scoping, doc.solution_type)
	return {
		"modules": [row.module_name for row in doc.modules],
		"catalogue": [row.module_name for row in catalogue(doc.solution_type)],
		"customisations": [row["item"] for row in answers["customisations"]],
		"timeline_template": answers["timeline_template"],
		"timelines": [row.title for row in timeline_templates()],
		"payment_terms": answers["payment_terms"],
		"payment_templates": [row.title for row in library("Payment Terms")],
		"amc": answers["amc"],
		"amc_plan": answers["amc_plan"],
		"amc_plans": [row.title for row in library("AMC Plan")],
		"users": answers["users"],
		"companies": answers["companies"],
		"hosting": answers["hosting"],
		"hostings": list(HOSTING),
		"mobile_app": answers["mobile_app"],
		"data_migration": answers["data_migration"],
		"integrations": answers["integrations"],
		"pricing_mode": doc.pricing_mode,
		"sections_off": [row.section_type for row in doc.sections if not cint(row.enabled)],
		"section_types": [kind for kind, _heading in MASTER_SECTIONS],
	}


def parse_edit(doc, message: str) -> dict:
	"""One small call: the message as structured changes (nothing is applied here)."""
	require_ai_budget()
	message = clip(message, MAX_MESSAGE)
	if not message:
		frappe.throw(_("Say what to change."))
	context = {"state": edit_state(doc), "message": message}
	answer = ai.call_ai(
		EDIT_PROMPT, json.dumps(context, default=str), "Proposal edit", (media.DOCTYPE, doc.name)
	)["response"]
	return answer if isinstance(answer, dict) else {}


def apply_edit(doc, ops: dict) -> list[str]:
	"""The changes the AI read, checked against the choices and applied to the scoping, modules
	and sections; the data sections are rebuilt (sections edited by hand are kept). Returns what
	changed, in words; `doc.flags.price_changed` says the quotation needs pricing again."""
	from tbo_smart.lead_pipeline.proposals import MASTER_SECTIONS

	state = edit_state(doc)
	answers = scoping_answers(doc.scoping, doc.solution_type)
	done = []
	names = [row.module_name for row in doc.modules]
	add = [m for m in ops.get("add_modules") or [] if m in state["catalogue"] and m not in names]
	remove = [m for m in ops.get("remove_modules") or [] if m in names]
	if add or remove:
		kept = [
			{field: row.get(field) for field in MODULE_LINE_FIELDS}
			for row in doc.modules
			if row.module_name not in remove
		]
		doc.set("modules", module_rows(doc, [row["module_name"] for row in kept] + add, keep=kept))
		answers["modules"] = [row.module_name for row in doc.modules]
		doc.flags.price_changed = True
		done += [_("Added {0}").format(m) for m in add] + [_("Removed {0}").format(m) for m in remove]
	for key, choices, label in (
		("timeline_template", state["timelines"], _("Timeline")),
		("payment_terms", state["payment_templates"], _("Payment terms")),
		("amc_plan", state["amc_plans"], _("AMC plan")),
		("hosting", state["hostings"], _("Hosting")),
	):
		if ops.get(key) in choices and ops[key] != answers.get(key):
			answers[key] = ops[key]
			done.append(_("{0}: {1}").format(label, ops[key]))
	for key, label in (
		("amc", _("AMC")),
		("mobile_app", _("Mobile app")),
		("data_migration", _("Data migration")),
	):
		if isinstance(ops.get(key), bool) and ops[key] != answers[key]:
			answers[key] = ops[key]
			done.append(_("{0}: {1}").format(label, _("yes") if ops[key] else _("no")))
	for key, label in (("users", _("Users")), ("companies", _("Companies"))):
		if key in ops and ops[key] not in (None, ""):
			value = headcount(ops[key])
			if value != answers[key]:
				answers[key] = value
				done.append(_("{0}: {1}").format(label, _(value) if value == UNLIMITED else value))
	if isinstance(ops.get("integrations"), str) and ops["integrations"].strip() != answers["integrations"]:
		answers["integrations"] = ops["integrations"].strip()[:500]
		done.append(_("Integrations: {0}").format(answers["integrations"] or _("none")))
	new = customisation_rows(ops.get("add_customisations"))
	gone = {str(item).strip().casefold() for item in ops.get("remove_customisations") or []}
	if new or gone:
		answers["customisations"] = [
			row for row in answers["customisations"] if row["item"].casefold() not in gone
		] + new
		answers["customisation"] = bool(answers["customisations"])
		doc.flags.price_changed = True
		done.append(_("Customisations updated"))
	if ops.get("pricing_mode") in ("Itemised", "Consolidated") and ops["pricing_mode"] != doc.pricing_mode:
		doc.pricing_mode = ops["pricing_mode"]
		done.append(_("Price shown: {0}").format(ops["pricing_mode"]))
	kinds = {kind for kind, _heading in MASTER_SECTIONS}
	off = {kind for kind in ops.get("sections_off") or [] if kind in kinds}
	on = {kind for kind in ops.get("sections_on") or [] if kind in kinds}
	if isinstance(ops.get("amc"), bool):
		(on if ops["amc"] else off).add("AMC")
	for row in doc.sections:
		if row.section_type in off and cint(row.enabled):
			row.enabled = 0
			done.append(_("{0} off").format(_(row.section_type)))
		elif row.section_type in on and not cint(row.enabled):
			row.enabled = 1
			done.append(_("{0} on").format(_(row.section_type)))
	doc.scoping = frappe.as_json(answers)
	if done:
		for row in doc.sections:
			if cint(row.edited):
				continue
			text = rebuilt_body(doc, row)
			if text is not None:
				row.body = text or None
	return done


def rebuilt_body(doc, row) -> str | None:
	"""A section's text rebuilt from the data; the Proposed Solution keeps the AI's benefits
	under the rebuilt component table (None: the section isn't built from data)."""
	text = build_section(doc, row.section_type)
	if text is None or row.section_type != "Proposed Solution":
		return text
	marker = f"## {_('Business benefits')}"
	benefits = (row.body or "").partition(marker)[2].strip()
	return with_data(doc, row.section_type, benefits) if benefits else text


EDIT_PROMPT = """You turn a salesperson's request about an IT services proposal into structured
changes. You get the proposal's current "state" (with the allowed choices) and their "message".
Only use values from the choices; leave out anything the message doesn't ask for.

Answer only JSON with any of: "add_modules", "remove_modules" (names from catalogue / modules),
"timeline_template" (from timelines), "payment_terms" (from payment_templates), "amc" (true /
false), "amc_plan" (from amc_plans), "hosting" (from hostings), "users", "companies" (a number or "Unlimited"), "mobile_app",
"data_migration" (true / false), "integrations" (text), "add_customisations" (["short line", …]),
"remove_customisations" (as listed), "pricing_mode" ("Itemised" or "Consolidated"),
"sections_off", "sections_on" (from section_types)."""
