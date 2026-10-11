"""What the session user may do in TBO Smart, computed from their real tboindia roles and settings.

`get_capabilities` is the one canonical rule. The app reads it (through `ess.get_me` or
`get_my_capabilities`) to decide what to show, and every endpoint checks the same flag with
`require`, so the app never offers something the server would refuse.
Contract: docs/tbo-smart-api-ess-crm.md §4.
"""

import frappe
from frappe import _
from frappe.model.workflow import get_workflow_name
from frappe.utils import cint

HR_APPROVER_ROLES = {"HR Manager", "HR User"}
CRM_ROLES = {"Sales User", "Sales Manager", "System Manager"}
CRM_MANAGER_ROLES = {"Sales Manager", "System Manager"}
# The `scope` a caller may pass for each crm_scope, narrowest first.
CRM_SCOPE_PARAMS = {"own": ["mine"], "team": ["mine", "team"], "all": ["mine", "team", "all"]}
APPROVAL_FIELDS = {
	"leave": ("Leave Application", "leave_approver", "leave_approvers"),
	"expense": ("Expense Claim", "expense_approver", "expense_approvers"),
}


@frappe.whitelist()
def get_my_capabilities() -> dict:
	"""The session user's capabilities. Works without an Employee, unlike `ess.get_me`."""
	return get_capabilities()


def get_capabilities() -> frappe._dict:
	"""Every TBO Smart capability of the session user. Not cached: roles and settings
	changed in the ERP apply on the very next call."""
	user = frappe.session.user
	roles = set(frappe.get_roles(user))
	employee = frappe.db.get_value(
		"Employee", {"user_id": user, "status": "Active"}, ["name", "company"], as_dict=True
	)
	checkin_allowed = bool(employee) and bool(
		cint(frappe.db.get_single_value("HR Settings", "allow_employee_checkin_from_mobile_app"))
	)
	has_reports = bool(employee) and bool(
		frappe.db.exists("Employee", {"reports_to": employee.name, "status": "Active"})
	)
	is_hr_manager = "HR Manager" in roles
	crm = bool(roles & CRM_ROLES)
	crm_manager = crm and bool(roles & CRM_MANAGER_ROLES)
	crm_scope = get_crm_scope(user, roles) if crm else None
	return frappe._dict(
		{
			"ess": bool(employee),
			"checkin_allowed": checkin_allowed,
			"geolocation_required": checkin_allowed
			and bool(cint(frappe.db.get_single_value("HR Settings", "allow_geolocation_tracking"))),
			"approve_leave": can_approve("leave", user, roles),
			"approve_expense": can_approve("expense", user, roles),
			"team_today": has_reports,
			"team_insights": has_reports or is_hr_manager,
			"team_scope": "company" if is_hr_manager else ("direct_reports" if has_reports else None),
			"crm": crm,
			"crm_manager": crm_manager,
			"crm_scope": crm_scope,
			"crm_scopes": allowed_crm_scopes(user, crm_scope),
			"sales_insights": crm_manager,
		}
	)


def can_approve(kind: str, user: str, roles: set) -> bool:
	"""HR Manager / HR User, the named approver of any active employee or department, or a
	user whose roles can move a step of the doctype's workflow."""
	if roles & HR_APPROVER_ROLES:
		return True
	doctype, employee_field, department_field = APPROVAL_FIELDS[kind]
	if frappe.db.exists("Employee", {employee_field: user, "status": "Active"}):
		return True
	if frappe.db.exists(
		"Department Approver", {"parenttype": "Department", "parentfield": department_field, "approver": user}
	):
		return True
	workflow = get_workflow_name(doctype)
	return bool(
		workflow
		and frappe.db.exists("Workflow Transition", {"parent": workflow, "allowed": ("in", list(roles))})
	)


def get_crm_scope(user: str, roles: set) -> str:
	"""The widest CRM visibility, mirroring crm.permissions.org_hierarchy.

	System Manager: all. With the sales hierarchy on and the user in it, CRM limits even a
	Sales Manager to their subtree: team if anyone reports to them there, else own.
	A Sales Manager outside the hierarchy: all. Everyone else: own (owned or assigned).
	"""
	if user == "Administrator" or "System Manager" in roles:
		return "all"
	if in_sales_hierarchy(user):
		return "team" if len(get_sales_team(user)) > 1 else "own"
	return "all" if "Sales Manager" in roles else "own"


def allowed_crm_scopes(user: str, crm_scope: str | None) -> list[str]:
	"""The `scope` values this user may pass; `team` only when they have a sales team."""
	if not crm_scope:
		return []
	scopes = list(CRM_SCOPE_PARAMS[crm_scope])
	if crm_scope == "all" and len(get_sales_team(user)) <= 1:
		scopes.remove("team")
	return scopes


def sales_hierarchy_enabled() -> bool:
	"""FCRM Settings → Sales hierarchy; older Frappe CRM releases have no such setting."""
	return frappe.get_meta("FCRM Settings").has_field("enable_sales_hierarchy") and bool(
		cint(frappe.db.get_single_value("FCRM Settings", "enable_sales_hierarchy"))
	)


def in_sales_hierarchy(user: str) -> bool:
	return sales_hierarchy_enabled() and bool(frappe.db.exists("CRM Sales Hierarchy", {"user": user}))


def get_sales_team(user: str) -> list[str]:
	"""The user and everyone below them in the CRM sales hierarchy (one nested-set query)."""
	if not in_sales_hierarchy(user):
		return [user]
	Manager = frappe.qb.DocType("CRM Sales Hierarchy").as_("manager")
	Member = frappe.qb.DocType("CRM Sales Hierarchy").as_("member")
	members = (
		frappe.qb.from_(Manager)
		.join(Member)
		.on((Member.lft >= Manager.lft) & (Member.lft <= Manager.rgt))
		.select(Member.user)
		.where(Manager.user == user)
	).run(pluck=True)
	return sorted({user, *members})


def require(flag: str, message: str | None = None) -> frappe._dict:
	"""Refuse with PermissionError unless the session user has `flag`; return the capabilities."""
	capabilities = get_capabilities()
	if not capabilities.get(flag):
		frappe.throw(message or _("Your role does not allow this."), frappe.PermissionError)
	return capabilities


def resolve_crm_scope(capabilities: frappe._dict, scope: str | None) -> str:
	"""Check a requested CRM scope against the caller's crm_scopes; default to the widest."""
	allowed = capabilities.crm_scopes
	scope = scope or allowed[-1]
	if scope not in allowed:
		frappe.throw(
			_("Scope {0} is not open to you. Allowed: {1}.").format(scope, ", ".join(allowed)),
			frappe.PermissionError,
		)
	return scope


def scope_users(scope: str) -> list[str] | None:
	"""Owners a resolved scope stands for: mine → me, team → my sales team, all → no filter."""
	if scope == "mine":
		return [frappe.session.user]
	if scope == "team":
		return get_sales_team(frappe.session.user)
	return None


def list_scope_users(capabilities: frappe._dict, scope: str) -> list[str] | None:
	"""Owner filter for frappe.get_list on leads and deals.

	At the caller's widest scope CRM's own permission rule already is the scope (it also
	keeps records assigned to them), so no extra filter is added. A narrower scope filters
	on the owner.
	"""
	if scope == capabilities.crm_scopes[-1]:
		return None
	return scope_users(scope)
