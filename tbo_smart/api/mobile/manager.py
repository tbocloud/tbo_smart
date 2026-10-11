"""TBO Smart manager façade (v1): my team today, approvals waiting for me, sales snapshot.

Every endpoint refuses callers who are not in the matching role. Contract:
docs/tbo-smart-api-ess-crm.md.
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions, get_workflow_name
from frappe.query_builder import Case
from frappe.query_builder.functions import Coalesce, Count, IfNull, Min, Sum
from frappe.utils import add_days, cint, flt, getdate, strip_html
from hrms.api import get_expense_claims, get_leave_applications

from tbo_smart.api.mobile.utils import get_session_employee

APPROVAL_DOCTYPES = {
	"leave": ("Leave Application", "leave_approver", "status"),
	"expense": ("Expense Claim", "expense_approver", "approval_status"),
}
APPROVER_ROLES = {"Leave Approver", "Expense Approver"}
SALES_MANAGER_ROLES = {"Sales Manager", "System Manager"}
APPROVALS_LIMIT = 100
CLOSING_SOON_DAYS = 7


# ---------- Team attendance ----------


@frappe.whitelist()
def get_team_today() -> list[dict]:
	"""Direct reports of the session employee: checked in, not yet, or on approved leave today.

	Reads other employees' check-ins, so it is limited to the caller's own direct reports
	(Employee.reports_to) and returns only the first IN time, never the location.
	"""
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
	if not team:
		frappe.throw(_("Only managers with direct reports can see team attendance."), frappe.PermissionError)

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


# ---------- Approvals ----------


@frappe.whitelist()
def get_approvals() -> list[dict]:
	"""Leave Applications and Expense Claims waiting for the session user, newest first.

	Uses the HRMS PWA queries: without a workflow, documents where I am the approver;
	with a workflow, documents in states my roles can move.
	"""
	require_roles(APPROVER_ROLES, _("Only leave or expense approvers have approvals."))
	user = frappe.session.user
	employee = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name") or ""
	leaves = get_leave_applications(employee, approver_id=user, for_approval=True, limit=APPROVALS_LIMIT)
	claims = get_expense_claims(employee, approver_id=user, for_approval=True, limit=APPROVALS_LIMIT)
	approvals = [serialize_leave_approval(row) for row in leaves] + [
		serialize_claim_approval(row) for row in claims
	]
	return sorted(approvals, key=lambda row: str(row["submitted"]), reverse=True)


@frappe.whitelist(methods=["POST"])
def decide_approval(kind: str, name: str, approve: bool, reason: str | None = None) -> dict:
	"""Approve or reject one leave application or expense claim through the HRMS path.

	With a workflow on the doctype the matching workflow action is applied; otherwise
	the status is set and the document submitted, exactly as HRMS approvers do.
	A rejection needs a reason; it is added to the document as a comment.
	"""
	if kind not in APPROVAL_DOCTYPES:
		frappe.throw(_("Kind must be leave or expense."))
	approve = cint(approve)
	if not approve and not (reason or "").strip():
		frappe.throw(_("Give a reason for the rejection."))

	doctype, approver_field, status_field = APPROVAL_DOCTYPES[kind]
	doc = frappe.get_doc(doctype, name)
	if doc.docstatus != 0:
		frappe.throw(_("{0} {1} has already been decided.").format(_(doctype), name))

	if get_workflow_name(doctype):
		apply_workflow(doc.as_dict(), pick_workflow_action(doc, approve))
		doc.reload()
	else:
		if doc.get(approver_field) != frappe.session.user:
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
	words = ("approv",) if approve else ("reject",)
	for transition in get_transitions(doc):
		label = f"{transition.action} {transition.next_state}".lower()
		if any(word in label for word in words):
			return transition.action
	frappe.throw(
		_("No workflow action to {0} {1} is open to you.").format(
			_("approve") if approve else _("reject"), doc.name
		),
		frappe.PermissionError,
	)


def require_roles(roles: set, message: str) -> None:
	if not roles & set(frappe.get_roles()):
		frappe.throw(message, frappe.PermissionError)


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
		"days": days,
		"amount": None,
		"submitted": row.creation,
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
		"days": None,
		"amount": flt(row.total_claimed_amount),
		"submitted": row.creation,
	}


# ---------- Sales snapshot ----------


@frappe.whitelist()
def get_sales_snapshot() -> dict:
	"""Open pipeline by stage, deals won this month and open deals closing within a week.

	Values are in the company's base currency (deal_value x exchange_rate), the same
	formula as Frappe CRM's own dashboard. Sales Managers see every deal, as in CRM.
	"""
	require_roles(SALES_MANAGER_ROLES, _("Only Sales Managers can see the sales snapshot."))
	Deal = frappe.qb.DocType("CRM Deal")
	Status = frappe.qb.DocType("CRM Deal Status")
	value = Coalesce(Deal.deal_value, 0) * IfNull(Deal.exchange_rate, 1)
	is_open = Status.type.notin(["Won", "Lost"])

	stages = (
		frappe.qb.from_(Status)
		.left_join(Deal)
		.on(Deal.status == Status.name)
		.select(
			Status.name.as_("stage"),
			Status.type.as_("type"),
			Count(Deal.name).as_("count"),
			Sum(value).as_("amount"),
			Sum(value * IfNull(Deal.probability, 0) / 100).as_("weighted"),
		)
		.where(is_open)
		.groupby(Status.name, Status.type, Status.position)
		.orderby(Status.position)
	).run(as_dict=True)

	today = getdate()
	month_start = today.replace(day=1)
	won = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(Count(Deal.name).as_("count"), Sum(value).as_("amount"))
		.where((Status.type == "Won") & (Deal.closed_date >= month_start) & (Deal.closed_date <= today))
	).run(as_dict=True)[0]

	closing = (
		frappe.qb.from_(Deal)
		.join(Status)
		.on(Deal.status == Status.name)
		.select(
			Deal.name,
			Deal.organization,
			Deal.lead_name,
			Deal.status.as_("stage"),
			value.as_("amount"),
			Deal.probability,
			Deal.expected_closure_date,
			Deal.deal_owner,
			Case().when(Deal.expected_closure_date < today, 1).else_(0).as_("overdue"),
		)
		.where(is_open & (Deal.expected_closure_date <= add_days(today, CLOSING_SOON_DAYS)))
		.orderby(Deal.expected_closure_date)
		.limit(50)
	).run(as_dict=True)

	return {
		"currency": frappe.db.get_single_value("FCRM Settings", "currency")
		or frappe.db.get_default("currency"),
		"by_stage": [
			{"stage": row.stage, "type": row.type, "count": row["count"], "amount": flt(row.amount)}
			for row in stages
		],
		"pipeline": sum(flt(row.amount) for row in stages),
		"weighted": sum(flt(row.weighted) for row in stages),
		"won_this_month": {"count": won["count"] or 0, "amount": flt(won.amount)},
		"closing_this_week": [
			{**row, "amount": flt(row.amount), "overdue": bool(row.overdue)} for row in closing
		],
	}
