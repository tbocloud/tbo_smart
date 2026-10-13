from unittest.mock import patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from tbo_smart.api.mobile import ess
from tbo_smart.checkin import geofence, place
from tbo_smart.test_utils import (
	clear_place_cache,
	delete_checkins,
	make_shift_assignment,
	make_shift_location,
	make_staff,
	nominatim_response,
	offset_point,
	reset_checkin_settings,
	set_checkin_settings,
	set_hr_settings,
)

STAFF = "geo.staff@tbosmart.test"
SHIFT_STAFF = "geo.shift@tbosmart.test"
FIELD_STAFF = "geo.field@tbosmart.test"
REMOTE_STAFF = "geo.remote@tbosmart.test"
OFFICE = "TBO Geo Kottayam Office"
BRANCH = "TBO Geo Branch"
SITE = "TBO Geo Client Site"


class TestCheckinRadius(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_hr_settings(allow_employee_checkin_from_mobile_app=1, allow_geolocation_tracking=0)
		reset_checkin_settings()
		make_shift_location(OFFICE, radius=100)
		make_shift_location(BRANCH, radius=100, metres_north=5000)
		self.staff = make_staff(STAFF)
		delete_checkins(self.staff)

	def tearDown(self):
		frappe.set_user("Administrator")

	def punch(self, user: str, metres_north: float = 0, accuracy: float | None = 10, **kwargs) -> dict:
		frappe.set_user(user)
		latitude, longitude = offset_point(metres_north)
		return ess.check_in("IN", latitude=latitude, longitude=longitude, accuracy=accuracy, **kwargs)

	def test_nothing_configured_blocks_nothing(self):
		self.assertFalse(geofence.get_policy(self.staff, STAFF).enforced)
		punch = self.punch(STAFF, metres_north=20_000)
		self.assertFalse(punch["outside_radius"])
		frappe.set_user(STAFF)
		self.assertFalse(ess.get_checkin_locations()["enforced"])

	def test_switch_off_blocks_nothing(self):
		set_checkin_settings(locations=[OFFICE], enforce_checkin_radius=0)
		self.assertTrue(self.punch(STAFF, metres_north=20_000)["name"])

	def test_inside_the_fallback_location_is_accepted(self):
		set_checkin_settings(locations=[OFFICE])
		punch = self.punch(STAFF, metres_north=60)
		saved = frappe.db.get_value(
			"Employee Checkin",
			punch["name"],
			["tbo_nearest_location", "tbo_distance_m", "tbo_location_accuracy", "tbo_outside_radius"],
			as_dict=True,
		)
		self.assertEqual(saved.tbo_nearest_location, OFFICE)
		self.assertAlmostEqual(saved.tbo_distance_m, 60, delta=1)
		self.assertEqual(saved.tbo_location_accuracy, 10)
		self.assertFalse(saved.tbo_outside_radius)

	def test_outside_is_refused_naming_the_nearest_location_and_distance(self):
		set_checkin_settings(locations=[OFFICE, BRANCH])
		with self.assertRaises(geofence.CheckinOutsideRadiusError) as caught:
			self.punch(STAFF, metres_north=1200)
		message = str(caught.exception)
		self.assertIn("1.2 km from TBO Geo Kottayam Office", message)
		self.assertIn("allowed 100 m", message)
		self.assertFalse(frappe.db.exists("Employee Checkin", {"employee": self.staff}))

	def test_inside_any_listed_location_is_accepted(self):
		set_checkin_settings(locations=[OFFICE, BRANCH])
		self.assertTrue(self.punch(STAFF, metres_north=5030)["name"])

	def test_accuracy_widens_the_radius_up_to_50_m(self):
		set_checkin_settings(locations=[OFFICE])
		# 130 m out with ±40 m: within 100 + 40.
		self.assertTrue(self.punch(STAFF, metres_north=130, accuracy=40)["name"])
		delete_checkins(self.staff)
		# 170 m out with ±80 m: the tolerance stops at 50, so 150 m is the limit.
		with self.assertRaises(geofence.CheckinOutsideRadiusError):
			self.punch(STAFF, metres_north=170, accuracy=80)

	def test_poor_accuracy_is_refused(self):
		set_checkin_settings(locations=[OFFICE])
		with self.assertRaises(geofence.CheckinAccuracyError) as caught:
			self.punch(STAFF, metres_north=10, accuracy=150)
		self.assertIn("not precise enough", str(caught.exception))

		frappe.set_user("Administrator")
		set_checkin_settings(locations=[OFFICE], checkin_max_accuracy=200)
		self.assertTrue(self.punch(STAFF, metres_north=10, accuracy=150)["name"])

	def test_shift_location_wins_over_the_fallback_list(self):
		make_shift_location(SITE, radius=200, metres_north=3000)
		set_checkin_settings(locations=[OFFICE])
		employee = make_staff(SHIFT_STAFF)
		delete_checkins(employee)
		make_shift_assignment(employee, SITE)

		frappe.set_user(SHIFT_STAFF)
		locations = ess.get_checkin_locations()
		self.assertTrue(locations["enforced"])
		self.assertEqual([row["name"] for row in locations["locations"]], [SITE])
		with self.assertRaises(geofence.CheckinOutsideRadiusError) as caught:
			self.punch(SHIFT_STAFF, metres_north=0)
		self.assertIn(SITE, str(caught.exception))
		self.assertTrue(self.punch(SHIFT_STAFF, metres_north=3100)["name"])

	def test_exempt_role_checks_in_anywhere_flagged(self):
		make_shift_location(SITE, radius=200, metres_north=3000)
		set_checkin_settings(checkin_anywhere_roles="HR Manager\nSales User")
		employee = make_staff(FIELD_STAFF, roles=["Sales User"])
		delete_checkins(employee)
		make_shift_assignment(employee, SITE)

		punch = self.punch(FIELD_STAFF, metres_north=0)
		self.assertTrue(punch["outside_radius"])
		self.assertAlmostEqual(
			frappe.db.get_value("Employee Checkin", punch["name"], "tbo_distance_m"), 3000, delta=10
		)
		frappe.set_user(FIELD_STAFF)
		self.assertEqual(ess.get_checkin_locations()["exempt"], geofence.EXEMPT_ROLE)

	def test_remote_employee_checks_in_anywhere_flagged(self):
		set_checkin_settings(locations=[OFFICE])
		employee = make_staff(REMOTE_STAFF, tbo_allow_remote_checkin=1)
		frappe.db.set_value("Employee", employee, "tbo_allow_remote_checkin", 1)
		delete_checkins(employee)
		punch = self.punch(REMOTE_STAFF, metres_north=40_000, accuracy=400)
		self.assertTrue(punch["outside_radius"])
		delete_checkins(employee)
		# Inside is not flagged.
		self.assertFalse(self.punch(REMOTE_STAFF, metres_north=10)["outside_radius"])

	def test_validate_hook_blocks_a_desk_insert(self):
		set_checkin_settings(locations=[OFFICE])
		frappe.set_user(STAFF)
		latitude, longitude = offset_point(2000)
		checkin = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.staff,
				"log_type": "IN",
				"time": now_datetime(),
				"latitude": latitude,
				"longitude": longitude,
			}
		)
		with self.assertRaises(geofence.CheckinOutsideRadiusError):
			checkin.insert(ignore_permissions=True)

		without_location = frappe.get_doc(
			{"doctype": "Employee Checkin", "employee": self.staff, "log_type": "IN", "time": now_datetime()}
		)
		with self.assertRaises(geofence.CheckinOutsideRadiusError) as caught:
			without_location.insert(ignore_permissions=True)
		self.assertIn("Turn on location", str(caught.exception))

	def test_hr_entry_for_someone_else_keeps_the_hrms_rule(self):
		set_checkin_settings(locations=[OFFICE])
		latitude, longitude = offset_point(2000)
		checkin = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": self.staff,
				"log_type": "IN",
				"time": now_datetime(),
				"latitude": latitude,
				"longitude": longitude,
				"device_id": "Gate biometric",
			}
		).insert(ignore_permissions=True)
		self.assertFalse(checkin.tbo_outside_radius)

	def test_settings_refuse_a_location_without_radius(self):
		make_shift_location("TBO Geo No Radius", radius=0)
		with self.assertRaises(frappe.ValidationError):
			set_checkin_settings(locations=["TBO Geo No Radius"])


