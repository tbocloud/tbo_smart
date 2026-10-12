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

from tbo_smart.api.mobile.capabilities import get_sales_team
from tbo_smart.finance import exceptions_enabled, finance_enabled, get_crm_roles, get_setting

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


def sync_crm_read(granted: list) -> list:
	"""Keep the finance roles' read access on CRM Deal and CRM Organization (Custom DocPerm,
	which also opens the FCRM module to them) in line with the settings.

	`granted` lists what this setting did earlier: rows it added ({"name"}) and existing rows
	it widened ({"name", "restore": original read/if_owner}). Entries no longer wanted, or all
	of them once access is off, are undone: added rows are deleted, widened rows get their
	original values back. A pre-existing row that already gives full read is never touched.
	Returns the entries it now owns."""
	wanted = (
		{(doctype, role) for role in get_crm_roles() for doctype in CRM_READ_DOCTYPES}
		if finance_crm_access_on()
		else set()
	)
	kept = []
	for entry in granted:
		entry = entry if isinstance(entry, dict) else {"name": entry}
		row = frappe.db.get_value("Custom DocPerm", entry["name"], ["parent", "role"], as_dict=True)
		if not row:
			continue
		if (row.parent, row.role) in wanted:
			kept.append(entry)
			wanted.discard((row.parent, row.role))
			continue
		if entry.get("restore"):
			frappe.db.set_value("Custom DocPerm", entry["name"], entry["restore"])
		else:
			frappe.delete_doc("Custom DocPerm", entry["name"], ignore_permissions=True, force=True)
		frappe.clear_cache(doctype=row.parent)
	for doctype, role in sorted(wanted):
		kept += grant_read(doctype, role)
	return kept


def grant_read(doctype: str, role: str) -> list[dict]:
	"""Full read for `role` on `doctype` at permission level 0; what was done, for undoing."""
	existing = frappe.db.get_value(
		"Custom DocPerm",
		{"parent": doctype, "role": role, "permlevel": 0},
		["name", "read", "if_owner"],
		as_dict=True,
	)
	if existing and existing.read and not existing.if_owner:
		return []
	if existing:
		frappe.db.set_value("Custom DocPerm", existing.name, {"read": 1, "if_owner": 0})
		frappe.clear_cache(doctype=doctype)
		return [{"name": existing.name, "restore": {"read": existing.read, "if_owner": existing.if_owner}}]
	add_permission(doctype, role, 0, "read")
	return [
		{"name": frappe.db.get_value("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0})}
	]


# ---------- Phase 2: receivables and exceptions ----------

# Roles that see every receivable and exception (plan §9: accounts, finance, management).
FINANCE_VIEW_ROLES = {
	"System Manager",
	"Accounts User",
	"Accounts Manager",
	"TBO Finance Approver",
	"TBO Management",
	"Sales Manager",
	"Sales & Marketing Head",
	"Director",
	"CEO",
}


def sees_all_receivables(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & FINANCE_VIEW_ROLES)


def get_deal_owners_in_scope(user: str | None = None) -> list[str] | None:
	"""Deal owners whose receivables `user` sees; None means everyone. A salesperson sees
	their own deals, a manager in the sales hierarchy their team."""
	user = user or frappe.session.user
	if sees_all_receivables(user):
		return None
	return get_sales_team(user)


def exception_query_conditions(user: str | None = None) -> str:
	"""permission_query_conditions for TBO Finance Exception."""
	user = user or frappe.session.user
	owners = get_deal_owners_in_scope(user)
	if owners is None:
		return ""
	if not exceptions_enabled():
		return "1=0"
	escaped = ", ".join(frappe.db.escape(owner) for owner in owners)
	return (
		f"(`tabTBO Finance Exception`.owner = {frappe.db.escape(user)} or `tabTBO Finance Exception`.crm_deal in "
		f"(select name from `tabCRM Deal` where deal_owner in ({escaped})))"
	)


def has_exception_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	"""has_permission for TBO Finance Exception: everyone in scope reads; only the finance
	roles edit (status changes go through the API either way). Salespeople see nothing
	while every exception-raising phase is off."""
	user = user or frappe.session.user
	owners = get_deal_owners_in_scope(user)
	if owners is None:
		return True
	if not exceptions_enabled():
		return False
	if doc.owner == user or (
		doc.crm_deal and frappe.db.get_value("CRM Deal", doc.crm_deal, "deal_owner") in owners
	):
		return ptype in (None, "read", "select", "print", "email", "create") or doc.owner == user
	return False
