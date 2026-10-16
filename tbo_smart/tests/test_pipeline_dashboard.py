import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime, nowdate

from tbo_smart.api import lead_pipeline_dashboard as api
from tbo_smart.api import lead_quotation
from tbo_smart.lead_pipeline import dashboard
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_LOST,
	CLOSED_WON,
	MEETING_SCHEDULED,
	NEW_LEAD,
	POSITIVE,
	PROPOSAL_SENT,
)
from tbo_smart.test_utils import (
	change_lead_status,
	delete_lead_deals,
	delete_lead_meetings,
	delete_lead_quotations,
	delete_smart_lead_records,
	disable_lead_quotations,
	disable_sales_hierarchy,
	enable_lead_quotations,
	hours_from_now,
	make_crm_lead,
	make_crm_lead_source,
	make_lead_meeting,
	make_lead_task,
	make_quotation_item,
	make_user,
	move_meeting,
	set_lead_values,
	set_smart_lead_settings,
)

BDE = "pd.bde@tbosmart.test"
BDE2 = "pd.bde2@tbosmart.test"
MANAGER = "pd.manager@tbosmart.test"
OWNERS = (BDE, BDE2, MANAGER)
SOURCE = "TBO Smart Test Website"
SERVICE = "TBO Smart Dashboard Service"


class TestPipelineDashboard(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])
		disable_sales_hierarchy(OWNERS)
		make_crm_lead_source(SOURCE)
		make_quotation_item(SERVICE)
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls.clean()
		disable_lead_quotations()
		set_smart_lead_settings(enable_pipeline_dashboard=0)
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	@classmethod
	def clean(cls):
		leads = frappe.get_all("CRM Lead", filters={"lead_owner": ("in", OWNERS)}, pluck="name")
		delete_lead_quotations(leads)
		delete_lead_meetings(leads)
		delete_lead_deals(leads)
		delete_smart_lead_records(OWNERS)

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		# statuses are set with the pipeline off, so no deals are made for the won leads
		set_smart_lead_settings(enable_lead_pipeline=0)
		self.new = make_crm_lead(BDE, "Fresh", source=SOURCE)
		self.hot = make_crm_lead(BDE, "Keen", organization="Keen Traders")
		change_lead_status(self.hot, POSITIVE)
		set_lead_values(self.hot, lead_score=72, lead_temperature="Hot")
		self.won = make_crm_lead(BDE, "Winner")
		change_lead_status(self.won, CLOSED_WON)
		self.lost = make_crm_lead(BDE, "Gone")
		change_lead_status(self.lost, CLOSED_LOST, lost_reason="Other", lost_notes="Price")
		self.other_won = make_crm_lead(BDE2, "Elsewhere")
		change_lead_status(self.other_won, CLOSED_WON)
		enable_lead_quotations(enable_pipeline_dashboard=1)
		held = make_lead_meeting(self.hot)
		move_meeting(held, hours_from_now(-3))
		frappe.db.set_value(
			"TBO Lead Meeting", held, {"status": "MOM Submitted", "submitted_on": now_datetime()}
		)
		pending = make_lead_meeting(self.hot)
		move_meeting(
			pending, hours_from_now(-30), status="MOM Pending", mom_pending_since=hours_from_now(-28)
		)
		make_lead_meeting(self.new, hours_from_now(30))
		make_lead_task(self.new, add_days(nowdate(), -2), BDE)
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
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_a_bde_sees_their_own_pipeline(self):
		frappe.set_user(BDE)
		board = api.get_pipeline_dashboard()
		counts = {row["status"]: row["count"] for row in board["leads_by_status"]}
		self.assertEqual(board["scope"], "own")
		# the meetings and the sent quotation moved the open leads on
		self.assertEqual((counts[MEETING_SCHEDULED], counts[PROPOSAL_SENT], counts[POSITIVE]), (1, 1, 0))
		self.assertEqual(counts[CLOSED_WON], 1)
		self.assertEqual((board["won"], board["lost"], board["win_rate"]), (1, 1, 50.0))
		self.assertEqual(board["lost_reasons"], [{"reason": "Other", "count": 1}])
		self.assertEqual(board["conversion_rate"], 25.0)
		self.assertEqual(board["meetings_completed"]["first"], 1)
		self.assertEqual(board["mom_pending"]["count"], 1)
		self.assertEqual(board["upcoming_meetings"]["count"], 1)
		self.assertGreaterEqual(board["overdue_follow_ups"], 1)
		self.assertEqual([row.name for row in board["high_potential"]["rows"]], [self.hot])
		row = next(row for row in board["by_bde"] if row["user"] == BDE)
		self.assertEqual(
			(row["meetings_held"], row["mom_pending"], row["mom_rate"], row["quotations_sent"], row["won"]),
			(1, 1, 50.0, 1, 1),
		)
		quotes = board["quotations"]
		self.assertEqual((quotes["count"], quotes["awaiting_client"], quotes["active"]["count"]), (1, 1, 1))
		self.assertEqual(quotes["active"]["value"], 10000)
		self.assertNotIn(BDE2, [row["user"] for row in board["by_bde"]])
		self.assertRaises(frappe.PermissionError, api.get_pipeline_dashboard, bde=BDE2)

	def test_a_manager_sees_everyone_and_filters(self):
		frappe.set_user(MANAGER)
		board = api.get_pipeline_dashboard()
		self.assertEqual(board["scope"], "all")
		# a BDE filter narrows the figures, not the manager's scope
		self.assertEqual(api.get_pipeline_dashboard(bde=BDE)["scope"], "all")
		self.assertGreaterEqual(board["won"], 2)
		self.assertEqual(api.get_pipeline_dashboard(bde=BDE2)["won"], 1)
		by_source = api.get_pipeline_dashboard(bde=BDE, source=SOURCE)
		self.assertEqual(sum(row["count"] for row in by_source["leads_by_status"]), 1)
		self.assertEqual(api.get_pipeline_dashboard(bde=BDE, status=MEETING_SCHEDULED)["leads_created"], 1)
		self.assertEqual(api.get_pipeline_dashboard(bde=BDE, status=NEW_LEAD)["leads_created"], 0)
		self.assertRaises(
			frappe.ValidationError,
			api.get_pipeline_dashboard,
			from_date=nowdate(),
			to_date=add_days(nowdate(), -1),
		)

	def test_cards_follow_the_dashboard_user(self):
		frappe.set_user(BDE)
		cards = {
			card["name"]: card["data"]
			for card in dashboard.get_cards(add_days(nowdate(), -30), nowdate(), BDE)
		}
		self.assertEqual(cards["tbo_pipeline_mom_pending"]["value"], 1)
		self.assertEqual(cards["tbo_pipeline_win_rate"]["value"], 50.0)
		self.assertEqual(cards["tbo_pipeline_active_quotes"]["value"], 10000)
		self.assertEqual(dashboard.get_cards(nowdate(), nowdate(), BDE2), [])

	def test_switch_off(self):
		set_smart_lead_settings(enable_pipeline_dashboard=0)
		frappe.set_user(BDE)
		self.assertFalse(api.get_access()["enabled"])
		self.assertRaises(frappe.ValidationError, api.get_pipeline_dashboard)
		self.assertEqual(dashboard.get_cards(nowdate(), nowdate(), BDE), [])
