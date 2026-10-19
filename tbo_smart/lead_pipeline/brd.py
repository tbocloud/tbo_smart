"""The BRD: the client's business requirements (docs/client-documents.md §5).

Made from the signed project agreement. Every section starts from data at no AI cost (the client
profile, the requirements, the modules' workflows, the customisations, the scoping answers);
with the AI on, one call writes the narrative sections from what the client actually told us (the
profile, the requirements box, the notes, the meetings' minutes, the proposal's narrative and the
client's own answers). It never invents a requirement: anything without evidence is written as
"To confirm with client", and becomes a question on the client's questionnaire page
(`/questions/<token>`). Answers come back to the BRD, and *Update from answers* rewrites only the
sections the new answers touch, in one small call.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from tbo_smart import ai
from tbo_smart.lead_pipeline import proposal_builder as builder

AI_SECTIONS = (
	"Business Objectives",
	"Stakeholders",
	"Current Process",
	"Pain Points",
	"Desired Process",
	"Business Rules",
	"Reports Needed",
	"Success Criteria",
)
TO_CONFIRM = "To confirm with client"
MAX_QUESTIONS = 60
MAX_ANSWER = 2000
PROPOSAL_NARRATIVE = ("Executive Summary", "Client Background", "Business Challenges")
NARRATIVE_CHARS = 2500


def confirm(what: str) -> str:
	return f"{_(TO_CONFIRM)}: {what}"


def answers_for(doc, kind: str) -> list[dict]:
	"""The client's answers for one section, oldest first."""
	return [
		{"question": row.question, "answer": row.answer}
		for row in doc.get("questions") or []
		if row.section_type == kind and (row.answer or "").strip()
	]


def with_answers(doc, kind: str, text: str) -> str:
	"""A data section with the client's own answers under it, word for word."""
	answered = answers_for(doc, kind)
	if not answered:
		return text
	lines = "\n".join(f"- {row['question']} {row['answer']}" for row in answered)
	return "\n\n".join(part for part in (text, f"## {_('From the client')}\n{lines}") if part)


def profile(doc) -> dict:
	from tbo_smart.lead_pipeline.proposal_profile import clean_profile

	return clean_profile(doc.get("client_profile"))


def split_items(text: str | None) -> list[str]:
	"""Comma-, semicolon- or line-separated items from a profile field."""
	parts = []
	for line in (text or "").replace(";", "\n").splitlines():
		parts += [item.strip(" -•") for item in line.split(",")]
	return [part for part in parts if part]


# ---------- the sections from data (the AI writes over the narrative ones) ----------


def business_objectives(doc) -> str:
	return with_answers(
		doc,
		"Business Objectives",
		builder.bullet_list([confirm(_("the business objectives and how they are measured"))]),
	)


def stakeholders(doc) -> str:
	lead = frappe.db.get_value("Lead", doc.lead, ["lead_name", "company_name"], as_dict=True)
	rows = [(lead.lead_name or confirm(_("name")), _("Client's project owner"), _("Decisions and sign-offs"))]
	# an owner for each module that runs a business process (it has a workflow), not a service
	for entry in builder.chosen_modules(doc):
		if builder.lines(entry.get("workflow")):
			rows.append(
				(confirm(_("name")), _("{0} owner").format(entry.module_name), _("Requirements and testing"))
			)
	if doc.get("prepared_by"):
		rows.append((frappe.utils.get_fullname(doc.prepared_by), _("TBO"), _("Business analysis")))
	return with_answers(doc, "Stakeholders", builder.table((_("Name"), _("Role"), _("Responsibility")), rows))


def current_process(doc) -> str:
	found = profile(doc)
	lines = []
	if found.get("current_software"):
		lines.append(_("Today the client uses {0}.").format(found["current_software"]))
	if found.get("key_processes"):
		lines.append(_("Key processes: {0}.").format(found["key_processes"]))
	parts = ["\n".join(lines)] if lines else []
	parts += [
		f"## {row.module_name}\n{confirm(_('how this is done today, by whom and with which tools'))}"
		for row in doc.modules
	]
	return with_answers(doc, "Current Process", "\n\n".join(parts))


