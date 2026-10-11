"""Shared plumbing for the TBO Smart mobile façades: the session employee, paging and insight periods."""

from itertools import pairwise

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	add_months,
	date_diff,
	flt,
	get_first_day,
	get_first_day_of_week,
	get_quarter_start,
	get_year_start,
	getdate,
)

MAX_PAGE_LENGTH = 100


class NoEmployeeError(frappe.DoesNotExistError):
	"""The session user has no active Employee record (Employee.user_id)."""


def get_session_employee(fields: list[str] | None = None) -> frappe._dict:
	"""The active Employee linked to the session user, or NoEmployeeError.

	The identity always comes from the session; no endpoint accepts an employee id.
	"""
	employee = frappe.db.get_value(
		"Employee",
		{"user_id": frappe.session.user, "status": "Active"},
		["name", "employee_name", "company", *(fields or [])],
		as_dict=True,
	)
	if not employee:
		frappe.throw(
			_("No active Employee record is linked to {0}. Ask HR to link one.").format(frappe.session.user),
			NoEmployeeError,
			title=_("No employee record"),
		)
	return employee


def page_window(page: int, page_length: int) -> tuple[int, int]:
	"""Start offset and clamped page length for a 1-based page number."""
	page_length = min(max(int(page_length or 20), 1), MAX_PAGE_LENGTH)
	page = max(int(page or 1), 1)
	return (page - 1) * page_length, page_length


def page_envelope(rows: list, page: int, page_length: int) -> dict:
	"""Wrap a list fetched with page_length + 1 rows into {data, page, has_more}."""
	_start, page_length = page_window(page, page_length)
	return {"data": rows[:page_length], "page": max(int(page or 1), 1), "has_more": len(rows) > page_length}


def get_full_names(users: set | list) -> dict[str, str]:
	"""Map user ids to full names in one query."""
	users = [u for u in set(users) if u]
	if not users:
		return {}
	User = frappe.qb.DocType("User")
	rows = frappe.qb.from_(User).select(User.name, User.full_name).where(User.name.isin(users)).run()
	return {name: full_name or name for name, full_name in rows}


# ---------- Insights: periods and short-lived cache ----------

PERIODS = ("week", "month", "quarter", "year")
TREND_PERIODS = 8
INSIGHTS_CACHE_SECONDS = 300
INSIGHTS_CACHE_PREFIX = "tbo_smart:insights"


def period_windows(period: str, today=None) -> frappe._dict:
	"""The current period to date, the same span of the previous one, and an 8-period trend.

	Periods start on the site's first day of the week, the 1st of the month, the quarter or
	the year. Comparing to-date spans keeps a half-finished period fair against the last one.
	"""
	if period not in PERIODS:
		frappe.throw(_("Period must be one of {0}.").format(", ".join(PERIODS)))
	today = getdate(today)
	start = period_start(today, period)
	starts = [shift_period(start, period, offset) for offset in range(1 - TREND_PERIODS, 2)]
	trend = [
		frappe._dict(label=period_label(begin, period), from_date=begin, to_date=add_days(end, -1))
		for begin, end in pairwise(starts)
	]
	previous_start = trend[-2].from_date
	return frappe._dict(
		period=period,
		from_date=start,
		to_date=today,
		end_date=trend[-1].to_date,
		previous_from=previous_start,
		previous_to=min(add_days(previous_start, date_diff(today, start)), trend[-2].to_date),
		trend=trend,
	)


def period_start(day, period: str):
	if period == "week":
		return get_first_day_of_week(day)
	if period == "month":
		return get_first_day(day)
	if period == "quarter":
		return get_quarter_start(day)
	return get_year_start(day)


def shift_period(start, period: str, offset: int):
	if period == "week":
		return getdate(add_days(start, 7 * offset))
	return getdate(add_months(start, offset * {"month": 1, "quarter": 3, "year": 12}[period]))


def period_label(start, period: str) -> str:
	if period == "week":
		return start.strftime("%G-W%V")
	if period == "month":
		return start.strftime("%Y-%m")
	if period == "quarter":
		return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
	return str(start.year)


def trend_index(windows: frappe._dict, day) -> int | None:
	"""Which trend bucket a date falls in, or None outside the trend."""
	day = getdate(day)
	for index, window in enumerate(windows.trend):
		if window.from_date <= day <= window.to_date:
			return index
	return None


def in_window(day, from_date, to_date) -> bool:
	return bool(day) and from_date <= getdate(day) <= to_date


def cached_insights(kind: str, parts: tuple, build) -> dict:
	"""`build()` cached for a few minutes per (kind, user, *parts)."""
	key = ":".join([INSIGHTS_CACHE_PREFIX, kind, frappe.session.user, *(str(part) for part in parts)])
	value = frappe.cache.get_value(key, expires=True)
	if value is None:
		value = build()
		frappe.cache.set_value(key, value, expires_in_sec=INSIGHTS_CACHE_SECONDS)
	return value


def percent(part: float, whole: float) -> float:
	return round(flt(part) * 100 / flt(whole), 1) if flt(whole) else 0.0
