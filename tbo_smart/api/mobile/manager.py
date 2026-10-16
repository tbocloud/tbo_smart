"""TBO Smart manager façade: my team today, approvals waiting for me, team insights, sales snapshot.

Every endpoint checks the capability it advertises (tbo_smart.api.mobile.capabilities), so
the app and the server agree on who may call what. Insights are read-only aggregates.
Contract: docs/tbo-smart-api-ess-crm.md.
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions, get_workflow_name
from frappe.query_builder import Case
from frappe.query_builder.functions import Count, IfNull, Min, Sum
from frappe.utils import add_days, cint, date_diff, flt, getdate, now_datetime, strip_html
from hrms.api import get_expense_claims, get_filters, get_leave_applications

from tbo_smart.api.mobile.capabilities import (
	ADVANCE_DOCTYPE,
	DECISION_WORDS,
	HR_APPROVER_ROLES,
	get_capabilities,
	is_decision,
	require,
	scope_users,
)
from tbo_smart.api.mobile.crm import deal_value, open_stage, owned_by
from tbo_smart.api.mobile.utils import (
	cached_insights,
	get_session_employee,
	percent,
	period_windows,
)
from tbo_smart.sales import default_currency

APPROVAL_DOCTYPES = {
	"leave": ("Leave Application", "leave_approver", "status"),
	"expense": ("Expense Claim", "expense_approver", "approval_status"),
	# HRMS has no approver field on Employee Advance; see routed_advances.
	"advance": (ADVANCE_DOCTYPE, None, "status"),
}
APPROVALS_LIMIT = 100
CLOSING_SOON_DAYS = 7
ATTENTION_LIMIT = 10


# ---------- Team attendance ----------


@frappe.whitelist()
def get_team_today() -> list[dict]:
	"""Direct reports of the session employee: checked in, not yet, or on approved leave today.

	Reads other employees' check-ins, so it is limited to the caller's own direct reports
	(Employee.reports_to) and returns only the first IN time, never the location.
	"""
	require("team_today", _("Only managers with direct reports can see team attendance."))
	manager = get_session_employee()
	Employee = frappe.qb.DocType("Employee")
	team = (
		frappe.qb.from_(Employee)
		.select(
			Employee.name,
			Employee.employee_name,
			Employee.user_id,
			Employee.image,
			Employee.department,
			Employee.designation,
		)
		.where((Employee.reports_to == manager.name) & (Employee.status == "Active"))
		.orderby(Employee.employee_name)
	).run(as_dict=True)
	employees = [member.name for member in team]
	today = getdate()
	first_in = get_first_check_ins(employees, today)
	on_leave = get_leaves_on(employees, today)
	return [
		{
			"id": member.user_id or member.name,
			"employee": member.name,
			"employee_name": member.employee_name,
			"image": member.image,
			"department": member.department,
			"designation": member.designation,
			"attendance": "leave"
			if member.name in on_leave
			else ("in" if member.name in first_in else "not_yet"),
			"checked_in_at": first_in.get(member.name),
			"leave_type": on_leave.get(member.name),
		}
		for member in team
	]


def get_first_check_ins(employees: list[str], day) -> dict:
	Checkin = frappe.qb.DocType("Employee Checkin")
	rows = (
		frappe.qb.from_(Checkin)
		.select(Checkin.employee, Min(Checkin.time).as_("first_in"))
		.where(
			(Checkin.employee.isin(employees))
			& (Checkin.log_type == "IN")
			& (Checkin.time >= day)
			& (Checkin.time < add_days(day, 1))
		)
		.groupby(Checkin.employee)
	).run()
	return dict(rows)


def get_leaves_on(employees: list[str], day) -> dict:
	Leave = frappe.qb.DocType("Leave Application")
	rows = (
		frappe.qb.from_(Leave)
		.select(Leave.employee, Leave.leave_type)
		.where(
			(Leave.employee.isin(employees))
			& (Leave.docstatus == 1)
			& (Leave.status == "Approved")
			& (Leave.from_date <= day)
			& (Leave.to_date >= day)
		)
	).run()
	return dict(rows)


# ---------- Team insights ----------


@frappe.whitelist()
def get_team_insights(period: str = "month") -> dict:
	"""Read-only attendance and approval aggregates for my team, cached for a few minutes.

	The team is my active direct reports, or the whole company for an HR Manager
	(capabilities.team_scope). Names appear only in `people`, as counts per person; no
	check-in time or location is ever returned.
	"""
	capabilities = require("team_insights", _("Only managers and HR Managers can see team insights."))
	windows = period_windows(period)
	return cached_insights(
		"team", (period, capabilities.team_scope), lambda: build_team_insights(capabilities, windows)
	)


def build_team_insights(capabilities: frappe._dict, windows: frappe._dict) -> dict:
	employees = get_insight_team(capabilities.team_scope)
	today = getdate()
	insights = {
		"period": windows.period,
		"from_date": windows.from_date,
		"to_date": windows.to_date,
		"previous": {"from_date": windows.previous_from, "to_date": windows.previous_to},
		"scope": capabilities.team_scope,
		"team_size": len(employees),
		"generated_at": now_datetime(),
	}
	if not employees:
		return {**insights, **empty_team_insights(windows)}

	days = attendance_by_day(employees, windows.trend[0].from_date, today)
	current = attendance_kpis(days, windows.from_date, windows.to_date)
	previous = attendance_kpis(days, windows.previous_from, windows.previous_to)
	trend = [
		{
			"label": w.label,
			"from_date": w.from_date,
			"to_date": w.to_date,
			**attendance_kpis(days, w.from_date, w.to_date),
		}
		for w in windows.trend
	]
	Checkin = frappe.qb.DocType("Employee Checkin")
	checked_in_today = (
		frappe.qb.from_(Checkin)
		.select(Count(Checkin.employee).distinct())
		.where(
			(Checkin.employee.isin(employees))
			& (Checkin.log_type == "IN")
			& (Checkin.time >= today)
			& (Checkin.time < add_days(today, 1))
		)
	).run()[0][0]
	return {
		**insights,
		"attendance": {
			**current,
			"present_pct_delta": round(current["present_pct"] - previous["present_pct"], 1),
			"late_delta": current["late"] - previous["late"],
			"absent_delta": current["absent"] - previous["absent"],
			"on_leave_today": len(get_leaves_on(employees, today)),
			"checked_in_today": checked_in_today or 0,
		},
		"trend": trend,
		"pending": get_pending_approvals(employees, today),
		"people": get_attendance_attention(employees, windows.from_date, windows.to_date),
	}


def empty_team_insights(windows: frappe._dict) -> dict:
	zero = attendance_kpis({}, windows.from_date, windows.to_date)
	return {
		"attendance": {
			**zero,
			"present_pct_delta": 0.0,
			"late_delta": 0,
			"absent_delta": 0,
			"on_leave_today": 0,
			"checked_in_today": 0,
		},
		"trend": [
			{"label": w.label, "from_date": w.from_date, "to_date": w.to_date, **zero} for w in windows.trend
		],
		"pending": {
			"leave": {"count": 0, "oldest_days": None},
			"expense": {"count": 0, "oldest_days": None, "amount": 0.0},
		},
		"people": [],
	}


def get_insight_team(team_scope: str) -> list[str]:
	"""Active employees in the team: direct reports, or the HR Manager's company (all
	companies when the HR Manager has no Employee). Never includes the caller."""
	Employee = frappe.qb.DocType("Employee")
	me = frappe.db.get_value(
		"Employee", {"user_id": frappe.session.user, "status": "Active"}, ["name", "company"], as_dict=True
	)
	query = frappe.qb.from_(Employee).select(Employee.name).where(Employee.status == "Active")
	if team_scope == "company":
		if me:
			query = query.where((Employee.company == me.company) & (Employee.name != me.name))
	else:
		query = query.where(Employee.reports_to == me.name)
	return query.run(pluck=True)


def attendance_by_day(employees: list[str], from_date, to_date) -> dict:
	"""{date: {"present": n, "half_day": n, "absent": n, "on_leave": n, "late": n, "marked": n}}
	from one grouped query over submitted Attendance."""
	Attendance = frappe.qb.DocType("Attendance")
	rows = (
		frappe.qb.from_(Attendance)
		.select(
			Attendance.attendance_date,
			Attendance.status,
			IfNull(Attendance.late_entry, 0).as_("late"),
			Count(Attendance.name).as_("count"),
		)
		.where(
			(Attendance.employee.isin(employees))
			& (Attendance.docstatus == 1)
			& (Attendance.attendance_date.between(from_date, to_date))
		)
		.groupby(Attendance.attendance_date, Attendance.status, IfNull(Attendance.late_entry, 0))
	).run(as_dict=True)
	status_keys = {
		"Present": "present",
		"Work From Home": "present",
		"Half Day": "half_day",
		"Absent": "absent",
		"On Leave": "on_leave",
	}
	days = {}
	for row in rows:
		day = days.setdefault(
			getdate(row.attendance_date),
			{"present": 0, "half_day": 0, "absent": 0, "on_leave": 0, "late": 0, "marked": 0},
		)
		key = status_keys.get(row.status)
		if key:
			day[key] += row["count"]
		day["marked"] += row["count"]
		if cint(row.late):
			day["late"] += row["count"]
	return days


def attendance_kpis(days: dict, from_date, to_date) -> dict:
	"""present % = (Present + Work From Home + ½ Half Day) / marked days, over a date span."""
	totals = {"present": 0, "half_day": 0, "absent": 0, "on_leave": 0, "late": 0, "marked": 0}
	for day, counts in days.items():
		if from_date <= day <= to_date:
			for key in totals:
				totals[key] += counts[key]
	return {
		"present_pct": percent(totals["present"] + totals["half_day"] / 2, totals["marked"]),
		"late": totals["late"],
		"absent": totals["absent"],
		"on_leave_days": totals["on_leave"],
		"marked_days": totals["marked"],
	}


def get_attendance_attention(employees: list[str], from_date, to_date) -> list[dict]:
	"""Only the people with late check-ins or absences in the period, as counts, most first."""
	Attendance = frappe.qb.DocType("Attendance")
	late = Sum(Case().when(Attendance.late_entry == 1, 1).else_(0))
	absent = Sum(Case().when(Attendance.status == "Absent", 1).else_(0))
	rows = (
		frappe.qb.from_(Attendance)
		.select(
			Attendance.employee,
			Attendance.employee_name,
			late.as_("late"),
			absent.as_("absent"),
		)
		.where(
			(Attendance.employee.isin(employees))
			& (Attendance.docstatus == 1)
			& (Attendance.attendance_date.between(from_date, to_date))
			& ((Attendance.late_entry == 1) | (Attendance.status == "Absent"))
		)
		.groupby(Attendance.employee, Attendance.employee_name)
		.orderby(late + absent, order=frappe.qb.desc)
		.limit(ATTENTION_LIMIT)
	).run(as_dict=True)
	return [
		{
			"employee": row.employee,
			"employee_name": row.employee_name,
			"late": cint(row.late),
			"absent": cint(row.absent),
		}
		for row in rows
	]


def get_pending_approvals(employees: list[str], today) -> dict:
	"""Open leave and draft expense claims of the team: count and age of the oldest, in days."""
	Leave = frappe.qb.DocType("Leave Application")
	leave = (
		frappe.qb.from_(Leave)
		.select(Count(Leave.name).as_("count"), Min(Leave.posting_date).as_("oldest"))
		.where((Leave.employee.isin(employees)) & (Leave.docstatus == 0) & (Leave.status == "Open"))
	).run(as_dict=True)[0]
	Claim = frappe.qb.DocType("Expense Claim")
	claim = (
		frappe.qb.from_(Claim)
		.select(
			Count(Claim.name).as_("count"),
			Min(Claim.posting_date).as_("oldest"),
			Sum(Claim.total_claimed_amount).as_("amount"),
		)
		.where((Claim.employee.isin(employees)) & (Claim.docstatus == 0) & (Claim.approval_status == "Draft"))
	).run(as_dict=True)[0]
	return {
		"leave": {"count": leave["count"] or 0, "oldest_days": age_in_days(leave.oldest, today)},
		"expense": {
			"count": claim["count"] or 0,
			"oldest_days": age_in_days(claim.oldest, today),
			"amount": flt(claim.amount),
		},
	}


def age_in_days(day, today) -> int | None:
	return date_diff(today, day) if day else None


# ---------- Approvals ----------


@frappe.whitelist()
def get_approvals() -> list[dict]:
	"""Leave Applications, Expense Claims and Employee Advances waiting for the session user,
	newest first.

	Uses the HRMS PWA queries: without a workflow, documents where I am the approver (HR
	Manager and HR User: every open one they can read); with a workflow, documents in
	states my roles can move. Each kind only for approve_leave / approve_expense /
	approve_advance.
	"""
	capabilities = get_capabilities()
	if not (capabilities.approve_leave or capabilities.approve_expense or capabilities.approve_advance):
		frappe.throw(_("Only leave, expense or advance approvers have approvals."), frappe.PermissionError)
	user = frappe.session.user
	employee = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name") or ""
	approver_id = None if is_hr_approver() else user
	leaves, claims = [], []
	if capabilities.approve_leave:
		leaves = get_leave_applications(
			employee, approver_id=approver_id, for_approval=True, limit=APPROVALS_LIMIT
		)
	if capabilities.approve_expense:
		claims = get_expense_claims(
			employee, approver_id=approver_id, for_approval=True, limit=APPROVALS_LIMIT
		)
	advances = get_advance_approvals(employee) if capabilities.approve_advance else []
	approvals = (
		[serialize_leave_approval(row) for row in leaves]
		+ [serialize_claim_approval(row) for row in claims]
		+ [serialize_advance_approval(row) for row in advances]
	)
	return sorted(approvals, key=lambda row: str(row["submitted"]), reverse=True)


@frappe.whitelist(methods=["POST"])
def decide_approval(kind: str, name: str, approve: bool, reason: str | None = None) -> dict:
	"""Approve or reject one leave application, expense claim or employee advance through the
	HRMS path.

	With a workflow on the doctype the matching workflow action is applied; otherwise the
	named approver (or an HR Manager / HR User) sets the status and submits, exactly as in
	HRMS. An employee advance without a workflow can only be approved (submitted): HRMS has
	no rejected state for it. A rejection needs a reason; it is added to the document as a
	comment.
	"""
	if kind not in APPROVAL_DOCTYPES:
		frappe.throw(_("Kind must be leave, expense or advance."))
	require(f"approve_{kind}", _("You do not approve {0}.").format(_(APPROVAL_DOCTYPES[kind][0])))
	approve = cint(approve)
	if not approve and not (reason or "").strip():
		frappe.throw(_("Give a reason for the rejection."))

	doctype, approver_field, status_field = APPROVAL_DOCTYPES[kind]
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus != 0:
		frappe.throw(_("{0} {1} has already been decided.").format(_(doctype), name))

	if kind == "advance":
		check_not_own_advance(doc)

	if get_workflow_name(doctype):
		apply_workflow(doc.as_dict(), pick_workflow_action(doc, approve))
		doc.reload()
	elif kind == "advance":
		if not is_hr_approver() and name not in routed_advances():
			frappe.throw(_("You are not the approver of {0}.").format(name), frappe.PermissionError)
		if not approve:
			frappe.throw(
				_(
					"An employee advance can only be rejected through a workflow. Ask the employee to withdraw it."
				)
			)
		doc.submit()
	else:
		if doc.get(approver_field) != frappe.session.user and not is_hr_approver():
			frappe.throw(_("You are not the approver of {0}.").format(name), frappe.PermissionError)
		set_decision(doc, kind, approve)
		doc.submit()

	if reason:
		doc.add_comment(
			"Comment", text=_("{0}: {1}").format(_("Approved") if approve else _("Rejected"), reason)
		)
	return {"kind": kind, "name": doc.name, "status": doc.get(status_field), "docstatus": doc.docstatus}


def set_decision(doc, kind: str, approve: bool) -> None:
	if kind == "leave":
		doc.status = "Approved" if approve else "Rejected"
		return
	doc.approval_status = "Approved" if approve else "Rejected"
	if approve:
		for line in doc.expenses:
			if not flt(line.sanctioned_amount):
				line.sanctioned_amount = line.amount


def pick_workflow_action(doc, approve: bool) -> str:
	"""The transition open to me whose action or next state reads as approve / reject."""
	words = DECISION_WORDS[:1] if approve else DECISION_WORDS[1:]
	for transition in get_transitions(doc):
		if is_decision(transition.action, transition.next_state, words):
			return transition.action
	frappe.throw(
		_("No workflow action to {0} {1} is open to you.").format(
			_("approve") if approve else _("reject"), doc.name
		),
		frappe.PermissionError,
	)


def is_hr_approver() -> bool:
	"""HR Manager and HR User can submit any Leave Application or Expense Claim in HRMS."""
	return bool(HR_APPROVER_ROLES & set(frappe.get_roles()))


def get_advance_approvals(employee: str) -> list:
	"""Draft Employee Advances waiting for me, through HRMS's own approval filters.

	HRMS has no approver field on Employee Advance, so without a workflow an HR Manager / HR
	User gets every draft they can read and anyone else the drafts routed to them
	(ess.create_advance shares each one with the employee's expense approver). With a
	workflow: drafts in states my roles can move.
	"""
	filters = get_filters(ADVANCE_DOCTYPE, employee, for_approval=True)
	if not get_workflow_name(ADVANCE_DOCTYPE) and not is_hr_approver():
		routed = routed_advances()
		if not routed:
			return []
		filters.name = ("in", routed)
	return frappe.get_list(
		ADVANCE_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"employee",
			"employee_name",
			"purpose",
			"posting_date",
			"advance_amount",
			"currency",
			"creation",
		],
		order_by="posting_date desc",
		limit=APPROVALS_LIMIT,
	)


def routed_advances() -> list[str]:
	"""Employee Advances shared with the session user with Submit: routed to me as approver."""
	DocShare = frappe.qb.DocType("DocShare")
	return (
		frappe.qb.from_(DocShare)
		.select(DocShare.share_name)
		.where(
			(DocShare.share_doctype == ADVANCE_DOCTYPE)
			& (DocShare.user == frappe.session.user)
			& (DocShare.submit == 1)
		)
	).run(pluck=True)


def check_not_own_advance(doc) -> None:
	own = frappe.db.get_value("Employee", doc.employee, "user_id")
	if own == frappe.session.user:
		frappe.throw(_("You cannot decide your own advance."), frappe.PermissionError)


def serialize_leave_approval(row) -> dict:
	days = flt(row.total_leave_days)
	return {
		"id": row.name,
		"kind": "leave",
		"ref": row.name,
		"who": row.employee_name,
		"employee": row.employee,
		"title": row.leave_type,
		"detail": strip_html(row.description or "").strip(),
		"from_date": row.from_date,
		"to_date": row.to_date,
		"half_day": bool(row.half_day),
		"half_day_date": row.half_day_date if row.half_day else None,
		"days": days,
		"amount": None,
		"submitted": row.creation,
		"can_reject": True,
	}


def serialize_claim_approval(row) -> dict:
	return {
		"id": row.name,
		"kind": "expense",
		"ref": row.name,
		"who": row.employee_name,
		"employee": row.employee,
		"title": row.expense_type,
		"detail": _("{0} expense lines").format(row.total_expenses) if cint(row.total_expenses) > 1 else "",
		"from_date": row.posting_date,
		"to_date": row.posting_date,
		"half_day": False,
		"half_day_date": None,
		"days": None,
		"amount": flt(row.total_claimed_amount),
		"submitted": row.creation,
		"can_reject": True,
	}


def serialize_advance_approval(row) -> dict:
	"""`can_reject` only with a workflow: HRMS has no rejected state for an advance."""
	return {
		"id": row.name,
		"kind": "advance",
		"ref": row.name,
		"who": row.employee_name,
		"employee": row.employee,
		"title": _("Employee Advance"),
		"detail": strip_html(row.purpose or "").strip(),
		"from_date": row.posting_date,
		"to_date": row.posting_date,
		"half_day": False,
		"half_day_date": None,
		"days": None,
		"amount": flt(row.advance_amount),
		"currency": row.currency,
		"submitted": row.creation,
		"can_reject": bool(get_workflow_name(ADVANCE_DOCTYPE)),
	}


# ---------- Sales snapshot ----------


@frappe.whitelist()
def get_sales_snapshot() -> dict:
	"""Open pipeline by stage, deals won this month and open deals closing within a week.

	Values are in the company currency (the deal's `base_opportunity_amount`). Covers the
	caller's widest crm_scope.
	"""
	capabilities = require("sales_insights", _("Only Sales Managers can see the sales snapshot."))
	Deal = frappe.qb.DocType("Opportunity")
	Stage = frappe.qb.DocType("Sales Stage")
	value = deal_value(Deal)
	is_open = open_stage(Stage)
	in_scope = owned_by(Deal.opportunity_owner, scope_users(capabilities.crm_scopes[-1]))

	stages = (
		frappe.qb.from_(Stage)
		.left_join(Deal)
		.on((Deal.sales_stage == Stage.name) & in_scope)
		.select(
			Stage.name.as_("stage"),
			Stage.tbo_stage_type.as_("type"),
			Count(Deal.name).as_("count"),
			Sum(value).as_("amount"),
			Sum(value * IfNull(Deal.probability, 0) / 100).as_("weighted"),
		)
		.where(is_open)
		.groupby(Stage.name, Stage.tbo_stage_type, Stage.tbo_position)
		.orderby(IfNull(Stage.tbo_position, 10_000))
		.orderby(Stage.name)
	).run(as_dict=True)

	today = getdate()
	month_start = today.replace(day=1)
	won = (
		frappe.qb.from_(Deal)
		.join(Stage)
		.on(Deal.sales_stage == Stage.name)
		.select(Count(Deal.name).as_("count"), Sum(value).as_("amount"))
		.where(
			in_scope
			& (Stage.tbo_stage_type == "Won")
			& (Deal.tbo_closed_on >= month_start)
			& (Deal.tbo_closed_on <= today)
		)
	).run(as_dict=True)[0]

	closing = (
		frappe.qb.from_(Deal)
		.join(Stage)
		.on(Deal.sales_stage == Stage.name)
		.select(
			Deal.name,
			Deal.customer_name.as_("organization"),
			Deal.contact_display.as_("lead_name"),
			Deal.sales_stage.as_("stage"),
			value.as_("amount"),
			Deal.probability,
			Deal.expected_closing.as_("expected_closure_date"),
			Deal.opportunity_owner.as_("deal_owner"),
			Case().when(Deal.expected_closing < today, 1).else_(0).as_("overdue"),
		)
		.where(in_scope & is_open & (Deal.expected_closing <= add_days(today, CLOSING_SOON_DAYS)))
		.orderby(Deal.expected_closing)
		.limit(50)
	).run(as_dict=True)

	return {
		"currency": default_currency(),
		"by_stage": [
			{
				"stage": row.stage,
				"type": row.type or "Ongoing",
				"count": row["count"],
				"amount": flt(row.amount),
			}
			for row in stages
		],
		"pipeline": sum(flt(row.amount) for row in stages),
		"weighted": sum(flt(row.weighted) for row in stages),
		"won_this_month": {"count": won["count"] or 0, "amount": flt(won.amount)},
		"closing_this_week": [
			{**row, "amount": flt(row.amount), "overdue": bool(row.overdue)} for row in closing
		],
	}