def pain_points(doc) -> str:
	items = split_items(profile(doc).get("pain_points")) or [confirm(_("the main problems today"))]
	return with_answers(doc, "Pain Points", builder.bullet_list(items))


def desired_process(doc) -> str:
	"""Each module's workflow from the module library: how it will run in the new system."""
	chains = {}
	for entry in builder.chosen_modules(doc):
		chains[entry.module_name] = [line for line in builder.lines(entry.get("workflow"))]
	parts = []
	for row in doc.modules:
		flows = []
		for line in chains.get(row.module_name, []):
			title, sep, chain = line.partition(":")
			flows.append(f"{title.strip()}: {chain.strip()}" if sep else line)
		body = (
			"\n".join(f"- {flow}" for flow in flows) if flows else confirm(_("the steps in the new system"))
		)
		parts.append(f"## {row.module_name}\n{body}")
	return with_answers(doc, "Desired Process", "\n\n".join(parts))


def business_rules(doc) -> str:
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	items = [
		f"{row['item']}: {row['detail']}" if row.get("detail") else row["item"]
		for row in answers["customisations"]
	]
	items.append(confirm(_("approval limits, numbering, and the rules each department follows")))
	return with_answers(doc, "Business Rules", builder.bullet_list(items))


def reports_needed(doc) -> str:
	items = []
	for row in doc.modules:
		for feature in builder.lines(row.features):
			if "report" in feature.casefold() or "dashboard" in feature.casefold():
				items.append(f"{row.module_name}: {feature}")
	items.append(confirm(_("the reports each role needs, and how often")))
	return with_answers(doc, "Reports Needed", builder.bullet_list(builder.unique(items)))


def integrations(doc) -> str:
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	text = (
		_("Systems to connect: {0}.").format(answers["integrations"])
		if answers["integrations"]
		else _("No integrations are in scope.")
	)
	return with_answers(doc, "Integrations", text)


def data_migration(doc) -> str:
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	if not answers["data_migration"] and "Data Migration" not in {row.module_name for row in doc.modules}:
		return with_answers(doc, "Data Migration", _("No data migration is in scope."))
	entry = next((row for row in builder.chosen_modules(doc) if row.module_name == "Data Migration"), None)
	lines = [
		line.partition("|")[2].strip() or line for line in builder.lines(entry.get("scope") if entry else "")
	]
	lines.append(confirm(_("which data, from which system, and how many years of history")))
	return with_answers(doc, "Data Migration", builder.bullet_list(lines))


def success_criteria(doc) -> str:
	return with_answers(
		doc,
		"Success Criteria",
		builder.bullet_list([confirm(_("how the client will judge that the project succeeded"))]),
	)


def constraints(doc) -> str:
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	items = []
	timeline = builder.chosen_timeline(doc)
	if timeline:
		items.append(_("Timeline: {0} as agreed in the project agreement.").format(timeline.title))
	if answers["users"]:
		items.append(_("Users: {0}.").format(answers["users"]))
	if answers["hosting"]:
		items.append(_("Hosting: {0}.").format(answers["hosting"]))
	items.append(_("Budget: the price in the project agreement; changes go through change requests."))
	return with_answers(doc, "Constraints", builder.bullet_list(items))


def assumptions(doc) -> str:
	return with_answers(doc, "Assumptions", builder.assumptions(doc))


def open_questions(doc) -> str:
	"""The questionnaire: each question with the client's answer, or still to confirm."""
	rows = [
		(row.section_type or "–", row.question, (row.answer or "").strip() or _(TO_CONFIRM))
		for row in doc.get("questions") or []
	]
	if not rows:
		return _("No open questions.")
	return builder.table((_("Section"), _("Question"), _("Answer")), rows)


