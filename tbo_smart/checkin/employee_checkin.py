"""Employee Checkin document events: the check-in radius and the place name
(docs/checkin-location.md). They run after HRMS's own `validate` and `after_insert`."""

import frappe
from frappe.utils import flt

from tbo_smart.checkin import geofence, place


def validate(doc, method=None):
	policy = get_radius_policy(doc)
	if policy:
		validate_checkin_radius(doc, policy)


def after_insert(doc, method=None):
	queue_place_name(doc)


def get_radius_policy(doc) -> frappe._dict | None:
	"""TBO Smart's rule, when it applies: the switch is on, this is the employee's own punch,
	and a location applies to them. Logs entered for someone else (HR, a biometric device's
	integration user) are governed by HRMS and DocType permissions."""
	user = frappe.db.get_value("Employee", doc.employee, "user_id")
	if not user or user != frappe.session.user:
		return None
	policy = geofence.get_policy(doc.employee, user, doc.time)
	return policy if policy.enforced else None


def validate_checkin_radius(doc, policy: frappe._dict) -> None:
	doc.tbo_outside_radius = 0
	if not (doc.latitude or doc.longitude):
		if policy.exempt:
			return
		frappe.throw(geofence.location_required_message(policy.locations), geofence.CheckinOutsideRadiusError)

	accuracy = flt(doc.tbo_location_accuracy) or None
	nearest, distance, inside = geofence.measure(policy.locations, doc.latitude, doc.longitude, accuracy)
	doc.tbo_nearest_location = nearest.name
	doc.tbo_distance_m = round(distance)
	if policy.exempt:
		doc.tbo_outside_radius = 0 if inside else 1
		return
	if accuracy and accuracy > policy.max_accuracy:
		frappe.throw(geofence.accuracy_message(accuracy, policy.max_accuracy), geofence.CheckinAccuracyError)
	if not inside:
		frappe.throw(geofence.outside_message(nearest, distance), geofence.CheckinOutsideRadiusError)


def queue_place_name(doc) -> None:
	"""Name the place in the background when the device didn't; never delays the punch."""
	if doc.tbo_location_name or not (doc.latitude or doc.longitude):
		return
	place.queue_place_name(doc.name)
