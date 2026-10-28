"""Who may see a Lead or an Opportunity (docs/erpnext-lead-cutover.md §3).

Frappe CRM's rule, unchanged, on ERPNext's records (the `has_permission` and
`permission_query_conditions` hooks):

- Administrator and System Manager: everything;
- the finance roles (TBO Smart Settings, while finance roles may read deals): read every
  Opportunity;
- with the sales hierarchy on and the user in it: the records owned by, or assigned (an open or
  closed ToDo) to, the user or anyone below them;
- a Sales Manager outside the hierarchy: everything;
- everyone else: what they own or what is assigned to them;
- on top of the last two, the head of an active TBO Lead Department also sees every lead of
  it (sales/departments.py); a lead without a department is unaffected.

ERPNext's role permissions still decide first; this only narrows them.
"""

import frappe

from tbo_smart.sales import DEAL, LEAD, owner_field
from tbo_smart.sales.departments import headed_departments
from tbo_smart.sales.hierarchy import in_hierarchy, team_query

READ_PTYPES = {"read", "select", "print"}


def unrestricted(user: str, doctype: str, ptype: str | None) -> bool:
	"""Finance roles read every deal, past the hierarchy; never for writes."""
	if doctype != DEAL or (ptype or "read") not in READ_PTYPES:
		return False
	from tbo_smart.finance.access import is_finance_user

	return is_finance_user(user)


def sees_everything(user: str, doctype: str, ptype: str | None) -> bool:
	if user == "Administrator":
		return True
	roles = frappe.get_roles(user)
	if "System Manager" in roles or unrestricted(user, doctype, ptype):
		return True
	return "Sales Manager" in roles and not in_hierarchy(user)


def conditions(user: str, doctype: str):
	"""The criterion on `doctype` for a user who doesn't see everything."""
	criterion = own_conditions(user, doctype)
	if doctype == LEAD:
		criterion = criterion | frappe.qb.DocType(LEAD).tbo_department.isin(headed_departments(user))
	return criterion


def own_conditions(user: str, doctype: str):
	Table = frappe.qb.DocType(doctype)
	ToDo = frappe.qb.DocType("ToDo").as_("_tbo_todo")
	owner = Table[owner_field(doctype)]
	assigned = frappe.qb.from_(ToDo).select(ToDo.reference_name)
	if in_hierarchy(user):
		team = team_query(user)
		return ((owner == user) | owner.isin(team)) | Table.name.isin(
			assigned.where(
				(ToDo.reference_type == doctype)
				& (ToDo.status != "Cancelled")
				& ((ToDo.allocated_to == user) | ToDo.allocated_to.isin(team))
			)
		)
	return (owner == user) | Table.name.isin(
		assigned.where(
			(ToDo.reference_type == doctype) & (ToDo.status != "Cancelled") & (ToDo.allocated_to == user)
		)
	)


def query_conditions(user: str | None, doctype: str) -> str:
	user = user or frappe.session.user
	if sees_everything(user, doctype, "read"):
		return ""
	return conditions(user, doctype).get_sql(with_namespace=True, quote_char="`", secondary_quote_char="'")


def check(doc, ptype: str | None, user: str | None, doctype: str) -> bool:
	user = user or frappe.session.user
	if sees_everything(user, doctype, ptype) or ptype == "create" or not doc.name or doc.is_new():
		return True
	Table = frappe.qb.DocType(doctype)
	return bool(
		frappe.qb.from_(Table)
		.select(Table.name)
		.where((Table.name == doc.name) & conditions(user, doctype))
		.limit(1)
		.run()
	)


def get_lead_query_conditions(user: str | None = None) -> str:
	return query_conditions(user, LEAD)


def get_opportunity_query_conditions(user: str | None = None) -> str:
	return query_conditions(user, DEAL)


def has_lead_permission(doc, ptype=None, user=None) -> bool:
	return check(doc, ptype, user, LEAD)


def has_opportunity_permission(doc, ptype=None, user=None) -> bool:
	return check(doc, ptype, user, DEAL)
