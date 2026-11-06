"""Contact numbers of a lead (docs/lead-numbers.md): more than one per lead, none ever lost.

Telecallers hear a second number on a call (the decision-maker's, the office's). Each number is
a row of the Lead's `tbo_numbers` table (TBO Lead Number): its label, who it belongs to, its
status and who added it and how. The lead keeps its `mobile_no`, set to the main number on every
save, so reports, search and the older code keep working; `phone` and `whatsapp_no` keep what
they hold and any number found there becomes a row.

Changing the main number never deletes the old one: it stays as an "Old" row. Only a manager
removes a row. Every change leaves a note on the lead's timeline.

The doc-level helpers (`add_row`, `set_primary`, `set_status`, `remove_row`) change the Lead in
memory and queue timeline notes in `doc.flags.number_log`; the caller saves once and the Lead's
on_update hook (`write_log`) writes the notes.
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Right
from frappe.utils import cint, get_fullname, now_datetime

from tbo_smart.smart_leads.utils import normalise_phone, tel_link, whatsapp_link

NUMBER = "TBO Lead Number"
FIELD = "tbo_numbers"
MATCH_DIGITS = 10

LABELS = ("Mobile", "Office", "WhatsApp", "Decision-maker", "Other")
ACTIVE, NOT_REACHABLE, WRONG_NUMBER, OLD = "Active", "Not reachable", "Wrong number", "Old"
STATUSES = (ACTIVE, NOT_REACHABLE, WRONG_NUMBER, OLD)
# a number a caller may still try; Wrong number and Old are kept for the record
USABLE = (ACTIVE, NOT_REACHABLE)
SOURCES = ("Import", "Lead Bank", "Added on a call", "Manual")
# the lead's own number fields, and the label a number found in each gets
LEGACY_FIELDS = (("mobile_no", "Mobile"), ("phone", "Office"), ("whatsapp_no", "WhatsApp"))
ROW_FIELDS = [
	"name",
	"idx",
	"number",
	"digits",
	"label",
	"person",
	"designation",
	"is_primary",
	"status",
	"note",
	"source",
	"added_by",
	"added_on",
]
MAX_NUMBER = 40
MAX_TEXT = 140


# ---------- reading ----------


def rows_of(lead: str) -> list[frappe._dict]:
	"""The lead's number rows in the order they were added."""
	return frappe.get_all(
		NUMBER,
		filters={"parent": lead, "parenttype": "Lead", "parentfield": FIELD},
		fields=ROW_FIELDS,
		order_by="idx asc",
	)


def display_order(row) -> tuple:
	"""Main number first, then the ones still usable, then Old and Wrong numbers; each in the order added."""
	return (0 if cint(row.is_primary) else 1 if row.status in USABLE else 2, cint(row.idx))


def serialize(rows) -> list[dict]:
	"""[{id, number, e164, label, person, designation, is_primary, status, note, source, added_by,
	added_by_name, added_on, tel, whatsapp}] main number first, then usable ones, then Old and Wrong."""
	rows = sorted(rows, key=display_order)
	names = {row.added_by: get_fullname(row.added_by) for row in rows if row.added_by}
	return [
		{
			"id": row.name,
			"number": row.number,
			"e164": f"+{row.digits}" if row.digits else None,
			"label": row.label,
			"person": row.person or "",
			"designation": row.designation or "",
			"is_primary": cint(row.is_primary),
			"status": row.status,
			"note": row.note or "",
			"source": row.source,
			"added_by": row.added_by,
			"added_by_name": names.get(row.added_by),
			"added_on": row.added_on,
			"tel": tel_link(row.number),
			"whatsapp": whatsapp_link(row.number),
		}
		for row in rows
	]


def numbers_of(lead: str) -> list[dict]:
	return serialize(rows_of(lead))


def usable_by_lead(leads: list[str]) -> dict[str, list[dict]]:
	"""{lead: [{id, number, label, person, is_primary}]} the numbers a caller may try, the main
	number first; leads without a row are left out."""
	if not leads:
		return {}
	Row = frappe.qb.DocType(NUMBER)
	rows = (
		frappe.qb.from_(Row)
		.select(Row.parent, Row.name, Row.idx, Row.number, Row.label, Row.person, Row.is_primary, Row.status)
		.where((Row.parenttype == "Lead") & (Row.parentfield == FIELD) & Row.parent.isin(leads))
		.where(Row.status.isin(USABLE))
	).run(as_dict=True)
	found: dict[str, list] = {}
	for row in sorted(rows, key=display_order):
		found.setdefault(row.parent, []).append(
			{
				"id": row.name,
				"number": row.number,
				"label": row.label,
				"person": row.person or "",
				"is_primary": cint(row.is_primary),
			}
		)
	return found


