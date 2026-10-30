"""Find prospects (docs/prospecting.md): businesses found by industry and district land in a
lead department's unassigned pool, each read for a reason to call.

Only public business data from OpenStreetMap, DataForSEO's Business Listings and Serper's
Google Maps results is used; nothing is taken from LinkedIn, Justdial or any other site whose terms
forbid it.
"""

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart.sales.departments import DEPARTMENT, DEPARTMENT_USER, MANAGER_ROLES

SETTINGS = "TBO Smart Settings"
RUN = "TBO Prospect Run"
USER_AGENT = "TBO-Smart-Prospecting/1.0 (+https://teambackoffice.com; public business data only)"
MAX_DISTRICTS = 10


def setting(fieldname: str):
	return frappe.db.get_single_value(SETTINGS, fieldname)


def enabled() -> bool:
	return bool(cint(setting("enable_prospecting")))


def headed_departments(user: str | None = None) -> list[str]:
	"""The active departments `user` heads."""
	user = user or frappe.session.user
	return frappe.get_all(
		DEPARTMENT_USER,
		filters={"parenttype": DEPARTMENT, "parentfield": "heads", "user": user},
		pluck="parent",
	)


def can_prospect(department: str | None = None) -> bool:
	"""Managers for any department; a department head for their own departments."""
	if set(frappe.get_roles()) & MANAGER_ROLES:
		return True
	heads = headed_departments()
	return bool(heads) if department is None else department in heads


def require_prospector(department: str | None = None) -> None:
	if not enabled():
		frappe.throw(_("Find prospects is switched off in TBO Smart Settings."))
	if not can_prospect(department):
		frappe.throw(
			_("Only a Sales Manager, System Manager or the department's head can find prospects."),
			frappe.PermissionError,
		)
