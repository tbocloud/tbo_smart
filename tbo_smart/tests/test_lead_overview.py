import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, now_datetime, nowdate

from tbo_smart.api import hub_leads as hub
from tbo_smart.api import lead_quotation
from tbo_smart.lead_pipeline import overview
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_LOST,
	CLOSED_WON,
	CONTACTED,
	POSITIVE,
	PROPOSAL_SENT,
	STATUS_NAMES,
)
from tbo_smart.test_utils import (
	age_lead,
	change_lead_stage,
	delete_lead_pipeline_records,
	disable_lead_quotations,
	disable_sales_hierarchy,
	enable_lead_quotations,
	hours_from_now,
	make_lead,
	make_lead_meeting,
	make_lead_note,
	make_lead_source,
	make_lead_task,
	make_quotation_item,
	make_user,
	move_meeting,
	set_lead_values,
	set_smart_lead_settings,
)

HUB = "ov.integration@tbosmart.test"
BDE = "ov.bde@tbosmart.test"
BDE2 = "ov.bde2@tbosmart.test"
MANAGER = "ov.manager@tbosmart.test"
OWNERS = (BDE, BDE2, MANAGER)
SOURCE = "TBO Smart Overview Website"
SERVICE = "TBO Smart Overview Service"


class TestLeadOverview(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])
		disable_sales_hierarchy(OWNERS)
		make_lead_source(SOURCE)
		make_quotation_item(SERVICE)
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_lead_pipeline_records(OWNERS)
		disable_lead_quotations()
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_lead_pipeline_records(OWNERS)
		set_smart_lead_settings(enable_lead_pipeline=0, lead_stale_days=21)
		self.new = make_lead(BDE, "Fresh", source=SOURCE, tbo_segment="Small")
		self.hot = make_lead(BDE, "Keen", tbo_segment="Large")
		change_lead_stage(self.hot, POSITIVE)
		set_lead_values(self.hot, tbo_score=72, tbo_temperature="Hot")
		self.won = make_lead(BDE, "Winner", source=SOURCE)
		change_lead_stage(self.won, CONTACTED)
		change_lead_stage(self.won, PROPOSAL_SENT)
		change_lead_stage(self.won, CLOSED_WON)
		self.lost = make_lead(BDE, "Gone")
		change_lead_stage(self.lost, CLOSED_LOST, tbo_lost_reason="Other", tbo_lost_notes="Price")
		self.stale = make_lead(BDE, "Quiet")
		age_lead(self.stale, 40)
		self.revived = make_lead(BDE, "Revived")
		age_lead(self.revived, 40)
		make_lead_note(self.revived, "Called back, wants a demo")
		self.theirs = make_lead(BDE2, "Elsewhere")
		change_lead_stage(self.theirs, CLOSED_WON)
		enable_lead_quotations()
		held = make_lead_meeting(self.hot)
		move_meeting(held, hours_from_now(-3))
		frappe.db.set_value(
			"TBO Lead Meeting", held, {"status": "MOM Submitted", "submitted_on": now_datetime()}
		)
		make_lead_task(self.new, add_days(nowdate(), -2), BDE)
		make_lead_task(self.hot, add_days(nowdate(), -5), BDE, status="Closed")
		frappe.set_user(BDE)
		quote = lead_quotation.save_quotation(
			self.hot,
			json.dumps(
				{
					"valid_till": add_days(nowdate(), 20),
					"items": [{"item_code": SERVICE, "qty": 2, "price_list_rate": 5000}],
				}
			),
		)
		lead_quotation.mark_sent(quote["name"], "Manual", follow_up_days=0)
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_period_ranges(self):
		today = getdate(nowdate())
		start, end = overview.period_range("week")
		self.assertEqual((start.weekday(), end), (0, today))
		self.assertEqual(overview.period_range("month")[0].day, 1)
		self.assertEqual(overview.period_range("quarter")[0].month % 3, 1)
		self.assertEqual(
			overview.period_range("custom", "2026-01-01", "2026-01-31"),
			(getdate("2026-01-01"), getdate("2026-01-31")),
		)
		self.assertRaises(frappe.ValidationError, overview.period_range, "custom")
		self.assertRaises(frappe.ValidationError, overview.period_range, "decade")

	def test_overview_follows_the_acting_users_scope(self):
		mine = hub.get_overview(acting_user=BDE)
		self.assertEqual(mine["scope"], "own")
		self.assertEqual(frappe.session.user, HUB)
		totals = mine["totals"]
		# the aged leads were created before this month
		self.assertEqual((totals["new"], totals["won"], totals["lost"]), (4, 1, 1))
		# the hot lead's quotation and the won lead's stage log both count as a proposal sent
		self.assertEqual((totals["meetings"], totals["quotations_sent"], totals["hot"]), (1, 2, 1))
		self.assertEqual((totals["overdue"], totals["stale"]), (1, 1))
		self.assertEqual(mine["lost_reasons"], [{"reason": "Other", "count": 1}])
		everyone = hub.get_overview(acting_user=MANAGER)
		self.assertEqual(everyone["scope"], "all")
		self.assertGreaterEqual(everyone["totals"]["won"], 2)
		self.assertRaises(frappe.PermissionError, hub.get_overview, owner=BDE2, acting_user=BDE)
		# dates alone are a custom range, as for the lists the numbers open
		old = hub.get_overview(from_date="2020-01-01", to_date="2020-01-31", acting_user=BDE)
		self.assertEqual((old["period"], old["totals"]["new"]), ("custom", 0))
		just_bde2 = hub.get_overview(owner=BDE2, acting_user=MANAGER)
		self.assertEqual((just_bde2["totals"]["new"], just_bde2["totals"]["won"]), (1, 1))

	def test_pipeline_value_funnel_and_breakdowns(self):
		result = hub.get_overview(acting_user=BDE)
		stages = {row["stage"]: row for row in result["pipeline"]}
		self.assertEqual(len(stages), len(STATUS_NAMES))
		# sending the quotation moved the hot lead on to Proposal Sent
		self.assertEqual(frappe.db.get_value("Lead", self.hot, "tbo_stage"), PROPOSAL_SENT)
		self.assertEqual(stages[PROPOSAL_SENT]["count"], 1)
		self.assertGreater(stages[PROPOSAL_SENT]["value"], 0)
		self.assertEqual(result["totals"]["open"], 4)
		self.assertEqual(result["totals"]["open_value"], stages[PROPOSAL_SENT]["value"])
		funnel = {row["step"]: row["count"] for row in result["funnel"]}
		self.assertEqual(funnel["created"], 4)
		self.assertEqual((funnel["proposal"], funnel["won"]), (2, 1))
		self.assertGreaterEqual(funnel["contacted"], funnel["meeting"])
		segments = {row["value"]: row for row in result["by_segment"]}
		self.assertEqual((segments["Small"]["new"], segments["Large"]["open"]), (1, 1))
		sources = {row["value"]: row for row in result["by_source"]}
		self.assertEqual((sources[SOURCE]["new"], sources[SOURCE]["won"]), (2, 1))
		[owner] = result["by_owner"]
		self.assertEqual((owner["user"], owner["new"], owner["won"], owner["meetings_held"]), (BDE, 4, 1, 1))

	def test_every_number_opens_its_leads(self):
		def listed(metric, **filters):
			rows = hub.list_leads(metric=metric, page_length=100, acting_user=BDE, **filters)["rows"]
			return {row["name"] for row in rows}

		self.assertEqual(listed("won"), {self.won})
		self.assertEqual(listed("lost"), {self.lost})
		self.assertEqual(listed("stale"), {self.stale})
		self.assertEqual(listed("overdue"), {self.new})
		self.assertEqual(listed("hot"), {self.hot})
		self.assertEqual(listed("open"), {self.new, self.hot, self.stale, self.revived})
		self.assertEqual(listed("meetings"), {self.hot})
		# the hot lead's sent quotation and the won lead's passage through Proposal Sent
		self.assertEqual(listed("quotations"), {self.hot, self.won})
		self.assertIn(self.revived, listed("contacted"))
		self.assertNotIn(self.stale, listed("contacted"))
		self.assertEqual(listed("new"), {self.new, self.hot, self.won, self.lost})
		self.assertEqual(listed("new", period="custom", from_date="2020-01-01", to_date="2020-01-31"), set())
		# dates alone are a custom range
		self.assertEqual(listed("new", from_date="2020-01-01", to_date="2020-01-31"), set())
		self.assertEqual(listed("won", view="mine"), {self.won})
		self.assertRaises(frappe.ValidationError, hub.list_leads, metric="nope", acting_user=BDE)
		# plain filters the overview links to
		self.assertEqual(
			{row["name"] for row in hub.list_leads(source=SOURCE, acting_user=BDE)["rows"]},
			{self.new, self.won},
		)
		self.assertEqual(
			[row["name"] for row in hub.list_leads(temperature="Hot", acting_user=BDE)["rows"]], [self.hot]
		)

	def test_sales_activity_for_the_scoreboard(self):
		# the hub's integration user asks for everyone, with nobody acting
		result = hub.get_sales_activity(str(overview.period_range("month")[0]), nowdate())
		people = {row["user"]: row for row in result["people"]}
		bde = people[BDE]
		self.assertEqual((bde["won"], bde["new"], bde["conversion"]), (1, 4, 25.0))
		self.assertEqual((bde["moms_submitted"], bde["meetings_held"], bde["quotations_sent"]), (1, 1, 1))
		self.assertEqual(
			(bde["follow_ups_done"], bde["follow_ups_on_time"], bde["follow_ups_overdue"]), (1, 0, 1)
		)
		self.assertGreaterEqual(bde["contacted"], 1)
		self.assertEqual(people[BDE2]["won"], 1)
		self.assertNotIn("leads", bde)
		# a salesperson asking gets their own scope only
		frappe.set_user(BDE)
		self.assertEqual([row["user"] for row in hub.get_sales_activity()["people"]], [BDE])
		self.assertRaises(frappe.PermissionError, hub.get_sales_activity, acting_user=BDE2)