def typed_numbers(lead: str) -> list[str]:
	"""Every number the lead has as typed, the main one first: its rows, then anything still only
	in its mobile, phone or WhatsApp field."""
	numbers = [row["number"] for row in numbers_of(lead)]
	values = frappe.db.get_value("Lead", lead, [field for field, _label in LEGACY_FIELDS], as_dict=True) or {}
	have = [normalise_phone(number) for number in numbers]
	for field, _label in LEGACY_FIELDS:
		digits = normalise_phone(values.get(field))
		if digits and not any(same_digits(digits, other) for other in have):
			numbers.append(values[field])
			have.append(digits)
	return numbers


def resolve(lead: str, number: str | None) -> str | None:
	"""The lead's number, as typed, that `number` is (spaces, +91 and a trunk 0 don't matter); None
	for blank. Throws for a number the lead doesn't have."""
	if not (number or "").strip():
		return None
	wanted = normalise_phone(number)
	for own in typed_numbers(lead):
		if wanted and same_digits(wanted, normalise_phone(own)):
			return own
	frappe.throw(_("Choose one of the lead's own numbers."))


# ---------- finding leads by number ----------


def same_digits(first: str | None, second: str | None) -> bool:
	"""Two normalised numbers are the same: equal, or equal in their last ten digits."""
	if not first or not second:
		return False
	return first == second or (
		min(len(first), len(second)) >= MATCH_DIGITS and first[-MATCH_DIGITS:] == second[-MATCH_DIGITS:]
	)


def number_criterion(lead_table, key: str):
	"""A query criterion on `lead_table` (the Lead): one of its number rows, whatever its status,
	ends in the digits `key`."""
	Row = frappe.qb.DocType(NUMBER)
	rows = (
		frappe.qb.from_(Row)
		.select(Row.parent)
		.where((Row.parenttype == "Lead") & (Right(Row.digits, len(key)) == key))
	)
	return lead_table.name.isin(rows)


def leads_with(number: str | None) -> list[str]:
	"""The leads with this number among their rows, whatever its status."""
	digits = normalise_phone(number)
	if not digits:
		return []
	Row = frappe.qb.DocType(NUMBER)
	key = digits[-MATCH_DIGITS:]
	return (
		frappe.qb.from_(Row)
		.select(Row.parent)
		.distinct()
		.where((Row.parenttype == "Lead") & (Right(Row.digits, len(key)) == key))
	).run(pluck=True)


# ---------- changing a lead's numbers in memory ----------


def table(doc) -> list:
	return doc.get(FIELD) or []


def row_with(doc, digits: str | None):
	return next((row for row in table(doc) if same_digits(row.digits, digits)), None)


def primary_of(doc):
	return next((row for row in table(doc) if cint(row.is_primary)), None)


def log(doc, text: str) -> None:
	doc.flags.setdefault("number_log", []).append(text)


def clean_number(value) -> tuple[str, str]:
	"""(the number as typed, tidied; its normalised digits). Throws for something that isn't a phone number."""
	text = " ".join(str(value or "").split())
	digits = normalise_phone(text)
	if not digits or len(text) > MAX_NUMBER:
		frappe.throw(_("{0} is not a phone number.").format(text or _("Nothing")))
	return text, digits


def clean_text(value) -> str:
	text = " ".join(str(value or "").split())
	if len(text) > MAX_TEXT:
		frappe.throw(_("Keep each detail under {0} characters.").format(MAX_TEXT))
	return text


def apply_details(row, label=None, person=None, designation=None, note=None) -> None:
	"""Set what was given (None leaves a field as it is; "" clears it)."""
	if label is not None:
		if label not in LABELS:
			frappe.throw(_("label must be one of {0}.").format(", ".join(LABELS)))
		row.label = label
	if person is not None:
		row.person = clean_text(person) or None
	if designation is not None:
		row.designation = clean_text(designation) or None
	if note is not None:
		row.note = (note or "").strip() or None


