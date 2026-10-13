"""Custom fields and settings defaults for check-in location, made on install and every
migrate. Only on sites with HRMS: Employee Checkin and Shift Location come from it."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

SETTINGS = "TBO Smart Settings"
# New fields on a Single get no stored value from their JSON default; these are written once.
SETTINGS_DEFAULTS = {
	"enforce_checkin_radius": 1,
	"checkin_max_accuracy": 100,
	"checkin_anywhere_roles": "HR Manager",
	"reverse_geocode_url": "https://nominatim.openstreetmap.org/reverse",
}


def hrms_installed() -> bool:
	return "hrms" in frappe.get_installed_apps()


def setup_checkin_location() -> None:
	if not hrms_installed():
		return
	create_custom_fields(get_checkin_fields(), ignore_validate=True)
	set_settings_defaults()


def get_checkin_fields() -> dict:
	read_only = {"read_only": 1, "no_copy": 1}
	return {
		"Employee Checkin": [
			{
				"fieldname": "tbo_location_name",
				"fieldtype": "Data",
				"label": "Place",
				"insert_after": "device_id",
				"in_list_view": 1,
				"description": "Named by the phone, or looked up from the coordinates after the punch.",
				**read_only,
			},
			{
				"fieldname": "tbo_outside_radius",
				"fieldtype": "Check",
				"label": "Outside radius",
				"insert_after": "tbo_location_name",
				"in_standard_filter": 1,
				"description": "Allowed anyway: remote check-in or a role that may check in anywhere.",
				**read_only,
			},
			{
				"fieldname": "tbo_location_accuracy",
				"fieldtype": "Float",
				"label": "GPS accuracy (m)",
				"insert_after": "latitude",
				"precision": "0",
				**read_only,
			},
			{
				"fieldname": "tbo_nearest_location",
				"fieldtype": "Link",
				"options": "Shift Location",
				"label": "Nearest allowed location",
				"insert_after": "longitude",
				**read_only,
			},
			{
				"fieldname": "tbo_distance_m",
				"fieldtype": "Int",
				"label": "Distance from it (m)",
				"insert_after": "tbo_nearest_location",
				**read_only,
			},
		],
		"Employee": [
			{
				"fieldname": "tbo_allow_remote_checkin",
				"fieldtype": "Check",
				"label": "Allow remote check-in",
				"insert_after": "attendance_device_id",
				"description": "TBO Smart: may check in from anywhere (work from home). Such punches are flagged Outside radius.",
			},
		],
	}


def set_settings_defaults() -> None:
	"""Write a default only for a field that has never been saved, so owners' choices stay."""
	Singles = frappe.qb.Table("tabSingles")
	stored = set(
		frappe.qb.from_(Singles)
		.select(Singles.field)
		.where((Singles.doctype == SETTINGS) & (Singles.field.isin(list(SETTINGS_DEFAULTS))))
		.run(pluck=True)
	)
	for field, value in SETTINGS_DEFAULTS.items():
		if field not in stored:
			frappe.db.set_single_value(SETTINGS, field, value)
