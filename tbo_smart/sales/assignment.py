"""Round-robin assignment of new leads (docs/lead-departments.md §5).

A department set to "Round robin" hands each new lead that has no owner to its next member in
turn: enabled users only, in the members list's order, wrapping round, starting after the one
who got the last lead (`last_assigned_user`). The owner is set, the person is given the lead as a
ToDo assignment (which notifies them on the ERP) and the department remembers who it was.
"""

import frappe
from frappe import _

# the assignment is the system's (a guest's website form has no permission to give a lead away)
from frappe.desk.form.assign_to import _add as add_assignment
from frappe.utils import cint

from tbo_smart.sales.departments import DEPARTMENT, DEPARTMENT_USER

ROUND_ROBIN = "Round robin"


def claim_owner(doc, method=None) -> None:
	"""doc_events: Lead before_insert (website form, import, hub create with the owner left
	empty). Frappe fills an empty Lead Owner with the session user before this runs, so the owner
	counts as empty when it is that default: a user outside the department who is only the one
	inserting. A caller that means round robin sets `doc.flags.round_robin`."""
	if not doc.get("tbo_department") or not wants_round_robin(doc):
		return
	user = next_member(doc.tbo_department)
	if user:
		doc.lead_owner = user
		doc.flags.round_robin_assignee = user


def wants_round_robin(doc) -> bool:
	owner = doc.get("lead_owner")
	if doc.flags.get("round_robin") or not owner:
		return True
	if owner != frappe.session.user:
		return False
	people = frappe.get_all(DEPARTMENT_USER, filters={"parent": doc.tbo_department}, pluck="user")
	return owner not in people


def assign_new_lead(doc, method=None) -> None:
	"""doc_events: Lead after_insert: the ToDo assignment for the member `claim_owner` chose."""
	user = doc.flags.get("round_robin_assignee")
	if not user:
		return
	assigned_by = "Administrator" if frappe.session.user == "Guest" else frappe.session.user
	add_assignment(
		{
			"assign_to": [user],
			"doctype": doc.doctype,
			"name": doc.name,
			"assigned_by": assigned_by,
			"description": _("New {0} lead, assigned by round robin").format(doc.tbo_department),
		},
		ignore_permissions=True,
	)


def is_round_robin(department: str | None) -> bool:
	"""Whether new leads of `department` are given out in turn."""
	return bool(department) and (frappe.db.get_value(DEPARTMENT, department, "assignment") == ROUND_ROBIN)


def next_member(department: str) -> str | None:
	"""The member who gets the next lead, remembered as the department's last assigned user; None
	when the department is not on round robin, is off, or has no enabled member. The department's
	row is locked until the lead is saved, so two leads inserted together get two people."""
	row = frappe.db.get_value(
		DEPARTMENT,
		department,
		["assignment", "is_active", "last_assigned_user"],
		as_dict=True,
		for_update=True,
	)
	if not row or row.assignment != ROUND_ROBIN or not cint(row.is_active):
		return None
	members = frappe.get_all(
		DEPARTMENT_USER,
		filters={"parent": department, "parentfield": "members"},
		pluck="user",
		order_by="idx asc",
	)
	enabled = set(frappe.get_all("User", filters={"name": ("in", members), "enabled": 1}, pluck="name"))
	start = members.index(row.last_assigned_user) + 1 if row.last_assigned_user in members else 0
	for user in members[start:] + members[:start]:
		if user in enabled:
			frappe.db.set_value(DEPARTMENT, department, "last_assigned_user", user, update_modified=False)
			return user
	return None
