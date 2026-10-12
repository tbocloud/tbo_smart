"""Who may see what in the finance workflow (docs/crm-finance-workflow.md §9).

- A deal's finance data is open to whoever may read the deal under Frappe CRM's own rules
  (owner, assignee, sales hierarchy, Sales Manager), and to the finance roles in TBO Smart
  Settings when "Let finance roles open the CRM" is on.
- Amounts on invoices and payments are hidden from plain salespeople only when "BDE can see
  invoice/payment amounts" is off (open question Q17).
- `has_crm_app_access` and `has_unrestricted_crm_access` are the fork's `crm_app_permission`
  and `crm_unrestricted_access` hooks (tbocloud/crm docs/tbo-finance.md).
"""

import frappe
from frappe import _
from frappe.permissions import add_permission
from frappe.utils import cint

from tbo_smart.finance import finance_enabled, get_crm_roles, get_setting

# Roles that always see amounts: managers, approvers and accounts.
AMOUNT_ROLES = {
	"System Manager",
	"Sales Manager",
	"Accounts User",
	"Accounts Manager",
	"Business Development Manager",
	"Sales & Marketing Head",
	"Director",
	"CEO",
}
# Phase 1 closes a deal's finance with these roles; Phase 4 adds TBO Finance Approver.
CLOSE_ROLES = {"Accounts Manager", "System Manager"}
# The finance roles only ever read deals through the fork's bypass.
READ_PTYPES = {"read", "select", "print"}
CRM_READ_DOCTYPES = ("CRM Deal", "CRM Organization")


def finance_crm_access_on() -> bool:
	return finance_enabled() and bool(cint(get_setting("finance_crm_access")))


def is_finance_user(user: str | None = None) -> bool:
	"""The user holds one of the finance roles and finance roles may use the CRM."""
	user = user or frappe.session.user
	if user == "Guest" or not finance_crm_access_on():
		return False
	return bool(set(frappe.get_roles(user)) & set(get_crm_roles()))


def can_read_deal(deal: str) -> bool:
	return bool(frappe.has_permission("CRM Deal", "read", doc=deal)) or is_finance_user()


def require_deal(deal: str) -> None:
	"""Refuse unless the finance workflow is on, the deal exists and the user may read it."""
	if not finance_enabled():
		frappe.throw(_("The finance workflow is not switched on."), frappe.ValidationError)
	if not isinstance(deal, str) or not frappe.db.exists("CRM Deal", deal):
		frappe.throw(_("Deal {0} not found.").format(deal), frappe.DoesNotExistError)
	if not can_read_deal(deal):
		frappe.throw(_("You cannot see this deal."), frappe.PermissionError)


def can_write_deal(deal: str) -> bool:
	return bool(frappe.has_permission("CRM Deal", "write", doc=deal))


def can_see_amounts() -> bool:
	if cint(get_setting("finance_bde_can_see_amounts")):
		return True
	return bool(set(frappe.get_roles()) & AMOUNT_ROLES) or is_finance_user()


def can_close() -> bool:
	return bool(set(frappe.get_roles()) & CLOSE_ROLES)


def has_crm_app_access() -> bool:
	"""crm_app_permission hook: finance roles may open /crm."""
	return is_finance_user()


def has_unrestricted_crm_access(user: str, doctype: str, ptype: str | None = None) -> bool:
	"""crm_unrestricted_access hook: finance roles read every deal, past the sales hierarchy.
	Never for writes, so an accounts user who is also a salesperson keeps CRM's write rules."""
	return doctype == "CRM Deal" and (ptype or "read") in READ_PTYPES and is_finance_user(user)


def grant_crm_read() -> None:
	"""Give the finance roles read on CRM Deal and CRM Organization (a Custom DocPerm each),
	which also opens the FCRM module to them. Run when the settings are saved with CRM access
	on; turning it off leaves the rows, but the hooks above stop admitting those roles."""
	if not finance_crm_access_on():
		return
	for role in get_crm_roles():
		for doctype in CRM_READ_DOCTYPES:
			if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
				add_permission(doctype, role, 0, "read")
