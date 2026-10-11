"""Factories and toggles shared by the tbo_smart tests. Never define helpers inside test files."""

import erpnext
import frappe
from erpnext.setup.doctype.employee.test_employee import make_employee
from frappe.utils import add_days, get_year_ending, get_year_start, getdate, nowdate

TEST_HOLIDAY_LIST = "TBO Smart Test Holidays"


def make_user(email: str, roles: list[str] | tuple = ()) -> str:
	"""A website-enabled User with exactly the extra roles given (adds, never removes)."""
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.add_roles(*roles)
	return email


def make_staff(email: str, roles: list[str] | tuple = (), **employee_fields) -> str:
	"""An active Employee linked to a User with the Employee role plus `roles`.

	`employee_fields` go onto the Employee (reports_to, leave_approver, expense_approver, ...).
	Returns the Employee name.
	"""
	make_user(email, ("Employee", *roles))
	ensure_company_holiday_list()
	return make_employee(email, company=erpnext.get_default_company(), **employee_fields)


def ensure_company_holiday_list() -> str:
	"""Give the default company a holiday list for this year (one holiday, no weekly offs)."""
	company = erpnext.get_default_company()
	if not frappe.db.exists("Holiday List", TEST_HOLIDAY_LIST):
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": TEST_HOLIDAY_LIST,
				"from_date": get_year_start(getdate()),
				"to_date": get_year_ending(getdate()),
				"holidays": [{"holiday_date": get_year_ending(getdate()), "description": "Year end"}],
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value("Company", company, "default_holiday_list", TEST_HOLIDAY_LIST)
	return TEST_HOLIDAY_LIST


def set_hr_settings(**values) -> None:
	"""Write HR Settings fields directly (e.g. allow_employee_checkin_from_mobile_app=1)."""
	for field, value in values.items():
		frappe.db.set_single_value("HR Settings", field, value)


def make_leave_type(name: str = "TBO Smart Casual Leave") -> str:
	"""A paid Leave Type that allows a negative balance, so tests can apply without an allocation."""
	if not frappe.db.exists("Leave Type", name):
		frappe.get_doc({"doctype": "Leave Type", "leave_type_name": name, "allow_negative": 1}).insert(
			ignore_permissions=True
		)
	return name


def make_leave_allocation(employee: str, leave_type: str, days: float = 10) -> str:
	"""A submitted Leave Allocation for this calendar year."""
	allocation = frappe.get_doc(
		{
			"doctype": "Leave Allocation",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": get_year_start(getdate()),
			"to_date": get_year_ending(getdate()),
			"new_leaves_allocated": days,
		}
	)
	allocation.insert(ignore_permissions=True)
	allocation.submit()
	return allocation.name


def make_leave_application(
	employee: str, leave_type: str, days_ahead: int = 7, approver: str | None = None
) -> str:
	"""An Open one-day Leave Application, inserted as Administrator."""
	day = add_days(nowdate(), days_ahead)
	application = frappe.get_doc(
		{
			"doctype": "Leave Application",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": day,
			"to_date": day,
			"leave_approver": approver,
			"status": "Open",
			"posting_date": nowdate(),
		}
	)
	application.insert(ignore_permissions=True)
	return application.name


def make_expense_claim_type(name: str = "TBO Smart Travel") -> str:
	"""An Expense Claim Type with a default expense account for the default company."""
	company = erpnext.get_default_company()
	if not frappe.db.exists("Expense Claim Type", name):
		account = frappe.db.get_value(
			"Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name"
		)
		frappe.get_doc(
			{
				"doctype": "Expense Claim Type",
				"expense_type": name,
				"accounts": [{"company": company, "default_account": account}],
			}
		).insert(ignore_permissions=True)
	return name


def make_salary_slip_record(employee: str, start_date: str, net_pay: float = 1000) -> str:
	"""A submitted Salary Slip row written straight to the DB (no salary structure needed).

	Only for permission and listing tests: it skips Salary Slip validation on purpose.
	"""
	slip = frappe.new_doc("Salary Slip")
	slip.update(
		{
			"employee": employee,
			"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
			"company": erpnext.get_default_company(),
			"posting_date": start_date,
			"start_date": start_date,
			"end_date": add_days(start_date, 27),
			"gross_pay": net_pay,
			"net_pay": net_pay,
			"docstatus": 1,
		}
	)
	slip.set_new_name()
	slip.db_insert()
	return slip.name


def make_crm_lead(owner: str, first_name: str = "Test Lead", **fields) -> str:
	"""A CRM Lead owned by `owner`, inserted as Administrator."""
	lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": first_name, "lead_owner": owner, **fields})
	lead.insert(ignore_permissions=True)
	return lead.name


def make_crm_deal(owner: str, status: str = "Qualification", deal_value: float = 1000, **fields) -> str:
	"""A CRM Deal owned by `owner`, inserted as Administrator."""
	deal = frappe.get_doc(
		{"doctype": "CRM Deal", "deal_owner": owner, "status": status, "deal_value": deal_value, **fields}
	)
	deal.insert(ignore_permissions=True)
	return deal.name


def enable_mobile_push() -> None:
	frappe.db.set_single_value("TBO Smart Settings", "enable_mobile_push", 1)


def disable_mobile_push() -> None:
	frappe.db.set_single_value("TBO Smart Settings", "enable_mobile_push", 0)


def make_mobile_device(user: str, token: str, platform: str = "android") -> str:
	"""A TBO Mobile Device row for `user`, owned by that user."""
	device = frappe.get_doc(
		{"doctype": "TBO Mobile Device", "user": user, "expo_push_token": token, "platform": platform}
	)
	device.insert(ignore_permissions=True)
	frappe.db.set_value("TBO Mobile Device", device.name, "owner", user)
	return device.name
