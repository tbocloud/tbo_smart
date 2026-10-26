import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, getdate, now_datetime, nowdate

from tbo_smart.api import hub_leads as hub
from tbo_smart.api import lead_quotation
from tbo_smart.lead_pipeline.daily_report import week_start
from tbo_smart.lead_pipeline.statuses import CONTACTED, POSITIVE
from tbo_smart.test_utils import (
	age_lead,
	change_lead_stage,
	delete_calls_of,
	delete_lead_pipeline_records,
	disable_lead_quotations,
	disable_sales_hierarchy,
	enable_lead_quotations,
	make_call,
	make_lead,
	make_lead_meeting,
	make_lead_task,
	make_quotation_item,
	make_user,
	move_meeting,
	set_stage_log_time,
)

HUB = "dr.integration@tbosmart.test"
BDE = "dr.bde@tbosmart.test"
BDE2 = "dr.bde2@tbosmart.test"
MANAGER = "dr.manager@tbosmart.test"
OWNERS = (BDE, BDE2, MANAGER)
SERVICE = "TBO Smart Daily Report Service"


class DailyReportTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])
		disable_sales_hierarchy(OWNERS)
		make_quotation_item(SERVICE)
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_calls_of(OWNERS)
		delete_lead_pipeline_records(OWNERS)
		disable_lead_quotations()
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_calls_of(OWNERS)
		delete_lead_pipeline_records(OWNERS)
		self.three_days_ago = add_days(now_datetime(), -3)
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def old_lead(self, owner: str = BDE, name: str = "Old Lead") -> str:
		"""A lead of `owner` created five days ago."""
		lead = make_lead(owner, name)
		age_lead(lead, 5)
		return lead

	def report(self, acting_user: str = BDE, **filters) -> dict:
		return hub.get_daily_report(acting_user=acting_user, **filters)

	def row(self, result: dict, user: str = BDE) -> dict:
		return next(row for row in result["people"] if row["user"] == user)


