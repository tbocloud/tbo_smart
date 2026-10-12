"""Custom fields, roles, property setters and email templates of the finance workflow, created
in code on install and on every migrate so every site gets the same ones
(docs/crm-finance-workflow.md §3.1). None of them changes behaviour while a phase is off.
"""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

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


REMINDER_STEPS = ("D-3", "D0", "D+7", "D+15", "D+30", "Weekly")
TASK_TYPES = ("Sales follow-up", "Collection", "Onboarding")
FINANCE_ROLES = ("TBO Finance Approver", "TBO Management")
COLLECTIONS_VIEW = "Collections"
# English reminder emails (open question Q14). Finance edits the wording in Email Template.
EMAIL_TEMPLATES = {
	"TBO Payment Due Soon": (
		"Payment due on {{ due_date }}: invoice {{ invoice }}",
		"<p>Dear {{ customer_name }},</p><p>This is a friendly reminder that {{ amount }} on invoice "
		"{{ invoice }} is due on {{ due_date }}. The invoice is attached.</p>"
		"<p>If you have already paid, thank you, and please ignore this message.</p><p>{{ company }}</p>",
	),
	"TBO Payment Due Today": (
		"Payment due today: invoice {{ invoice }}",
		"<p>Dear {{ customer_name }},</p><p>{{ amount }} on invoice {{ invoice }} is due today, "
		"{{ due_date }}. The invoice is attached.</p><p>{{ company }}</p>",
	),
	"TBO Payment Reminder": (
		"Reminder: invoice {{ invoice }} is overdue",
		"<p>Dear {{ customer_name }},</p><p>{{ amount }} on invoice {{ invoice }} was due on "
		"{{ due_date }} and is now {{ days_overdue }} days overdue. Please arrange payment, or reply "
		"to let us know when to expect it.</p><p>{{ company }}</p>",
	),
}
DUNNING_TYPES = {
	"Reminder 2": "We have not yet received payment of the amounts below. Please settle them within seven days.",
	"Final Notice": "This is our final notice for the overdue amounts below. Please settle them immediately to avoid further action.",
}


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
			{
				"fieldname": "tbo_accounts_owner",
				"fieldtype": "Link",
				"options": "User",
				"label": "Accounts owner",
				"insert_after": "tbo_closed",
				"description": "Accounts person who owns collection on this deal.",
			},
		],
		"Payment Schedule": [
			{
				"fieldname": "tbo_last_reminder",
				"fieldtype": "Select",
				"options": "\n" + "\n".join(REMINDER_STEPS),
				"label": "Last reminder",
				"insert_after": "outstanding",
				"read_only": 1,
				"allow_on_submit": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "tbo_last_reminder_on",
				"fieldtype": "Date",
				"label": "Last reminder on",
				"insert_after": "tbo_last_reminder",
				"read_only": 1,
				"allow_on_submit": 1,
				"no_copy": 1,
			},
		],
		"CRM Task": [
			{
				"fieldname": "tbo_task_type",
				"fieldtype": "Select",
				"options": "\n" + "\n".join(TASK_TYPES),
				"label": "Task type",
				"insert_after": "priority",
				"in_standard_filter": 1,
			},
			{
				"fieldname": "tbo_sales_invoice",
				"fieldtype": "Link",
				"options": "Sales Invoice",
				"label": "Sales invoice",
				"insert_after": "tbo_task_type",
			},
			{
				"fieldname": "tbo_promised_date",
				"fieldtype": "Date",
				"label": "Promised date",
				"insert_after": "tbo_sales_invoice",
			},
			{
				"fieldname": "tbo_promised_amount",
				"fieldtype": "Currency",
				"label": "Promised amount",
				"insert_after": "tbo_promised_date",
			},
			{
				"fieldname": "tbo_escalation_level",
				"fieldtype": "Int",
				"label": "Escalation level",
				"insert_after": "tbo_promised_amount",
				"read_only": 1,
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


def setup_finance() -> None:
	"""Everything the finance workflow needs on a site; safe to run again."""
	create_finance_fields()
	create_finance_roles()
	track_task_changes()
	create_email_templates()
	set_allocation_start_date()


def set_allocation_start_date() -> None:
	"""Q8: allocation starts on the deploy date, the first migrate with Phase 3, unless set."""
	if not frappe.db.get_single_value("TBO Smart Settings", "allocation_start_date"):
		frappe.db.set_single_value("TBO Smart Settings", "allocation_start_date", frappe.utils.today())


def create_finance_roles() -> None:
	for role in FINANCE_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)


def track_task_changes() -> None:
	"""CRM Task edits (follow-up remarks, promise to pay) get Version history (gap G14)."""
	make_property_setter(
		"CRM Task", None, "track_changes", 1, "Check", for_doctype=True, validate_fields_for_doctype=False
	)


def create_email_templates() -> None:
	for name, (subject, response) in EMAIL_TEMPLATES.items():
		if not frappe.db.exists("Email Template", name):
			frappe.get_doc(
				{
					"doctype": "Email Template",
					"name": name,
					"subject": subject,
					"use_html": 1,
					"response_html": response,
				}
			).insert(ignore_permissions=True)


def create_collections_view() -> None:
	"""A public "Collections" list view on the CRM Tasks page: collection tasks only."""
	filters = json.dumps({"tbo_task_type": "Collection"})
	if frappe.db.exists("CRM View Settings", {"dt": "CRM Task", "label": COLLECTIONS_VIEW, "public": 1}):
		return
	frappe.get_doc(
		{
			"doctype": "CRM View Settings",
			"label": COLLECTIONS_VIEW,
			"dt": "CRM Task",
			"type": "list",
			"route_name": "Tasks",
			"public": 1,
			"icon": "wallet",
			"filters": filters,
			"order_by": "due_date asc",
			"load_default_columns": 1,
		}
	).insert(ignore_permissions=True)


def after_migrate() -> None:
	setup_finance()
