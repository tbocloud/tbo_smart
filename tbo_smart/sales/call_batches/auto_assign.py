"""The daily auto-assign (docs/call-batches.md §14): managers hand leads out by hand on the hub's
Assign leads page; whoever they did not reach by the department's time gets leads automatically.

Per department, on its settings (`auto_assign`, `auto_assign_at`, `auto_assign_skip_sunday`,
`auto_assign_skip_saturdays`): from that time on, once a day and not on the days it skips, each
member with a call plan (plans.py) who was not handed a lead of the department by hand today and
is at work (not on leave or a holiday, `is_off_today`) is topped up to their plan: the plan's
calls for the day minus the open leads of the plan's product, industry and district they own and
have never called. The leads are the next unassigned ones of those, as Assign leads would pick them
(hot first, then newest, rows locked so the batches never take the same one).
"""

import frappe
from frappe.query_builder.functions import Count
from frappe.utils import cint, get_datetime, get_time, getdate, now_datetime

from tbo_smart.sales import stale
from tbo_smart.sales.call_batches import is_off_today
from tbo_smart.sales.call_batches.handout import HANDED_NOTE, hand, has_phone, next_unassigned
from tbo_smart.sales.call_batches.plans import plan_district, plan_industry, plan_product, plan_total
from tbo_smart.sales.departments import DEPARTMENT

AUTO_NOTE = "Assigned to you automatically: nobody handed you leads by the set time"
DEFAULT_TIME = "11:00:00"
DEFAULT_SATURDAYS = "2nd"
# which Saturdays of the month are skipped, by their number in the month
SATURDAYS = {"None": (), "2nd": (2,), "2nd and 4th": (2, 4), "All": (1, 2, 3, 4, 5)}


def run_auto_assign(now=None, departments: list[str] | None = None) -> dict:
	"""Every 5 minutes (hooks): the departments whose time has come today (`departments`: only
	these). {department: {user: leads given}}. A department runs once a day: the day is marked
	before it starts, so a failure is logged and not repeated every 5 minutes."""
	now = get_datetime(now) if now else now_datetime()
	filters = {"is_active": 1, "auto_assign": 1}
	if departments is not None:
		filters["name"] = ("in", list(departments) or [""])
	done = {}
	for name in frappe.get_all(DEPARTMENT, filters=filters, pluck="name"):
		department = frappe.get_doc(DEPARTMENT, name)
		if not is_due(department, now):
			continue
		frappe.db.set_value(DEPARTMENT, name, "auto_assigned_on", getdate(now), update_modified=False)
		frappe.db.commit()  # nosemgrep: the mark must hold even if handing out fails
		try:
			done[name] = assign_department(department, getdate(now))
			frappe.db.commit()  # nosemgrep: one department's leads are kept when the next fails
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Auto-assign failed for {name}", message=frappe.get_traceback())
	return done


def is_due(department, now) -> bool:
	day = getdate(now)
	if department.auto_assigned_on and getdate(department.auto_assigned_on) == day:
		return False
	if skips(department, day):
		return False
	return get_time(now) >= get_time(department.auto_assign_at or DEFAULT_TIME)


def skips(department, day) -> bool:
	"""The department does not auto-assign on `day`: a Sunday, or one of the Saturdays it skips."""
	day = getdate(day)
	if day.weekday() == 6:
		return bool(cint(department.auto_assign_skip_sunday))
	if day.weekday() == 5:
		saturdays = SATURDAYS.get(department.auto_assign_skip_saturdays or DEFAULT_SATURDAYS, ())
		return (day.day - 1) // 7 + 1 in saturdays
	return False


def assign_department(department, day) -> dict[str, int]:
	"""{user: leads given} for the members topped up."""
	given = {}
	for row in department.members:
		if not frappe.db.get_value("User", row.user, "enabled"):
			continue  # left the company: leads handed to them would never be called
		count = missing(department, row.user, day)
		if count <= 0:
			continue
		leads = next_unassigned(
			department,
			count,
			plan_product(department, row.user),
			plan_industry(department, row.user),
			plan_district(department, row.user),
		)
		for lead in leads:
			hand(lead, row.user, AUTO_NOTE)
		if leads:
			given[row.user] = len(leads)
	return given


def missing(department, user: str, day) -> int:
	"""How many leads `user` is short of their plan today; 0 when they are left alone (no plan, a
	plan of no calls, off today, or handed leads by hand today)."""
	total = plan_total(department, user)
	if total <= 0 or is_off_today(user, day) or handed_by_hand(department.name, user, day):
		return 0
	return total - uncalled(
		department.name,
		user,
		plan_product(department, user),
		plan_industry(department, user),
		plan_district(department, user),
	)


def handed_by_hand(department: str, user: str, day) -> bool:
	"""`user` was given a lead of `department` on Assign leads on `day`."""
	ToDo, Lead = frappe.qb.DocType("ToDo"), frappe.qb.DocType("Lead")
	start = get_datetime(f"{getdate(day)} 00:00:00")
	return bool(
		frappe.qb.from_(ToDo)
		.join(Lead)
		.on(Lead.name == ToDo.reference_name)
		.select(ToDo.name)
		.where(
			(ToDo.reference_type == "Lead")
			& (ToDo.allocated_to == user)
			& (ToDo.description.like(f"%{HANDED_NOTE}%"))
			& (ToDo.creation >= start)
			& (Lead.tbo_department == department)
		)
		.limit(1)
		.run()
	)


def uncalled(department: str, user: str, product: str, industry: str = "", district: str = "") -> int:
	"""The open leads with a phone `user` owns in `department` (of `product`, `industry` and
	`district` when given) that were never called."""
	Lead = frappe.qb.DocType("Lead")
	query = (
		frappe.qb.from_(Lead)
		.select(Count(Lead.name))
		.where(
			(Lead.tbo_department == department)
			& (Lead.lead_owner == user)
			& Lead.tbo_stage.isin(list(stale.STALE_STAGES))
			& Lead.tbo_last_call_on.isnull()
			& has_phone(Lead)
		)
	)
	if product:
		query = query.where(Lead.tbo_product == product)
	if industry:
		query = query.where(Lead.industry == industry)
	if district:
		query = query.where(Lead.tbo_district == district)
	return query.run()[0][0]
