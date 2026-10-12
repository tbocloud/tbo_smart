"""Exceptions raised by the system (docs/crm-finance-workflow.md §3.5)."""

import frappe

DOCTYPE = "TBO Finance Exception"


def raise_exception(dedupe_key: str | None = None, **fields) -> str | None:
	"""Insert a System exception unless one with the same `dedupe_key` is already open,
	waiting or approved (a rejected one can be raised again). Returns its name, or None when
	it already existed."""
	if dedupe_key and frappe.db.exists(
		DOCTYPE, {"dedupe_key": dedupe_key, "status": ("in", ["Open", "Pending Approval", "Approved"])}
	):
		return None
	doc = frappe.get_doc({"doctype": DOCTYPE, "source": "System", "dedupe_key": dedupe_key, **fields})
	doc.insert(ignore_permissions=True)
	return doc.name
