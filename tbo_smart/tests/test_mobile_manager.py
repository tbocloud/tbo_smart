import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from tbo_smart.api.mobile import ess, manager
from tbo_smart.test_utils import (
	make_deal,
	make_half_day_leave_application,
	make_leave_application,
	make_leave_type,
	make_staff,
	make_user,
	set_hr_settings,
)

MANAGER = "mgr.lead@tbosmart.test"
REPORT = "mgr.report@tbosmart.test"
OUTSIDER = "mgr.outsider@tbosmart.test"


class TestMobileManager(FrappeTestCase):
	def setUp(self):
		set_hr_settings(allow_employee_checkin_from_mobile_app=1, allow_geolocation_tracking=0)
		self.manager = make_staff(MANAGER, ["Leave Approver", "Expense Approver", "Sales Manager"])
		self.report = make_staff(REPORT, reports_to=self.manager, leave_approver=MANAGER)
		self.outsider = make_staff(OUTSIDER, ["Leave Approver", "Sales User"])
		self.leave_type = make_leave_type()

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_team_today_lists_direct_reports_without_location(self):
		frappe.set_user(REPORT)
		ess.check_in("IN", latitude=10.0, longitude=76.0)
		frappe.set_user(MANAGER)
		team = {row["employee"]: row for row in manager.get_team_today()}
		self.assertEqual(set(team), {self.report})
		self.assertEqual(team[self.report]["attendance"], "in")
		self.assertIsNotNone(team[self.report]["checked_in_at"])
		self.assertNotIn("latitude", team[self.report])

	def test_team_today_refuses_people_without_reports(self):
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			manager.get_team_today()

	def test_approvals_refuse_non_approvers(self):
		frappe.set_user(REPORT)
		with self.assertRaises(frappe.PermissionError):
			manager.get_approvals()

	def test_approver_sees_and_approves_leave_by_submitting_it(self):
		application = make_leave_application(self.report, self.leave_type, days_ahead=5, approver=MANAGER)
		frappe.set_user(MANAGER)
		waiting = {row["ref"]: row for row in manager.get_approvals()}
		self.assertEqual(waiting[application]["kind"], "leave")

		result = manager.decide_approval("leave", application, True, "Enjoy")
		self.assertEqual((result["status"], result["docstatus"]), ("Approved", 1))
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{"reference_name": application, "comment_type": "Comment", "content": ("like", "%Enjoy%")},
			)
		)

	def test_approvals_show_which_day_is_the_half_day(self):
		application = make_half_day_leave_application(
			self.report, make_leave_type("TBO Smart Half Day Leave", include_holiday=1), MANAGER
		)
		frappe.set_user(MANAGER)
		row = {row["ref"]: row for row in manager.get_approvals()}[application.name]
		self.assertTrue(row["half_day"])
		self.assertEqual(str(row["half_day_date"]), str(application.half_day_date))
		self.assertEqual(row["days"], 2.5)

	def test_rejection_needs_a_reason(self):
		application = make_leave_application(self.report, self.leave_type, days_ahead=6, approver=MANAGER)
		frappe.set_user(MANAGER)
		with self.assertRaises(frappe.ValidationError):
			manager.decide_approval("leave", application, False)
		result = manager.decide_approval("leave", application, False, "Go-live week")
		self.assertEqual((result["status"], result["docstatus"]), ("Rejected", 1))

	def test_only_the_named_approver_can_decide(self):
		application = make_leave_application(self.report, self.leave_type, days_ahead=8, approver=MANAGER)
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			manager.decide_approval("leave", application, True)
		self.assertEqual(frappe.db.get_value("Leave Application", application, "docstatus"), 0)

	def test_sales_snapshot_for_sales_managers_only(self):
		make_deal(MANAGER, deal_value=5000, expected_closing=add_days(nowdate(), 3))
		frappe.set_user(MANAGER)
		snapshot = manager.get_sales_snapshot()
		for key in ("by_stage", "pipeline", "weighted", "won_this_month", "closing_this_week", "currency"):
			self.assertIn(key, snapshot)
		self.assertGreaterEqual(snapshot["pipeline"], 5000)

		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			manager.get_sales_snapshot()

	def test_unknown_kind_is_rejected(self):
		frappe.set_user(make_user(MANAGER))
		with self.assertRaises(frappe.ValidationError):
			manager.decide_approval("timesheet", "X", True)
