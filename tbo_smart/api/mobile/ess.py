"""TBO Smart ESS façade (v1): the employee's own HR data, always for the session user.

Thin wrappers over HRMS. Reads of the employee's own documents go through
frappe.get_list so the site's DocType and User Permissions still apply on top of
the explicit "own employee" filter. Contract: docs/tbo-smart-api-ess-crm.md.
"""

from urllib.parse import quote

import frappe
from erpnext.setup.doctype.employee.employee import get_holiday_list_for_employee
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import (
	add_to_date,
	cint,
	flt,
	get_first_day,
	get_last_day,
	getdate,
	now_datetime,
	nowdate,
	strip_html,
)
from frappe.utils.print_format import download_pdf
from hrms.api import (
	get_attendance_calendar_events,
	get_company_cost_center_and_expense_account,
	get_expense_approval_details,
	get_expense_claim_types,
	get_holidays_for_employee,
	get_leave_approval_details,
	upload_base64_file,
)
from hrms.api import get_leave_types as hrms_get_leave_types
from hrms.hr.doctype.leave_application.leave_application import get_leave_details, get_number_of_leave_days
from hrms.hr.doctype.shift_assignment.shift_assignment import get_employee_shift

from tbo_smart.api.mobile.capabilities import get_capabilities, require
from tbo_smart.api.mobile.utils import get_full_names, get_session_employee, page_envelope, page_window
from tbo_smart.checkin import geofence
from tbo_smart.checkin.place import clean_device_place

DUPLICATE_PUNCH_SECONDS = 60
APP_DEVICE_ID = "TBO Smart"


# ---------- Profile ----------


@frappe.whitelist()
def get_me() -> dict:
	"""The session user's Employee and capabilities (what the app may show them).

	is_manager, is_approver and is_sales_manager are v1 aliases of team_today,
	approve_leave or approve_expense, and crm_manager.
	"""
	employee = get_session_employee(
		[
			"user_id",
			"designation",
			"department",
			"branch",
			"reports_to",
			"date_of_joining",
			"cell_number",
			"image",
		]
	)
	reports_to_name = (
		frappe.db.get_value("Employee", employee.reports_to, "employee_name") if employee.reports_to else None
	)
	capabilities = get_capabilities()
	return {
		"name": employee.name,
		"employee_name": employee.employee_name,
		"user": employee.user_id,
		"designation": employee.designation,
		"department": employee.department,
		"company": employee.company,
		"branch": employee.branch,
		"reports_to": reports_to_name,
		"reports_to_id": employee.reports_to,
		"date_of_joining": employee.date_of_joining,
		"phone": employee.cell_number,
		"image": employee.image,
		"is_manager": capabilities.team_today,
		"is_approver": capabilities.approve_leave or capabilities.approve_expense,
		"is_sales_manager": capabilities.crm_manager,
		"capabilities": capabilities,
	}


# ---------- Check-in ----------


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def check_in(
	log_type: str,
	latitude: float | None = None,
	longitude: float | None = None,
	accuracy: float | None = None,
	place_name: str | None = None,
) -> dict:
	"""Record an Employee Checkin at server time.

	HRMS validates the shift; the Employee Checkin validate event (tbo_smart.checkin) applies
	the check-in radius. `place_name` is the phone's own reverse-geocoded address; without it the
	server looks the place up in the background."""
	if log_type not in ("IN", "OUT"):
		frappe.throw(_("Log type must be IN or OUT."))
	employee = get_session_employee()
	require("checkin_allowed", _("Check-in from the mobile app is turned off in HR Settings."))
	now = now_datetime()
	if frappe.db.exists(
		"Employee Checkin",
		{"employee": employee.name, "time": (">=", add_to_date(now, seconds=-DUPLICATE_PUNCH_SECONDS))},
	):
		frappe.throw(_("You punched less than a minute ago."), frappe.DuplicateEntryError)

	checkin = frappe.new_doc("Employee Checkin")
	checkin.employee = employee.name
	checkin.log_type = log_type
	# Server time only: for the Employee role HRMS also resets `time` (permlevel 1) to its "Now" default.
	checkin.time = now
	checkin.latitude = flt(latitude) if latitude is not None else None
	checkin.longitude = flt(longitude) if longitude is not None else None
	checkin.device_id = describe_fix(accuracy)
	checkin.tbo_location_accuracy = flt(accuracy) if accuracy is not None else None
	checkin.tbo_location_name = clean_device_place(place_name)
	checkin.insert()
	return serialize_checkin(checkin)


