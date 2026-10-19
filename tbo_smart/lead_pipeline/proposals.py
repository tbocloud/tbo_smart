"""Proposals to a lead (docs/proposals.md).

A proposal (TBO Proposal) is a lead's document in ordered blocks (cover, about us, challenge,
solution, scope, timeline, pricing, terms, custom), started from a template (TBO Proposal
Template) and optionally drafted by the AI from the lead, its notes and its meetings' minutes.
Its pricing is the lead's own ERPNext Quotation (lead_quotation.save_quotation), so the price
lives in one place and the segment band and discount approval apply. The client reads it at
`/proposal/<token>` (www/proposal.py) and accepts or declines there; acceptance moves the
lead along the pipeline's won path (quotations.set_status, transitions.advance_lead).

Nothing goes to the client until the salesperson presses Send, and Send is refused while the
pricing waits for approval. Everything is behind TBO Smart Settings → Proposals (off by
default; needs Quotations on the lead); the AI draft has its own switch, also off.
"""

import re
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import add_days, cint, escape_html, flt, get_url, getdate, now_datetime, today

from tbo_smart import ai
from tbo_smart.lead_pipeline import proposal_builder as builder
from tbo_smart.lead_pipeline import proposal_media as media
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.lead_pipeline import quotations_enabled, setting
from tbo_smart.lead_pipeline.statuses import CLOSED_WON, PROPOSAL_SENT
from tbo_smart.lead_pipeline.transitions import advance_lead
from tbo_smart.sales.activities import plain_text

DOCTYPE = "TBO Proposal"
TEMPLATE = "TBO Proposal Template"
DRAFT, SENT, VIEWED, ACCEPTED, DECLINED, EXPIRED = (
	"Draft",
	"Sent",
	"Viewed",
	"Accepted",
	"Declined",
	"Expired",
)
WITH_CLIENT = (SENT, VIEWED)
# The Universal IT Proposal (docs/proposals.md §2): 23 sections in three parts, each with its
# default heading. A template is a preset on top of it: which sections are on, the default modules
# and the scoping questions.
MASTER_SECTIONS = (
	("Cover", "Cover"),
	("Executive Summary", "Executive Summary"),
	("Company Overview", "Company Overview"),
	("Client Background", "Client Background & Business Understanding"),
	("Business Challenges", "Business Challenges"),
	("Proposed Solution", "Proposed Solution"),
	("Scope of Work", "Scope of Work"),
	("Modules & Features", "Functional Modules & Features"),
	("Proposed Workflow", "Proposed Workflow"),
	("Technical Architecture", "Technical Architecture"),
	("Deliverables", "Project Deliverables"),
	("Implementation Plan", "Implementation Plan"),
	("Project Timeline", "Project Timeline"),
	("Project Team", "Project Team & Responsibilities"),
	("Investment", "Investment"),
	("Payment Terms", "Payment Terms"),
	("Warranty & Support", "Warranty & Post-Implementation Support"),
	("AMC", "Annual Maintenance Contract (AMC)"),
	("Assumptions", "Assumptions & Dependencies"),
	("Out of Scope", "Out of Scope"),
	("Risks & Mitigation", "Risks & Mitigation"),
	("Terms & Conditions", "Terms & Conditions"),
	("Acceptance", "Acceptance & Approval"),
)
PART_STARTS = {
	"Executive Summary": "Part A — Business Proposal",
	"Scope of Work": "Part B — Technical & Implementation Proposal",
	"Investment": "Part C — Commercial Proposal",
}
SECTION_TYPES = (*(kind for kind, _heading in MASTER_SECTIONS), "Image", "Custom")


def parts_of() -> dict:
	"""{section type: its part}: each master section belongs to the part it falls in."""
	part, found = None, {}
	for kind, _heading in MASTER_SECTIONS:
		part = PART_STARTS.get(kind, part)
		found[kind] = part
	return found


PART_OF = parts_of()
DEFAULT_HEADINGS = dict(MASTER_SECTIONS)
# the v1.0 block types, and what each became (patches.v1.proposal_master_sections)
OLD_TYPES = {
	"About us": "Company Overview",
	"Challenge": "Business Challenges",
	"Solution": "Proposed Solution",
	"Scope": "Scope of Work",
	"Timeline": "Project Timeline",
	"Pricing": "Investment",
	"Terms": "Terms & Conditions",
}
PRICING = "Investment"
ACCEPTANCE = "Acceptance"
THEMES = ("Ivory",)
PRICING_MODES = ("Itemised", "Consolidated")
DEFAULT_VALIDITY = 30
MAX_SECTIONS = 30
MAX_HEADING = 140
MAX_BODY = 8000
MAX_REASON = 1000
TOKEN = re.compile(r"^[0-9a-f]{32}$")

TRACKING_FIELDS = (
	"public_token",
	"sent_on",
	"sent_to",
	"first_viewed_on",
	"last_viewed_on",
	"decided_on",
	"decided_by_name",
	"decided_by_email",
	"decline_reason",
)
# a new proposal's workflow fields, whatever a caller sent (Proposal.check_locked_fields)
NEW_PROPOSAL = {
	"status": DRAFT,
	"quotation": None,
	"proposal_family": None,
	"revision_no": 0,
	"is_latest": 1,
	"view_count": 0,
	**{field: None for field in TRACKING_FIELDS if field != "public_token"},
}
# set only by this module's workflow (Proposal.check_locked_fields)
LOCKED_FIELDS = (
	"status",
	"quotation",
	"proposal_family",
	"revision_no",
	"is_latest",
	"view_count",
	*TRACKING_FIELDS,
)


def proposals_enabled() -> bool:
	"""TBO Smart Settings → Proposals (needs Quotations on the lead)."""
	return quotations_enabled() and bool(cint(setting("enable_proposals")))


def ai_enabled() -> bool:
	"""TBO Smart Settings → AI proposal drafts, with a provider set up."""
	return proposals_enabled() and bool(cint(setting("enable_proposal_ai"))) and ai.ai_ready()


def require_proposals() -> None:
	if not proposals_enabled():
		frappe.throw(_("Proposals are not switched on."), frappe.ValidationError)


def validity_days() -> int:
	return cint(setting("proposal_validity_days")) or DEFAULT_VALIDITY


def clean(value, limit: int) -> str:
	return str(value or "").strip()[:limit]


# ---------- the documents ----------


