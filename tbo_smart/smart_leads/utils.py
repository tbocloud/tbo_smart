"""Phone numbers, working days and leave for the smart lead features (docs/smart-leads.md)."""

import re
from functools import lru_cache
from urllib.parse import quote

import erpnext
import frappe
from frappe.geo.country_info import get_country_info
from frappe.utils import add_days, getdate

NON_DIGITS = re.compile(r"\D")
INDIA = "91"


def system_country() -> str | None:
	"""The site's country (System Settings → Country)."""
	return frappe.db.get_single_value("System Settings", "country")


@lru_cache(maxsize=32)
def dialling_code(country: str | None) -> str:
	"""The country's dialling code as digits ("Saudi Arabia" → "966"); India's for none or unknown."""
	isd = get_country_info(country).get("isd") if country else None
	return NON_DIGITS.sub("", isd or "") or INDIA


def default_country_code() -> str:
	"""The code a number typed without one gets: the site's country's, India's when it has none."""
	return dialling_code(system_country())


# digits of a national number (no country code, no trunk 0), by dialling code; others: 10
NATIONAL_DIGITS = {"91": 10, "966": 9, "971": 9, "974": 8, "965": 8, "973": 8, "968": 8}


def match_digits() -> int:
	"""How many trailing digits make two numbers the same: the length of a national number in
	the site's country (India 10, Saudi Arabia and the UAE 9). Then 050 123 4567, 501234567 and
	+966 50 123 4567 are one number however a lead's fields hold it."""
	return NATIONAL_DIGITS.get(default_country_code(), 10)


def phone_tail(digits: str | None) -> str | None:
	"""The last `match_digits` digits of a number: the key numbers are matched on."""
	return digits[-match_digits() :] if digits else None


# weekdays off (Monday 0 … Sunday 6), by dialling code; others: Sunday
WEEKENDS = {"966": (4, 5), "974": (4, 5), "965": (4, 5), "973": (4, 5), "968": (4, 5), "971": (5, 6)}
SUNDAY_ONLY = (6,)


def weekly_off_days() -> tuple[int, ...]:
	"""The site country's weekend (Friday and Saturday in Saudi Arabia, Sunday in India)."""
	return WEEKENDS.get(default_country_code(), SUNDAY_ONLY)


def is_weekly_off(day) -> bool:
	return getdate(day).weekday() in weekly_off_days()


def normalise_phone(phone: str | None, country: str | None = None) -> str | None:
	"""International digits without "+", or None when there is no usable number.

	"+" or "00" starts an international number, kept as it is. Otherwise a single trunk 0 is
	dropped and the `country` code is added (default: the site's country, System Settings →
	Country; India, 91, when unset), unless the number already starts with it and is longer than
	10 digits ("919847012345", "966501234567").
	"""
	raw = (phone or "").strip()
	digits = NON_DIGITS.sub("", raw)
	if raw.startswith("00"):
		digits = digits[2:]
	elif not raw.startswith("+"):
		country = country or default_country_code()
		digits = digits.removeprefix("0")
		if len(digits) <= 10 or not digits.startswith(country):
			digits = country + digits
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
