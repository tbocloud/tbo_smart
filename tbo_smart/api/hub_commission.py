"""Sales commission for the TBO Support hub (docs/hub-leads-api.md §2.18, docs/commission.md).

Every method runs as the acting user (see hub_leads.acting_as). A salesperson sees their own
commission, a department head their department's, a Sales Manager, System Manager or TBO Management
everyone's and the rest.
"""

import frappe

from tbo_smart.api.hub_leads import acting_as
from tbo_smart.lead_pipeline import commission
from tbo_smart.sales import departments


@frappe.whitelist()
def get_commission(
	month: str, user: str | None = None, department: str | None = None, acting_user: str | None = None
) -> dict:
	"""The month's commission, live (a statement that is approved or paid stands as it was
	approved): {month, currency, scope ("own" / "department" / "all"), can_manage, rows: [{user,
	full_name, plans, collected, target, achievement, reached, commission, status, statement,
	frozen, projects, breakdown, payments}], totals, unattributed (managers, no filters)}."""
	with acting_as(acting_user):
		return commission.build_month(month, user, department)


@frappe.whitelist(methods=["POST"])
def generate_statements(month: str, acting_user: str | None = None) -> list[dict]:
	"""Managers only: create or refresh the month's Draft statements. Approved and Paid ones are
	never touched (`skipped`)."""
	with acting_as(acting_user):
		commission.require_manager()
		return commission.generate(month)


@frappe.whitelist(methods=["POST"])
def approve_statement(name: str, acting_user: str | None = None) -> dict:
	"""Managers only: a Draft statement becomes Approved (approved_by, approved_on)."""
	with acting_as(acting_user):
		doc = commission.approve(name)
		return {"name": doc.name, "status": doc.status, "approved_by": doc.approved_by}


@frappe.whitelist(methods=["POST"])
def mark_paid(name: str, acting_user: str | None = None) -> dict:
	"""Managers only: an Approved statement becomes Paid."""
	with acting_as(acting_user):
		doc = commission.mark_paid(name)
		return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def get_commission_plans(acting_user: str | None = None) -> dict:
	"""Managers only: {plans, currency, departments, lead_types}."""
	with acting_as(acting_user):
		result = commission.get_plans()
		result["departments"] = frappe.get_all("TBO Lead Department", pluck="name", order_by="name asc")
		result["lead_types"] = list(departments.LEAD_TYPES)
		return result


@frappe.whitelist(methods=["POST"])
def save_commission_plan(values: dict | str, name: str | None = None, acting_user: str | None = None) -> dict:
	"""Managers only: create (no `name`) or update a plan (plan_name, is_active, basis, department,
	lead_type, project_type, user, monthly_target, above_target_rate, slabs)."""
	with acting_as(acting_user):
		return commission.save_plan(values, name)


@frappe.whitelist()
def get_project_commission(project: str, acting_user: str | None = None) -> dict:
	"""Managers only: a project's commission set-up (or None) with what was collected on it and
	the commission credited so far."""
	with acting_as(acting_user):
		return commission.get_project_setup(project)


@frappe.whitelist(methods=["POST"])
def save_project_commission(values: dict | str, acting_user: str | None = None) -> dict:
	"""Managers only: set who earns on a project (earners with shares adding up to 100) and its
	plan or own slabs."""
	with acting_as(acting_user):
		return commission.save_project_setup(values)