class Proposal:
	"""TBO Proposal's controller (tbo_smart.tbo_smart.doctype.tbo_proposal)."""

	def validate(self):
		self.check_sections()
		self.check_payment_terms()
		self.check_images()
		self.check_letter_head()
		self.check_quotation()
		self.check_locked_fields()

	def before_insert(self):
		self.set_token()

	def after_insert(self):
		self.start_family()

	def check_sections(self):
		if len(self.sections) > MAX_SECTIONS:
			frappe.throw(_("A proposal has at most {0} blocks.").format(MAX_SECTIONS))
		for row in self.sections:
			row.heading = clean(row.heading, MAX_HEADING) or None
			row.body = clean(row.body, MAX_BODY) or None

	def check_payment_terms(self):
		"""The payment milestones ("milestone | 30%") add up to 100%."""
		for row in self.sections:
			if row.section_type != "Payment Terms" or not cint(row.enabled):
				continue
			found = first_table(text_parts(row.body))
			if not found:
				continue
			total = sum(flt(str(cells[1]).replace("%", "")) for cells in found["rows"] if len(cells) > 1)
			if round(total, 2) != 100:
				frappe.throw(_("The payment milestones add up to {0}%, not 100%.").format(f"{total:g}"))

	def check_images(self):
		media.check_images(self)

	def check_letter_head(self):
		media.check_letter_head(self)

	def check_quotation(self):
		"""The pricing is the lead's own quotation, never another lead's (the client's page shows it)."""
		if not self.quotation:
			return
		row = frappe.db.get_value(
			"Quotation",
			self.quotation,
			["quotation_to", "party_name", "tbo_lead", "opportunity"],
			as_dict=True,
		)
		if not row or q.lead_of(row) != self.lead:
			frappe.throw(_("The pricing must be a quotation of this lead."), frappe.PermissionError)

	def check_locked_fields(self):
		"""Status, link, revision and tracking fields change only through this module
		(`flags.proposal_workflow`), and a sent proposal's content only through a revision."""
		if self.flags.proposal_workflow:
			return
		if self.is_new():
			# a new proposal starts as a fresh draft whatever was sent (its link is set by before_insert)
			self.update(NEW_PROPOSAL)
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if any(self.get(field) != before.get(field) for field in LOCKED_FIELDS):
			frappe.throw(_("Those fields are set by the proposal's workflow."), frappe.PermissionError)
		if before.status != DRAFT:
			frappe.throw(
				_("A proposal that is {0} can't be changed. Create a revision instead.").format(
					_(before.status)
				)
			)

	def set_token(self):
		"""The client's link: unguessable, and new for every revision."""
		self.public_token = frappe.generate_hash(length=32)

	def start_family(self):
		"""A new proposal starts its own family, revision 0, the latest."""
		if not self.proposal_family:
			self.db_set({"proposal_family": self.name, "revision_no": 0, "is_latest": 1})


class ProposalTemplate:
	"""TBO Proposal Template's controller."""

	def validate(self):
		self.check_sections()
		self.check_packages()

	def check_sections(self):
		if not self.sections or len(self.sections) > MAX_SECTIONS:
			frappe.throw(_("A template has 1 to {0} blocks.").format(MAX_SECTIONS))

	def check_packages(self):
		seen = set()
		for row in self.packages:
			if row.package in seen:
				frappe.throw(_("{0} is in the template twice.").format(row.package))
			seen.add(row.package)


def has_permission(doc, ptype=None, user=None) -> bool:
	"""A proposal is open to whoever may read its lead (tbo_smart.sales.permissions); changing it
	needs write on the lead. Deleting stays with System Manager."""
	user = user or frappe.session.user
	if ptype == "delete":
		return "System Manager" in frappe.get_roles(user)
	lead_ptype = (
		"read" if ptype in ("read", "select", "print", "export", "report", "email", "share") else "write"
	)
	return bool(frappe.has_permission("Lead", lead_ptype, doc=doc.lead, user=user))


def get_permission_query_conditions(user=None) -> str:
	"""List queries: only proposals of the leads the user may read."""
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return ""
	leads = frappe.get_list("Lead", fields=["name"], user=user, limit_page_length=0, run=0)
	return f"`tab{DOCTYPE}`.`lead` in ({leads})"


# ---------- making one ----------


def template_payload(name: str) -> dict:
	return templates(include_disabled=True, names=[name])[0]


def templates(include_disabled: bool = False, names: list[str] | None = None) -> list[dict]:
	"""Templates with their blocks and packages, by name, in three queries however many."""
	filters = {} if include_disabled else {"enabled": 1}
	if names is not None:
		filters["name"] = ("in", names or [""])
	parents = frappe.get_all(
		TEMPLATE,
		filters=filters,
		fields=[
			"name",
			"template_name",
			"solution_type",
			"description",
			"enabled",
			"page_guide",
			"questions",
			"default_modules",
		],
		order_by="template_name asc",
	)
	payloads = {
		row.name: {
			**row,
			"enabled": cint(row.enabled),
			"questions": lines(row.questions),
			"default_modules": lines(row.default_modules),
			"sections": [],
			"packages": [],
		}
		for row in parents
	}
	children = {"parent": ("in", list(payloads) or [""]), "parenttype": TEMPLATE}
	for row in frappe.get_all(
		"TBO Proposal Template Section",
		filters={**children, "parentfield": "sections"},
		fields=["parent", "section_type", "enabled", "heading", "guidance", "body"],
		order_by="idx asc",
	):
		payloads[row.pop("parent")]["sections"].append({**row, "enabled": cint(row.enabled)})
	for row in frappe.get_all(
		"TBO Proposal Template Package",
		filters={**children, "parentfield": "packages"},
		fields=["parent", "package"],
		order_by="idx asc",
	):
		payloads[row.parent]["packages"].append(row.package)
	return list(payloads.values())


def save_template(values: dict) -> str:
	"""Create or update a template (preset editors only): name, solution type, description,
	enabled, length guide, scoping questions, default modules, sections (on or off) and default
	packages."""
	from tbo_smart.lead_pipeline.presets import require_preset_editor

	require_preset_editor()
	name = (values.get("name") or "").strip()
	doc = (
		frappe.get_doc(TEMPLATE, name)
		if name and frappe.db.exists(TEMPLATE, name)
		else frappe.new_doc(TEMPLATE)
	)
	template_name = clean(values.get("template_name") or name, MAX_HEADING)
	if not template_name:
		frappe.throw(_("Give the template a name."))
	if frappe.db.exists(TEMPLATE, {"template_name": template_name, "name": ("!=", doc.name or "")}):
		frappe.throw(
			_("A template called {0} already exists.").format(template_name), frappe.DuplicateEntryError
		)
	sections = values.get("sections")
	if not isinstance(sections, list):
		frappe.throw(_("A template needs its blocks."))
	doc.update(
		{
			"template_name": template_name,
			"solution_type": clean(values.get("solution_type"), MAX_HEADING) or None,
			"description": clean(values.get("description"), MAX_BODY) or None,
			"enabled": cint(values.get("enabled", 1)),
			"questions": "\n".join(key for key in (values.get("questions") or []) if key in QUESTIONS)
			or None,
			"default_modules": "\n".join(
				clean(name, MAX_HEADING) for name in (values.get("default_modules") or []) if name
			)
			or None,
		}
	)
	if values.get("page_guide"):
		doc.page_guide = values["page_guide"]
	doc.set("sections", [])
	for row in sections:
		doc.append("sections", section_row(row, with_guidance=True))
	doc.set("packages", [])
	for package in values.get("packages") or []:
		doc.append("packages", {"package": package})
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	return doc.name