def add_row(
	doc,
	number,
	label: str | None = None,
	person=None,
	designation=None,
	make_primary: bool = False,
	note=None,
	source: str | None = None,
	quiet: bool = False,
):
	"""Add `number` to the lead (or bring back an Old or Wrong number it already has). A number the
	lead has in use is refused. The first usable number is the main one; `make_primary` makes this
	one the main number and the old main number stays as Old. `quiet`: leave no timeline note
	(a number found in the lead's own fields)."""
	text, digits = clean_number(number)
	user = get_fullname(frappe.session.user)
	row = row_with(doc, digits)
	if row and row.status in USABLE:
		frappe.throw(_("This lead already has {0}.").format(row.number))
	if row:
		row.status = ACTIVE
		if not quiet:
			log(doc, _("Number back in use: {0} by {1}").format(row.number, user))
	else:
		row = doc.append(
			FIELD,
			{
				"number": text,
				"digits": digits,
				"label": "Mobile",
				"status": ACTIVE,
				"source": source or number_source(doc),
				"added_by": frappe.session.user,
				"added_on": now_datetime(),
			},
		)
		if not quiet:
			log(doc, _("Number added: {0} by {1}").format(text, user))
	apply_details(row, label, person, designation, note)
	main = primary_of(doc)
	if make_primary or not main or main.status not in USABLE:
		set_primary(doc, row)
	return row


def set_primary(doc, row) -> None:
	"""Make `row` the main number. The old main number stays: as Old if it was still in use, else
	with the status it had (a Wrong number stays one)."""
	old = primary_of(doc)
	if old is row:
		return
	if old:
		old.is_primary = 0
		if old.status in USABLE:
			old.status = OLD
		log(
			doc,
			_("Primary changed from {0} to {1} by {2}").format(
				old.number, row.number, get_fullname(frappe.session.user)
			),
		)
	row.is_primary = 1
	if row.status not in USABLE:
		row.status = ACTIVE
	doc.mobile_no = row.number


def set_status(doc, row, status: str) -> None:
	"""Give `row` a status. A main number that is no longer usable hands over to the next usable one."""
	if status not in STATUSES:
		frappe.throw(_("status must be one of {0}.").format(", ".join(STATUSES)))
	if row.status != status:
		log(doc, _("{0} marked {1} by {2}").format(row.number, _(status), get_fullname(frappe.session.user)))
	row.status = status
	if status not in USABLE and cint(row.is_primary):
		successor = next((other for other in table(doc) if other is not row and other.status in USABLE), None)
		if successor:
			set_primary(doc, successor)


def remove_row(doc, row) -> None:
	"""Take the number off the lead for good, from its fields too so it isn't added back."""
	digits = row.digits
	log(doc, _("Number removed: {0} by {1}").format(row.number, get_fullname(frappe.session.user)))
	doc.remove(row)
	for field, _label in LEGACY_FIELDS:
		if same_digits(normalise_phone(doc.get(field)), digits):
			doc.set(field, None)


def has_usable(lead: str) -> bool:
	"""The lead has a number a caller may still try."""
	return bool(
		frappe.db.exists(
			NUMBER, {"parent": lead, "parenttype": "Lead", "parentfield": FIELD, "status": ("in", USABLE)}
		)
	)


def usable_left(doc) -> bool:
	return any(row.status in USABLE for row in table(doc))


def number_source(doc) -> str:
	return doc.flags.get("number_source") or ("Import" if frappe.flags.in_import else "Manual")


# ---------- hooks ----------


def sync(doc, method=None) -> None:
	"""doc_events: Lead validate. Tidy the rows, turn numbers typed into mobile_no, phone or
	WhatsApp into rows, keep exactly one main number and copy it to mobile_no. Typing a new
	mobile_no on a lead that has rows makes it the main number (the old one stays as Old)."""
	if not doc.meta.has_field(FIELD):
		return
	seen = []
	for row in table(doc):
		row.number, row.digits = clean_number(row.number)
		if any(same_digits(row.digits, other) for other in seen):
			frappe.throw(_("{0} is listed twice.").format(row.number))
		seen.append(row.digits)
		row.label = row.label or "Mobile"
		row.status = row.status or ACTIVE
		if not row.added_on:
			row.added_on, row.added_by = now_datetime(), row.added_by or frappe.session.user
			row.source = row.source or number_source(doc)
	before = doc.get_doc_before_save()
	main = primary_of(doc)
	typed = normalise_phone(doc.mobile_no)
	if (
		table(doc)
		and before
		and typed
		and doc.mobile_no != before.mobile_no
		and not (main and same_digits(main.digits, typed))
	):
		known = row_with(doc, typed)
		if known:
			set_primary(doc, known)
		else:
			add_row(doc, doc.mobile_no, make_primary=True)
	for field, label in LEGACY_FIELDS:
		digits = normalise_phone(doc.get(field))
		if digits and not row_with(doc, digits):
			add_row(doc, doc.get(field), label=label, quiet=True)
	keep_one_primary(doc)
	main = primary_of(doc)
	if main:
		doc.mobile_no = main.number


