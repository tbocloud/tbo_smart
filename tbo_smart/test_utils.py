"""Factories and toggles shared by the tbo_smart tests. Never define helpers inside test files."""

import erpnext
import frappe
from erpnext.setup.doctype.employee.test_employee import make_employee
from frappe.utils import add_days, get_year_ending, get_year_start, getdate, now_datetime, nowdate
from frappe.utils.nestedset import rebuild_tree

from tbo_smart.api.mobile.capabilities import get_capabilities
from tbo_smart.api.mobile.utils import INSIGHTS_CACHE_PREFIX

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


def make_attendance(employee: str, day: str, status: str = "Present", late_entry: int = 0) -> str:
	"""A submitted Attendance for one employee and day, inserted as Administrator."""
	attendance = frappe.get_doc(
		{
			"doctype": "Attendance",
			"employee": employee,
			"company": frappe.db.get_value("Employee", employee, "company"),
			"attendance_date": day,
			"status": status,
			"late_entry": late_entry,
		}
	)
	attendance.insert(ignore_permissions=True)
	attendance.submit()
	return attendance.name


def make_sales_hierarchy_node(user: str, reports_to: str | None = None) -> str:
	"""A CRM Sales Hierarchy node for `user` under the node `reports_to`; rebuilds the tree."""
	node = frappe.get_doc(
		{
			"doctype": "CRM Sales Hierarchy",
			"user": user,
			"reports_to": reports_to,
			"is_group": 1,
		}
	)
	node.insert(ignore_permissions=True)
	rebuild_tree("CRM Sales Hierarchy")
	return node.name


def enable_sales_hierarchy() -> None:
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)


def disable_sales_hierarchy(users: list[str] | tuple = ()) -> None:
	"""Turn the CRM sales hierarchy off and delete the nodes of `users`.

	CRM Lead and Deal inserts commit (their after_insert assigns and shares), so a setting
	changed in a test is already persisted; the cleanup is committed for the same reason.
	"""
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 0)
	for user in users:
		frappe.db.delete("CRM Sales Hierarchy", {"user": user})
	frappe.cache.delete_value("crm_sales_hierarchy_subtree")
	# CRM's committing after_insert already persisted the fixtures, so the undo must persist too.
	frappe.db.commit()  # nosemgrep


def clear_insights_cache() -> None:
	"""Drop every cached team or sales insight so a test sees its own fixtures."""
	frappe.cache.delete_keys(INSIGHTS_CACHE_PREFIX)


def age_document(doctype: str, name: str, days: int) -> None:
	"""Move a document's `modified` back by `days` without touching anything else."""
	frappe.db.set_value(doctype, name, "modified", add_days(now_datetime(), -days), update_modified=False)


def capabilities_as(user: str) -> frappe._dict:
	"""Switch the session to `user` and return their capabilities."""
	frappe.set_user(user)
	return get_capabilities()


def get_deal_base_value(name: str) -> float:
	"""A deal's value in the CRM base currency (deal_value x exchange_rate), as insights count it."""
	deal_value, exchange_rate = frappe.db.get_value("CRM Deal", name, ["deal_value", "exchange_rate"])
	return deal_value * (exchange_rate or 1)


def get_all_keys(value) -> list[str]:
	"""Every dict key anywhere in a nested API response."""
	keys = []
	if isinstance(value, dict):
		for key, item in value.items():
			keys += [key, *get_all_keys(item)]
	elif isinstance(value, list):
		for item in value:
			keys += get_all_keys(item)
	return keys


def delete_crm_records(owners: list[str] | tuple) -> None:
	"""Delete the CRM Deals, Leads, Notes and Tasks of `owners` and commit.

	CRM inserts commit, so their fixtures outlive the test's rollback; this keeps reruns
	and later classes starting from a clean slate.
	"""
	for doctype, owner_field in (
		("CRM Deal", "deal_owner"),
		("CRM Lead", "lead_owner"),
		("FCRM Note", "owner"),
		("CRM Task", "assigned_to"),
	):
		frappe.db.delete(doctype, {owner_field: ("in", list(owners))})
	# CRM's committing after_insert already persisted these fixtures, so the delete must persist too.
	frappe.db.commit()  # nosemgrep