BUILDERS = {
	"Business Objectives": business_objectives,
	"Stakeholders": stakeholders,
	"Current Process": current_process,
	"Pain Points": pain_points,
	"Desired Process": desired_process,
	"Business Rules": business_rules,
	"Reports Needed": reports_needed,
	"Integrations": integrations,
	"Data Migration": data_migration,
	"Success Criteria": success_criteria,
	"Constraints": constraints,
	"Assumptions": assumptions,
	"Open Questions": open_questions,
}


# ---------- the questionnaire ----------


def library_questions(doc) -> list[dict]:
	"""The questions every BRD starts with, from its modules and scoping (no AI)."""
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	asked = [
		("Business Objectives", _("What are the three most important results you expect from this project?")),
		("Stakeholders", _("Who will own the project on your side, and who owns each department involved?")),
		(
			"Success Criteria",
			_("How will you judge, three months after go-live, that the project succeeded?"),
		),
	]
	# a module that runs a business process (it has a workflow), not a service like training
	for name in [
		entry.module_name for entry in builder.chosen_modules(doc) if builder.lines(entry.get("workflow"))
	]:
		asked.append(
			("Current Process", _("{0}: how is this done today, by whom, and with which tools?").format(name))
		)
		asked.append(("Reports Needed", _("{0}: which reports do you need, and how often?").format(name)))
	if answers["integrations"]:
		asked.append(
			(
				"Integrations",
				_("What should pass between the new system and {0}, and how often?").format(
					answers["integrations"]
				),
			)
		)
	if answers["data_migration"]:
		asked.append(
			("Data Migration", _("Which data should be moved, from which system, and how many years of it?"))
		)
	asked.append(("Business Rules", _("Which approvals, limits or numbering rules must the system follow?")))
	return [{"section_type": kind, "question": text, "asked_by": "Library"} for kind, text in asked]


def merge_questions(doc, rows: list[dict]) -> int:
	"""Add questions not asked yet (by their text), up to MAX_QUESTIONS. Returns how many."""
	seen = {(row.question or "").strip().casefold() for row in doc.get("questions") or []}
	added = 0
	for row in rows:
		text = str(row.get("question") or "").strip()[:500]
		kind = row.get("section_type") if row.get("section_type") in BUILDERS else None
		if not text or text.casefold() in seen or len(doc.questions) >= MAX_QUESTIONS:
			continue
		seen.add(text.casefold())
		doc.append(
			"questions",
			{"section_type": kind, "question": text, "asked_by": row.get("asked_by") or "Presales"},
		)
		added += 1
	return added


def save_questions(doc, rows: list) -> None:
	"""Presales' questions for a draft BRD: the list replaces the open ones; answered questions
	are kept as the client gave them."""
	from tbo_smart.lead_pipeline.proposals import require_draft

	require_draft(doc)
	if not isinstance(rows, list) or len(rows) > MAX_QUESTIONS:
		frappe.throw(_("Send at most {0} questions.").format(MAX_QUESTIONS))
	answered = [row for row in doc.questions if (row.answer or "").strip()]
	keep = {(row.question or "").strip().casefold() for row in answered}
	doc.set("questions", [row.as_dict() for row in answered])
	merge_questions(
		doc,
		[
			{
				**row,
				"asked_by": row.get("asked_by") if row.get("asked_by") in ("Library", "AI") else "Presales",
			}
			for row in rows
			if isinstance(row, dict) and str(row.get("question") or "").strip().casefold() not in keep
		],
	)
	doc.save()


def questionnaire_url(doc) -> str:
	return frappe.utils.get_url(f"/questions/{doc.questions_token}")


