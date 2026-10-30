"""Lead sources on ERPNext v15 and v16.

ERPNext v16 replaced the "Lead Source" DocType with Frappe's "UTM Source", and renamed the source field on
Lead, Opportunity, Quotation and Sales Order from `source` to `utm_source`. Its patch
`v15_0.migrate_to_utm_analytics` copies the existing Lead Sources into UTM Source and deletes the old
DocType. tbo_smart runs on both versions, so every use of a lead's source asks here. Values read through
`select()` keep the key `source`, so API responses don't change.
"""

import frappe


def on_utm() -> bool:
	"""True from ERPNext v16 on, where a lead's source is a UTM Source."""
	return frappe.get_meta("Lead").has_field("utm_source")


def doctype() -> str:
	return "UTM Source" if on_utm() else "Lead Source"


def field() -> str:
	"""The source field on Lead and Opportunity."""
	return "utm_source" if on_utm() else "source"


def select() -> str:
	"""The source field for a `fields` list, read under the key `source`."""
	return "utm_source as source" if on_utm() else "source"


def fields(names: list[str]) -> list[str]:
	"""A `fields` list with "source" read from this version's field."""
	return [select() if name == "source" else name for name in names]


def exists(name: str | None) -> bool:
	return bool(name) and bool(frappe.db.exists(doctype(), name))


def names(check_permissions: bool = False) -> list[str]:
	"""Every source, by name."""
	get = frappe.get_list if check_permissions else frappe.get_all
	return get(doctype(), pluck="name", order_by="name asc")


def ensure(name: str) -> str:
	"""The source `name`, made when the site doesn't have it."""
	if not exists(name):
		if on_utm():
			frappe.get_doc({"doctype": "UTM Source", "name": name}).insert(ignore_permissions=True)
		else:
			frappe.get_doc({"doctype": "Lead Source", "source_name": name}).insert(ignore_permissions=True)
	return name
