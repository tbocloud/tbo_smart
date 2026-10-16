"""The sales hierarchy: who reports to whom among salespeople (docs/erpnext-lead-cutover.md §3).

TBO Sales Hierarchy is a tree of users (each node: a user and the node they report to), the
same as Frappe CRM's CRM Sales Hierarchy it replaces. While TBO Smart Settings → Sales
hierarchy is on, a user in the tree sees the leads and deals of everyone below them, and the
follow-up and MOM escalations go to the node above (the BDM).
"""

import frappe
from frappe.utils import cint

from tbo_smart.sales import SETTINGS

DOCTYPE = "TBO Sales Hierarchy"


def hierarchy_enabled() -> bool:
	return bool(cint(frappe.db.get_single_value(SETTINGS, "enable_sales_hierarchy")))


def in_hierarchy(user: str) -> bool:
	return hierarchy_enabled() and bool(frappe.db.exists(DOCTYPE, {"user": user}))


def team_query(user: str):
	"""Subquery of the users at or below `user`'s node (one nested-set join)."""
	Manager = frappe.qb.DocType(DOCTYPE).as_("_tbo_manager")
	Member = frappe.qb.DocType(DOCTYPE).as_("_tbo_member")
	return (
		frappe.qb.from_(Manager)
		.join(Member)
		.on((Member.lft >= Manager.lft) & (Member.lft <= Manager.rgt))
		.select(Member.user)
		.where(Manager.user == user)
	)


def get_sales_team(user: str) -> list[str]:
	"""The user and everyone below them in the sales hierarchy; just the user outside it."""
	if not in_hierarchy(user):
		return [user]
	return sorted({user, *team_query(user).run(pluck=True)})


def managers_of(users) -> dict[str, str]:
	"""{user: the user of the node they report to} (their BDM), for users in the tree."""
	users = [user for user in set(users or ()) if user]
	if not users:
		return {}
	Node = frappe.qb.DocType(DOCTYPE)
	Parent = frappe.qb.DocType(DOCTYPE).as_("parent_node")
	rows = (
		frappe.qb.from_(Node)
		.join(Parent)
		.on(Parent.name == Node.reports_to)
		.select(Node.user, Parent.user.as_("manager"))
		.where(Node.user.isin(users) & Parent.user.isnotnull())
	).run()
	return dict(rows)
