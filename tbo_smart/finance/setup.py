"""Custom fields of the finance workflow, created in code on install and on every migrate so
every site gets the same fields (docs/crm-finance-workflow.md §3.1)."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

STAGES = (
	"Onboarding",
	"Quotation",
	"Invoiced",
	"Collecting",
	"Collected",
	"Settling",
	"Reconciled",
	"Closed",
	"On Exception",
)


def get_finance_fields() -> dict:
	deal_link = {"fieldname": "crm_deal", "fieldtype": "Link", "options": "CRM Deal", "label": "CRM Deal"}
	return {
		"CRM Deal": [
			{
				"fieldname": "tbo_company",
				"fieldtype": "Link",
				"options": "Company",
				"label": "Invoicing company",
				"insert_after": "erpnext_customer",
				"description": "The company that quotes and invoices this deal. Defaults to ERPNext CRM Settings → ERPNext company.",
			},
			{
				"fieldname": "tbo_stage",
				"fieldtype": "Select",
				"options": "\n" + "\n".join(STAGES),
				"label": "Post-sale stage",
				"insert_after": "tbo_company",
				"read_only": 1,
				"no_copy": 1,
				"description": "Computed from the deal's quotations, orders, invoices and payments.",
			},
			{
				"fieldname": "tbo_onboarding_complete",
				"fieldtype": "Check",
				"label": "Onboarding complete",
				"insert_after": "tbo_stage",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "tbo_closed",
				"fieldtype": "Check",
				"label": "Finance closed",
				"insert_after": "tbo_onboarding_complete",
				"read_only": 1,
				"no_copy": 1,
			},
		],
		"Sales Order": [{**deal_link, "insert_after": "customer_name", "read_only": 1}],
		"Sales Invoice": [{**deal_link, "insert_after": "customer_name"}],
		"Payment Entry": [
			{
				**deal_link,
				"insert_after": "party_name",
				"read_only": 1,
				"description": "Set from the references; blank when they belong to different deals.",
			}
		],
	}


def create_finance_fields() -> None:
	create_custom_fields(get_finance_fields(), ignore_validate=True)


def after_migrate() -> None:
	create_finance_fields()
