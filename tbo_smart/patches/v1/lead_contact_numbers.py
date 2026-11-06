"""Every lead's mobile, phone and WhatsApp number become rows of its number table
(docs/lead-numbers.md). Leads that already have rows are left alone, so the patch can run again."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from tbo_smart.sales import erpnext_installed, numbers
from tbo_smart.smart_leads.setup import get_lead_fields
from tbo_smart.smart_leads.utils import normalise_phone


def execute():
	if not erpnext_installed():
		return
	# the table field is made after migrate's patches on a first run; the rows need it now
	create_custom_fields(get_lead_fields(), ignore_validate=True)
	have_rows = set(frappe.get_all(numbers.NUMBER, filters={"parenttype": "Lead"}, pluck="parent"))
	leads = frappe.get_all(
		"Lead",
		fields=["name", "owner", "creation", *(field for field, _label in numbers.LEGACY_FIELDS)],
		limit_page_length=0,
	)
	for lead in leads:
		if lead.name not in have_rows:
			add_rows(lead)


def add_rows(lead) -> None:
	"""A row for each different number in the lead's fields, the mobile first and the main one."""
	seen = []
	for field, label in numbers.LEGACY_FIELDS:
		digits = normalise_phone(lead.get(field))
		if not digits or any(numbers.same_digits(digits, other) for other in seen):
			continue
		seen.append(digits)
		frappe.get_doc(
			{
				"doctype": numbers.NUMBER,
				"name": frappe.generate_hash(length=10),
				"parent": lead.name,
				"parenttype": "Lead",
				"parentfield": numbers.FIELD,
				"idx": len(seen),
				"number": " ".join(lead[field].split()),
				"digits": digits,
				"label": label,
				"is_primary": int(len(seen) == 1),
				"status": numbers.ACTIVE,
				"source": "Import",
				"added_by": lead.owner,
				"added_on": lead.creation,
			}
		).db_insert()