class TestDailyReport(DailyReportTestCase):
	def test_calls_total_and_connected_count_the_agents_day(self):
		frappe.set_user("Administrator")
		lead = make_lead(BDE, "Caller")
		make_call(BDE, lead)
		make_call(BDE, lead, status="Missed")
		make_call(BDE, lead, status="Answered", direction="Incoming")
		make_call(BDE, lead, started_at=add_days(now_datetime(), -1))
		make_call(BDE2, lead)
		frappe.set_user(HUB)
		row = self.row(self.report(MANAGER))
		self.assertEqual((row["calls_total"], row["calls_connected"]), (3, 2))
		other = self.row(self.report(MANAGER), BDE2)
		self.assertEqual((other["calls_total"], other["calls_connected"]), (1, 1))
		yesterday = self.row(self.report(date=add_days(nowdate(), -1)))
		self.assertEqual((yesterday["calls_total"], yesterday["calls_connected"]), (1, 1))

	def test_fresh_and_follow_up_leads_never_overlap(self):
		frappe.set_user("Administrator")
		first_call = make_lead(BDE, "First call today")
		make_call(BDE, first_call)
		make_call(BDE, first_call)
		earlier_call = self.old_lead(name="Called before")
		make_call(BDE, earlier_call, started_at=self.three_days_ago)
		make_call(BDE, earlier_call)
		first_move = self.old_lead(name="Moved today")
		change_lead_stage(first_move, CONTACTED)
		earlier_move = self.old_lead(name="Moved before")
		change_lead_stage(earlier_move, CONTACTED)
		set_stage_log_time(earlier_move, 2, self.three_days_ago)
		make_lead_task(earlier_move, nowdate(), BDE, status="Closed")
		self.old_lead(name="Untouched")
		frappe.set_user(HUB)
		row = self.row(self.report())
		self.assertEqual((row["fresh_leads"], row["follow_up_leads"]), (2, 2))
		self.assertEqual(row["calls_total"], 4)

	def test_positive_leads_entered_positive_that_day(self):
		frappe.set_user("Administrator")
		today = make_lead(BDE, "Positive today", company_name="Today Traders")
		change_lead_stage(today, POSITIVE)
		before = make_lead(BDE, "Positive before")
		change_lead_stage(before, POSITIVE)
		set_stage_log_time(before, 2, add_days(now_datetime(), -2))
		frappe.set_user(HUB)
		row = self.row(self.report())
		self.assertEqual(
			row["positive_leads"],
			[{"name": today, "lead_name": "Positive today", "company_name": "Today Traders"}],
		)
		self.assertEqual(len(self.row(self.report(date=add_days(nowdate(), -2)))["positive_leads"]), 1)

	def sent_quotation(self, lead: str, days_ago: int = 0) -> str:
		"""A quotation of 2 x 5000 sent by BDE on the lead, `days_ago` back."""
		enable_lead_quotations()
		frappe.set_user(BDE)
		quote = lead_quotation.save_quotation(
			lead,
			json.dumps(
				{
					"valid_till": add_days(nowdate(), 20),
					"items": [{"item_code": SERVICE, "qty": 2, "price_list_rate": 5000}],
				}
			),
		)
		lead_quotation.mark_sent(quote["name"], "Manual", follow_up_days=0)
		frappe.set_user("Administrator")
		if days_ago:
			frappe.db.set_value(
				"Quotation",
				quote["name"],
				"tbo_sent_on",
				add_days(now_datetime(), -days_ago),
				update_modified=False,
			)
		return quote["name"]

	def test_new_quotations_are_the_ones_sent_that_day(self):
		frappe.set_user("Administrator")
		today = self.sent_quotation(make_lead(BDE, "Quoted today"))
		self.sent_quotation(make_lead(BDE, "Quoted before"), days_ago=1)
		frappe.set_user(HUB)
		quotations = self.row(self.report())["new_quotations"]
		self.assertEqual(
			(quotations["count"], quotations["value"]),
			(1, frappe.db.get_value("Quotation", today, "base_grand_total")),
		)
		self.assertGreater(quotations["value"], 0)
		[item] = quotations["items"]
		self.assertEqual(
			(item["kind"], item["name"], item["lead_name"]), ("quotation", today, "Quoted today")
		)
		self.assertEqual(self.row(self.report(date=add_days(nowdate(), -1)))["new_quotations"]["count"], 1)
		# the one sent yesterday is in this week unless today is Monday
		on_monday = week_start(nowdate()) == getdate(nowdate())
		self.assertEqual(self.row(self.report())["week_to_date"]["proposals"], 1 if on_monday else 2)

	def test_meetings_and_follow_ups(self):
		frappe.set_user("Administrator")
		lead = make_lead(BDE, "Met")
		move_meeting(make_lead_meeting(lead), add_to_date(now_datetime(), minutes=-1))
		make_lead_task(lead, nowdate(), BDE, status="Closed")
		make_lead_task(lead, add_days(nowdate(), -2), BDE)
		frappe.set_user(HUB)
		row = self.row(self.report())
		self.assertEqual((row["meetings_held"], row["follow_ups_done"], row["follow_ups_overdue"]), (1, 1, 1))

	def test_new_leads_leave_out_imports(self):
		frappe.set_user("Administrator")
		mine = make_lead(BDE, "Ravi Kumar", company_name="Kumar Traders")
		make_lead(BDE, "Imported", crm_lead_ref="tboindia-old:CRM-LEAD-2025-00002")
		self.old_lead(name="Made before")
		frappe.set_user(HUB)
		row = self.row(self.report())
		self.assertEqual(
			row["new_leads"], [{"name": mine, "lead_name": "Ravi Kumar", "company_name": "Kumar Traders"}]
		)

	def test_week_to_date_runs_from_monday(self):
		frappe.set_user("Administrator")
		monday = week_start(nowdate())
		on_monday = make_lead(BDE, "Monday lead")
		frappe.db.set_value("Lead", on_monday, "creation", monday, update_modified=False)
		make_lead(BDE, "Today lead")
		frappe.set_user(HUB)
		week = self.row(self.report())["week_to_date"]
		self.assertEqual(week["leads"], 2)
		self.assertEqual(self.report()["week_from"], monday)
		last_week = self.report(date=add_days(monday, -1))
		self.assertEqual(self.row(last_week)["week_to_date"]["leads"], 0)

	def test_scope_follows_the_acting_user(self):
		frappe.set_user("Administrator")
		make_lead(BDE, "Mine")
		make_lead(BDE2, "Theirs")
		frappe.set_user(HUB)
		self.assertEqual([row["user"] for row in self.report(BDE)["people"]], [BDE])
		everyone = {row["user"] for row in self.report(MANAGER)["people"]}
		self.assertTrue({BDE, BDE2} <= everyone)
		just = self.report(MANAGER, owner=BDE2)["people"]
		self.assertEqual([(row["user"], len(row["new_leads"])) for row in just], [(BDE2, 1)])
		self.assertRaises(frappe.PermissionError, self.report, BDE, owner=BDE2)
		self.assertRaises(frappe.PermissionError, hub.get_daily_report)
		self.assertEqual(frappe.session.user, HUB)


class TestLogCall(DailyReportTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.lead = make_lead(BDE, "Logged call", mobile_no="9847012345")
		frappe.set_user(HUB)

	def test_a_logged_call_is_an_outgoing_tbo_call_of_the_acting_user(self):
		made = hub.log_call(self.lead, "Connected", "Wants a demo", acting_user=BDE)
		call = frappe.get_doc("TBO Call", made["name"])
		self.assertEqual(
			(call.direction, call.status, call.agent, call.lead, call.customer_number),
			("Outgoing", "Answered", BDE, self.lead, "9847012345"),
		)
		self.assertTrue(call.call_id.startswith("manual-"))
		self.assertEqual(frappe.db.get_value("Event", call.event, "status"), "Completed")
		hub.log_call(self.lead, "No answer", acting_user=BDE)
		hub.log_call(self.lead, "Busy", acting_user=BDE)
		hub.log_call(self.lead, "Voicemail", acting_user=BDE)
		row = self.row(self.report())
		self.assertEqual((row["calls_total"], row["calls_connected"]), (4, 1))
		self.assertEqual(
			sorted(frappe.get_all("TBO Call", filters={"lead": self.lead}, pluck="status")),
			["Answered", "Missed", "Missed", "Voicemail"],
		)

	def test_the_outcome_and_the_lead_are_checked(self):
		self.assertRaises(frappe.ValidationError, hub.log_call, self.lead, "Talked", acting_user=BDE)
		self.assertRaises(frappe.PermissionError, hub.log_call, self.lead, "Connected", acting_user=BDE2)
		self.assertFalse(frappe.db.exists("TBO Call", {"lead": self.lead}))