@frappe.whitelist()
def get_today() -> dict:
	"""Today's punches, shift and attendance for the session employee."""
	employee = get_session_employee()
	today = getdate()
	checkins = frappe.get_list(
		"Employee Checkin",
		filters={"employee": employee.name, "time": ("between", [today, today])},
		fields=CHECKIN_FIELDS,
		order_by="time asc",
	)
	shift = get_employee_shift(employee.name, now_datetime(), consider_default_shift=True)
	status = "not_checked_in"
	if checkins:
		status = "checked_in" if checkins[-1].log_type == "IN" else "checked_out"
	return {
		"date": today,
		"server_time": now_datetime(),
		"shift_name": shift.shift_type.name if shift else None,
		"shift_start": shift.start_datetime.strftime("%H:%M") if shift else None,
		"shift_end": shift.end_datetime.strftime("%H:%M") if shift else None,
		"status": status,
		"attendance": frappe.db.get_value(
			"Attendance", {"employee": employee.name, "attendance_date": today, "docstatus": 1}, "status"
		),
		"checkins": [serialize_checkin(row) for row in checkins],
	}


@frappe.whitelist()
def get_checkin_locations() -> dict:
	"""Where the session employee may check in today, so the app can show the distance
	before the punch. The server stays authoritative: check_in applies the same rule."""
	employee = get_session_employee()
	policy = geofence.get_policy(employee.name, frappe.session.user, now_datetime())
	return {
		"enforced": policy.enforced,
		"exempt": policy.exempt,
		"max_accuracy": policy.max_accuracy,
		"tolerance_cap": policy.tolerance_cap,
		"locations": [
			{
				"name": row.name,
				"location_name": row.location_name,
				"latitude": row.latitude,
				"longitude": row.longitude,
				"radius": row.radius,
			}
			for row in policy.locations
		],
	}


CHECKIN_FIELDS = [
	"name",
	"log_type",
	"time",
	"device_id",
	"latitude",
	"longitude",
	"tbo_location_name",
	"tbo_outside_radius",
]


def describe_fix(accuracy: float | None) -> str:
	"""HRMS has no accuracy field; keep it readable in "Location / Device ID"."""
	if accuracy is None:
		return APP_DEVICE_ID
	return f"{APP_DEVICE_ID} ±{round(flt(accuracy))} m"


def serialize_checkin(row) -> dict:
	return {
		"name": row.name,
		"log_type": row.log_type,
		"time": row.time,
		"location": row.device_id,
		"place_name": row.tbo_location_name,
		"outside_radius": bool(row.tbo_outside_radius),
		"latitude": row.latitude,
		"longitude": row.longitude,
	}


# ---------- Attendance and holidays ----------


@frappe.whitelist()
def get_attendance(year: int, month: int) -> list[dict]:
	"""One row per day that has an attendance record or a holiday. month is 1-12."""
	employee = get_session_employee()
	from_date = get_first_day(f"{cint(year)}-{cint(month):02d}-01")
	to_date = get_last_day(from_date)
	events = get_attendance_calendar_events(str(from_date), str(to_date))
	weekly_offs = get_weekly_offs(employee.name, from_date, to_date)
	return [
		{"date": date, "status": "Weekly off" if status == "Holiday" and date in weekly_offs else status}
		for date, status in sorted(events.items())
	]


def get_weekly_offs(employee: str, from_date, to_date) -> set[str]:
	holiday_list = get_holiday_list_for_employee(employee, raise_exception=False)
	if not holiday_list:
		return set()
	Holiday = frappe.qb.DocType("Holiday")
	dates = (
		frappe.qb.from_(Holiday)
		.select(Holiday.holiday_date)
		.where(
			(Holiday.parent == holiday_list)
			& (Holiday.weekly_off == 1)
			& (Holiday.holiday_date.between(from_date, to_date))
		)
	).run(pluck=True)
	return {str(date) for date in dates}


