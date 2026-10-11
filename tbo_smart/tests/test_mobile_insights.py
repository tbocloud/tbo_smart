import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, nowdate

from tbo_smart.api.mobile import crm, manager
from tbo_smart.api.mobile.utils import period_windows
from tbo_smart.test_utils import (
	age_document,
	clear_insights_cache,
	delete_crm_records,
	get_all_keys,
	get_deal_base_value,
	make_attendance,
	make_crm_deal,
	make_crm_lead,
	make_leave_application,
	make_leave_type,
	make_staff,
	make_user,
	set_hr_settings,
)

TEAM_MANAGER = "ins.manager@tbosmart.test"
ON_TIME = "ins.ontime@tbosmart.test"
AWAY = "ins.away@tbosmart.test"
PLAIN = "ins.plain@tbosmart.test"
SELLER_MANAGER = "ins.salesmgr@tbosmart.test"
SALES_USER = "ins.seller@tbosmart.test"
LOCATION_KEYS = {"latitude", "longitude", "location", "geolocation", "device_id", "checked_in_at", "time"}


class TestPeriodWindows(FrappeTestCase):
	def test_month_compares_the_same_span_of_last_month(self):
		windows = period_windows("month", "2026-10-11")
		self.assertEqual((str(windows.from_date), str(windows.to_date)), ("2026-10-01", "2026-10-11"))
		self.assertEqual((str(windows.previous_from), str(windows.previous_to)), ("2026-09-01", "2026-09-11"))
		self.assertEqual(str(windows.end_date), "2026-10-31")
		self.assertEqual([w.label for w in windows.trend][::7], ["2026-03", "2026-10"])
		self.assertEqual(len(windows.trend), 8)

	def test_quarter_and_year(self):
		quarter = period_windows("quarter", "2026-10-11")
		self.assertEqual(str(quarter.previous_from), "2026-07-01")
		self.assertEqual(quarter.trend[0].label, "2024-Q4")
		year = period_windows("year", "2026-10-11")
		self.assertEqual((str(year.previous_from), str(year.previous_to)), ("2025-01-01", "2025-10-11"))
		self.assertEqual(year.trend[0].label, "2019")

	def test_unknown_period_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			period_windows("fortnight")


