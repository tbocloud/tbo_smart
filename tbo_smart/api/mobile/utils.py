"""Shared plumbing for the TBO Smart mobile façades: the session employee and paging."""

import frappe
from frappe import _

MAX_PAGE_LENGTH = 100


class NoEmployeeError(frappe.DoesNotExistError):
	"""The session user has no active Employee record (Employee.user_id)."""


def get_session_employee(fields: list[str] | None = None) -> frappe._dict:
	"""The active Employee linked to the session user, or NoEmployeeError.

	The identity always comes from the session; no endpoint accepts an employee id.
	"""
	employee = frappe.db.get_value(
		"Employee",
		{"user_id": frappe.session.user, "status": "Active"},
		["name", "employee_name", "company", *(fields or [])],
		as_dict=True,
	)
	if not employee:
		frappe.throw(
			_("No active Employee record is linked to {0}. Ask HR to link one.").format(frappe.session.user),
			NoEmployeeError,
			title=_("No employee record"),
		)
	return employee


def page_window(page: int, page_length: int) -> tuple[int, int]:
	"""Start offset and clamped page length for a 1-based page number."""
	page_length = min(max(int(page_length or 20), 1), MAX_PAGE_LENGTH)
	page = max(int(page or 1), 1)
	return (page - 1) * page_length, page_length


def page_envelope(rows: list, page: int, page_length: int) -> dict:
	"""Wrap a list fetched with page_length + 1 rows into {data, page, has_more}."""
	_start, page_length = page_window(page, page_length)
	return {"data": rows[:page_length], "page": max(int(page or 1), 1), "has_more": len(rows) > page_length}


def get_full_names(users: set | list) -> dict[str, str]:
	"""Map user ids to full names in one query."""
	users = [u for u in set(users) if u]
	if not users:
		return {}
	User = frappe.qb.DocType("User")
	rows = frappe.qb.from_(User).select(User.name, User.full_name).where(User.name.isin(users)).run()
	return {name: full_name or name for name, full_name in rows}