def questionnaire_open(doc) -> bool:
	"""The client can answer while the BRD is a draft or with them, and is the latest."""
	from tbo_smart.lead_pipeline.proposals import DRAFT, WITH_CLIENT

	return doc.status in (DRAFT, *WITH_CLIENT) and bool(cint(doc.is_latest))


def by_questions_token(token: str | None):
	from tbo_smart.lead_pipeline.client_documents import document_enabled
	from tbo_smart.lead_pipeline.proposals import DOCTYPE, TOKEN

	if not isinstance(token, str) or not TOKEN.match(token) or not document_enabled("BRD"):
		return None
	name = frappe.db.get_value(DOCTYPE, {"questions_token": token, "document_type": "BRD"}, "name")
	return frappe.get_doc(DOCTYPE, name) if name else None


def share_questionnaire(doc) -> str:
	"""The client's questionnaire link (it shows only the questions, never the draft)."""
	if not doc.questions:
		frappe.throw(_("Add a question first."))
	if not questionnaire_open(doc):
		frappe.throw(_("The questionnaire is closed: the BRD was answered or revised."))
	doc.db_set("questions_sent_on", now_datetime(), update_modified=False)
	return questionnaire_url(doc)


def send_questionnaire(doc, recipients: str | None, message: str | None) -> str:
	"""Email the client the questionnaire link, as a Communication on the lead."""
	from frappe.core.doctype.communication.email import make

	url = share_questionnaire(doc)
	lead = frappe.db.get_value("Lead", doc.lead, ["email_id", "lead_name", "company_name"], as_dict=True)
	to = (recipients or lead.email_id or "").strip()
	if not to:
		frappe.throw(_("The lead has no email. Give the address to send it to."))
	text = (message or "").strip() or "\n\n".join(
		[
			_("Dear {0},").format(lead.lead_name or lead.company_name),
			_(
				"To write your business requirements accurately, we have a few questions. Answer them on this page, at your own pace; you can come back to it."
			),
		]
	)
	paragraphs = "".join(f"<p>{frappe.utils.escape_html(part)}</p>" for part in text.split("\n\n"))
	make(
		doctype="Lead",
		name=doc.lead,
		recipients=to,
		subject=_("A few questions for your business requirements"),
		content=f'{paragraphs}<p><a href="{url}">{frappe.utils.escape_html(_("Answer the questions"))}</a></p><p>{frappe.utils.escape_html(url)}</p>',
		send_email=1,
	)
	return url


def questionnaire_context(doc) -> dict:
	"""What the questionnaire page shows: the client, the questions by section and their own
	answers. Nothing of the draft BRD itself."""
	from tbo_smart.lead_pipeline import document_types as kinds
	from tbo_smart.lead_pipeline.proposals import client_name, page_context

	headings = kinds.of(doc).headings
	groups: dict[str, list] = {}
	for row in doc.questions:
		groups.setdefault(row.section_type or "", []).append(
			{"name": row.name, "question": row.question, "answer": row.answer or ""}
		)
	base = page_context(doc, preview=True)
	return {
		"client": client_name(doc),
		"company": base["company"],
		"letter_head": base["letter_head"],
		"share": {
			**base["share"],
			"title": _("Questions for {0}").format(client_name(doc)),
			"description": _("Questions from {0}").format(base["share"]["brand"]),
		},
		"open": questionnaire_open(doc),
		"token": doc.questions_token,
		"groups": [
			{"heading": _(headings.get(kind, kind)) if kind else _("Other"), "questions": rows}
			for kind, rows in groups.items()
		],
		"answered": sum(1 for row in doc.questions if (row.answer or "").strip()),
		"total": len(doc.questions),
	}


