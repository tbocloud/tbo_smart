"""Where an employee may check in: the check-in radius rule (docs/checkin-location.md).

Built on HRMS's own pieces: Shift Location (centre and `checkin_radius`), the active Shift
Assignment's `shift_location`, `get_distance_between_coordinates` and
`CheckinRadiusExceededError`. On top of HRMS it adds a fallback list of locations for
employees without a shift, a GPS-accuracy tolerance, and exemptions.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate
from hrms.hr.doctype.employee_checkin.employee_checkin import CheckinRadiusExceededError
from hrms.hr.utils import get_distance_between_coordinates

SETTINGS = "TBO Smart Settings"
# A fix that is ±accuracy m off may still be inside: the allowance grows by the reported
# accuracy, capped so a vague fix can't stretch the radius far.
ACCURACY_TOLERANCE_CAP = 50
DEFAULT_MAX_ACCURACY = 100
# Why a check-in outside the radius is still accepted (and flagged).
EXEMPT_REMOTE = "remote"
EXEMPT_ROLE = "role"


class CheckinOutsideRadiusError(CheckinRadiusExceededError):
	"""Outside every allowed location. Subclasses HRMS's error so callers handle both alike."""


class CheckinAccuracyError(frappe.ValidationError):
	"""The reported GPS accuracy is worse than TBO Smart Settings allows."""


def enforcement_on() -> bool:
	return bool(cint(frappe.db.get_single_value(SETTINGS, "enforce_checkin_radius")))


def max_accuracy() -> int:
	return cint(frappe.db.get_single_value(SETTINGS, "checkin_max_accuracy")) or DEFAULT_MAX_ACCURACY


def get_exempt_roles() -> set[str]:
	"""Roles that may check in anywhere, one per line in TBO Smart Settings."""
	text = frappe.db.get_single_value(SETTINGS, "checkin_anywhere_roles") or ""
	return {line.strip() for line in text.splitlines() if line.strip()}


def get_policy(employee: str, user: str, on_date=None) -> frappe._dict:
	"""The rule for one employee on one day.

	`enforced` is true only when the switch is on and at least one usable location applies,
	so a site with no locations configured never blocks anyone. `exempt` says why this
	employee may check in anywhere (or is None).
	"""
	locations = get_allowed_locations(employee, on_date)
	return frappe._dict(
		enforced=enforcement_on() and bool(locations),
		exempt=get_exemption(employee, user),
		locations=locations,
		max_accuracy=max_accuracy(),
		tolerance_cap=ACCURACY_TOLERANCE_CAP,
	)


def get_allowed_locations(employee: str, on_date=None) -> list[frappe._dict]:
	"""The usable Shift Locations of the employee's active Shift Assignments on that day, else
	the usable ones in TBO Smart Settings' fallback list. A location without coordinates or with
	a radius of 0 is not usable (HRMS also skips a 0 radius), so an assignment pointing only at
	such a location falls back to the list."""
	return usable_locations(get_shift_locations(employee, getdate(on_date))) or usable_locations(
		get_fallback_locations()
	)


def usable_locations(names: list[str]) -> list[frappe._dict]:
	if not names:
		return []
	Location = frappe.qb.DocType("Shift Location")
	rows = (
		frappe.qb.from_(Location)
		.select(
			Location.name,
			Location.location_name,
			Location.latitude,
			Location.longitude,
			Location.checkin_radius,
		)
		.where(Location.name.isin(names))
		.orderby(Location.name)
	).run(as_dict=True)
	return [
		frappe._dict(
			name=row.name,
			location_name=row.location_name or row.name,
			latitude=flt(row.latitude),
			longitude=flt(row.longitude),
			radius=cint(row.checkin_radius),
		)
		for row in rows
		if cint(row.checkin_radius) > 0 and (flt(row.latitude) or flt(row.longitude))
	]


def get_shift_locations(employee: str, on_date) -> list[str]:
	"""Locations on submitted, active Shift Assignments covering the day (HRMS's filters,
	by date rather than by the punch's shift so an early or late punch is still checked)."""
	Assignment = frappe.qb.DocType("Shift Assignment")
	return (
		frappe.qb.from_(Assignment)
		.select(Assignment.shift_location)
		.distinct()
		.where(
			(Assignment.employee == employee)
			& (Assignment.docstatus == 1)
			& (Assignment.status == "Active")
			& (Assignment.shift_location.isnotnull())
			& (Assignment.shift_location != "")
			& (Assignment.start_date <= on_date)
			& ((Assignment.end_date.isnull()) | (Assignment.end_date >= on_date))
		)
	).run(pluck=True)


def get_fallback_locations() -> list[str]:
	"""TBO Smart Settings → Allowed check-in locations."""
	return frappe.get_all(
		"TBO Checkin Location",
		filters={"parent": SETTINGS, "parenttype": SETTINGS, "parentfield": "checkin_locations"},
		pluck="shift_location",
		order_by="idx",
	)


def get_exemption(employee: str, user: str) -> str | None:
	if cint(frappe.db.get_value("Employee", employee, "tbo_allow_remote_checkin")):
		return EXEMPT_REMOTE
	if user and get_exempt_roles() & set(frappe.get_roles(user)):
		return EXEMPT_ROLE
	return None


def measure(locations: list[frappe._dict], latitude: float, longitude: float, accuracy: float | None):
	"""(nearest location, its distance in metres, inside) where inside means within the
	radius plus min(accuracy, cap) of *any* location."""
	tolerance = min(max(flt(accuracy), 0), ACCURACY_TOLERANCE_CAP)
	nearest, nearest_distance, inside = None, None, False
	for location in locations:
		distance = get_distance_between_coordinates(
			location.latitude, location.longitude, flt(latitude), flt(longitude)
		)
		if nearest_distance is None or distance < nearest_distance:
			nearest, nearest_distance = location, distance
		if distance <= location.radius + tolerance:
			inside = True
	return nearest, nearest_distance, inside


def format_distance(metres: float) -> str:
	if metres >= 1000:
		return _("{0} km").format(f"{metres / 1000:.1f}")
	return _("{0} m").format(round(metres))


def outside_message(location: frappe._dict, distance: float) -> str:
	return _("You're {0} from {1} (allowed {2}). Check-in not allowed.").format(
		format_distance(distance), location.location_name, format_distance(location.radius)
	)


def accuracy_message(accuracy: float, limit: int) -> str:
	return _(
		"Location not precise enough (±{0} m; ±{1} m or better needed). Move outdoors or turn on precise location, then try again."
	).format(round(flt(accuracy)), limit)


def location_required_message(locations: list[frappe._dict]) -> str:
	return _("Turn on location to check in. Check-in is allowed only at {0}.").format(
		", ".join(location.location_name for location in locations)
	)