@frappe.whitelist()
def get_holidays(year: int | None = None) -> list[dict]:
	"""Public holidays (not weekly offs) on the employee's holiday list for a year."""
	employee = get_session_employee()
	year = cint(year) or getdate().year
	return [
		{"date": row.holiday_date, "description": row.description}
		for row in get_holidays_for_employee(employee.name)
		if getdate(row.holiday_date).year == year
	]


# ---------- Leave ----------


@frappe.whitelist()
def get_leave_balances() -> list[dict]:
	"""Allocated, used, pending and remaining days per leave type, as of today."""
	employee = get_session_employee()
	allocation = get_leave_details(employee.name, nowdate())["leave_allocation"]
	return [
		{
			"leave_type": leave_type,
			"allocated": row["total_leaves"],
			"used": row["leaves_taken"],
			"pending": row["leaves_pending_approval"],
			"balance": row["remaining_leaves"],
		}
		for leave_type, row in allocation.items()
	]


@frappe.whitelist()
def get_leave_types() -> list[str]:
	"""Leave types the employee can apply for today (allocated types plus leave without pay)."""
	employee = get_session_employee()
	return hrms_get_leave_types(employee.name, nowdate())


@frappe.whitelist()
def get_leave_approver() -> dict:
	"""Who will approve the employee's leave, and whether HR requires one."""
	employee = get_session_employee()
	details = get_leave_approval_details(employee.name)
	return {
		"leave_approver": details.get("leave_approver"),
		"leave_approver_name": details.get("leave_approver_name"),
		"is_mandatory": bool(details.get("is_mandatory")),
	}


@frappe.whitelist()
def get_leave_applications(page: int = 1, page_length: int = 20) -> dict:
	"""The employee's own leave applications, newest first."""
	employee = get_session_employee()
	start, length = page_window(page, page_length)
	rows = frappe.get_list(
		"Leave Application",
		filters={"employee": employee.name},
		fields=LEAVE_FIELDS,
		order_by="from_date desc, creation desc",
		start=start,
		page_length=length + 1,
	)
	names = get_full_names({row.leave_approver for row in rows})
	return page_envelope([serialize_leave(row, names) for row in rows], page, page_length)


@frappe.whitelist(methods=["POST"])
def apply_leave(
	leave_type: str,
	from_date: str,
	to_date: str,
	half_day: bool = False,
	reason: str | None = None,
	half_day_date: str | None = None,
) -> dict:
	"""Create an Open Leave Application for the session employee, routed to their leave approver.

	On a leave of several days, `half_day_date` picks the day that is a half day (default:
	the first day); it must fall inside the range."""
	employee = get_session_employee()
	half_day_date = resolve_half_day_date(from_date, to_date, half_day, half_day_date)
	approver = get_leave_approval_details(employee.name).get("leave_approver")
	application = frappe.new_doc("Leave Application")
	application.update(
		{
			"employee": employee.name,
			"company": employee.company,
			"leave_type": leave_type,
			"from_date": from_date,
			"to_date": to_date,
			"half_day": cint(half_day),
			"half_day_date": half_day_date,
			"description": reason,
			"leave_approver": approver,
			"posting_date": nowdate(),
			"status": "Open",
		}
	)
	application.insert()
	return serialize_leave(application, get_full_names({approver}))


@frappe.whitelist()
def get_leave_days(
	leave_type: str,
	from_date: str,
	to_date: str,
	half_day: bool = False,
	half_day_date: str | None = None,
) -> dict:
	"""Leave days the application would count, from HRMS's own calculation: a half day takes
	0.5 off its date, and holidays and weekly offs are left out unless the leave type
	includes them."""
	employee = get_session_employee()
	half_day_date = resolve_half_day_date(from_date, to_date, half_day, half_day_date)
	days = get_number_of_leave_days(
		employee.name, leave_type, from_date, to_date, cint(half_day), half_day_date
	)
	return {"days": flt(days), "half_day_date": half_day_date}