def section_row(row, with_guidance: bool = False) -> dict:
	if not isinstance(row, dict) or row.get("section_type") not in SECTION_TYPES:
		frappe.throw(_("Each block is one of: {0}.").format(", ".join(SECTION_TYPES)))
	values = {
		"section_type": row["section_type"],
		"enabled": cint(row.get("enabled", 1)),
		"heading": clean(row.get("heading"), MAX_HEADING) or None,
		"body": clean(row.get("body"), MAX_BODY) or None,
	}
	if with_guidance:
		values["guidance"] = clean(row.get("guidance"), MAX_BODY) or None
	else:
		# a proposal's block may carry an image (proposal_media.check_images keeps it honest)
		values.update(
			{
				"image": row.get("image") or None,
				"image_alt": row.get("image_alt"),
				"image_caption": row.get("image_caption"),
				"image_size": row.get("image_size") or media.IMAGE_SIZES[0],
			}
		)
	return values


def create_proposal(
	lead: str,
	template: str | None = None,
	ai_draft: bool = False,
	scoping: dict | None = None,
	requirements: str | None = None,
	client_profile: dict | None = None,
	chat: list | None = None,
) -> tuple:
	"""A complete draft for the lead, as the session user: the template's sections (on or off),
	the scoping answers, its modules priced for the lead's segment, every data section built from
	the libraries, fitting library images, the pricing from the modules, and with `ai_draft` the
	narrative sections written by the AI in one call. Returns (proposal, warnings)."""
	from tbo_smart.lead_pipeline.proposal_profile import clean_profile, require_profile

	if template and not frappe.db.exists(TEMPLATE, {"name": template, "enabled": 1}):
		frappe.throw(_("Template {0} not found.").format(template), frappe.DoesNotExistError)
	if ai_draft and not ai_enabled():
		frappe.throw(_("AI proposal drafts are not switched on."))
	profile = clean_profile(client_profile)
	require_profile(profile)
	tpl = template_payload(template) if template else None
	solution = (tpl and (tpl["solution_type"] or tpl["template_name"])) or None
	answers = builder.scoping_answers(scoping, solution)
	if not answers["modules"] and tpl:
		answers["modules"] = tpl["default_modules"]
	for key, name in builder.IMPLIED_MODULES.items():
		if answers.get(key) and name not in answers["modules"]:
			answers["modules"].append(name)
	client = client_label(lead)
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"lead": lead,
			"template": template,
			"solution_type": solution,
			"prepared_by": frappe.session.user,
			"title": clean(_("{0} for {1}").format(solution or _("Proposal"), client), MAX_HEADING),
			"status": DRAFT,
			"theme": THEMES[0],
			"pricing_mode": PRICING_MODES[0],
			"valid_till": add_days(today(), validity_days()),
			"scoping": frappe.as_json(answers),
			"requirements": clean(requirements, MAX_BODY) or None,
			"client_profile": frappe.as_json(profile),
			"chat": frappe.as_json(builder.chat_log(chat)),
			"sections": [section_row(row) for row in (tpl["sections"] if tpl else default_sections())],
		}
	)
	doc.set("modules", builder.module_rows(doc, answers["modules"]))
	pick_templates(doc, answers)
	builder.build_all(doc)
	doc.insert()
	# after the insert: the images are attached to it and the AI usage log names it
	builder.pick_images(doc)
	doc.save()
	warnings = price_new_proposal(doc)
	if ai_draft:
		try:
			warnings += builder.write_sections(doc)
			doc.save()
		except ai.AIProviderError as e:
			# the proposal is still made, with the data sections, and the person told why
			warnings.append(
				_("The AI couldn't write the narrative ({0}). Write it, or try Regenerate.").format(e.summary)
			)
	return doc, warnings


def pick_templates(doc, answers: dict) -> None:
	"""The timeline (suggested from the effort) and the payment terms (the first) when presales
	didn't pick them, kept on the scoping answers so the builder shows the pick."""
	if not answers.get("timeline_template"):
		answers["timeline_template"] = builder.suggest_timeline(doc)
	if not answers.get("payment_terms"):
		presets = builder.library("Payment Terms", doc)
		answers["payment_terms"] = presets[0].title if presets else None
	doc.scoping = frappe.as_json(answers)


def client_label(lead: str) -> str:
	label = frappe.db.get_value("Lead", lead, ["company_name", "lead_name"], as_dict=True)
	return label.company_name or label.lead_name


def price_new_proposal(doc) -> list[str]:
	"""The modules priced into the lead's quotation, when there are any; what stops it is a
	warning, not an error (the proposal is made either way)."""
	if not doc.modules:
		return []
	try:
		save_pricing(doc, builder.pricing_values(doc))
	except frappe.ValidationError as e:
		frappe.clear_last_message()
		return [_("Not priced yet: {0}").format(str(e))]
	return []


def default_sections() -> list[dict]:
	"""Without a template: the master structure, every section on."""
	return [{"section_type": kind, "heading": heading} for kind, heading in MASTER_SECTIONS]


QUESTIONS = (
	"modules",
	"customisation",
	"mobile_app",
	"web_app",
	"integrations",
	"data_migration",
	"users",
	"hosting",
	"amc",
	"timeline_weeks",
	"source_code",
	"architecture",
)


def lines(value: str | None) -> list[str]:
	return [line.strip() for line in (value or "").splitlines() if line.strip()]


def meeting_minutes(lead: str) -> list[dict]:
	"""The submitted minutes of the lead's meetings, oldest first."""
	from tbo_smart.lead_pipeline.meetings import DOCTYPE as MEETING
	from tbo_smart.lead_pipeline.meetings import MOM_SUBMITTED

	rows = frappe.get_all(
		MEETING,
		filters={"lead": lead, "status": MOM_SUBMITTED},
		fields=[
			"stage_label",
			"scheduled_at",
			"discussion_points",
			"client_requirements",
			"decisions",
			"objections",
			"quotation_feedback",
		],
		order_by="scheduled_at asc",
	)
	return [
		{
			"meeting": row.stage_label,
			"on": str(getdate(row.scheduled_at)) if row.scheduled_at else None,
			**{key: row[key] for key in row if key not in ("stage_label", "scheduled_at") and row[key]},
		}
		for row in rows
	]


# ---------- editing ----------


def require_draft(doc) -> None:
	if doc.status != DRAFT:
		frappe.throw(
			_("A proposal that is {0} can't be changed. Create a revision instead.").format(_(doc.status))
		)


def save_content(doc, data: dict) -> None:
	"""A draft's title, letter head, price shown, validity, scoping answers, modules and
	sections. There is one theme, so a `theme` sent is ignored."""
	require_draft(doc)
	if "title" in data:
		doc.title = clean(data.get("title"), MAX_HEADING)
		if not doc.title:
			frappe.throw(_("Give the proposal a title."))
	if "pricing_mode" in data:
		if data.get("pricing_mode") not in PRICING_MODES:
			frappe.throw(_("The price shown is one of: {0}.").format(", ".join(PRICING_MODES)))
		doc.pricing_mode = data["pricing_mode"]
	if "client_profile" in data:
		from tbo_smart.lead_pipeline.proposal_profile import clean_profile

		doc.client_profile = frappe.as_json(clean_profile(data.get("client_profile")))
	if "requirements" in data:
		doc.requirements = clean(data.get("requirements"), MAX_BODY) or None
	if "scoping" in data:
		doc.scoping = frappe.as_json(builder.scoping_answers(data.get("scoping"), doc.solution_type))
	if "modules" in data:
		if not isinstance(data["modules"], list) or len(data["modules"]) > builder.MAX_MODULES:
			frappe.throw(_("modules must be a list of at most {0}.").format(builder.MAX_MODULES))
		doc.set("modules", builder.list_prices(doc, [builder.module_line(row) for row in data["modules"]]))
	if "letter_head" in data:
		doc.letter_head_override = data.get("letter_head") or None
	if "valid_till" in data:
		valid_till = data.get("valid_till") or None
		if valid_till and getdate(valid_till) < getdate(today()):
			frappe.throw(_("Valid till can't be in the past."))
		doc.valid_till = valid_till
	if "sections" in data:
		if not isinstance(data["sections"], list):
			frappe.throw(_("sections must be a list of blocks."))
		rows = [section_row(row) for row in data["sections"]]
		mark_edits(doc.sections, rows)
		doc.set("sections", rows)
	doc.save()


