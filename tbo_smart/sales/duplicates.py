"""Duplicate leads (docs/lead-departments.md §6): stop two people working the same enquiry.

A lead matches on email (case-insensitive), phone (the last 10 digits, across the lead's mobile,
phone and WhatsApp numbers) or company (case-insensitive, exact). The search covers every lead,
whoever may see it, and returns only the fields that say whose work it is: the point is that a
salesperson learns the lead exists and who owns it, not that they may open it.

Email and phone matches block a new lead; a company match alone is only shown.
"""

import re

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Lower, Trim

from tbo_smart.api.mobile.crm import any_of, phone_criterion, text_criterion
from tbo_smart.api.mobile.utils import get_full_names

MAX_MATCHES = 5
PHONE_DIGITS = 10
MIN_PHONE_DIGITS = 7
PHONE_FIELDS = ("mobile_no", "phone", "whatsapp_no")
MATCH_FIELDS = ("name", "lead_name", "company_name", "email_id", "mobile_no", "lead_owner", "tbo_stage")


def match_keys(email: str | None, phone: str | None, company: str | None) -> frappe._dict:
	"""Normalised search keys: lowercase email, the phone's last 10 digits (7 at least), and the
	lowercase company; each None when it is empty or too short to compare."""
	digits = re.sub(r"\D", "", phone or "")
	return frappe._dict(
		email=(email or "").strip().lower() or None,
		phone=digits[-PHONE_DIGITS:] if len(digits) >= MIN_PHONE_DIGITS else None,
		company=(company or "").strip().lower() or None,
	)


def find_duplicates(
	email: str | None = None,
	phone: str | None = None,
	company: str | None = None,
	exclude: str | None = None,
) -> list[dict]:
	"""Up to 5 leads matching, email and phone matches first then the newest:
	[{name, lead_name, company_name, email_id, mobile_no, lead_owner, owner_name, tbo_stage,
	matched_on}], matched_on being some of "email", "phone", "company". No permission check."""
	keys = match_keys(email, phone, company)
	Lead = frappe.qb.DocType("Lead")
	checks = {}
	if keys.email:
		checks["email"] = text_criterion([Lead.email_id], keys.email)
	if keys.phone:
		checks["phone"] = phone_criterion([Lead[field] for field in PHONE_FIELDS], keys.phone)
	if keys.company:
		checks["company"] = Lower(Trim(Lead.company_name)) == keys.company
	if not checks:
		return []
	hard = any_of([check for key, check in checks.items() if key != "company"])
	query = frappe.qb.from_(Lead).select(
		*(Lead[field] for field in MATCH_FIELDS),
		*(Case().when(check, 1).else_(0).as_(f"on_{key}") for key, check in checks.items()),
	)
	query = query.where(any_of(list(checks.values())))
	if exclude:
		query = query.where(Lead.name != exclude)
	if keys.email or keys.phone:
		query = query.orderby(Case().when(hard, 1).else_(0), order=frappe.qb.desc)
	rows = query.orderby(Lead.modified, order=frappe.qb.desc).limit(MAX_MATCHES).run(as_dict=True)
	names = get_full_names({row.lead_owner for row in rows if row.lead_owner})
	return [
		{
			**{field: row[field] for field in MATCH_FIELDS},
			"owner_name": names.get(row.lead_owner),
			"matched_on": [key for key in checks if row.get(f"on_{key}")],
		}
		for row in rows
	]


def blocking(matches: list[dict]) -> dict | None:
	"""The first match on email or phone: those are the same person, a company alone is not."""
	return next((match for match in matches if set(match["matched_on"]) & {"email", "phone"}), None)


def duplicate_message(match: dict) -> str:
	owner = match["owner_name"] or match["lead_owner"] or _("no owner")
	return _("Possible duplicate of {0} ({1})").format(match["name"], owner)


def refuse_duplicate(email: str | None, phone: str | None, company: str | None) -> None:
	"""Raise DuplicateEntryError when a lead already has this email or phone; the matches go in
	the response as `matches`."""
	matches = find_duplicates(email, phone, company)
	match = blocking(matches)
	if match:
		frappe.response["matches"] = matches
		frappe.throw(duplicate_message(match), frappe.DuplicateEntryError)


def skip_import_duplicates(doc, method=None) -> None:
	"""doc_events: Lead validate. A lead coming in through Data Import is refused when its email
	or phone belongs to a lead already, so the import skips the row and lists it as an error. The
	CRM cutover (flag `tbo_cutover`) copies leads as they were, duplicates included."""
	if not (frappe.flags.in_import and not frappe.flags.get("tbo_cutover") and doc.is_new()):
		return
	match = blocking(
		find_duplicates(doc.get("email_id"), doc.get("mobile_no") or doc.get("phone"), None, doc.name)
	)
	if match:
		frappe.throw(duplicate_message(match), frappe.DuplicateEntryError)