class TestTeamInsights(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		set_hr_settings(allow_employee_checkin_from_mobile_app=1, allow_geolocation_tracking=0)
		cls.manager = make_staff(TEAM_MANAGER, ["Leave Approver"])
		cls.on_time = make_staff(ON_TIME, reports_to=cls.manager, leave_approver=TEAM_MANAGER)
		cls.away = make_staff(AWAY, reports_to=cls.manager)
		make_staff(PLAIN)
		today = nowdate()
		make_attendance(cls.on_time, today, "Present", late_entry=1)
		make_attendance(cls.away, today, "Absent")
		make_leave_application(cls.on_time, make_leave_type(), days_ahead=5, approver=TEAM_MANAGER)

	def setUp(self):
		clear_insights_cache()

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_insights_cache()

	def test_figures_for_direct_reports(self):
		frappe.set_user(TEAM_MANAGER)
		insights = manager.get_team_insights("month")
		self.assertEqual((insights["scope"], insights["team_size"]), ("direct_reports", 2))
		attendance = insights["attendance"]
		self.assertEqual(attendance["present_pct"], 50.0)
		self.assertEqual((attendance["late"], attendance["absent"], attendance["marked_days"]), (1, 1, 2))
		self.assertEqual(attendance["on_leave_today"], 0)
		self.assertEqual(len(insights["trend"]), 8)
		self.assertEqual(insights["trend"][-1]["late"], 1)
		self.assertEqual(insights["pending"]["leave"]["count"], 1)
		self.assertEqual(insights["pending"]["leave"]["oldest_days"], 0)
		people = {row["employee"]: row for row in insights["people"]}
		self.assertEqual((people[self.on_time]["late"], people[self.away]["absent"]), (1, 1))

	def test_never_returns_location_or_punch_times(self):
		frappe.set_user(TEAM_MANAGER)
		keys = set(get_all_keys(manager.get_team_insights("week")))
		self.assertFalse(keys & LOCATION_KEYS)

	def test_refused_without_team_insights(self):
		frappe.set_user(PLAIN)
		with self.assertRaises(frappe.PermissionError):
			manager.get_team_insights()


class TestSalesInsights(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		make_user(SELLER_MANAGER, ["Sales Manager"])
		make_user(SALES_USER, ["Sales User"])
		delete_crm_records((SELLER_MANAGER, SALES_USER))
		cls.open_deal = make_crm_deal(
			SELLER_MANAGER, "Qualification", 5000, probability=40, expected_closure_date=nowdate()
		)
		cls.won = make_crm_deal(SELLER_MANAGER, "Won", 3000)
		cls.lost = make_crm_deal(SELLER_MANAGER, "Lost", 1000, lost_reason="Other", lost_notes="Price")
		cls.stalled = make_crm_deal(SELLER_MANAGER, "Negotiation", 2000)
		age_document("CRM Deal", cls.stalled, 30)
		cls.lead = make_crm_lead(SELLER_MANAGER, "Funnel One")
		make_crm_lead(SELLER_MANAGER, "Funnel Two", status="Qualified")

	@classmethod
	def tearDownClass(cls):
		delete_crm_records((SELLER_MANAGER, SALES_USER))
		super().tearDownClass()

	def setUp(self):
		clear_insights_cache()

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_insights_cache()

	def test_figures_for_my_deals(self):
		frappe.set_user(SELLER_MANAGER)
		crm.log_activity("lead", self.lead, "note", "Sent the brochure")
		insights = crm.get_sales_insights("month", "mine")
		self.assertEqual(insights["scope"], "mine")

		pipeline = insights["pipeline"]
		stages = {row["stage"]: row for row in pipeline["by_stage"]}
		open_value = get_deal_base_value(self.open_deal)
		self.assertEqual(pipeline["count"], 2)
		self.assertEqual(stages["Qualification"]["count"], 1)
		self.assertAlmostEqual(stages["Qualification"]["value"], open_value)
		self.assertAlmostEqual(stages["Qualification"]["weighted"], open_value * 0.4)
		self.assertAlmostEqual(pipeline["value"], open_value + get_deal_base_value(self.stalled))

		self.assertEqual((insights["won"]["count"], insights["lost"]["count"]), (1, 1))
		self.assertAlmostEqual(insights["won"]["value"], get_deal_base_value(self.won))
		self.assertEqual(insights["win_rate"], 50.0)

		self.assertEqual(insights["stalled"]["count"], 1)
		self.assertEqual([row["name"] for row in insights["stalled"]["deals"]], [self.stalled])

		funnel = insights["leads_funnel"]
		self.assertEqual((funnel["new"], funnel["qualified"], funnel["converted"]), (2, 1, 0))

		activity = {row["user"]: row for row in insights["activity"]}
		self.assertEqual(activity[SELLER_MANAGER]["notes"], 1)

		self.assertEqual(len(insights["trend"]), 8)
		self.assertEqual(insights["trend"][-1]["new_leads"], 2)
		self.assertAlmostEqual(insights["trend"][-1]["won_value"], get_deal_base_value(self.won))
		self.assertIn(self.open_deal, [row["name"] for row in insights["closing"]])
		self.assertEqual(getdate(insights["from_date"]).day, 1)

	def test_all_scope_includes_other_owners(self):
		make_crm_deal(SALES_USER, "Qualification", 700)
		frappe.set_user(SELLER_MANAGER)
		mine = crm.get_sales_insights("month", "mine")["pipeline"]["count"]
		everyone = crm.get_sales_insights("month", "all")["pipeline"]["count"]
		self.assertGreater(everyone, mine)

	def test_refused_without_sales_insights_or_outside_scope(self):
		frappe.set_user(SALES_USER)
		with self.assertRaises(frappe.PermissionError):
			crm.get_sales_insights()
		frappe.set_user(SELLER_MANAGER)
		with self.assertRaises(frappe.PermissionError):
			crm.get_sales_insights("month", "team")