def mark_edits(before, rows: list[dict]) -> None:
	"""A section whose text someone changed is marked edited, so regenerating asks first. Rows
	are matched by type and occurrence (the third Custom block to the third), so moving blocks
	doesn't count as an edit."""
	saved: dict[tuple, object] = {}
	seen: dict[str, int] = {}
	for row in before:
		seen[row.section_type] = seen.get(row.section_type, 0) + 1
		saved[(row.section_type, seen[row.section_type])] = row
	seen = {}
	for row in rows:
		seen[row["section_type"]] = seen.get(row["section_type"], 0) + 1
		old = saved.get((row["section_type"], seen[row["section_type"]]))
		changed = old is None or (old.body or None) != (row["body"] or None)
		row["edited"] = 1 if changed else cint(old.edited)


def rebuild_section(doc, index: int, force: bool = False) -> dict:
	"""One section written again: an AI section by the AI (one small call), any other from the
	libraries and the scoping answers (no AI). A section edited by hand is left as it is unless
	`force`. Returns {warnings, confirm}: `confirm` asks before writing over an edit."""
	require_draft(doc)
	if not 0 <= cint(index) < len(doc.sections):
		frappe.throw(_("That section isn't in the proposal."))
	row = doc.sections[cint(index)]
	if cint(row.edited) and not force:
		return {
			"warnings": [],
			"confirm": _("{0} was edited by hand. Write over your edits?").format(
				row.heading or _(row.section_type)
			),
		}
	warnings = []
	if row.section_type in builder.AI_SECTIONS:
		if not ai_enabled():
			frappe.throw(_("AI proposal drafts are not switched on."))
		warnings = builder.write_sections(doc, [row.section_type], "Proposal section")
	else:
		text = builder.build_section(doc, row.section_type)
		if text is None:
			frappe.throw(
				_("{0} isn't built from the libraries; edit it by hand.").format(_(row.section_type))
			)
		row.body = text or None
	row.edited = 0
	doc.save()
	return {"warnings": warnings, "confirm": None}


def regenerate_all(doc, force: bool = False) -> dict:
	"""Every section again after the scoping changed: the data sections from the libraries (no AI)
	and, with the AI on, the narrative and the customisations in one call. Sections edited by hand
	are kept unless `force`. Returns {warnings, kept: [their headings]}."""
	require_draft(doc)
	kept = [row.heading or _(row.section_type) for row in doc.sections if cint(row.edited) and not force]
	for row in doc.sections:
		if cint(row.edited) and not force:
			continue
		text = builder.build_section(doc, row.section_type)
		if text is not None:
			row.body = text or None
			row.edited = 0
	warnings = []
	if ai_enabled():
		kinds = [
			row.section_type
			for row in doc.sections
			if row.section_type in builder.AI_SECTIONS and (force or not cint(row.edited))
		]
		try:
			warnings = builder.write_sections(doc, kinds, whole=True)
		except ai.AIProviderError as e:
			warnings = [_("The AI couldn't write the narrative ({0}).").format(e.summary)]
		for row in doc.sections:
			if row.section_type in kinds:
				row.edited = 0
	doc.save()
	return {"warnings": warnings, "kept": kept}


def edit_by_chat(doc, message: str) -> dict:
	"""The assistant: a request in words ("add Payroll", "make it 90 days", "remove AMC") read by
	one small call into structured changes, applied (never a rewrite of the proposal) and kept in
	the transcript. Returns {reply, changes, warnings}."""
	require_draft(doc)
	if not ai_enabled():
		frappe.throw(_("AI proposal drafts are not switched on. Edit the form instead."))
	changes = builder.apply_edit(doc, builder.parse_edit(doc, message))
	warnings = []
	if doc.flags.price_changed:
		warnings = price_new_proposal(doc)
	reply = (
		_("Done: {0}.").format("; ".join(changes))
		if changes
		else _("I couldn't match that to a change. Say it another way, or edit the form.")
	)
	log = builder.chat_log(doc.chat)
	now = str(now_datetime())
	log += [{"role": "presales", "text": message, "at": now}, {"role": "assistant", "text": reply, "at": now}]
	doc.chat = frappe.as_json(builder.chat_log(log))
	doc.save()
	return {"reply": reply, "changes": changes, "warnings": warnings}


def price_from_modules(doc) -> None:
	"""The quotation's lines from the modules and the customisation days (builder.pricing_values),
	through the quotation builder, so the band and the approvals apply."""
	save_pricing(doc, builder.pricing_values(doc))


def save_pricing(doc, values: dict) -> None:
	"""The pricing block: the lead's quotation, made or updated through the quotation builder
	(segment rates, band and approval apply). A quotation already sent is revised first, so the
	one the client had stays as it was."""
	from tbo_smart.api import lead_quotation

	require_draft(doc)
	quotation = doc.quotation if doc.quotation and frappe.db.exists("Quotation", doc.quotation) else None
	if quotation and frappe.db.get_value("Quotation", quotation, "docstatus") != 0:
		quotation = q.create_revision(frappe.get_doc("Quotation", quotation)).name
	saved = lead_quotation.save_quotation(doc.lead, values, quotation)
	if doc.quotation != saved["name"]:
		doc.quotation = saved["name"]
		doc.flags.proposal_workflow = True
		doc.save()


def create_revision(doc):
	"""A new draft copied from this proposal with the next revision number in its family; it
	becomes the latest and gets its own link. The earlier ones stay as they were."""
	family = doc.proposal_family or doc.name
	# the family's first proposal's row lock: two revisions at once take the next numbers in turn
	frappe.db.get_value(DOCTYPE, family, "name", for_update=True)
	members = frappe.get_all(DOCTYPE, filters={"proposal_family": family}, fields=["name", "revision_no"])
	revision = frappe.copy_doc(doc)
	revision.prepared_by = frappe.session.user
	# its images are copied onto it once it has a name (proposal_media.copy_images)
	images = [row.image for row in revision.sections]
	for row in revision.sections:
		row.image = None
	revision.update(
		{
			"proposal_family": family,
			"revision_no": max([cint(row.revision_no) for row in members] + [cint(doc.revision_no)]) + 1,
			"is_latest": 1,
			"status": DRAFT,
			"valid_till": add_days(today(), validity_days()),
			**{field: None for field in TRACKING_FIELDS},
			"view_count": 0,
		}
	)
	revision.flags.proposal_workflow = True
	revision.insert()
	if any(images):
		for row, image in zip(revision.sections, images, strict=True):
			row.image = image
		media.copy_images(doc, revision)
		revision.save()
	for row in members:
		if row.name != revision.name:
			frappe.db.set_value(DOCTYPE, row.name, "is_latest", 0, update_modified=False)
	return revision


