"""What telephony needs on every migrate (docs/telephony-myoperator.md §3)."""

import frappe

from tbo_smart.sales import erpnext_installed
from tbo_smart.telephony import LEAD_SOURCE


def after_migrate() -> None:
	"""The Lead Source new caller leads get."""
	if erpnext_installed() and not frappe.db.exists("Lead Source", LEAD_SOURCE):
		frappe.get_doc({"doctype": "Lead Source", "source_name": LEAD_SOURCE}).insert(ignore_permissions=True)