def save_answers(doc, answers: dict) -> int:
	"""The client's answers ({question row: text}), kept word for word under the row lock; a
	changed answer goes back into the BRD on the next update. Returns how many changed, and tells
	the lead owner when any did."""
	from tbo_smart.lead_pipeline.proposals import DOCTYPE, as_system, client_name, tell_owner

	frappe.db.get_value(DOCTYPE, doc.name, "name", for_update=True)
	# read again under the lock: an answer saved, or the BRD sent, meanwhile is seen
	doc.reload()
	if not questionnaire_open(doc):
		frappe.throw(_("This questionnaire is closed. Thank you."))
	rows = {row.name: row for row in doc.questions}
	if set(answers) - set(rows):
		frappe.throw(_("That question isn't on this page."), frappe.DoesNotExistError)
	now, changed = now_datetime(), 0
	for name, text in answers.items():
		text = str(text or "").strip()[:MAX_ANSWER]
		if text == (rows[name].answer or ""):
			continue
		frappe.db.set_value(
			"TBO Document Question",
			name,
			{"answer": text or None, "answered_on": now if text else None, "applied": 0},
			update_modified=False,
		)
		changed += 1
	if changed:
		frappe.db.set_value(DOCTYPE, doc.name, "questions_answered_on", now, update_modified=False)
		with as_system():
			tell_owner(
				doc, _("{0} answered {1} questions for {2}").format(client_name(doc), changed, doc.title)
			)
	return changed


def unapplied(doc) -> list:
	return [row for row in doc.questions if (row.answer or "").strip() and not cint(row.applied)]


# ---------- the AI ----------


def brief(doc, kinds: list[str]) -> dict:
	"""Only what the client told us: compact, and the client's own words."""
	from tbo_smart.lead_pipeline.document_types import lineage
	from tbo_smart.lead_pipeline.proposal_profile import profile_text
	from tbo_smart.lead_pipeline.proposals import client_name, meeting_minutes

	proposal = next((row.name for row in lineage(doc) if row.document_type == "Proposal"), None)
	narrative = ""
	if proposal:
		narrative = "\n\n".join(
			f"{row.section_type}: {row.body}"
			for row in frappe.get_all(
				"TBO Proposal Section",
				filters={
					"parent": proposal,
					"parenttype": "TBO Proposal",
					"section_type": ("in", PROPOSAL_NARRATIVE),
				},
				fields=["section_type", "body"],
				order_by="idx asc",
			)
			if row.body
		)
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	return {
		"client": client_name(doc),
		"client_profile": profile_text(profile(doc)),
		"requirements": builder.clip(doc.get("requirements") or "", builder.REQUIREMENTS_CHARS),
		"notes": builder.clip(builder.lead_notes(doc.lead), builder.NOTES_CHARS),
		"meetings": builder.clip(
			"\n".join(
				"; ".join(f"{key}: {value}" for key, value in row.items() if value)
				for row in reversed(meeting_minutes(doc.lead))
			),
			builder.MINUTES_CHARS,
		),
		"proposal": builder.clip(narrative, NARRATIVE_CHARS),
		"modules": [row.module_name for row in doc.modules],
		"customisations": [
			f"{row['item']}: {row['detail']}" if row.get("detail") else row["item"]
			for row in answers["customisations"]
		],
		"client_answers": [
			{"section": row.section_type, "question": row.question, "answer": row.answer}
			for row in doc.questions
			if (row.answer or "").strip()
		],
		"sections": list(kinds),
	}