def revisions(doc) -> list[dict]:
	return frappe.get_all(
		DOCTYPE,
		filters={"proposal_family": doc.proposal_family or doc.name},
		fields=["name", "revision_no", "status", "is_latest", "sent_on", "modified"],
		order_by="revision_no asc",
	)


# ---------- sending ----------


def public_url(doc, preview: bool = False) -> str:
	return get_url(f"/proposal/{doc.public_token}" + ("?preview=1" if preview else ""))


def send_blockers(doc, by_email: bool = True) -> list[str]:
	"""Why the proposal can't be sent now, in words for the salesperson; empty when it can."""
	from tbo_smart.quotation_control import QuotationControl
	from tbo_smart.tbo_smart.doctype.tbo_quotation_approval_settings.tbo_quotation_approval_settings import (
		format_limit,
	)

	reasons = []
	if not cint(doc.is_latest):
		reasons.append(_("Only the latest revision can be sent."))
	if doc.status not in (DRAFT, *WITH_CLIENT):
		reasons.append(_("A proposal that is {0} can't be sent.").format(_(doc.status)))
	if not doc.sections:
		reasons.append(_("Add at least one block."))
	if doc.valid_till and getdate(doc.valid_till) < getdate(today()):
		reasons.append(_("Its validity has passed. Set a new valid till date."))
	if by_email and not has_outgoing_email():
		reasons.append(_("The ERP site has no outgoing email account yet. Ask an admin to set one up."))
	if not doc.quotation:
		return reasons
	quotation = frappe.get_doc("Quotation", doc.quotation)
	if quotation.docstatus == 2 or quotation.get("tbo_quote_status") in q.CLOSED:
		reasons.append(
			_("Its quotation {0} is {1}. Price it again.").format(
				quotation.name, _(quotation.get("tbo_quote_status") or "Cancelled")
			)
		)
	elif price_expired(quotation):
		reasons.append(
			_("Its quotation {0} was valid till {1}. Price it again.").format(
				quotation.name, frappe.utils.formatdate(quotation.valid_till)
			)
		)
	elif quotation.docstatus == 0:
		for kind, percent, limit in QuotationControl(quotation).uncovered():
			waiting = q.draft_status(quotation) == q.PENDING
			reasons.append(
				(
					_("The pricing's {0} of {1}% is waiting for approval (your limit is {2}).")
					if waiting
					else _(
						"The pricing's {0} of {1}% is above your limit ({2}). Request approval, or bring it within the limit."
					)
				).format(_(kind).lower(), percent, format_limit(limit))
			)
	return reasons


def has_outgoing_email() -> bool:
	"""An email account the ERP site sends a lead's email from."""
	from frappe.email.doctype.email_account.email_account import EmailAccount

	return bool(EmailAccount.find_outgoing(match_by_doctype="Lead"))


def send(doc, recipients: str | None = None, message: str | None = None, attach_pdf: bool = False) -> None:
	"""Email the client the link (and with `attach_pdf` the PDF), recorded as a Communication on
	the lead. The quotation goes out with it (quotations.mark_sent: submitted, Sent to Client,
	the lead to Proposal Sent and a follow-up); without one the lead moves to Proposal Sent."""
	refuse_while_blocked(doc)
	lead = frappe.db.get_value("Lead", doc.lead, ["email_id", "lead_name", "company_name"], as_dict=True)
	to = (recipients or lead.email_id or "").strip()
	if not to:
		frappe.throw(_("The lead has no email. Give the address to send it to."))
	release(doc, "Email", to)
	email(doc, lead, to, message, attach_pdf)


def share(doc) -> str:
	"""The client's link without an email: the proposal goes out exactly as Send does (refused
	while anything blocks it, the quotation sent, the content fixed) and its link opens for the
	client. Until then a draft's link shows nothing (docs/proposals.md §6). Returns the link."""
	if doc.status not in WITH_CLIENT:
		refuse_while_blocked(doc, by_email=False)
		release(doc, "Manual", None)
	return public_url(doc)


def refuse_while_blocked(doc, by_email: bool = True) -> None:
	reasons = send_blockers(doc, by_email)
	if reasons:
		frappe.throw("<br>".join(escape_html(reason) for reason in reasons), title=_("Not sent"))


def release(doc, via: str, to: str | None) -> None:
	"""The proposal goes to the client: its quotation goes out with it (or the lead moves to
	Proposal Sent), it becomes Sent, and the uploads it doesn't show are deleted."""
	if doc.quotation:
		quotation = frappe.get_doc("Quotation", doc.quotation)
		if quotation.get("tbo_quote_status") not in q.WITH_CLIENT:
			q.mark_sent(quotation, via)
	else:
		advance_lead(doc.lead, PROPOSAL_SENT, _("proposal {0} sent").format(doc.name))
	if doc.status == DRAFT:
		doc.status = SENT
	doc.sent_on = now_datetime()
	doc.sent_to = to or doc.sent_to
	doc.valid_till = doc.valid_till or add_days(today(), validity_days())
	doc.flags.proposal_workflow = True
	doc.save()
	media.drop_unused_images(doc)


def email(doc, lead, to: str, message: str | None, attach_pdf: bool) -> None:
	from frappe.core.doctype.communication.email import make

	company = frappe.db.get_value("Company", frappe.defaults.get_global_default("company"), "company_name")
	greeting = _("Dear {0},").format(lead.lead_name or lead.company_name)
	text = (message or "").strip() or "\n\n".join(
		[
			greeting,
			_(
				"Thank you for your time. Our proposal is ready for you to read, and you can accept it on the same page."
			),
		]
	)
	paragraphs = "".join(
		f"<p>{escape_html(part).replace(chr(10), '<br>')}</p>" for part in text.split("\n\n")
	)
	link = public_url(doc)
	content = (
		f"{paragraphs}<p><a href=\"{link}\">{escape_html(_('Read the proposal: {0}').format(doc.title))}</a></p>"
		f"<p>{escape_html(link)}</p>"
	)
	attachments = [{"fname": f"{doc.name}.pdf", "fcontent": render_pdf(doc)}] if attach_pdf else None
	make(
		doctype="Lead",
		name=doc.lead,
		recipients=to,
		subject=_("{0}: our proposal").format(company or doc.title) if company else doc.title,
		content=content,
		send_email=1,
		attachments=attachments,
	)


# ---------- the client's page ----------


def by_token(token: str | None):
	"""The proposal behind a link, or None (a malformed or unknown token)."""
	if not isinstance(token, str) or not TOKEN.match(token):
		return None
	name = frappe.db.get_value(DOCTYPE, {"public_token": token}, "name")
	return frappe.get_doc(DOCTYPE, name) if name else None


