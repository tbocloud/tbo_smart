from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_WON,
	NOT_INTERESTED,
	PROPOSAL_SENT,
	WRONG_NUMBER,
)
from tbo_smart.test_utils import (
	change_lead_stage,
	delete_call_batches,
	delete_lead_departments,
	delete_sales_records,
	disable_sales_hierarchy,
	make_batch_department,
	make_call,
	make_call_batch,
	make_callable_lead,
	make_lead_source,
	make_user,
)

HUB = "analysis.integration@tbosmart.test"
BD = "analysis.bd@tbosmart.test"
BD2 = "analysis.bd2@tbosmart.test"
MANAGER = "analysis.manager@tbosmart.test"
OWNERS = (BD, BD2, MANAGER)
DEPARTMENT = "TBO Test Analysis"
WEBSITE = "TBO Analysis Website"
REFERRAL = "TBO Analysis Referral"


class TestLeadAnalysis(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BD, ["Sales User"])
		make_user(BD2, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])
		make_lead_source(WEBSITE)
		make_lead_source(REFERRAL)

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_sales_hierarchy(OWNERS)
		department = make_batch_department(DEPARTMENT, members=[BD, BD2])
		lead = lambda name, owner, **fields: make_callable_lead(owner, name, department, **fields)  # noqa: E731
		self.connected = lead("Connected", BD, source=WEBSITE)
		self.unanswered = lead("Unanswered", BD, source=WEBSITE)
		self.declined = lead("Declined", BD, source=WEBSITE)
		self.waiting = lead("Waiting", BD, source=WEBSITE)
		self.earlier = lead("Earlier", BD, source=WEBSITE)
		self.theirs = lead("Theirs", BD2, source=REFERRAL)
		change_lead_stage(self.connected, PROPOSAL_SENT)
		change_lead_stage(self.connected, CLOSED_WON)
		change_lead_stage(self.unanswered, WRONG_NUMBER)
		change_lead_stage(self.declined, NOT_INTERESTED)
		make_call_batch(
			BD,
			department,
			[self.connected, self.unanswered, self.declined, self.waiting],
			outcomes={
				self.connected: "Connected",
				self.unanswered: "Not answered",
				self.declined: "Not interested",
			},
		)
		# a slot that ended with one call nobody made
		make_call_batch(BD, department, [self.earlier], slot="Earlier", ends_in_hours=-0.5)
		make_call_batch(BD2, department, [self.theirs], outcomes={self.theirs: "Connected"})
		make_call(BD, status="Answered")
		make_call(BD, status="Missed")
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.clean()

	def clean(self):
		delete_call_batches(OWNERS)
		delete_sales_records(OWNERS)
		frappe.db.delete("TBO Call", {"agent": ("in", [BD, BD2])})
		for lead in frappe.get_all("Lead", filters={"tbo_department": DEPARTMENT}, pluck="name"):
			frappe.delete_doc("Lead", lead, force=True, ignore_permissions=True)
		delete_lead_departments((DEPARTMENT,))

	def analysis(self, user: str, **params) -> dict:
		today = nowdate()
		return hub.get_lead_analysis(
			period="custom", from_date=today, to_date=today, acting_user=user, **params
		)

	def test_calling_performance_counts_each_persons_batches_and_calls(self):
		rows = {row["user"]: row for row in self.analysis(MANAGER)["calling"]}
		person = rows[BD]
		self.assertEqual(
			(person["given"], person["called"], person["reached"], person["overdue"]),
			(5, 3, 2, 1),
		)
		self.assertEqual((person["completion"], person["reach_rate"]), (60.0, 66.7))
		self.assertEqual((person["calls"], person["missed"], person["talk_seconds"]), (2, 1, 60))
		self.assertEqual((rows[BD2]["given"], rows[BD2]["reach_rate"]), (1, 100.0))

	def test_a_salesperson_sees_only_their_own_figures(self):
		analysis = self.analysis(BD)
		self.assertEqual({row["user"] for row in analysis["calling"]}, {BD})
		self.assertRaises(frappe.PermissionError, self.analysis, BD, owner=BD2)

	def test_lead_quality_groups_the_leads_created_in_the_period(self):
		rows = {row["value"]: row for row in self.analysis(MANAGER)["quality"]["source"]["rows"]}
		website = rows[WEBSITE]
		self.assertEqual(
			(website["leads"], website["called"], website["called_rate"], website["reached"]),
			(5, 3, 60.0, 2),
		)
		self.assertEqual(
			(website["reach_rate"], website["wrong_rate"], website["not_interested_rate"]),
			(66.7, 20.0, 20.0),
		)
		self.assertEqual((website["moved_on"], website["won"]), (1, 1))
		self.assertEqual((rows[REFERRAL]["leads"], rows[REFERRAL]["reach_rate"]), (1, 100.0))
		self.assertIn(
			DEPARTMENT, {row["value"] for row in self.analysis(MANAGER)["quality"]["department"]["rows"]}
		)

	def test_best_and_worst_wait_for_enough_calls(self):
		source = self.analysis(MANAGER)["quality"]["source"]
		self.assertEqual((source["best"], source["worst"]), (None, None))
		with patch("tbo_smart.lead_pipeline.analysis.MIN_CALLED", 1):
			source = self.analysis(MANAGER)["quality"]["source"]
		self.assertEqual((source["best"], source["worst"]), (REFERRAL, WEBSITE))
