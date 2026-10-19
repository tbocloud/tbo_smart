"""Proposals and the client documents after them for the TBO Support hub
(docs/hub-proposals-api.md, docs/client-documents.md).

The same rules as tbo_smart.api.hub_leads: the hub's integration user names the person in
`acting_user`, the call runs as them (`hub_leads.acting_as`), a proposal opens only when they
may read its lead and changes need write on it. Writes are POST. Every method needs TBO Smart
Settings → Proposals.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint

from tbo_smart.api import lead_quotation
from tbo_smart.api.hub_leads import acting_as
from tbo_smart.api.quotation_adjustment import request_adjustment
from tbo_smart.lead_pipeline import brd, presets, rate_card
from tbo_smart.lead_pipeline import client_documents as documents
from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline import proposal_builder as builder
from tbo_smart.lead_pipeline import proposal_library as library
from tbo_smart.lead_pipeline import proposal_media as media
from tbo_smart.lead_pipeline import proposal_profile as profile
from tbo_smart.lead_pipeline import proposals as p
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.quotation_control import QuotationControl

TRACKING = (
	"sent_on",
	"sent_to",
	"first_viewed_on",
	"last_viewed_on",
	"view_count",
	"decided_on",
	"decided_by_name",
	"decided_by_email",
	"decided_by_designation",
	"decline_reason",
)


def parse(value, kind=dict):
	value = frappe.parse_json(value) if isinstance(value, str) else value
	if not isinstance(value, kind):
		frappe.throw(_("Send {0} as JSON.").format("an object" if kind is dict else "a list"))
	return value


def get_doc(name: str, ptype: str = "read"):
	p.require_proposals()
	if not isinstance(name, str) or not frappe.db.exists(p.DOCTYPE, name):
		frappe.throw(_("Proposal {0} not found.").format(name), frappe.DoesNotExistError)
	doc = frappe.get_doc(p.DOCTYPE, name)
	doc.check_permission(ptype)
	return doc


def payload(doc) -> dict:
	"""The proposal as the hub's builder shows it."""
	quotation = None
	if doc.quotation and frappe.db.exists("Quotation", doc.quotation):
		qdoc = frappe.get_doc("Quotation", doc.quotation)
		quotation = {
			**lead_quotation.serialize(qdoc),
			"needs_approval": [
				{"kind": kind, "percent": percent, "limit": limit}
				for kind, percent, limit in (
					QuotationControl(qdoc).uncovered() if qdoc.docstatus == 0 else []
				)
			],
		}
	can_write = bool(frappe.has_permission("Lead", "write", doc.lead))  # a flag for the screen - nosemgrep
	spec = kinds.of(doc)
	return {
		"name": doc.name,
		"document_type": spec.name,
		"document_label": kinds.label(spec.name),
		# what the cover calls it ("Business Requirements Document")
		"kicker": kinds.words(spec.name)["kicker"],
		"accepted_label": kinds.accepted_label(spec.name),
		"source_document": doc.source_document,
		"can_price": spec.priced,
		"signature": spec.signature,
		"start_date": doc.start_date,
		"title": doc.title,
		"lead": doc.lead,
		"lead_name": doc.lead_name,
		"client": p.client_name(doc),
		"lead_email": frappe.db.get_value("Lead", doc.lead, "email_id"),
		"template": doc.template,
		"solution_type": doc.solution_type,
		"page_guide": frappe.db.get_value(p.TEMPLATE, doc.template, "page_guide") if doc.template else None,
		"prepared_by": frappe.utils.get_fullname(doc.prepared_by) if doc.prepared_by else None,
		"status": doc.status,
		"theme": doc.theme,
		"pricing_mode": doc.pricing_mode or p.PRICING_MODES[0],
		"scoping": builder.scoping_answers(doc.scoping, doc.solution_type),
		"requirements": doc.requirements,
		"client_profile": profile.clean_profile(doc.client_profile),
		"chat": builder.chat_log(doc.chat),
		"modules": [
			{
				field: row.get(field)
				for field in (
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
			}
			for row in doc.modules
		],
		"letter_head": doc.letter_head_override,
		"letter_head_preview": media.letter_head_context(doc),
		"valid_till": doc.valid_till,
		"sections": [section_payload(doc, row) for row in doc.sections],
		"quotation": quotation,
		"pricing": rate_card.lead_pricing(doc.lead),
		"public_url": p.public_url(doc) if doc.status != p.DRAFT else None,
		"preview_url": p.public_url(doc, preview=True),
		"revision_no": cint(doc.revision_no),
		"is_latest": cint(doc.is_latest),
		"revisions": p.revisions(doc),
		"tracking": {field: doc.get(field) for field in TRACKING},
		"can_write": can_write,
		"send_blockers": p.send_blockers(doc) if can_write else [],
		"share_blockers": p.send_blockers(doc, by_email=False) if can_write else [],
		# the AI's cost, for the people who set prices and presets
		"ai": ai_payload(doc) if presets.can_edit_presets() else None,
		"questionnaire": questionnaire_payload(doc) if spec.questionnaire else None,
	}


def questionnaire_payload(doc) -> dict:
	"""The BRD's questions for the client, their answers and the link's state."""
	return {
		"questions": [
			{
				"name": row.name,
				"section_type": row.section_type,
				"question": row.question,
				"answer": row.answer,
				"answered_on": row.answered_on,
				"applied": cint(row.applied),
				"asked_by": row.asked_by,
			}
			for row in doc.questions
		],
		"url": brd.questionnaire_url(doc) if doc.questions_sent_on else None,
		"open": brd.questionnaire_open(doc),
		"sent_on": doc.questions_sent_on,
		"answered_on": doc.questions_answered_on,
		"new_answers": len(brd.unapplied(doc)),
	}


def ai_payload(doc) -> dict:
	return {
		**builder.usage(doc),
		"estimate_tokens": builder.estimate_tokens(doc) if p.ai_enabled() else 0,
		"daily_limit": builder.daily_limit(),
	}


def section_payload(doc, row) -> dict:
	spec = kinds.of(doc)
	return {
		"section_type": row.section_type,
		"enabled": cint(row.enabled),
		"edited": cint(row.edited),
		"ai": row.section_type in spec.ai_sections,
		"rebuildable": row.section_type in spec.ai_sections or row.section_type in spec.builders,
		"heading": row.heading,
		"body": row.body,
		"image": row.image,
		"image_url": media.image_url(doc, row.image) if row.image else None,
		"image_alt": row.image_alt,
		"image_caption": row.image_caption,
		"image_size": row.image_size or media.IMAGE_SIZES[0],
	}


@frappe.whitelist()
def get_proposal_options(acting_user: str | None = None) -> dict:
	"""{enabled, ai, can_edit_templates, templates, packages, section_types, themes}."""
	with acting_as(acting_user):
		if not p.proposals_enabled():
			return {"enabled": False}
		editor = presets.can_edit_presets()
		return {
			"enabled": True,
			"ai": p.ai_enabled(),
			"can_edit_templates": editor,
			"templates": p.templates(include_disabled=editor),
			"packages": frappe.get_all(
				presets.PACKAGE, filters={"enabled": 1}, pluck="name", order_by="name asc"
			),
			"section_types": list(p.SECTION_TYPES),
			"default_headings": p.DEFAULT_HEADINGS,
			"ai_sections": list(builder.AI_SECTIONS),
			"themes": list(p.THEMES),
			"pricing_modes": list(p.PRICING_MODES),
			"questions": list(p.QUESTIONS),
			"hosting": list(builder.HOSTING),
			"profile_required": list(profile.REQUIRED),
			"timelines": [{"title": row.title, "days": row.weeks} for row in builder.timeline_templates()],
			"payment_terms": [row.title for row in builder.library("Payment Terms")],
			"amc_plans": [row.title for row in builder.library("AMC Plan")],
			"catalogue": builder.catalogue(),
			"images": [
				{
					"name": row.name,
					"title": row.title,
					"alt_text": row.alt_text,
					"sections": builder.lines(row.sections),
					"solution_types": builder.lines(row.solution_types),
					"url": frappe.utils.get_url(row.image) if row.image else None,
				}
				for row in builder.library_images()
			],
			"letter_heads": media.letter_heads(),
			"default_letter_head": media.default_letter_head(),
			"proposal_letter_head": p.setting("proposal_letter_head"),
			"document_types": [document_type_payload(spec) for spec in documents.enabled_types()],
		}


def document_type_payload(spec) -> dict:
	"""A switched-on type of client document, as the hub's builder needs it."""
	return {
		"name": spec.name,
		"label": kinds.label(spec.name),
		"accepted_label": kinds.accepted_label(spec.name),
		"follows": spec.follows,
		"section_types": list(spec.section_types),
		"default_headings": spec.headings,
		"ai_sections": list(spec.ai_sections),
		"priced": spec.priced,
		"signature": spec.signature,
		"questionnaire": spec.questionnaire,
	}


@frappe.whitelist()
def list_proposals(lead: str, acting_user: str | None = None) -> list[dict]:
	with acting_as(acting_user):
		p.require_proposals()
		frappe.has_permission("Lead", "read", lead, throw=True)
		return p.lead_proposals(lead)


@frappe.whitelist(methods=["POST"])
def create_proposal(
	lead: str,
	template: str | None = None,
	ai_draft: int | str = 0,
	scoping: dict | str | None = None,
	requirements: str | None = None,
	client_profile: dict | str | None = None,
	chat: list | str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""A complete draft from the template and the scoping answers (and the requirements text it
	came from, kept on the proposal), the narrative written by the AI when `ai_draft`. Returns
	{proposal, warnings}."""
	with acting_as(acting_user):
		p.require_proposals()
		frappe.has_permission("Lead", "write", lead, throw=True)
		doc, warnings = p.create_proposal(
			lead,
			template or None,
			bool(cint(ai_draft)),
			parse(scoping) if scoping else None,
			requirements,
			parse(client_profile) if client_profile else None,
			parse(chat, list) if chat else None,
		)
		return {"proposal": payload(doc), "warnings": warnings}


@frappe.whitelist(methods=["POST"])
def create_document(source: str, document_type: str, acting_user: str | None = None) -> dict:
	"""The next client document (a Quotation from an accepted proposal, a Work Order from an
	accepted quotation), pre-filled from it. Returns {proposal, warnings}."""
	with acting_as(acting_user):
		p.require_proposals()
		doc, warnings = documents.create_document(source, document_type)
		return {"proposal": payload(doc), "warnings": warnings}


@frappe.whitelist()
def get_document_chain(lead: str, acting_user: str | None = None) -> dict:
	"""The lead's documents in order, each with its status, and the one next action."""
	with acting_as(acting_user):
		p.require_proposals()
		frappe.has_permission("Lead", "read", lead, throw=True)
		return documents.chain(lead)


@frappe.whitelist(methods=["POST"])
def save_questions(name: str, questions: list | str, acting_user: str | None = None) -> dict:
	"""A draft BRD's questions for the client ([{section_type, question}]); answered ones stay."""
	with acting_as(acting_user):
		doc = get_questionnaire_doc(name)
		brd.save_questions(doc, parse(questions, list))
		return payload(doc)


@frappe.whitelist(methods=["POST"])
def share_questionnaire(name: str, acting_user: str | None = None) -> dict:
	"""The client's questionnaire link (only the questions; never the draft). Returns {proposal, url}."""
	with acting_as(acting_user):
		doc = get_questionnaire_doc(name)
		url = brd.share_questionnaire(doc)
		return {"proposal": payload(doc), "url": url}


@frappe.whitelist(methods=["POST"])
def send_questionnaire(
	name: str, recipients: str | None = None, message: str | None = None, acting_user: str | None = None
) -> dict:
	"""Email the client the questionnaire link, on the lead's timeline. Returns {proposal, url}."""
	with acting_as(acting_user):
		doc = get_questionnaire_doc(name)
		url = brd.send_questionnaire(doc, recipients, message)
		return {"proposal": payload(doc), "url": url}


@frappe.whitelist(methods=["POST"])
def apply_answers(name: str, acting_user: str | None = None) -> dict:
	"""The client's new answers into the BRD: only the sections they touch are rebuilt (the
	narrative ones in one small AI call). Returns {proposal, sections, warnings}."""
	with acting_as(acting_user):
		doc = get_questionnaire_doc(name)
		result = brd.apply_answers(doc)
		return {"proposal": payload(doc), **result}


def get_questionnaire_doc(name: str):
	doc = get_doc(name, "write")
	if not kinds.of(doc).questionnaire:
		frappe.throw(_("Only a BRD has a client questionnaire."))
	return doc


@frappe.whitelist()
def get_proposal(name: str, acting_user: str | None = None) -> dict:
	with acting_as(acting_user):
		return payload(get_doc(name))


@frappe.whitelist(methods=["POST"])
def save_proposal(name: str, data: dict | str, acting_user: str | None = None) -> dict:
	"""A draft's `title`, `letter_head` (blank: the default), `pricing_mode`, `valid_till`,
	`start_date` (an agreement's), `scoping`, `modules` and `sections` (each key optional; `theme`
	is ignored: there is one)."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		p.save_content(doc, parse(data))
		return payload(doc)


@frappe.whitelist(methods=["POST"])
def upload_proposal_image(name: str, filename: str, content: str, acting_user: str | None = None) -> dict:
	"""A private image on a draft (`content` base64, at most 5 MB, PNG / JPEG / WebP). Returns
	{file, url}: put `file` in a Cover or Image block's `image` and save."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		return media.upload_image(doc, filename, media.decode(content))


@frappe.whitelist(methods=["POST"])
def regenerate_section(
	name: str, index: int | str, force: int | str = 0, acting_user: str | None = None
) -> dict:
	"""One section written again: an AI section by the AI (one small call, within the day's
	limit), any other from the libraries and the scoping answers. A section edited by hand comes
	back with `confirm` (a question) and is written over only with `force`. Returns {proposal,
	warnings, confirm}."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		result = p.rebuild_section(doc, cint(index), bool(cint(force)))
		return {"proposal": payload(doc), **result}


@frappe.whitelist(methods=["POST"])
def regenerate_proposal(name: str, force: int | str = 0, acting_user: str | None = None) -> dict:
	"""Every section again after the scoping changed (the AI's in one call when it is on); the
	sections edited by hand are kept unless `force`. Returns {proposal, warnings, kept}."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		result = p.regenerate_all(doc, bool(cint(force)))
		return {"proposal": payload(doc), **result}


@frappe.whitelist()
def get_client_profile(lead: str, acting_user: str | None = None) -> dict:
	"""The client profile's starting point: what the lead says and the website summary read
	earlier. Returns {profile, web_read_on, from_web}."""
	with acting_as(acting_user):
		p.require_proposals()
		frappe.has_permission("Lead", "read", lead, throw=True)
		return profile.profile_defaults(lead)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60 * 60)
def read_client_website(
	lead: str, url: str | None = None, refresh: int | str = 0, acting_user: str | None = None
) -> dict:
	"""The client's website read (guarded: public http(s) only, small and quick) and summarised
	into profile fields by one AI call, kept on the lead; the kept one again unless `refresh`.
	Returns {profile}."""
	with acting_as(acting_user):
		p.require_proposals()
		frappe.has_permission("Lead", "write", lead, throw=True)
		if not p.ai_enabled():
			frappe.throw(_("AI proposal drafts are not switched on. Type the profile."))
		return {"profile": profile.read_website(lead, url, bool(cint(refresh)))}


@frappe.whitelist()
def get_requirements_from_lead(lead: str, acting_user: str | None = None) -> dict:
	"""The lead's notes and minutes as one text, to start the requirements box (no AI)."""
	with acting_as(acting_user):
		p.require_proposals()
		frappe.has_permission("Lead", "read", lead, throw=True)
		return {"text": builder.requirements_from_lead(lead)}


@frappe.whitelist(methods=["POST"])
def extract_requirements(lead: str, text: str, acting_user: str | None = None) -> dict:
	"""The requirements box read into the scoping form by one small AI call. Returns {template,
	scoping} for presales to confirm; nothing is saved."""
	with acting_as(acting_user):
		p.require_proposals()
		frappe.has_permission("Lead", "write", lead, throw=True)
		if not p.ai_enabled():
			frappe.throw(_("AI proposal drafts are not switched on. Fill the form by hand."))
		return builder.extract_requirements(lead, text)


@frappe.whitelist(methods=["POST"])
def chat_edit(name: str, message: str, acting_user: str | None = None) -> dict:
	"""The assistant on a draft: the request read into structured changes by one small call and
	applied; the transcript kept. Returns {proposal, reply, changes, warnings}."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		result = p.edit_by_chat(doc, message)
		return {"proposal": payload(doc), **result}


@frappe.whitelist(methods=["POST"])
def price_from_modules(name: str, acting_user: str | None = None) -> dict:
	"""The quotation's lines from the proposal's modules and customisation days."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		p.price_from_modules(doc)
		return payload(doc)


@frappe.whitelist(methods=["POST"])
def share_proposal(name: str, acting_user: str | None = None) -> dict:
	"""The client's link without an email: a draft goes out as Send does (refused while anything
	blocks it), a sent one just gives its link. Returns {proposal, url}."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		url = p.share(doc)
		return {"proposal": payload(doc), "url": url}


@frappe.whitelist(methods=["POST"])
def use_library_image(name: str, image: str, acting_user: str | None = None) -> dict:
	"""A library image on a draft. Returns {file, url, alt}: put `file` in a section and save."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		return builder.use_library_image(doc, image)


@frappe.whitelist()
def get_proposal_library(acting_user: str | None = None) -> dict:
	"""The module catalogue, the section libraries and the image library, with the pick lists."""
	with acting_as(acting_user):
		p.require_proposals()
		return library.library_payload()


@frappe.whitelist(methods=["POST"])
def save_library_record(kind: str, data: dict | str, acting_user: str | None = None) -> dict:
	"""Create or update a `module`, a library `item`, or a library `image`'s tags (editors).
	Returns the library."""
	with acting_as(acting_user):
		p.require_proposals()
		library.save_record(kind, parse(data))
		return library.library_payload()


@frappe.whitelist(methods=["POST"])
def draft_library_module(module: str, acting_user: str | None = None) -> dict:
	"""The AI drafts a catalogue module's proposal text once, for an editor to review and save
	(library upkeep; a proposal itself never calls this). Returns {draft}."""
	with acting_as(acting_user):
		p.require_proposals()
		return {"draft": library.draft_module(module)}


@frappe.whitelist(methods=["POST"])
def delete_library_record(kind: str, name: str, acting_user: str | None = None) -> dict:
	with acting_as(acting_user):
		p.require_proposals()
		library.delete_record(kind, name)
		return library.library_payload()


@frappe.whitelist(methods=["POST"])
def upload_library_image(
	filename: str, content: str, data: dict | str, acting_user: str | None = None
) -> dict:
	"""A new library image (editors; `content` base64, PNG / JPEG / WebP up to 5 MB) with its
	`data`: title, alt_text, sections, solution_types, modules. Returns the library."""
	with acting_as(acting_user):
		p.require_proposals()
		library.upload_library_image(filename, media.decode(content), parse(data))
		return library.library_payload()


@frappe.whitelist()
def get_pricing_form(name: str, acting_user: str | None = None) -> dict:
	"""The quotation builder for the pricing block (lead_quotation.get_quotation_form): the
	catalogue priced for the lead's segment, its band, and the current quotation's values."""
	with acting_as(acting_user):
		doc = get_doc(name)
		return lead_quotation.get_quotation_form(doc.lead, doc.quotation or None)


@frappe.whitelist(methods=["POST"])
def save_pricing(name: str, values: dict | str, acting_user: str | None = None) -> dict:
	"""Price the proposal: save_quotation's `values` on the lead's quotation (a sent one is
	revised first)."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		p.save_pricing(doc, parse(values))
		return payload(doc)


@frappe.whitelist(methods=["POST"])
def request_approval(name: str, reason: str, acting_user: str | None = None) -> dict:
	"""Ask the next level up to approve the pricing's discount or commission above the limit."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		if not doc.quotation:
			frappe.throw(_("Price the proposal first."))
		quotation = frappe.get_doc("Quotation", doc.quotation)
		missing = QuotationControl(quotation).uncovered() if quotation.docstatus == 0 else []
		if not missing:
			frappe.throw(_("The pricing needs no approval."))
		if q.draft_status(quotation) == q.PENDING:
			frappe.throw(_("The pricing is already waiting for approval."))
		for kind, percent, _limit in missing:
			request_adjustment(quotation.name, kind, percent, reason)
		return payload(doc)


@frappe.whitelist(methods=["POST"])
def send_proposal(
	name: str,
	recipients: str | None = None,
	message: str | None = None,
	attach_pdf: int | str = 0,
	acting_user: str | None = None,
) -> dict:
	"""Email the client the link (and the PDF with `attach_pdf`). Refused with the reasons while
	anything blocks it (`send_blockers`), e.g. pricing waiting for approval."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		p.send(doc, recipients, message, bool(cint(attach_pdf)))
		return payload(doc)


@frappe.whitelist(methods=["POST"])
def create_revision(name: str, acting_user: str | None = None) -> dict:
	"""The next version as a draft; the one the client has stays as it is."""
	with acting_as(acting_user):
		doc = get_doc(name, "write")
		return payload(p.create_revision(doc))


@frappe.whitelist(methods=["POST"])
def set_default_letter_head(letter_head: str | None = None, acting_user: str | None = None) -> dict:
	"""TBO Smart Settings → Proposal letter head (the roles that edit presets and prices; blank:
	the Letter Head marked default). Returns the options."""
	with acting_as(acting_user):
		p.require_proposals()
		presets.require_preset_editor()
		media.set_default_letter_head(letter_head or None)
	return get_proposal_options(acting_user)


@frappe.whitelist(methods=["POST"])
def save_template(data: dict | str, acting_user: str | None = None) -> dict:
	"""Create or update a template (the roles that edit presets and prices). Returns the options."""
	with acting_as(acting_user):
		p.require_proposals()
		p.save_template(parse(data))
	return get_proposal_options(acting_user)