def public_doc(token: str | None, preview: bool = False):
	"""The proposal a client may read: sent (or decided), or any revision in preview."""
	doc = by_token(token)
	if not doc or not proposals_enabled() or (doc.status == DRAFT and not preview):
		return None
	return doc


@contextmanager
def as_system():
	"""The client is Guest; their accept or decline moves the lead as a system action."""
	user = frappe.session.user
	frappe.set_user("Administrator")  # the token was checked; the pipeline moves - nosemgrep
	try:
		yield
	finally:
		frappe.set_user(user)  # back to the guest - nosemgrep


def record_view(doc) -> None:
	"""One more view; the first moves Sent to Viewed (and its quotation to Viewed /
	Acknowledged) and tells the lead's owner."""
	# under the row lock, so two views at once both count and neither undoes an answer
	locked = frappe.db.get_value(DOCTYPE, doc.name, ["status", "view_count"], as_dict=True, for_update=True)
	if locked.status not in WITH_CLIENT:
		return
	values = {"view_count": cint(locked.view_count) + 1, "last_viewed_on": now_datetime()}
	first = locked.status == SENT
	if first:
		values.update({"status": VIEWED, "first_viewed_on": values["last_viewed_on"]})
	frappe.db.set_value(DOCTYPE, doc.name, values, update_modified=False)
	doc.update(values)
	if not first:
		return
	with as_system():
		quotation = quotation_with_client(doc)
		if quotation and quotation.tbo_quote_status == q.SENT:
			q.set_status(quotation, q.VIEWED, _("opened proposal {0}").format(doc.name))
		tell_owner(doc, _("{0} opened the proposal {1}").format(client_name(doc), doc.title))


def decide(doc, accept: bool, name: str | None, email_address: str | None, reason: str | None = None) -> None:
	"""The client accepts or declines. Once decided it stays so: the same answer again changes
	nothing, the other one is refused. Accepting moves the quotation to Accepted (the lead to
	Closed – Won, which makes its deal) or, without a quotation, the lead itself."""
	target = ACCEPTED if accept else DECLINED
	# read under the row lock: a revision or another answer at the same moment is seen
	locked = frappe.db.get_value(
		DOCTYPE, doc.name, ["status", "is_latest", "valid_till", "quotation"], as_dict=True, for_update=True
	)
	doc.update(locked)
	status = locked.status
	if status == target:
		return
	if status in (ACCEPTED, DECLINED):
		frappe.throw(_("This proposal was already {0}.").format(_(status).lower()))
	if status not in WITH_CLIENT or not cint(locked.is_latest):
		frappe.throw(_("This proposal can't be answered any more. Ask us for the latest one."))
	if locked.valid_till and getdate(locked.valid_till) < getdate(today()):
		frappe.throw(_("This proposal has expired. Ask us for a new one."))
	if price_unavailable(doc):
		# its price has expired or was withdrawn: there is nothing left to accept or decline
		frappe.throw(_("The price in this proposal is no longer valid. Ask us for a new one."))
	name = clean(name, MAX_HEADING)
	email_address = clean(email_address, MAX_HEADING)
	if accept and not (name and email_address):
		frappe.throw(_("Type your name and email to accept."))
	if email_address:
		frappe.utils.validate_email_address(email_address, throw=True)
	values = {
		"status": target,
		"decided_on": now_datetime(),
		"decided_by_name": name or None,
		"decided_by_email": email_address or None,
		"decline_reason": None if accept else (clean(reason, MAX_REASON) or None),
	}
	frappe.db.set_value(DOCTYPE, doc.name, values)
	doc.update(values)
	with as_system():
		move_pipeline(doc, accept)
		who = name or email_address or client_name(doc)
		tell_owner(
			doc,
			(_("{0} accepted the proposal {1}") if accept else _("{0} declined the proposal {1}")).format(
				who, doc.title
			),
		)


def move_pipeline(doc, accept: bool) -> None:
	quotation = quotation_with_client(doc)
	note = _("on proposal {0}").format(doc.name)
	if accept:
		if quotation:
			q.set_status(quotation, q.ACCEPTED, note)
		else:
			advance_lead(doc.lead, CLOSED_WON, _("proposal {0} accepted").format(doc.name))
	elif quotation:
		q.set_status(quotation, q.REJECTED, doc.decline_reason or _("declined proposal {0}").format(doc.name))


def quotation_with_client(doc):
	"""The proposal's quotation while it is with the client and its price still valid (the
	nightly expiry may not have run yet)."""
	if not doc.quotation or not frappe.db.exists("Quotation", doc.quotation):
		return None
	quotation = frappe.get_doc("Quotation", doc.quotation)
	if quotation.get("tbo_quote_status") not in q.WITH_CLIENT or price_expired(quotation):
		return None
	return quotation


def price_unavailable(doc) -> bool:
	"""The proposal is priced, but its quotation is no longer with the client or valid."""
	return bool(doc.quotation) and not quotation_with_client(doc)


def price_expired(quotation) -> bool:
	return bool(quotation.valid_till and getdate(quotation.valid_till) < getdate(today()))


def client_name(doc) -> str:
	label = frappe.db.get_value("Lead", doc.lead, ["company_name", "lead_name"], as_dict=True)
	return label.company_name or label.lead_name


def tell_owner(doc, subject: str) -> None:
	"""A timeline comment on the lead and an in-app note (and push) to its owner."""
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	frappe.get_doc("Lead", doc.lead).add_comment("Info", subject)
	owner = frappe.db.get_value("Lead", doc.lead, "lead_owner")
	if owner:
		enqueue_create_notification(
			[owner],
			{
				"type": "Alert",
				"document_type": "Lead",
				"document_name": doc.lead,
				"subject": subject,
				"from_user": "Administrator",
			},
		)


