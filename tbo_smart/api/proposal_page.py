"""The client's side of a proposal and the documents after it (docs/proposals.md §5,
docs/client-documents.md): what the page at /proposal/<token> calls. No login: the token is the only key, each call is rate limited, and every answer is the
same "not found" for a token that is malformed, unknown, a draft or switched off."""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from tbo_smart.lead_pipeline import brd, proposals
from tbo_smart.lead_pipeline import proposal_media as media


def get_public(token: str, preview: bool = False):
	doc = proposals.public_doc(token, preview)
	if not doc:
		frappe.throw(_("This document isn't available."), frappe.DoesNotExistError)
	return doc


@frappe.whitelist(allow_guest=True, methods=["POST"])  # the token is the key - nosemgrep
@rate_limit(limit=60, seconds=60 * 60)
def record_view(token: str) -> dict:
	"""The page was opened (not in preview): one more view, the first marks it Viewed. Only the
	client's views count: someone signed in on this site (staff checking the link) counts nothing."""
	doc = get_public(token)
	if frappe.session.user == "Guest" and doc.status in proposals.WITH_CLIENT:
		proposals.record_view(doc)
	return {"status": doc.status}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # the token is the key - nosemgrep
@rate_limit(limit=10, seconds=60 * 60)
def accept(token: str, name: str, email: str, designation: str | None = None) -> dict:
	"""The client accepts (or signs the agreement), with their name and email (and designation
	when signing). Accepting again changes nothing."""
	doc = get_public(token)
	proposals.decide(doc, True, name, email, designation=designation)
	return {"status": doc.status}


@frappe.whitelist(allow_guest=True, methods=["POST"])  # the token is the key - nosemgrep
@rate_limit(limit=10, seconds=60 * 60)
def decline(token: str, reason: str | None = None, name: str | None = None, email: str | None = None) -> dict:
	"""The client declines, optionally saying why. Declining again changes nothing."""
	doc = get_public(token)
	proposals.decide(doc, False, name, email, reason)
	return {"status": doc.status}


@frappe.whitelist(allow_guest=True)  # the token is the key - nosemgrep
@rate_limit(limit=30, seconds=60 * 60)
def download_pdf(token: str, preview: str | None = None) -> None:
	"""The page as a PDF."""
	doc = get_public(token, bool(preview))
	frappe.local.response.filename = f"{doc.name}.pdf"
	frappe.local.response.filecontent = proposals.render_pdf(doc)
	frappe.local.response.type = "pdf"


@frappe.whitelist(allow_guest=True)  # the token is the key - nosemgrep
@rate_limit(limit=600, seconds=60 * 60)
def image(token: str, file: str) -> None:
	"""An image the proposal shows (a block's, or its letter head's private one), never any other
	file: the same "not found" otherwise. Drafts' too, as the preview shows them."""
	doc = get_public(token, preview=True)
	found = media.servable_file(doc, file)
	mime = media.file_mime(found) if found else None
	if not mime:
		frappe.throw(_("This image isn't available."), frappe.DoesNotExistError)
	frappe.local.response.filename = found.file_name
	frappe.local.response.filecontent = found.get_content()
	frappe.local.response.content_type = mime
	frappe.local.response.display_content_as = "inline"
	frappe.local.response.type = "download"


@frappe.whitelist(allow_guest=True, methods=["POST"])  # the token is the key - nosemgrep
@rate_limit(limit=60, seconds=60 * 60)
def save_answers(token: str, answers: str | dict) -> dict:
	"""The client's answers on a BRD's questionnaire ({question row: text}); a blank one clears
	it. The owner is told once per save."""
	doc = brd.by_questions_token(token)
	if not doc:
		frappe.throw(_("This questionnaire isn't available."), frappe.DoesNotExistError)
	answers = frappe.parse_json(answers) if isinstance(answers, str) else answers
	if not isinstance(answers, dict) or len(answers) > brd.MAX_QUESTIONS:
		frappe.throw(_("Send the answers as a set of fields."))
	changed = brd.save_answers(doc, answers)
	return {"saved": changed}
