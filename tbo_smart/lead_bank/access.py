"""Who may use the Lead Bank. Unlike the leads API this asks for no sales role: any enabled
staff member may add a lead and see the leads they added, and nothing else of the CRM."""

import frappe
from frappe import _


def collector_user(acting_user: str | None) -> str:
	"""The staff member a Lead Bank call is for. The hub's integration user names them in
	`acting_user`; anyone else acts as themselves. They must be an enabled System User and not an
	integration user. The call itself never runs as them: every method reads and writes only what
	that person's own part of the Lead Bank needs."""
	from tbo_smart.api.hub_leads import integration_role

	caller = frappe.session.user
	acting_user = (acting_user or "").strip()
	role = integration_role()
	if role in frappe.get_roles(caller):
		if not acting_user:
			frappe.throw(_("Name the hub user this call is for (acting_user)."), frappe.PermissionError)
		user = acting_user
	else:
		if acting_user and acting_user != caller:
			frappe.throw(
				_("Only the hub's integration user may act for someone else."), frappe.PermissionError
			)
		user = caller
	row = frappe.db.get_value("User", user, ["enabled", "user_type"], as_dict=True)
	if not row or not row.enabled or row.user_type != "System User":
		frappe.throw(_("{0} is not an enabled user here.").format(user), frappe.PermissionError)
	if role in frappe.get_roles(user):
		frappe.throw(_("{0} is an integration user, not a person.").format(user), frappe.PermissionError)
	return user


def require_manager(user: str) -> None:
	"""Only a Sales Manager, System Manager or TBO Management may do this."""
	from tbo_smart.lead_pipeline.commission import is_manager

	if not is_manager(user):
		frappe.throw(
			_("Only a Sales Manager, System Manager or TBO Management can do this."), frappe.PermissionError
		)
