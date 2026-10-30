"""The two Lead fields call batches keep (docs/call-batches.md §9), made on every migrate."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from tbo_smart.sales import LEAD, erpnext_installed


def after_migrate() -> None:
	if not erpnext_installed():
		return
	create_custom_fields(
		{
			LEAD: [
				{
					"fieldname": "tbo_callback_at",
					"fieldtype": "Datetime",
					"label": "Call back at",
					"insert_after": "tbo_lost_notes",
					"hidden": 1,
					"read_only": 1,
					"no_copy": 1,
					"description": "When the person asked to be called back (daily call batches).",
				},
				{
					"fieldname": "tbo_wrong_number",
					"fieldtype": "Check",
					"label": "Wrong number",
					"insert_after": "tbo_callback_at",
					"read_only": 1,
					"no_copy": 1,
					"description": "A caller reported the number as wrong (daily call batches).",
				},
			]
		},
		ignore_validate=True,
	)