def keep_one_primary(doc) -> None:
	"""One main number, and a usable one while any number is usable."""
	rows = table(doc)
	flagged = [row for row in rows if cint(row.is_primary)]
	if len(flagged) > 1:
		best = next((row for row in flagged if row.status in USABLE), flagged[0])
		for row in flagged:
			row.is_primary = int(row is best)
	main = primary_of(doc)
	if rows and (not main or main.status not in USABLE):
		successor = next((row for row in rows if row.status in USABLE), None)
		if successor:
			set_primary(doc, successor)
		elif not main:
			rows[0].is_primary = 1


def write_log(doc, method=None) -> None:
	"""doc_events: Lead on_update. The notes the number changes queued, on the lead's timeline."""
	from tbo_smart.sales.activities import add_note

	for text in doc.flags.pop("number_log", None) or []:
		add_note("Lead", doc.name, text)


# ---------- changes by number (the API) ----------


def row_by_id(doc, number_id: str):
	row = next((row for row in table(doc) if row.name == number_id), None)
	if not row:
		frappe.throw(_("{0} is not a number of this lead.").format(number_id), frappe.DoesNotExistError)
	return row


def writable_lead(lead: str):
	doc = frappe.get_doc("Lead", lead)
	doc.check_permission("write")
	return doc


def can_remove() -> bool:
	"""Only a manager takes a number off a lead."""
	from tbo_smart.sales import departments

	return departments.can_manage()


def add_lead_number(
	lead: str,
	number: str,
	label: str | None = None,
	person: str | None = None,
	designation: str | None = None,
	make_primary: bool = False,
	note: str | None = None,
	on_call: bool = False,
) -> dict:
	"""Add a number to a lead the session user may write. Returns {numbers, duplicates}:
	`duplicates` the other leads that have the number (a warning, never a refusal)."""
	from tbo_smart.sales import duplicates

	doc = writable_lead(lead)
	add_row(
		doc,
		number,
		label,
		person,
		designation,
		bool(make_primary),
		note,
		source="Added on a call" if on_call else "Manual",
	)
	doc.save()
	return {
		"numbers": numbers_of(lead),
		"duplicates": duplicates.find_duplicates(None, number, None, lead),
	}


def update_lead_number(
	lead: str,
	number_id: str,
	status: str | None = None,
	label: str | None = None,
	person: str | None = None,
	designation: str | None = None,
	make_primary: bool = False,
	note: str | None = None,
) -> dict:
	"""Change a number's status, label, person, designation or note, or make it the main number.
	A number that stops being usable hands the main place to the next usable one."""
	doc = writable_lead(lead)
	row = row_by_id(doc, number_id)
	apply_details(row, label, person, designation, note)
	if status:
		set_status(doc, row, status)
	if make_primary:
		set_primary(doc, row)
	doc.save()
	return {"numbers": numbers_of(lead)}


def remove_lead_number(lead: str, number_id: str) -> dict:
	"""Take a number off the lead; managers only, a salesperson marks it Old or Wrong number instead."""
	if not can_remove():
		frappe.throw(
			_("Only a manager can remove a number. Mark it Old or Wrong number instead."),
			frappe.PermissionError,
		)
	doc = writable_lead(lead)
	remove_row(doc, row_by_id(doc, number_id))
	doc.save()
	return {"numbers": numbers_of(lead)}


def report_wrong_number(lead: str, number: str | None = None) -> bool:
	"""A call found `number` (default: the main one) wrong: mark it Wrong number. True when no
	usable number is left on the lead, so the lead itself is the wrong number. A lead without
	number rows has nothing to mark and counts as wrong. The caller has checked write access."""
	doc = frappe.get_doc("Lead", lead)
	if not table(doc):
		return True
	digits = normalise_phone(number)
	row = row_with(doc, digits) if digits else primary_of(doc)
	if not row:
		frappe.throw(_("Choose one of the lead's own numbers."))
	set_status(doc, row, WRONG_NUMBER)
	doc.save(ignore_permissions=True)
	return not usable_left(doc)
