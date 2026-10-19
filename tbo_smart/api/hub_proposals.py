"""Proposals for the TBO Support hub (docs/hub-proposals-api.md).

The same rules as tbo_smart.api.hub_leads: the hub's integration user names the person in
`acting_user`, the call runs as them (`hub_leads.acting_as`), a proposal opens only when they
may read its lead and changes need write on it. Writes are POST. Every method needs TBO Smart
Settings → Proposals.
"""

import base64
import binascii

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart.api import lead_quotation
from tbo_smart.api.hub_leads import acting_as
from tbo_smart.api.quotation_adjustment import request_adjustment
from tbo_smart.lead_pipeline import presets, rate_card
from tbo_smart.lead_pipeline import proposal_media as media
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
	return {
		"name": doc.name,
		"title": doc.title,
		"lead": doc.lead,
		"lead_name": doc.lead_name,
		"client": p.client_name(doc),
		"lead_email": frappe.db.get_value("Lead", doc.lead, "email_id"),
		"template": doc.template,
		"status": doc.status,
		"theme": doc.theme,
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
	}


def section_payload(doc, row) -> dict:
	return {
		"section_type": row.section_type,
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
			"themes": list(p.THEMES),
			"letter_heads": media.letter_heads(),
			"default_letter_head": media.default_letter_head(),
			"proposal_letter_head": p.setting("proposal_letter_head"),
		}


@frappe.whitelist()
def list_proposals(lead: str, acting_user: str | None = None) -> list[dict]:
	with acting_as(acting_user):
		p.require_proposals()
		frappe.has_permission("Lead", "read", lead, throw=True)
		return p.lead_proposals(lead)


@frappe.whitelist(methods=["POST"])
def create_proposal(
	lead: str, template: str | None = None, ai_draft: int | str = 0, acting_user: str | None = None
) -> dict:
	"""A draft from the template, written by the AI when `ai_draft`. Returns {proposal, warnings}."""
	with acting_as(acting_user):
		p.require_proposals()
		frappe.has_permission("Lead", "write", lead, throw=True)
		doc, warnings = p.create_proposal(lead, template or None, bool(cint(ai_draft)))
		return {"proposal": payload(doc), "warnings": warnings}


@frappe.whitelist()
def get_proposal(name: str, acting_user: str | None = None) -> dict:
	with acting_as(acting_user):
		return payload(get_doc(name))


@frappe.whitelist(methods=["POST"])
def save_proposal(name: str, data: dict | str, acting_user: str | None = None) -> dict:
	"""A draft's `title`, `theme`, `letter_head` (blank: the default), `valid_till` and
	`sections` (each key optional)."""
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
		try:
			data = base64.b64decode(content or "", validate=True)
		except (binascii.Error, ValueError):
			frappe.throw(_("Send the image as base64."))
		return media.upload_image(doc, filename, data)


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
