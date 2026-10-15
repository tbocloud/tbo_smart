"""Phone numbers, working days and leave for the smart lead features (docs/smart-leads.md)."""

import re
from urllib.parse import quote

import erpnext
import frappe
from frappe.utils import add_days, getdate

NON_DIGITS = re.compile(r"\D")
INDIA = "91"


def normalise_phone(phone: str | None) -> str | None:
	"""International digits without "+", or None when there is no usable number.

	A 10-digit number is Indian (TBO's default market): +91 is added. A trunk 0 before 10 digits
	is dropped the same way. "00" or "+" starts an international number, kept as it is.
	"""
	raw = (phone or "").strip()
	digits = NON_DIGITS.sub("", raw)
	if raw.startswith("00"):
		digits = digits[2:]
	elif not raw.startswith("+"):
		if len(digits) == 11 and digits.startswith("0"):
			digits = digits[1:]
		if len(digits) == 10:
			digits = INDIA + digits
	return digits if 8 <= len(digits) <= 15 else None


def tel_link(phone: str | None) -> str | None:
	digits = normalise_phone(phone)
	return f"tel:+{digits}" if digits else None


def whatsapp_link(phone: str | None, text: str | None = None) -> str | None:
	"""wa.me link to the number, with the message prefilled when given."""
	digits = normalise_phone(phone)
	if not digits:
		return None
	return f"https://wa.me/{digits}" + (f"?text={quote(text)}" if text else "")


class WorkCalendar:
	"""Working days from the default company's holiday list (weekly offs and holidays), and who
	is on approved HRMS leave. Without a holiday list every day is a working day."""

	def __init__(self):
		company = erpnext.get_default_company()
		holiday_list = company and frappe.get_cached_value("Company", company, "default_holiday_list")
		self.holidays = (
			{
				getdate(day)
				for day in frappe.get_all(
					"Holiday", filters={"parent": holiday_list}, pluck="holiday_date", limit_page_length=0
				)
			}
			if holiday_list
			else set()
		)

	def is_working_day(self, day) -> bool:
		return getdate(day) not in self.holidays

	def working_days_between(self, start, end) -> int:
		"""Working days after `start` up to and including `end` (0 when end <= start)."""
		start, end = getdate(start), getdate(end)
		count, day = 0, start
		while day < end:
			day = getdate(add_days(day, 1))
			count += self.is_working_day(day)
		return count

	def next_working_day(self, day, skip: int = 1):
		"""The `skip`-th working day after `day`."""
		day = getdate(day)
		while skip:
			day = getdate(add_days(day, 1))
			skip -= self.is_working_day(day)
		return day


def on_leave(users, day) -> dict[str, str]:
	"""{user: last day of leave} for users on approved HRMS leave on `day`. A half day doesn't
	count: they still work part of it. Empty without HRMS."""
	users = [user for user in set(users) if user]
	if not users or "hrms" not in frappe.get_installed_apps():
		return {}
	day = getdate(day)
	Employee = frappe.qb.DocType("Employee")
	Leave = frappe.qb.DocType("Leave Application")
	rows = (
		frappe.qb.from_(Leave)
		.join(Employee)
		.on(Employee.name == Leave.employee)
		.select(Employee.user_id, Leave.to_date, Leave.from_date, Leave.half_day, Leave.half_day_date)
		.where(
			(Employee.user_id.isin(users))
			& (Leave.status == "Approved")
			& (Leave.docstatus == 1)
			& (Leave.from_date <= day)
			& (Leave.to_date >= day)
		)
	).run(as_dict=True)
	away = {}
	for row in rows:
		half_day = row.half_day and (
			getdate(row.from_date) == getdate(row.to_date)
			or (row.half_day_date and getdate(row.half_day_date) == day)
		)
		if not half_day:
			away[row.user_id] = max(getdate(row.to_date), getdate(away.get(row.user_id) or row.to_date))
	return {user: str(until) for user, until in away.items()}


def enabled_users(users) -> set[str]:
	"""The enabled system users among `users`."""
	users = [user for user in set(users) if user]
	if not users:
		return set()
	return set(frappe.get_all("User", filters={"name": ("in", users), "enabled": 1}, pluck="name"))


def users_with_role(role: str | None) -> list[str]:
	"""Enabled system users holding `role` (Administrator left out)."""
	if not role:
		return []
	User = frappe.qb.DocType("User")
	HasRole = frappe.qb.DocType("Has Role")
	return (
		frappe.qb.from_(User)
		.join(HasRole)
		.on((HasRole.parent == User.name) & (HasRole.parenttype == "User"))
		.select(User.name)
		.distinct()
		.where(
			(User.enabled == 1)
			& (User.user_type == "System User")
			& (HasRole.role == role)
			& User.name.notin(["Administrator", "Guest"])
		)
		.orderby(User.name)
	).run(pluck=True)
