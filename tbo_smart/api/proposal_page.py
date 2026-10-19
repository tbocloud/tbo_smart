"""The client's side of a proposal (docs/proposals.md §5): what the page at /proposal/<token>
calls. No login: the token is the only key, each call is rate limited, and every answer is the
same "not found" for a token that is malformed, unknown, a draft or switched off."""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from tbo_smart.lead_pipeline import proposals


def get_public(token: str, preview: bool = False):
	doc = proposals.public_doc(token, preview)
	if not doc:
		frappe.throw(_("This proposal isn't available."), frappe.DoesNotExistError)
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
def accept(token: str, name: str, email: str) -> dict:
	"""The client accepts, with their name and email. Accepting again changes nothing."""
	doc = get_public(token)
	proposals.decide(doc, True, name, email)
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
