import frappe
from frappe.desk.form.assign_to import add as add_assignment
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, today

from tbo_smart.lead_pipeline.statuses import CLOSED_LOST, CONTACTED, NEW_LEAD
from tbo_smart.sales import lead_url
from tbo_smart.sales.activities import add_call, add_follow_up_task, add_log, add_note, get_timeline
from tbo_smart.sales.hierarchy import get_sales_team, managers_of
from tbo_smart.sales.opportunity import customer_of, stage_type
from tbo_smart.test_utils import (
	change_lead_stage,
	delete_sales_records,
	disable_sales_hierarchy,
	enable_sales_hierarchy,
	make_customer,
	make_deal,
	make_lead,
	make_sales_hierarchy_node,
	make_sales_stage,
	make_user,
	set_smart_lead_settings,
)

REP = "sales.rep@tbosmart.test"
REP2 = "sales.rep2@tbosmart.test"
LEAD_USER = "sales.teamlead@tbosmart.test"
MANAGER = "sales.manager@tbosmart.test"
OWNERS = (REP, REP2, LEAD_USER, MANAGER)


class TestSalesRecords(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		for user, roles in ((REP, ["Sales User"]), (REP2, ["Sales User"]), (LEAD_USER, ["Sales User"])):
			make_user(user, roles)
		make_user(MANAGER, ["Sales Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		disable_sales_hierarchy(OWNERS)

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_sales_hierarchy(OWNERS)

	def visible(self, user: str, doctype: str = "Lead") -> set[str]:
		frappe.set_user(user)
		try:
			return set(frappe.get_list(doctype, pluck="name", limit_page_length=0))
		finally:
			frappe.set_user("Administrator")

	def test_owners_assignees_and_managers(self):
		mine, theirs = make_lead(REP, "Rep Lead"), make_lead(REP2, "Rep2 Lead")
		self.assertIn(mine, self.visible(REP))
		self.assertNotIn(theirs, self.visible(REP))
		self.assertFalse(frappe.has_permission("Lead", "read", theirs, user=REP))
		# an assignment opens the lead to the assignee
		add_assignment({"assign_to": [REP], "doctype": "Lead", "name": theirs})
		self.assertIn(theirs, self.visible(REP))
		self.assertTrue(frappe.has_permission("Lead", "write", theirs, user=REP))
		# a Sales Manager outside the hierarchy sees everyone's
		self.assertTrue({mine, theirs} <= self.visible(MANAGER))

	def test_the_hierarchy_opens_the_team(self):
		enable_sales_hierarchy()
		make_sales_hierarchy_node(REP, make_sales_hierarchy_node(LEAD_USER))
		rep_lead, own_lead = make_lead(REP, "Team Rep Lead"), make_lead(LEAD_USER, "Team Lead Lead")
		other = make_lead(REP2, "Outside Lead")
		self.assertEqual(get_sales_team(LEAD_USER), sorted([LEAD_USER, REP]))
		self.assertEqual(managers_of([REP, REP2]), {REP: LEAD_USER})
		seen = self.visible(LEAD_USER)
		self.assertTrue({rep_lead, own_lead} <= seen)
		self.assertNotIn(other, seen)
		self.assertNotIn(own_lead, self.visible(REP))
		deal = make_deal(REP)
		self.assertIn(deal, self.visible(LEAD_USER, "Opportunity"))
		self.assertNotIn(deal, self.visible(REP2, "Opportunity"))

	def test_stage_log_and_lost_reason(self):
		lead = make_lead(REP, "Logged")
		change_lead_stage(lead, CONTACTED)
		self.assertRaises(frappe.ValidationError, change_lead_stage, lead, CLOSED_LOST)
		self.assertRaises(
			frappe.ValidationError, change_lead_stage, lead, CLOSED_LOST, tbo_lost_reason="Other"
		)
		change_lead_stage(lead, CLOSED_LOST, tbo_lost_reason="Other", tbo_lost_notes="Too far away")
		log = frappe.get_all(
			"TBO Stage Change",
			filters={"parent": lead, "parenttype": "Lead"},
			fields=["stage", "previous_stage", "changed_by"],
			order_by="idx",
		)
		self.assertEqual(
			[(row.stage, row.previous_stage) for row in log],
			[(NEW_LEAD, None), (CONTACTED, NEW_LEAD), (CLOSED_LOST, CONTACTED)],
		)

	def test_deal_stage_value_and_customer(self):
		make_sales_stage("TBO Test Won", "Won", 100, 90)
		make_sales_stage("TBO Test Lost", "Lost", 0, 91)
		customer = make_customer(f"TBO Sales {frappe.generate_hash(length=6)}")
		deal = make_deal(REP, "TBO Test Won", 2500, opportunity_from="Customer", party_name=customer)
		doc = frappe.get_doc("Opportunity", deal)
		self.assertEqual(stage_type(doc.sales_stage), "Won")
		self.assertEqual(getdate(doc.tbo_closed_on), getdate(today()))
		self.assertEqual(doc.base_opportunity_amount, 2500)
		self.assertEqual(customer_of(doc), customer)
		doc.sales_stage = "TBO Test Lost"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.status, "Lost")
		self.assertEqual([row.stage for row in doc.tbo_stage_log], ["TBO Test Won", "TBO Test Lost"])

	def test_activities_on_a_lead(self):
		lead = make_lead(REP, "Busy", mobile_no="9847001234")
		frappe.set_user(REP)
		add_note("Lead", lead, "Asked for the brochure")
		add_call("Lead", lead, "Talked for ten minutes")
		add_log("Lead", lead, "whatsapp", "Sent the brochure")
		task = add_follow_up_task("Lead", lead, today(), title="Call back", description="About the demo")
		timeline = get_timeline("Lead", lead)
		self.assertEqual([note["text"] for note in timeline["notes"]], ["Asked for the brochure"])
		self.assertEqual(
			sorted((row["kind"], row["text"]) for row in timeline["activities"]),
			[
				("call", "Talked for ten minutes"),
				("comment", "WhatsApp: Sent the brochure"),
				("note", "Asked for the brochure"),
			],
		)
		self.assertEqual(
			[(row["title"], row["assigned_to"]) for row in timeline["tasks"]], [("Call back", REP)]
		)
		# the follow-up is a ToDo on the lead, so it also opens the lead to its person
		self.assertEqual(
			frappe.db.get_value("ToDo", task.name, ["reference_type", "date"]), ("Lead", getdate(today()))
		)

	def test_lead_links_follow_the_setting(self):
		set_smart_lead_settings(lead_page_url=None)
		self.assertTrue(lead_url("CRM-LEAD-1").endswith("/app/lead/CRM-LEAD-1"))
		set_smart_lead_settings(lead_page_url="https://hub.example.com/helpdesk/leads")
		try:
			self.assertEqual(lead_url("CRM-LEAD-1"), "https://hub.example.com/helpdesk/leads/CRM-LEAD-1")
			self.assertEqual(
				lead_url(view="call_today"), "https://hub.example.com/helpdesk/leads?view=call_today"
			)
		finally:
			set_smart_lead_settings(lead_page_url=None)
