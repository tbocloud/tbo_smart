"""The client's proposal page, /proposal/<token> (docs/proposals.md §5).

Served without login: the token is the only key, and the page shows only what
`proposals.page_context` gives it. `?preview=1` shows a draft too and records no view.
"""

import frappe

from tbo_smart.lead_pipeline import proposals

no_cache = 1


def get_context(context):
	preview = bool(frappe.form_dict.get("preview"))
	doc = proposals.public_doc(frappe.form_dict.get("token"), preview)
	if not doc:
		raise frappe.DoesNotExistError
	context.proposal = proposals.page_context(doc, preview)
	context.pdf = False
	context.csrf_token = frappe.sessions.get_csrf_token() if frappe.session.user != "Guest" else ""
	return context
