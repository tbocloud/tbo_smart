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

from tbo_smart.sales.hierarchy import get_sales_team, in_hierarchy
from tbo_smart.tbo_smart.doctype.tbo_quotation_approval_settings.tbo_quotation_approval_settings import (
	get_settings,
)

HR_APPROVER_ROLES = {"HR Manager", "HR User"}
CRM_ROLES = {"Sales User", "Sales Manager", "System Manager"}
CRM_MANAGER_ROLES = {"Sales Manager", "System Manager"}
# The `scope` a caller may pass for each crm_scope, narrowest first.
CRM_SCOPE_PARAMS = {"own": ["mine"], "team": ["mine", "team"], "all": ["mine", "team", "all"]}
# A workflow transition counts as a decision when its action or next state reads like one.
DECISION_WORDS = ("approv", "reject")
ADVANCE_DOCTYPE = "Employee Advance"
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
			"advances": bool(employee) and can_request_advance(user),
			"approve_advance": can_approve_advance(user, roles),
			"team_today": has_reports,
			"team_insights": has_reports or is_hr_manager,
			"team_scope": "company" if is_hr_manager else ("direct_reports" if has_reports else None),
			"crm": crm,
			"crm_manager": crm_manager,
			"crm_scope": crm_scope,
			"crm_scopes": allowed_crm_scopes(user, crm_scope),
			"sales_insights": crm_manager,
			"approve_quotations": can_approve_quotations(user),
		}
	)


def can_approve(kind: str, user: str, roles: set) -> bool:
	"""HR Manager / HR User, the named approver of any active employee or department, or a
	user whose own roles (not All or Guest) can take an approve or reject step of the
	doctype's workflow."""
	if roles & HR_APPROVER_ROLES:
		return True
	doctype, employee_field, department_field = APPROVAL_FIELDS[kind]
	if frappe.db.exists("Employee", {employee_field: user, "status": "Active"}):
		return True
	if frappe.db.exists(
		"Department Approver", {"parenttype": "Department", "parentfield": department_field, "approver": user}
	):
		return True
	return has_workflow_decision(doctype, roles)


def has_workflow_decision(doctype: str, roles: set) -> bool:
	"""A workflow on `doctype` has an approve or reject transition allowed to one of the
	user's own roles (not All or Guest)."""
	workflow = get_workflow_name(doctype)
	own_roles = list(roles - {"All", "Guest"})
	if not workflow or not own_roles:
		return False
	transitions = frappe.get_all(
		"Workflow Transition",
		filters={"parent": workflow, "allowed": ("in", own_roles)},
		fields=["action", "next_state"],
	)
	return any(is_decision(row.action, row.next_state, DECISION_WORDS) for row in transitions)


def can_request_advance(user: str) -> bool:
	"""The user's roles may create an Employee Advance (the Employee role can in HRMS)."""
	return bool(frappe.has_permission(ADVANCE_DOCTYPE, "create", user=user))


def can_approve_advance(user: str, roles: set) -> bool:
	"""Employee Advance has no approver field or approval status in HRMS: approving it is
	submitting it.

	With a workflow on Employee Advance, a role that can take an approve or reject step.
	Without one: HR Manager / HR User whose roles can submit Employee Advance (Expense
	Approver by default), or the named Expense Approver of an active Employee or a
	Department, to whom ess.create_advance routes the drafts.
	"""
	if get_workflow_name(ADVANCE_DOCTYPE):
		return has_workflow_decision(ADVANCE_DOCTYPE, roles)
	if roles & HR_APPROVER_ROLES and frappe.has_permission(ADVANCE_DOCTYPE, "submit", user=user):
		return True
	return bool(frappe.db.exists("Employee", {"expense_approver": user, "status": "Active"})) or bool(
		frappe.db.exists(
			"Department Approver",
			{"parenttype": "Department", "parentfield": "expense_approvers", "approver": user},
		)
	)


def can_approve_quotations(user: str) -> bool:
	"""A quotation approval level that requests can wait at: some level ranks below mine (BDM,
	Sales & Marketing Head, Director, CEO), as get_adjustment_limits' `is_approver` decides."""
	settings = get_settings()
	level = settings.get_user_level(user)
	levels = settings.sorted_levels()
	return bool(level and levels and cint(levels[0].rank) < cint(level.rank))


def is_decision(action: str | None, next_state: str | None, words: tuple) -> bool:
	"""Whether a workflow transition's action or next state contains one of `words`."""
	label = f"{action or ''} {next_state or ''}".lower()
	return any(word in label for word in words)


def get_crm_scope(user: str, roles: set) -> str:
	"""The widest sales visibility, mirroring tbo_smart.sales.permissions.

	System Manager: all. With the sales hierarchy on and the user in it, even a Sales Manager
	sees only their subtree: team if anyone reports to them there, else own.
	A Sales Manager outside the hierarchy: all. Everyone else: own (owned or assigned).
	"""
	if user == "Administrator" or "System Manager" in roles:
		return "all"
	if in_hierarchy(user):
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