class TestCheckinPlaceName(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		set_hr_settings(allow_employee_checkin_from_mobile_app=1, allow_geolocation_tracking=0)
		reset_checkin_settings()
		set_checkin_settings(reverse_geocode_email="it@tbosmart.test")
		self.staff = make_staff(STAFF)
		delete_checkins(self.staff)
		clear_place_cache()

	def tearDown(self):
		frappe.set_user("Administrator")

	def punch(self, **kwargs) -> dict:
		frappe.set_user(STAFF)
		latitude, longitude = offset_point()
		return ess.check_in("IN", latitude=latitude, longitude=longitude, accuracy=18, **kwargs)

	def test_device_place_name_is_stored_without_a_lookup(self):
		with patch("tbo_smart.checkin.place.frappe.enqueue") as enqueue:
			punch = self.punch(place_name="  Kanjirappally,\n Kottayam, Kerala ")
		self.assertEqual(punch["place_name"], "Kanjirappally, Kottayam, Kerala")
		self.assertEqual(punch["location"], "TBO Smart ±18 m")
		enqueue.assert_not_called()

	def test_server_lookup_is_queued_when_the_device_sent_none(self):
		with patch("tbo_smart.checkin.place.frappe.enqueue") as enqueue:
			punch = self.punch()
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs["checkin"], punch["name"])
		self.assertTrue(enqueue.call_args.kwargs["enqueue_after_commit"])

	def test_no_lookup_when_the_url_is_empty(self):
		set_checkin_settings(reverse_geocode_url="")
		with patch("tbo_smart.checkin.place.frappe.enqueue") as enqueue:
			self.punch()
		enqueue.assert_not_called()

	def test_lookup_fills_the_name_and_is_cached(self):
		with patch("tbo_smart.checkin.place.frappe.enqueue"):
			punch = self.punch()
		answer = nominatim_response(
			{
				"town": "Kanjirappally",
				"county": "Kanjirappally Taluk",
				"state_district": "Kottayam",
				"state": "Kerala",
			}
		)
		with patch("tbo_smart.checkin.place.requests.get", return_value=answer) as get:
			place.fill_place_name(punch["name"])
			latitude, longitude = offset_point(3)  # same 4-decimal cell
			self.assertEqual(place.reverse_geocode(latitude, longitude), "Kanjirappally, Kottayam, Kerala")
		self.assertEqual(
			frappe.db.get_value("Employee Checkin", punch["name"], "tbo_location_name"),
			"Kanjirappally, Kottayam, Kerala",
		)
		get.assert_called_once()
		self.assertIn("TBO Smart", get.call_args.kwargs["headers"]["User-Agent"])
		self.assertEqual(get.call_args.kwargs["params"]["email"], "it@tbosmart.test")
		self.assertEqual(get.call_args.kwargs["params"]["zoom"], 18)

	def test_failed_lookup_leaves_the_name_blank_and_logs(self):
		with patch("tbo_smart.checkin.place.frappe.enqueue"):
			punch = self.punch()
		errors = frappe.db.count("Error Log")
		with patch("tbo_smart.checkin.place.requests.get", side_effect=requests.ConnectionError("down")):
			place.fill_place_name(punch["name"])
		self.assertIsNone(frappe.db.get_value("Employee Checkin", punch["name"], "tbo_location_name"))
		self.assertEqual(frappe.db.count("Error Log"), errors + 1)

		# A failure is not cached: the next lookup asks again.
		with patch(
			"tbo_smart.checkin.place.requests.get", return_value=nominatim_response(status=503)
		) as get:
			self.assertIsNone(place.reverse_geocode(*offset_point()))
		get.assert_called_once()

	def test_one_request_per_second(self):
		key = frappe.cache.make_key(place.SLOT_KEY)
		place.wait_for_slot()
		with patch(
			"tbo_smart.checkin.place.time.sleep", side_effect=lambda _: frappe.cache.delete(key)
		) as sleep:
			place.wait_for_slot()
		sleep.assert_called()
