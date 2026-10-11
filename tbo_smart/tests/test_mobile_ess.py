import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, get_first_day, getdate, now_datetime

from tbo_smart.api.mobile import ess
from tbo_smart.api.mobile.utils import NoEmployeeError
from tbo_smart.test_utils import (
	make_expense_claim_type,
	make_leave_allocation,
	make_leave_application,
	make_leave_type,
	make_salary_slip_record,
	make_staff,
	make_user,
	set_hr_settings,
)

STAFF = "ess.staff@tbosmart.test"
OTHER = "ess.other@tbosmart.test"
APPROVER = "ess.approver@tbosmart.test"


class TestMobileESS(FrappeTestCase):
	def setUp(self):
		set_hr_settings(allow_employee_checkin_from_mobile_app=1, allow_geolocation_tracking=0)
		make_user(APPROVER, ["Leave Approver", "Expense Approver"])
		self.staff = make_staff(STAFF, leave_approver=APPROVER, expense_approver=APPROVER)
		self.other = make_staff(OTHER)
		frappe.set_user(STAFF)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_get_me_returns_own_employee(self):
		me = ess.get_me()
		self.assertEqual(me["name"], self.staff)
		self.assertEqual(me["user"], STAFF)
		for key in (
			"employee_name",
			"designation",
			"department",
			"company",
			"reports_to",
			"phone",
			"is_manager",
		):
			self.assertIn(key, me)

	def test_user_without_employee_gets_no_employee_error(self):
		frappe.set_user(make_user("ess.nobody@tbosmart.test"))
		with self.assertRaises(NoEmployeeError):
			ess.get_me()

	def test_check_in_records_server_time(self):
		before = now_datetime().replace(microsecond=0)
		punch = ess.check_in("IN", latitude=10.0158, longitude=76.3418, accuracy=12.4)
		after = now_datetime()
		saved = frappe.db.get_value(
			"Employee Checkin", punch["name"], ["employee", "time", "latitude", "device_id"], as_dict=True
		)
		self.assertEqual(saved.employee, self.staff)
		self.assertTrue(before <= saved.time <= after)
		self.assertAlmostEqual(saved.latitude, 10.0158, places=4)
		self.assertEqual(saved.device_id, "TBO Smart ±12 m")

	def test_check_in_refuses_a_double_tap(self):
		ess.check_in("IN")
		with self.assertRaises(frappe.DuplicateEntryError):
			ess.check_in("IN")

	def test_check_in_refused_when_mobile_check_in_is_off(self):
		set_hr_settings(allow_employee_checkin_from_mobile_app=0)
		with self.assertRaises(frappe.PermissionError):
			ess.check_in("IN")

	def test_check_in_rejects_unknown_log_type(self):
		with self.assertRaises(frappe.ValidationError):
			ess.check_in("LUNCH")

	def test_today_shows_own_punches(self):
		ess.check_in("IN")
		today = ess.get_today()
		self.assertEqual(today["status"], "checked_in")
		self.assertEqual([row["log_type"] for row in today["checkins"]], ["IN"])
		for key in ("shift_name", "shift_start", "shift_end", "attendance", "server_time"):
			self.assertIn(key, today)

	def test_attendance_and_holidays_shapes(self):
		self.assertIsInstance(ess.get_attendance(getdate().year, getdate().month), list)
		holidays = ess.get_holidays(getdate().year)
		self.assertTrue(all(set(row) == {"date", "description"} for row in holidays))

	def test_apply_leave_routes_to_approver_and_lists_own_only(self):
		leave_type = make_leave_type()
		frappe.set_user("Administrator")
		make_leave_allocation(self.staff, leave_type)
		make_leave_application(
			self.other, make_leave_type("TBO Smart Other Leave"), days_ahead=3, approver=APPROVER
		)
		frappe.set_user(STAFF)

		balances = {row["leave_type"]: row for row in ess.get_leave_balances()}
		self.assertEqual(balances[leave_type]["allocated"], 10)

		applied = ess.apply_leave(leave_type, str(getdate()), str(getdate()), half_day=True, reason="Bank")
		self.assertEqual(applied["status"], "Open")
		self.assertEqual(applied["approver_id"], APPROVER)
		self.assertEqual(applied["days"], 0.5)

		page = ess.get_leave_applications()
		self.assertEqual({row["name"] for row in page["data"]}, {applied["name"]})
		self.assertFalse(page["has_more"])

	def test_cannot_cancel_someone_elses_leave(self):
		frappe.set_user("Administrator")
		other_leave = make_leave_application(
			self.other, make_leave_type("TBO Smart Other Leave"), days_ahead=4, approver=APPROVER
		)
		frappe.set_user(STAFF)
		with self.assertRaises(frappe.PermissionError):
			ess.cancel_leave(other_leave)

	def test_create_expense_claim_with_receipt(self):
		frappe.set_user("Administrator")
		expense_type = make_expense_claim_type()
		frappe.set_user(STAFF)
		claim = ess.create_expense_claim(
			expense_type,
			480,
			str(getdate()),
			"Lunch",
			receipt_filename="bill.txt",
			receipt_base64="cmVjZWlwdA==",
		)
		self.assertEqual(claim["status"], "Pending")
		self.assertEqual(claim["amount"], 480)
		self.assertEqual(claim["approver_id"], APPROVER)
		self.assertEqual(claim["receipt"], "bill.txt")
		self.assertTrue(frappe.db.get_value("File", {"attached_to_name": claim["name"]}, "is_private"))
		self.assertEqual([row["name"] for row in ess.get_expense_claims()["data"]], [claim["name"]])

	def test_salary_slips_are_own_only(self):
		frappe.set_user("Administrator")
		month = get_first_day(add_months(getdate(), -1))
		own = make_salary_slip_record(self.staff, str(month))
		others = make_salary_slip_record(self.other, str(month))
		frappe.set_user(STAFF)

		slips = ess.get_salary_slips()["data"]
		self.assertEqual([row["name"] for row in slips], [own])
		self.assertIn("download_salary_slip", slips[0]["pdf_url"])
		with self.assertRaises(frappe.PermissionError):
			ess.download_salary_slip(others)
