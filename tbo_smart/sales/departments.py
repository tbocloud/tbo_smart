"""Lead departments (docs/lead-departments.md): GrowthX and the like, made by admins.

A lead belongs to at most one TBO Lead Department (`tbo_department`) and has a type
(`tbo_lead_type`, the department's unless set). The department's heads see every lead of it
(`sales.permissions`); a lead of a department is owned by one of its people, when it has any.
"""

import frappe
from frappe import _
from frappe.utils import cint

DEPARTMENT = "TBO Lead Department"
DEPARTMENT_USER = "TBO Lead Department User"
LEAD_TYPES = ("ERP / Software", "Content / Digital Marketing")
MANAGER_ROLES = {"System Manager", "Sales Manager"}
ASSIGNMENTS = ("Manual", "Round robin")
SAVE_FIELDS = {
	"department_name",
	"lead_type",
	"is_active",
	"assignment",
	"stale_after_days",
	"heads",
	"members",
}


def headed_departments(user: str):
	"""Subquery of the active departments `user` heads (one join, for the permission query)."""
	Department = frappe.qb.DocType(DEPARTMENT).as_("_tbo_department")
	Person = frappe.qb.DocType(DEPARTMENT_USER).as_("_tbo_department_user")
	return (
		frappe.qb.from_(Person)
		.join(Department)
		.on(Department.name == Person.parent)
		.select(Person.parent)
		.where(
			(Person.user == user)
			& (Person.parenttype == DEPARTMENT)
			& (Person.parentfield == "heads")
			& (Department.is_active == 1)
		)
	)


def validate_lead_department(doc, method=None) -> None:
	"""doc_events: Lead validate. A new department on a lead must be active; the owner of a
	lead in a department that has people is one of them. Checked when the department or the
	owner changes, so a lead is still editable after its department is switched off."""
	if not doc.get("tbo_department") or frappe.flags.in_import:
		return
	department_changed = doc.is_new() or doc.has_value_changed("tbo_department")
	owner_changed = department_changed or doc.has_value_changed("lead_owner")
	if not owner_changed:
		return
	if department_changed and not cint(frappe.db.get_value(DEPARTMENT, doc.tbo_department, "is_active")):
		frappe.throw(_("{0} is not an active department.").format(doc.tbo_department))
	if doc.get("lead_owner"):
		check_owner_in_department(doc.tbo_department, doc.lead_owner)


def check_owner_in_department(department: str, owner: str) -> None:
	people = frappe.get_all(DEPARTMENT_USER, filters={"parent": department}, pluck="user")
	if people and owner not in people:
		frappe.throw(
			_("{0} is not in the {1} department: its leads are owned by its heads and members.").format(
				owner, department
			)
		)


# ---------- reading and saving, for the hub ----------


def user_rows(department: str, table: str) -> list[dict]:
	return [
		{"user": row.user, "full_name": row.full_name or frappe.utils.get_fullname(row.user)}
		for row in frappe.get_all(
			DEPARTMENT_USER,
			filters={"parent": department, "parentfield": table},
			fields=["user", "full_name"],
			order_by="idx asc",
		)
	]


def serialize(name: str) -> dict:
	row = frappe.db.get_value(
		DEPARTMENT,
		name,
		["name", "lead_type", "is_active", "assignment", "stale_after_days"],
		as_dict=True,
	)
	return {
		"name": row.name,
		"department_name": row.name,
		"lead_type": row.lead_type,
		"is_active": cint(row.is_active),
		"assignment": row.assignment or ASSIGNMENTS[0],
		"stale_after_days": cint(row.stale_after_days),
		"heads": user_rows(name, "heads"),
		"members": user_rows(name, "members"),
	}


def get_departments() -> list[dict]:
	names = frappe.get_all(DEPARTMENT, pluck="name", order_by="name asc")
	return [serialize(name) for name in names]


def active_departments_for(user: str) -> list[dict]:
	"""[{name, lead_type, is_active, assignment, stale_after_days, is_head, is_member}] for the active departments."""
	departments = frappe.get_all(
		DEPARTMENT,
		filters={"is_active": 1},
		fields=["name", "lead_type", "is_active", "assignment", "stale_after_days"],
		order_by="name asc",
	)
	roles = {
		(row.parent, row.parentfield)
		for row in frappe.get_all(DEPARTMENT_USER, filters={"user": user}, fields=["parent", "parentfield"])
	}
	return [
		{
			"name": row.name,
			"lead_type": row.lead_type,
			"is_active": cint(row.is_active),
			"assignment": row.assignment or ASSIGNMENTS[0],
			"stale_after_days": cint(row.stale_after_days),
			"is_head": (row.name, "heads") in roles,
			"is_member": (row.name, "members") in roles,
		}
		for row in departments
	]


def require_manager() -> None:
	if not set(frappe.get_roles()) & MANAGER_ROLES:
		frappe.throw(
			_("Only a Sales Manager or System Manager may change lead departments."), frappe.PermissionError
		)


def can_manage() -> bool:
	return bool(set(frappe.get_roles()) & MANAGER_ROLES)


def save_department(values: dict, name: str | None = None) -> dict:
	"""Create (no `name`) or update a department from `values` (department_name, lead_type,
	is_active, assignment, stale_after_days, heads and members as lists of users); the name does
	not change."""
	require_manager()
	unknown = sorted(set(values) - SAVE_FIELDS)
	if unknown:
		frappe.throw(_("Unknown fields: {0}").format(", ".join(unknown)))
	if name:
		doc = frappe.get_doc(DEPARTMENT, name)
		if values.get("department_name") not in (None, name):
			frappe.throw(_("A department cannot be renamed."))
	else:
		if not (values.get("department_name") or "").strip():
			frappe.throw(_("Give the department a name."))
		doc = frappe.new_doc(DEPARTMENT)
		doc.department_name = values["department_name"].strip()
	if values.get("lead_type"):
		if values["lead_type"] not in LEAD_TYPES:
			frappe.throw(_("{0} is not a lead type.").format(values["lead_type"]))
		doc.lead_type = values["lead_type"]
	if "is_active" in values:
		doc.is_active = cint(values["is_active"])
	if values.get("assignment"):
		if values["assignment"] not in ASSIGNMENTS:
			frappe.throw(_("{0} is not an assignment mode.").format(values["assignment"]))
		doc.assignment = values["assignment"]
	if "stale_after_days" in values:
		doc.stale_after_days = max(cint(values["stale_after_days"]), 0)
	for table in ("heads", "members"):
		if table in values:
			users = list(dict.fromkeys(values[table] or []))
			doc.set(table, [])
			for user in users:
				doc.append(table, {"user": user})
	doc.save()
	return serialize(doc.name)


def set_active(name: str, active) -> dict:
	require_manager()
	doc = frappe.get_doc(DEPARTMENT, name)
	doc.is_active = cint(active)
	doc.save()
	return serialize(name)
