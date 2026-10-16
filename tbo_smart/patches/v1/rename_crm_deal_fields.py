"""The deal link on tbo_smart's own doctypes was `crm_deal` (a Frappe CRM CRM Deal); it is
`opportunity` (ERPNext's Opportunity) since the switch to ERPNext's records
(docs/erpnext-lead-cutover.md). Deal names are kept by the cutover, so the values stay valid."""

from frappe.model.utils.rename_field import rename_field

DOCTYPES = ("TBO Finance Exception", "TBO Fund Allocation", "TBO Fund Allocation Line")


def execute():
	for doctype in DOCTYPES:
		rename_field(doctype, "crm_deal", "opportunity")
