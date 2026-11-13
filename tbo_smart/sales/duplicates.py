"""Duplicate leads (docs/lead-departments.md §6): stop two people working the same enquiry.

A lead matches on email (case-insensitive), phone (the last 10 digits, across every number the
lead has: its number rows, whatever their status, and its mobile, phone and WhatsApp fields) or
company (case-insensitive, exact). The search covers every lead, whoever may see it, and returns only the fields that say whose work it is: the point is that a
salesperson learns the lead exists and who owns it, not that they may open it.

Email and phone matches block a new lead; a company match alone is only shown.

One lead per number: whatever way a lead comes in (the hub, the app, the desk, an import, an
integration), a new lead or a number new on a lead is refused when another lead has it
(`refuse_taken_numbers`), except the numbers many businesses share (TBO Smart Settings).
"""

import re

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Lower, Trim
from frappe.utils import get_datetime

from tbo_smart.api.mobile.crm import any_of, phone_criterion, text_criterion
from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.sales import SETTINGS, numbers

MAX_MATCHES = 5
PHONE_DIGITS = 10
MIN_PHONE_DIGITS = 7
PHONE_FIELDS = ("mobile_no", "phone", "whatsapp_no")
MATCH_FIELDS = ("name", "lead_name", "company_name", "email_id", "mobile_no", "lead_owner", "tbo_stage")


def match_keys(email: str | None, phone: str | None, company: str | None) -> frappe._dict:
	"""Normalised search keys: lowercase email, the phone's last 10 digits (7 at least), and the
	lowercase company; each None when it is empty or too short to compare."""
	key = phone_key(phone)
	return frappe._dict(
		email=(email or "").strip().lower() or None,
		# a shared number says nothing about who the lead is
		phone=key if key and key not in shared_numbers() else None,
		company=(company or "").strip().lower() or None,
	)


def phone_key(phone: str | None) -> str | None:
	"""A number's last 10 digits (7 at least), whatever spaces, +91 or trunk 0 it is typed with."""
	digits = re.sub(r"\D", "", phone or "")
	return digits[-PHONE_DIGITS:] if len(digits) >= MIN_PHONE_DIGITS else None


def shared_numbers() -> set[str]:
	"""The numbers many businesses share (a lead portal's virtual number), from TBO Smart
	Settings, by their last 10 digits: they may be on any number of leads."""
	text = frappe.db.get_single_value(SETTINGS, "shared_numbers") or ""
	return {key for line in text.splitlines() if (key := phone_key(line))}


def newer_duplicates(leads: list[str]) -> set[str]:
	"""The leads of `leads` with a number (the last 10 digits, on any number row whatever its
	status) that a lead created before them has too: copies of an older lead, made before one lead
	per number. Today's calls leaves them out so the client is called once, as the oldest lead.
	A shared number says nothing about who the lead is, so it does not count."""
	if not leads:
		return set()
	Row = frappe.qb.DocType(numbers.NUMBER)
	own = (
		frappe.qb.from_(Row)
		.select(Row.parent, Row.digits)
		.where((Row.parenttype == "Lead") & Row.parent.isin(list(leads)))
	).run()
	shared = shared_numbers()
	keys_of: dict[str, set[str]] = {}
	for lead, digits in own:
		key = phone_key(digits)
		if key and key not in shared:
			keys_of.setdefault(lead, set()).add(key)
	keys = {key for found in keys_of.values() for key in found}
	if not keys:
		return set()
	Lead = frappe.qb.DocType("Lead")
	key = numbers.Right(Row.digits, PHONE_DIGITS)
	holders = (
		frappe.qb.from_(Row)
		.join(Lead)
		.on(Lead.name == Row.parent)
		.select(key.as_("key"), Lead.name, Lead.creation)
		.where((Row.parenttype == "Lead") & key.isin(list(keys)))
	).run(as_dict=True)
	oldest: dict[str, tuple] = {}
	for row in holders:
		mark = (get_datetime(row.creation), row.name)
		if row.key not in oldest or mark < oldest[row.key]:
			oldest[row.key] = mark
	return {
		lead
		for lead, found in keys_of.items()
		if any(oldest[key][1] != lead for key in found if key in oldest)
	}


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
		checks["phone"] = phone_criterion(
			[Lead[field] for field in PHONE_FIELDS], keys.phone
		) | numbers.number_criterion(Lead, keys.phone)
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
	the response as `matches`. A phone match says a number belongs to one lead, as confirming the
	duplicate won't get past it (`refuse_taken_numbers`)."""
	matches = find_duplicates(email, phone, company)
	taken = next((match for match in matches if "phone" in match["matched_on"]), None)
	if taken:
		frappe.response["matches"] = matches
		frappe.throw(number_taken_message(taken, phone), frappe.DuplicateEntryError)
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


def refuse_taken_numbers(doc, method=None) -> None:
	"""doc_events: Lead validate. One lead per number: a new lead, or a number new on a lead, is
	refused when another lead already has it (the last 10 digits, on any of that lead's numbers,
	whatever their status), however the lead comes in. The numbers it had before stay, so an
	old duplicate can still be edited; a shared number may be on any lead; the CRM cutover
	(`tbo_cutover`) copies leads as they were; a merge (`doc.flags.merging`, the lead merged in)
	brings that lead's numbers along."""
	if frappe.flags.get("tbo_cutover"):
		return
	before = None if doc.is_new() else doc.get_doc_before_save()
	had = numbers_on(before) if before else {}
	for key, number in numbers_on(doc).items():
		if key in had:
			continue
		match = phone_match(number, doc.name, doc.flags.get("merging"))
		if match:
			frappe.throw(number_taken_message(match, number), frappe.DuplicateEntryError)


def numbers_on(doc) -> dict[str, str]:
	"""{last 10 digits: the number as typed} for every number of the lead: its mobile, phone and
	WhatsApp fields and its number rows."""
	typed = [doc.get(field) for field in PHONE_FIELDS]
	typed += [row.number for row in doc.get(numbers.FIELD) or []]
	found = {}
	for number in typed:
		key = phone_key(number)
		if key:
			found.setdefault(key, number.strip())
	return found


def phone_match(number: str, exclude: str | None, merging: str | None) -> dict | None:
	"""Another lead that has this number, other than the one being merged in."""
	return next(
		(
			match
			for match in find_duplicates(None, number, None, exclude)
			if "phone" in match["matched_on"] and match["name"] != merging
		),
		None,
	)


def number_taken_message(match: dict, number: str) -> str:
	"""Which lead has the number and whose it is, by name only: whoever is told (the app, an
	integration) may not see that lead, so never the owner's user id."""
	owner = _("no owner")
	if match["lead_owner"]:
		names = get_full_names([match["lead_owner"]], fallback_to_id=False)
		owner = names.get(match["lead_owner"]) or _("a teammate")
	return _(
		"Possible duplicate of {0} ({1}): it already has the number {2}, and a number belongs to one lead."
	).format(match["name"], owner, number)