def resolve_half_day_date(from_date: str, to_date: str, half_day, half_day_date: str | None):
	"""The half day's date, or None without a half day. A one-day leave's half day is that day."""
	if getdate(to_date) < getdate(from_date):
		frappe.throw(_("To date cannot be before from date."))
	if not cint(half_day):
		return None
	if getdate(from_date) == getdate(to_date):
		return str(getdate(from_date))
	day = getdate(half_day_date or from_date)
	if not getdate(from_date) <= day <= getdate(to_date):
		frappe.throw(
			_("The half day must be between {0} and {1}.").format(
				frappe.format(getdate(from_date), "Date"), frappe.format(getdate(to_date), "Date")
			)
		)
	return str(day)


@frappe.whitelist(methods=["POST"])
def cancel_leave(name: str) -> dict:
	"""Withdraw an Open application (needs Delete) or cancel a submitted one (needs Cancel)."""
	employee = get_session_employee()
	application = frappe.get_doc("Leave Application", name)
	if application.employee != employee.name:
		frappe.throw(_("You can only cancel your own leave."), frappe.PermissionError)

	if application.docstatus == 0 and application.status == "Open":
		frappe.delete_doc("Leave Application", name)
		return {"name": name, "status": "Cancelled", "withdrawn": True}
	if application.docstatus == 1 and application.status == "Approved":
		application.cancel()
		return {"name": name, "status": "Cancelled", "withdrawn": False}
	frappe.throw(_("Only open or approved leave can be cancelled."))


LEAVE_FIELDS = [
	"name",
	"leave_type",
	"from_date",
	"to_date",
	"half_day",
	"half_day_date",
	"total_leave_days",
	"status",
	"docstatus",
	"leave_approver",
	"description",
	"posting_date",
]


def serialize_leave(row, approver_names: dict) -> dict:
	return {
		"name": row.name,
		"leave_type": row.leave_type,
		"from_date": row.from_date,
		"to_date": row.to_date,
		"half_day": bool(row.half_day),
		"half_day_date": row.half_day_date,
		"days": flt(row.total_leave_days),
		"status": "Cancelled" if row.docstatus == 2 else row.status,
		"approver": approver_names.get(row.leave_approver, row.leave_approver),
		"approver_id": row.leave_approver,
		"reason": row.description,
		"posting_date": row.posting_date,
	}


# ---------- Expense claims ----------


@frappe.whitelist()
def get_expense_types() -> list[str]:
	return [row.name for row in get_expense_claim_types()]


@frappe.whitelist()
def get_expense_claims(page: int = 1, page_length: int = 20) -> dict:
	"""The employee's own expense claims, newest first, with the first line and receipt."""
	employee = get_session_employee()
	start, length = page_window(page, page_length)
	claims = frappe.get_list(
		"Expense Claim",
		filters={"employee": employee.name, "docstatus": ("!=", 2)},
		fields=CLAIM_FIELDS,
		order_by="posting_date desc, creation desc",
		start=start,
		page_length=length + 1,
	)
	return page_envelope(serialize_claims(claims), page, page_length)


@frappe.whitelist(methods=["POST"])
def create_expense_claim(
	expense_type: str,
	amount: float,
	expense_date: str,
	description: str | None = None,
	receipt_filename: str | None = None,
	receipt_base64: str | None = None,
) -> dict:
	"""Create a draft Expense Claim for the session employee, with an optional private receipt."""
	employee = get_session_employee()
	if flt(amount) <= 0:
		frappe.throw(_("Amount must be more than zero."))
	if bool(receipt_filename) != bool(receipt_base64):
		frappe.throw(_("Send both the receipt file name and its content."))

	accounts = get_company_cost_center_and_expense_account(employee.company) or {}
	claim = frappe.new_doc("Expense Claim")
	claim.update(
		{
			"employee": employee.name,
			"company": employee.company,
			"posting_date": nowdate(),
			"expense_approver": get_expense_approval_details(employee.name).get("expense_approver"),
			"payable_account": accounts.get("default_expense_claim_payable_account"),
			"cost_center": accounts.get("cost_center"),
		}
	)
	claim.append(
		"expenses",
		{
			"expense_date": expense_date,
			"expense_type": expense_type,
			"amount": flt(amount),
			"description": description,
		},
	)
	claim.insert()
	if receipt_base64:
		upload_base64_file(receipt_base64, receipt_filename, "Expense Claim", claim.name)
	return serialize_claims([claim])[0]