def write_sections(
	doc, kinds: list[str] | None = None, feature: str = "BRD draft", whole: bool | None = None
) -> list[str]:
	"""The BRD's one AI call: the narrative sections asked for (all by default) and the gaps as
	questions for the client. Text with a number that isn't in the brief is left as the data had
	it, with a warning. Returns the warnings."""
	kinds = [kind for kind in (kinds if kinds is not None else AI_SECTIONS) if kind in AI_SECTIONS]
	if not kinds:
		return []
	builder.require_ai_budget()
	context = brief(doc, kinds)
	answer = ai.call_ai(PROMPT, json.dumps(context, default=str), feature, ("TBO Proposal", doc.name))[
		"response"
	]
	answer = answer if isinstance(answer, dict) else {}
	known = ai.fact_numbers(context)
	written = answer.get("sections") if isinstance(answer.get("sections"), dict) else {}
	dropped = []
	for row in doc.sections:
		if row.section_type not in kinds:
			continue
		text = str(written.get(row.section_type) or "").strip()[:6000]
		if not text:
			continue
		if ai.invents_numbers(text, known):
			dropped.append(row.heading or _(row.section_type))
			continue
		row.body = text
	questions = answer.get("questions") if isinstance(answer.get("questions"), list) else []
	merge_questions(
		doc,
		[
			{"section_type": row.get("section"), "question": row.get("question"), "asked_by": "AI"}
			for row in questions
			if isinstance(row, dict) and not ai.invents_numbers(str(row.get("question") or ""), known)
		],
	)
	refresh_open_questions(doc)
	if not dropped:
		return []
	return [
		_(
			"Some text had numbers that aren't in what the client told us, so it was left as it was: {0}."
		).format(", ".join(dropped))
	]


def refresh_open_questions(doc) -> None:
	for row in doc.sections:
		if row.section_type == "Open Questions" and not cint(row.edited):
			row.body = open_questions(doc)


def apply_answers(doc) -> dict:
	"""The new answers into the BRD: the sections they touch rebuilt from data (answers quoted
	under them) and, with the AI on, the narrative ones among them rewritten in one call; the
	rest untouched, hand edits kept. Returns {sections: [headings], warnings}."""
	from tbo_smart.lead_pipeline.proposals import ai_enabled, require_draft

	require_draft(doc)
	fresh = unapplied(doc)
	if not fresh:
		frappe.throw(_("No new answers to add."))
	kinds = {row.section_type for row in fresh if row.section_type}
	touched, warnings = [], []
	for row in doc.sections:
		if row.section_type in kinds and not cint(row.edited) and row.section_type in BUILDERS:
			row.body = BUILDERS[row.section_type](doc) or None
			touched.append(row.heading or _(row.section_type))
	ai_kinds = [
		row.section_type
		for row in doc.sections
		if row.section_type in kinds & set(AI_SECTIONS) and not cint(row.edited)
	]
	if ai_kinds and ai_enabled():
		try:
			warnings = write_sections(doc, ai_kinds, "BRD section")
		except ai.AIProviderError as e:
			warnings = [
				_(
					"The AI couldn't rewrite them ({0}); the answers are added as the client wrote them."
				).format(e.summary)
			]
	for row in fresh:
		row.applied = 1
	refresh_open_questions(doc)
	doc.save()
	return {"sections": touched, "warnings": warnings}


PROMPT = """You write a Business Requirements Document (BRD) for an IT project by TBO (Team Back Office),
which implements ERPNext and Frappe and builds web and Flutter apps for companies in India. You get
only what the client told us: their profile, the requirements, notes, meeting minutes, the proposal's
narrative, the modules and customisations agreed, and the client's own answers to our questions.

The one rule: never invent a requirement, a process step, a person, a number or a rule. Write only
what the brief shows. Wherever something a BRD needs is not in the brief, write "To confirm with
client: <what>" in its place, and add a question for the client about it.

Sections (write those in "sections"): Business Objectives (a list), Stakeholders (a table "Name |
Role | Responsibility", unknown names as "To confirm with client"), Current Process (As-Is, a "## "
subheading per module, then a short list of steps), Pain Points (a list), Desired Process (To-Be, a
"## " subheading per module, then the steps), Business Rules (a list), Reports Needed (a list per
module), Success Criteria (a list). Plain text: a blank line between parts, "- " for list items,
"## " for subheadings, " | " between table cells. Clear business English.

Answer only JSON: {"sections": {"<section>": "<text>", ...}, "questions": [{"section": "<section>",
"question": "<one short question for the client>"}, ...]} with at most 15 questions, none already
answered in client_answers."""
