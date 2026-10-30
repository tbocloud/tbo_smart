"""The website's lead form (docs/website-lead-form.md): one public endpoint.

Guests may call it, so it never returns lead data, only `{"ok": true}`; a honeypot field and a
per-IP rate limit keep bots out. An enquiry from someone who is already a lead (same email or
phone) is not made twice: it is added as a note on the existing lead and its owner is told.
"""

import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, validate_email_address

from tbo_smart import lead_source
from tbo_smart.sales import LEAD, SETTINGS, erpnext_installed
from tbo_smart.sales.activities import add_note
from tbo_smart.sales.departments import DEPARTMENT, DEPARTMENT_USER
from tbo_smart.sales.duplicates import blocking, find_duplicates
from tbo_smart.sales.stages import system_save
from tbo_smart.smart_leads.notify import notify

DEFAULT_SOURCE = "Website"
FORM_LIMIT = 5
FORM_WINDOW = 60 * 60
MAX_TEXT = 140
MAX_MESSAGE = 2000
MIN_PHONE_DIGITS = 7


@frappe.whitelist(allow_guest=True, methods=["POST"])  # public form: honeypot, rate limit - nosemgrep
@rate_limit(limit=FORM_LIMIT, seconds=FORM_WINDOW)
def submit_website_lead(
	name: str | None = None,
	email: str | None = None,
	phone: str | None = None,
	company: str | None = None,
	message: str | None = None,
	department: str | None = None,
	source: str | None = DEFAULT_SOURCE,
	hp: str | None = None,
	lead_name: str | None = None,
	company_name: str | None = None,
	email_id: str | None = None,
	mobile_no: str | None = None,
) -> dict:
	"""A website enquiry becomes a lead (round robin gives it an owner when its department has
	that on) or, for a person we already have, a note on their lead. `hp` is the honeypot: a
	real visitor leaves it empty, and a form filled in with it is dropped without a word. Needs
	`name` and an `email` or `phone`; `lead_name`, `company_name`, `email_id` and `mobile_no`
	are accepted as other names for `name`, `company`, `email` and `phone`. Returns {"ok": true}."""
	if (hp or "").strip() or not erpnext_installed():
		return {"ok": True}
	name, email, phone = clean(name or lead_name), clean(email or email_id), clean(phone or mobile_no)
	company, message = clean(company or company_name), clean(message, MAX_MESSAGE)
	if not name:
		frappe.throw(_("Please tell us your name."))
	if not (email or phone):
		frappe.throw(_("Please give an email or a phone number."))
	if email:
		validate_email_address(email, throw=True)
	if phone and len(re.sub(r"\D", "", phone)) < MIN_PHONE_DIGITS:
		frappe.throw(_("That phone number looks too short."))
	existing = blocking(find_duplicates(email, phone, None))
	if existing:
		enquired_again(existing, message)
	else:
		create_lead(name, email, phone, company, message, department, clean(source) or DEFAULT_SOURCE)
	return {"ok": True}


def clean(value: str | None, limit: int = MAX_TEXT) -> str:
	return (value or "").strip()[:limit]


def enquired_again(existing: dict, message: str) -> None:
	"""A note on the lead we already have, and a notice to its owner (or its department's heads)."""
	add_note(LEAD, existing["name"], _("Website enquiry again: {0}").format(message or "-"))
	people = [existing["lead_owner"]] if existing["lead_owner"] else heads_of(existing["name"])
	notify(
		people,
		_("{0} sent a website enquiry again.").format(existing["lead_name"] or existing["name"]),
		LEAD,
		existing["name"],
	)


def heads_of(lead: str) -> list[str]:
	department = frappe.db.get_value(LEAD, lead, "tbo_department")
	if not department:
		return []
	return frappe.get_all(
		DEPARTMENT_USER, filters={"parent": department, "parentfield": "heads"}, pluck="user"
	)


def active_department(department: str | None) -> str | None:
	"""The department asked for when it is active, else TBO Smart Settings' default."""
	for candidate in (department, frappe.db.get_single_value(SETTINGS, "website_lead_department")):
		if candidate and cint(frappe.db.get_value(DEPARTMENT, candidate, "is_active")):
			return candidate
	return None


def get_lead_source(source: str) -> str:
	"""The lead source called `source`; anything unknown becomes "Website", made when missing."""
	if lead_source.exists(source):
		return source
	return lead_source.ensure(DEFAULT_SOURCE)


def create_lead(name, email, phone, company, message, department, source) -> None:
	first, _space, last = name.partition(" ")
	lead = frappe.new_doc(LEAD)
	lead.update(
		{
			"first_name": first,
			"last_name": last.strip(),
			"email_id": email or None,
			"mobile_no": phone or None,
			"company_name": company or None,
			lead_source.field(): get_lead_source(source),
			"tbo_department": active_department(department),
		}
	)
	lead.flags.ownerless = True
	system_save(lead, ignore_permissions=True)
	if message:
		add_note(LEAD, lead.name, _("Website enquiry: {0}").format(message))
