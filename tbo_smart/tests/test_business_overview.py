import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_WON,
	CONTACTED,
	FIRST_MEETING,
	NEGOTIATION,
	PROPOSAL_SENT,
)
from tbo_smart.test_utils import (
	change_lead_stage,
	delete_lead_departments,
	delete_lead_pipeline_records,
	enable_finance_receivables,
	make_deal,
	make_deal_invoice,
	make_finance_deal,
	make_lead,
	make_lead_department,
	make_management_user,
	make_user,
	set_smart_lead_settings,
)

HUB = "biz.integration@tbosmart.test"
BDE = "biz.bde@tbosmart.test"
BOSS = "biz.management@tbosmart.test"
ADMIN = "biz.sysmanager@tbosmart.test"
OWNERS = (BDE,)
DEPARTMENT = "TBO Smart Business Dept"


class TestBusinessOverview(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User"])
		make_management_user(BOSS)
		make_user(ADMIN, ["System Manager"])
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_lead_pipeline_records(OWNERS)
		delete_lead_departments([DEPARTMENT])
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_lead_pipeline_records(OWNERS)
		delete_lead_departments([DEPARTMENT])
		set_smart_lead_settings(enable_lead_pipeline=0)
		make_lead_department(DEPARTMENT, heads=[BDE])
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def business(self, **values):
		return hub.get_business_overview(acting_user=BOSS, department=DEPARTMENT, **values)

	def test_only_management_may_ask(self):
		self.assertRaises(frappe.PermissionError, hub.get_business_overview, acting_user=BDE)
		self.assertRaises(frappe.PermissionError, hub.get_options, acting_user="nobody@tbosmart.test")
		for user in (BOSS, ADMIN):
			result = hub.get_business_overview(acting_user=user, department=DEPARTMENT)
			self.assertEqual(result["department"], DEPARTMENT)
		self.assertTrue(hub.get_options(acting_user=BOSS)["can_view_business"])
		self.assertTrue(hub.get_options(acting_user=ADMIN)["can_view_business"])
		self.assertFalse(hub.get_options(acting_user=BDE)["can_view_business"])
		self.assertEqual(frappe.session.user, HUB)

	def test_unknown_department_is_refused(self):
		self.assertRaises(
			frappe.ValidationError, hub.get_business_overview, department="Nowhere", acting_user=BOSS
		)

	def test_pipeline_proposals_and_onboarding_come_from_the_stage_log(self):
		mine = {"tbo_department": DEPARTMENT}
		make_lead(BDE, "Fresh", **mine)
		met = make_lead(BDE, "Met", **mine)
		change_lead_stage(met, FIRST_MEETING)
		offered = make_lead(BDE, "Offered", **mine)
		change_lead_stage(offered, PROPOSAL_SENT)
		won = make_lead(BDE, "Won", **mine)
		change_lead_stage(won, CONTACTED)
		change_lead_stage(won, PROPOSAL_SENT)
		change_lead_stage(won, CLOSED_WON)
		deal = make_deal(BDE, deal_value=25000, tbo_lead=won)
		self.assertTrue(deal)

		result = self.business()
		stages = {row["stage"]: row["count"] for row in result["pipeline"]["stages"]}
		self.assertEqual(result["pipeline"]["count"], 3)
		self.assertEqual((stages[FIRST_MEETING], stages[PROPOSAL_SENT]), (1, 1))
		self.assertFalse(result["pipeline"]["weighted"])
		# leads moved by hand have no meeting record or sent document: the stage log counts
		self.assertEqual(result["meetings_held"], 1)
		self.assertEqual(result["proposals"]["sent"]["count"], 2)
		self.assertEqual(result["onboarding"]["won"]["count"], 1)
		# the won lead has no quotation: its deal's amount is its value
		self.assertEqual(result["onboarding"]["won"]["value"], 25000)
		self.assertEqual(result["onboarding"]["won"]["unpriced"], 0)
		funnel = {row["step"]: row["count"] for row in result["funnel"]}
		self.assertEqual((funnel["created"], funnel["meeting"], funnel["won"]), (4, 3, 1))
		self.assertEqual(result["new_leads"], 4)
		self.assertEqual(len(result["trend"]), 6)
		self.assertEqual(result["trend"][-1]["new"], 4)
		self.assertEqual(result["trend"][-1]["won"], 1)
		[row] = result["by_department"]
		self.assertEqual((row["department"], row["open"], row["won"]), (DEPARTMENT, 3, 1))

	def test_overview_agrees_with_the_business_view(self):
		met = make_lead(BDE, "Met")
		change_lead_stage(met, FIRST_MEETING)
		offered = make_lead(BDE, "Offered")
		change_lead_stage(offered, NEGOTIATION)
		change_lead_stage(offered, PROPOSAL_SENT)
		totals = hub.get_overview(acting_user=BDE)["totals"]
		self.assertEqual((totals["meetings"], totals["quotations_sent"]), (1, 1))

	def test_revenue_counts_invoices_and_what_is_owed(self):
		enable_finance_receivables()
		before = hub.get_business_overview(acting_user=BOSS)["revenue"]
		frappe.set_user("Administrator")
		deal = make_finance_deal(BDE)
		make_deal_invoice(deal, rate=1000)
		frappe.set_user(HUB)
		after = hub.get_business_overview(acting_user=BOSS)
		self.assertGreater(after["revenue"]["invoiced"], before["invoiced"])
		self.assertGreater(after["revenue"]["outstanding"], before["outstanding"])
		self.assertEqual(
			sum(row["invoiced"] for row in after["revenue"]["months"]), after["revenue"]["invoiced"]
		)
		self.assertTrue(after["top_clients"])
		self.assertIsNone(after["profit"]["profit"])
		with_profit = hub.get_business_overview(acting_user=BOSS, include_profit=1)
		self.assertTrue(with_profit["profit"]["available"])
		self.assertEqual(
			with_profit["profit"]["profit"],
			with_profit["profit"]["income"] - with_profit["profit"]["expense"],
		)