CLAIM_FIELDS = [
	"name",
	"posting_date",
	"approval_status",
	"status",
	"docstatus",
	"expense_approver",
	"total_claimed_amount",
	"total_sanctioned_amount",
	"company",
]


def serialize_claims(claims: list) -> list[dict]:
	"""Map claims to the app shape with two batched queries (lines, receipts)."""
	names = [claim.name for claim in claims]
	if not names:
		return []
	Detail = frappe.qb.DocType("Expense Claim Detail")
	lines = (
		frappe.qb.from_(Detail)
		.select(Detail.parent, Detail.expense_type, Detail.expense_date, Detail.description)
		.where((Detail.parent.isin(names)) & (Detail.parenttype == "Expense Claim"))
		.orderby(Detail.idx)
	).run(as_dict=True)
	first_line = {}
	for line in lines:
		first_line.setdefault(line.parent, line)

	File = frappe.qb.DocType("File")
	files = (
		frappe.qb.from_(File)
		.select(File.attached_to_name, File.file_name)
		.where((File.attached_to_doctype == "Expense Claim") & (File.attached_to_name.isin(names)))
		.orderby(File.creation)
	).run(as_dict=True)
	receipts = {}
	for file in files:
		receipts.setdefault(file.attached_to_name, file.file_name)

	approvers = get_full_names({claim.expense_approver for claim in claims})
	result = []
	for claim in claims:
		line = first_line.get(claim.name) or frappe._dict()
		result.append(
			{
				"name": claim.name,
				"expense_type": line.expense_type,
				"amount": flt(claim.total_claimed_amount),
				"sanctioned_amount": flt(claim.total_sanctioned_amount),
				"date": line.expense_date or claim.posting_date,
				"description": strip_html(line.description or "").strip(),
				"status": claim_status(claim),
				"approver": approvers.get(claim.expense_approver, claim.expense_approver),
				"approver_id": claim.expense_approver,
				"receipt": receipts.get(claim.name),
			}
		)
	return result


def claim_status(claim) -> str:
	if claim.status == "Paid":
		return "Paid"
	if claim.approval_status in ("Approved", "Rejected"):
		return claim.approval_status
	return "Pending"


# ---------- Salary slips ----------


@frappe.whitelist()
def get_salary_slips(page: int = 1, page_length: int = 12) -> dict:
	"""The employee's own submitted salary slips, newest first, each with a PDF URL."""
	employee = get_session_employee()
	start, length = page_window(page, page_length)
	slips = frappe.get_list(
		"Salary Slip",
		filters={"employee": employee.name, "docstatus": 1},
		fields=[
			"name",
			"start_date",
			"end_date",
			"posting_date",
			"currency",
			"gross_pay",
			"total_deduction",
			"net_pay",
			"total_working_days",
			"payment_days",
		],
		order_by="start_date desc",
		start=start,
		page_length=length + 1,
	)
	rows = [
		{
			"name": slip.name,
			"month": getdate(slip.start_date).strftime("%Y-%m"),
			"start_date": slip.start_date,
			"end_date": slip.end_date,
			"currency": slip.currency,
			"gross": flt(slip.gross_pay),
			"deductions": flt(slip.total_deduction),
			"net_pay": flt(slip.net_pay),
			"working_days": flt(slip.total_working_days),
			"payment_days": flt(slip.payment_days),
			"pdf_url": f"/api/method/tbo_smart.api.mobile.ess.download_salary_slip?name={quote(slip.name)}",
		}
		for slip in slips
	]
	return page_envelope(rows, page, page_length)


@frappe.whitelist(methods=["GET"])
def download_salary_slip(name: str) -> None:
	"""Stream the PDF of one of the session employee's own submitted slips."""
	employee = get_session_employee()
	slip = frappe.db.get_value("Salary Slip", name, ["employee", "docstatus"], as_dict=True)
	if not slip or slip.employee != employee.name or slip.docstatus != 1:
		frappe.throw(_("Salary slip not found."), frappe.PermissionError)
	print_format = frappe.get_meta("Salary Slip").default_print_format or "Standard"
	download_pdf("Salary Slip", name, format=print_format)