def expire_proposals() -> int:
	"""Daily: proposals still with the client after their valid till date expire."""
	if not proposals_enabled():
		return 0
	names = frappe.get_all(
		DOCTYPE,
		filters={"status": ("in", WITH_CLIENT), "valid_till": ("<", today())},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value(DOCTYPE, name, "status", EXPIRED)
	return len(names)


# ---------- what the client sees ----------


def page_context(doc, preview: bool = False, pdf: bool = False) -> dict:
	"""Everything the page and the PDF show, and nothing else: the cover, the contents, the
	numbered sections in their parts with their images, tables and diagrams, the letter head, the
	quotation's lines and totals, and the acceptance block. No emails, notes, owners or internal
	names. In the PDF the images are data URIs."""
	company = (
		frappe.db.get_value(
			"Company",
			frappe.db.get_value("Quotation", doc.quotation, "company")
			if doc.quotation
			else frappe.defaults.get_global_default("company"),
			["company_name", "company_logo"],
			as_dict=True,
		)
		or frappe._dict()
	)
	expired = bool(doc.valid_till and getdate(doc.valid_till) < getdate(today()))
	pricing = pricing_table(doc.quotation) if doc.quotation else None
	price_gone = doc.status in WITH_CLIENT and price_unavailable(doc)
	blocks = numbered_blocks(doc, pricing, pdf)
	letter_head = media.letter_head_context(doc, pdf)
	logo = get_url(company.company_logo) if (company.company_logo or "").startswith("/files/") else None
	cover = next((row for row in doc.sections if row.section_type == "Cover" and cint(row.enabled)), None)
	return {
		"title": doc.title,
		"theme": "ivory",
		"client": client_name(doc),
		"company": company.company_name,
		"logo": logo,
		"share": share_card(doc, company.company_name, logo, letter_head),
		"valid_till": frappe.utils.formatdate(doc.valid_till, "d MMMM yyyy") if doc.valid_till else None,
		"revision": cint(doc.revision_no),
		"cover": {
			"shown": bool(cover),
			"solution": doc.get("solution_type"),
			"tagline": text_parts(cover.body) if cover else [],
			"image": media.image_payload(doc, cover, pdf) if cover else None,
			"prepared_by": frappe.utils.get_fullname(doc.prepared_by) if doc.get("prepared_by") else None,
			"date": frappe.utils.formatdate(doc.sent_on or doc.creation, "d MMMM yyyy"),
			"reference": doc.name,
			"version": cint(doc.revision_no) + 1,
		},
		"toc": table_of_contents(blocks),
		"status": doc.status,
		"preview": preview,
		"superseded": not cint(doc.is_latest),
		"can_decide": not preview
		and doc.status in WITH_CLIENT
		and cint(doc.is_latest)
		and not expired
		and not price_gone,
		"price_unavailable": price_gone,
		"expired": expired or doc.status == EXPIRED,
		"decided_by": doc.decided_by_name,
		"decided_on": frappe.utils.formatdate(doc.decided_on, "d MMMM yyyy") if doc.decided_on else None,
		"letter_head": letter_head,
		"blocks": blocks,
		"pricing": pricing,
		"consolidated": doc.get("pricing_mode") == "Consolidated",
		"included": [row.module_name for row in doc.get("modules") or []],
		"acceptance": {
			"client": client_name(doc),
			"name": doc.decided_by_name,
			"email": doc.decided_by_email if doc.status == ACCEPTED else None,
			"date": frappe.utils.formatdate(doc.decided_on, "d MMMM yyyy")
			if doc.status == ACCEPTED and doc.decided_on
			else None,
			"accepted": doc.status == ACCEPTED,
			"our_company": company.company_name,
			"our_signatory": frappe.utils.get_fullname(doc.prepared_by) if doc.get("prepared_by") else None,
		},
		"token": doc.public_token,
	}


BUNDLED_ICON = "/assets/tbo_smart/images/tbo-proposal-icon"


def share_card(doc, company: str | None, logo: str | None, letter_head: dict | None) -> dict:
	"""The tab icon, and what a shared link shows (WhatsApp, mail): the title and the client's
	name only, never a price or the proposal's text. The icon is Website Settings' favicon, else
	its app logo or the company logo, else the bundled TBO icon."""
	website = [frappe.db.get_single_value("Website Settings", field) for field in ("favicon", "app_logo")]
	public = [url for url in website if (url or "").startswith("/files/")]
	icon = get_url(public[0]) if public else logo or get_url(f"{BUNDLED_ICON}.svg")
	header = (letter_head or {}).get("header") or {}
	brand = company or _("Team Back Office")
	return {
		"brand": brand,
		"title": f"{doc.title} · {brand}",
		"description": _("Proposal for {0} from {1}").format(client_name(doc), brand),
		"icon": icon,
		"touch_icon": logo or get_url(f"{BUNDLED_ICON}.png"),
		"image": header.get("image")
		if (header.get("image") or "").startswith("http")
		else logo or get_url(f"{BUNDLED_ICON}.png"),
		"url": public_url(doc),
	}


def numbered_blocks(doc, pricing, pdf: bool) -> list[dict]:
	"""The sections the client sees, in order: on, not empty (the pricing and acceptance always),
	numbered 1, 2, … after the cover, their subheadings 1.1, 1.2, …, each part's title before its
	first section."""
	blocks, number, part = [], 0, None
	for row in doc.sections:
		if not cint(row.enabled) or row.section_type == "Cover":
			continue
		block = page_block(doc, row, pdf, pricing)
		always = row.section_type == ACCEPTANCE or (row.section_type == PRICING and pricing)
		if not (block["parts"] or block["image"] or always):
			continue
		# a custom or image block stays in the part it is placed in
		new_part = PART_OF.get(row.section_type) or part
		block["part"] = _(new_part) if new_part and new_part != part else None
		part = new_part or part
		if not block["heading_hidden"]:
			number += 1
			block["number"] = number
			sub = 0
			for item in block["parts"]:
				if item["kind"] == "heading":
					sub += 1
					item["number"] = f"{number}.{sub}"
		blocks.append(block)
	return blocks


def table_of_contents(blocks: list[dict]) -> list[dict]:
	"""[{part, items: [{number, heading}]}] for the contents page."""
	toc = []
	for block in blocks:
		if not block.get("number"):
			continue
		if block["part"] or not toc:
			toc.append({"part": block["part"], "items": []})
		toc[-1]["items"].append({"number": block["number"], "heading": block["heading"]})
	return toc


def page_block(doc, row, pdf: bool, pricing: dict | None = None) -> dict:
	parts = text_parts(row.body) if row.section_type != "Image" else []
	block = {
		"type": row.section_type,
		"heading": row.heading or _(DEFAULT_HEADINGS.get(row.section_type, row.section_type)),
		# an Image block without a heading of its own names itself for screen readers only
		"heading_hidden": row.section_type == "Image" and not row.heading,
		"parts": parts,
		"image": media.image_payload(doc, row, pdf),
		"number": None,
	}
	if row.section_type == "Project Timeline":
		block["timeline"] = timeline(parts)
	elif row.section_type == "Modules & Features":
		block["modules"] = module_cards(parts)
	elif row.section_type == "Payment Terms":
		add_payment_amounts(parts, pricing)
	return block


def first_table(parts: list[dict]) -> dict | None:
	return next((part for part in parts if part["kind"] == "table"), None)


def timeline(parts: list[dict]) -> dict | None:
	"""The phases table ("phase | days") as bars, each starting where the one before ends."""
	found = first_table(parts)
	if not found:
		return None
	rows = [(cells[0], flt(cells[1])) for cells in found["rows"] if len(cells) > 1 and flt(cells[1]) > 0]
	total = sum(weeks for _name, weeks in rows)
	if not total:
		return None
	bars, start = [], 0.0
	for name, weeks in rows:
		bars.append(
			{
				"phase": name,
				"length": f"{weeks:g}",
				"left": round(start / total * 100, 2),
				"width": round(weeks / total * 100, 2),
			}
		)
		start += weeks
	found["hidden"] = True
	# the unit is the table's own ("days" in the timeline templates, "weeks" in older proposals)
	return {"bars": bars, "total": f"{total:g}", "unit": found["header"][1]}


def module_cards(parts: list[dict]) -> list[dict] | None:
	"""The modules table ("module | fit | what it covers | key features") as cards: the module
	map."""
	found = first_table(parts)
	if not found or len(found["header"]) < 2:
		return None
	found["hidden"] = True

	def cell(cells, index):
		value = cells[index].strip() if len(cells) > index else ""
		return "" if value == "–" else value

	features_at = 3 if len(found["header"]) > 3 else 2
	return [
		{
			"name": cell(cells, 0),
			"fit": cell(cells, 1),
			"description": cell(cells, 2) if features_at == 3 else "",
			"features": [item.strip() for item in cell(cells, features_at).split(";") if item.strip()],
		}
		for cells in found["rows"]
	]


def add_payment_amounts(parts: list[dict], pricing: dict | None) -> None:
	"""Each milestone's amount from its share of the total ("milestone | 30%")."""
	found = first_table(parts)
	if not found or not pricing:
		return
	found["header"] = [*found["header"][:2], _("Amount")]
	for cells in found["rows"]:
		share = flt(str(cells[1] if len(cells) > 1 else 0).replace("%", ""))
		cells[2:] = [
			frappe.utils.fmt_money(pricing["grand_total_value"] * share / 100, currency=pricing["currency"])
		]
	found["numeric"] = True


def text_parts(body: str | None) -> list[dict]:
	"""Plain text as the page lays it out, for the template to escape: a blank line starts a
	part; "## " a subheading; lines starting "- " a list; lines with " | " a table (the first its
	header); a line with " → " a chain of steps; anything else a paragraph."""
	parts = []
	for chunk in re.split(r"\n\s*\n", (body or "").strip()):
		lines = [line.strip() for line in chunk.splitlines() if line.strip()]
		while lines and lines[0].startswith("## "):
			parts.append({"kind": "heading", "text": lines.pop(0)[3:].strip(), "number": None})
		if not lines:
			continue
		if all(line.startswith(("- ", "• ")) for line in lines):
			parts.append({"kind": "list", "items": [line[2:].strip() for line in lines]})
		elif len(lines) > 1 and all(" | " in line for line in lines):
			rows = [[cell.strip() for cell in line.split(" | ")] for line in lines]
			parts.append(
				{"kind": "table", "header": rows[0], "rows": rows[1:], "hidden": False, "numeric": False}
			)
		elif len(lines) == 1 and " → " in lines[0]:
			parts.append(
				{"kind": "chain", "steps": [step.strip() for step in lines[0].split("→") if step.strip()]}
			)
		else:
			parts.append({"kind": "text", "lines": lines})
	return parts


def pricing_table(quotation: str) -> dict | None:
	if not frappe.db.exists("Quotation", quotation):
		return None
	doc = frappe.get_doc("Quotation", quotation)

	def money(value):
		return frappe.utils.fmt_money(value, currency=doc.currency)

	return {
		"lines": [pricing_line(row, money, setting("proposal_service_item")) for row in doc.items],
		"total": money(doc.total),
		"discount": money(doc.discount_amount) if doc.discount_amount else None,
		"net_total": money(doc.net_total),
		"taxes": [{"label": row.description, "amount": money(row.tax_amount)} for row in doc.taxes],
		"grand_total": money(doc.grand_total),
		"grand_total_value": flt(doc.grand_total),
		"currency": doc.currency,
		"in_words": doc.in_words,
		"payment_terms": [
			{
				"label": row.description or row.payment_term or _("Payment"),
				"due": frappe.utils.formatdate(row.due_date, "d MMM yyyy") if row.due_date else None,
				"amount": money(row.payment_amount),
			}
			for row in doc.get("payment_schedule") or []
		],
	}


def pricing_line(row, money, service_item: str | None = None) -> dict:
	"""One quotation line as the page shows it: the description as plain text, left out when it
	only repeats the item's name. A line on the proposal service item (a module without an Item
	of its own, the customisation days) is named by its description."""
	description = plain_text(row.description)
	item = row.item_name or row.item_code
	if service_item and row.item_code == service_item and description:
		item, description = description, None
	return {
		"item": item,
		"description": description if description and description != (row.item_name or "").strip() else None,
		"qty": frappe.utils.fmt_money(row.qty, precision=0) if row.qty == int(row.qty) else row.qty,
		"rate": money(row.rate),
		"amount": money(row.amount),
	}


def render_html(doc, preview: bool = False, pdf: bool = False) -> str:
	return frappe.render_template(  # our own template; the values are escaped in it - nosemgrep
		"tbo_smart/www/proposal.html",
		{"proposal": page_context(doc, preview, pdf), "pdf": pdf, "print": False},
	)


# A4, full bleed so the ivory reaches the edges (the side margins are padding in the page), and
# the letter head's header and footer on every page (Frappe's get_pdf repeats #header-html and
# #footer-html). The page numbers are drawn after (on_ivory): wkhtmltopdf's own text footer
# doesn't render beside an HTML one.
PDF_OPTIONS = {
	"page-size": "A4",
	"margin-top": "30mm",
	"margin-bottom": "24mm",
	"margin-left": "0mm",
	"margin-right": "0mm",
}


IVORY = (251 / 255, 248 / 255, 239 / 255)


def render_pdf(doc) -> bytes:
	from frappe.utils.pdf import get_pdf

	return on_ivory(get_pdf(render_html(doc, pdf=True), dict(PDF_OPTIONS)))


def on_ivory(pdf: bytes) -> bytes:
	"""Every page on ivory paper to its edges (wkhtmltopdf paints white below the content of the
	last page), with "n / N" at the bottom right of every page but the cover."""
	import io

	from pypdf import PdfReader, PdfWriter
	from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

	pages = PdfReader(io.BytesIO(pdf)).pages
	writer = PdfWriter()
	scratch = PdfWriter()
	font = DictionaryObject(
		{
			NameObject("/Type"): NameObject("/Font"),
			NameObject("/Subtype"): NameObject("/Type1"),
			NameObject("/BaseFont"): NameObject("/Helvetica"),
		}
	)

	def layer(width, height, ops: str):
		page = scratch.add_blank_page(width, height)
		stream = DecodedStreamObject()
		stream.set_data(ops.encode())
		page[NameObject("/Contents")] = scratch._add_object(stream)
		page[NameObject("/Resources")] = DictionaryObject(
			{NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
		)
		return page

	for number, page in enumerate(pages, start=1):
		width, height = float(page.mediabox.width), float(page.mediabox.height)
		sheet = writer.add_blank_page(width, height)
		sheet.merge_page(
			layer(width, height, f"{IVORY[0]:.4f} {IVORY[1]:.4f} {IVORY[2]:.4f} rg 0 0 {width} {height} re f")
		)
		sheet.merge_page(page)
		if number > 1:
			label = f"{number} / {len(pages)}"
			# 18 mm from the right edge, on the footer's line; Helvetica 8 pt is about 4.4 pt a digit
			x = width - 51 - len(label) * 4.4
			sheet.merge_page(
				layer(width, height, f"BT /F1 8 Tf 0.404 0.384 0.353 rg {x:.1f} 22 Td ({label}) Tj ET")
			)
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()


def lead_proposals(lead: str) -> list[dict]:
	"""The lead's proposals, newest first, for the lead page."""
	return frappe.get_all(
		DOCTYPE,
		filters={"lead": lead},
		fields=[
			"name",
			"title",
			"status",
			"revision_no",
			"is_latest",
			"proposal_family",
			"quotation",
			"sent_on",
			"view_count",
			"last_viewed_on",
			"decided_on",
			"modified",
		],
		order_by="creation desc",
	)
