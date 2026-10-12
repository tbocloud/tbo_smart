"""Install and migrate setup for tbo_smart."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from tbo_smart.finance.setup import create_finance_fields
from tbo_smart.quotation_control import COMMISSION_FIELD

# Designation levels for quotation approvals, lowest first. Limits ship unset on purpose:
# management sets them in TBO Quotation Approval Settings, and until then every non-zero
# adjustment by that level needs approval.
QUOTATION_LEVELS = (
	{"level": "BDE", "role": "Business Development Executive", "rank": 10},
	{"level": "BDM", "role": "Business Development Manager", "rank": 20},
	{"level": "Sales & Marketing Head", "role": "Sales & Marketing Head", "rank": 30},
	{"level": "Director", "role": "Director", "rank": 40, "approves_directly": 1},
	{"level": "CEO", "role": "CEO", "rank": 50, "approves_directly": 1, "unlimited": 1},
)


def after_install():
	setup_quotation_approval()
	create_finance_fields()


def setup_quotation_approval():
	"""Roles, the Quotation Commission % field and the default levels (only when none exist)."""
	create_level_roles()
	create_quotation_fields()
	create_default_levels()


def create_level_roles():
	for row in QUOTATION_LEVELS:
		if not frappe.db.exists("Role", row["role"]):
			frappe.get_doc({"doctype": "Role", "role_name": row["role"], "desk_access": 1}).insert(
				ignore_permissions=True
			)


def create_quotation_fields():
	create_custom_fields(
		{
			"Quotation": [
				{
					"fieldname": COMMISSION_FIELD,
					"fieldtype": "Percent",
					"label": "Commission %",
					"insert_after": "referral_sales_partner",
					"description": "Commission on this quotation. Above your limit it needs approval before submit.",
					"allow_on_submit": 0,
					"no_copy": 0,
				}
			]
		},
		ignore_validate=True,
	)


def create_default_levels():
	settings = frappe.get_single("TBO Quotation Approval Settings")
	if settings.levels:
		return
	for row in QUOTATION_LEVELS:
		settings.append("levels", row)
	settings.save(ignore_permissions=True)
