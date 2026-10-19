"""The client's questionnaire for a BRD, /questions/<token> (docs/client-documents.md §5).

Served without login: the token (the BRD's own questionnaire key, not its page's) is the only key,
and the page shows only the questions and the client's own answers, never the draft BRD.
"""

import frappe

from tbo_smart.lead_pipeline import brd

no_cache = 1


def get_context(context):
	doc = brd.by_questions_token(frappe.form_dict.get("token"))
	if not doc:
		raise frappe.DoesNotExistError
	context.q = brd.questionnaire_context(doc)
	context.pdf = False
	context.csrf_token = frappe.sessions.get_csrf_token() if frappe.session.user != "Guest" else ""
	return context
