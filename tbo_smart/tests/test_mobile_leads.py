import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from tbo_smart.api.mobile import crm, leads
from tbo_smart.lead_pipeline.statuses import CLOSED_LOST, CLOSED_WON, CONTACTED, POSITIVE
from tbo_smart.test_utils import (
	ai_lead_insight,
	change_lead_stage,
	delete_lead_pipeline_records,
	disable_sales_hierarchy,
	disable_smart_leads,
	enable_ai_leads,
	get_all_keys,
	get_fixture,
	make_deal,
	make_lead,
	make_lead_note,
	make_lead_source,
	make_lead_task,
	make_user,
	mock_ai,
	openai_answer,
	set_lead_values,
	set_smart_lead_settings,
)

BDE = "mobile.leads.bde@tbosmart.test"
BDE2 = "mobile.leads.bde2@tbosmart.test"
STAFF = "mobile.leads.staff@tbosmart.test"
OWNERS = (BDE, BDE2)
SOURCE = "TBO Mobile Leads Expo"


class TestMobileLeads(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		make_user(STAFF, ["Employee"])
		disable_sales_hierarchy(OWNERS)
		make_lead_source(SOURCE)

	def setUp(self):
		frappe.set_user("Administrator")
		disable_smart_leads()
		delete_lead_pipeline_records(OWNERS)
		frappe.cache.delete_keys("tbo_smart:ai_leads")
		self.lead = make_lead(BDE, "Anu Textiles", company_name="Anu Textiles", mobile_no="9847012345")

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_smart_leads()

	def read_lead(self) -> None:
		"""Switch AI runs leads on and let the AI read self.lead after a note."""
		enable_ai_leads()
		with mock_ai(openai_answer(ai_lead_insight())):
			make_lead_note(self.lead, "Asked for a demo of billing")

	def test_only_crm_users_reach_the_overview_and_the_ai(self):
		frappe.set_user(STAFF)
		self.assertRaises(frappe.PermissionError, leads.get_overview)
		self.assertRaises(frappe.PermissionError, leads.get_lead_ai, self.lead)
		frappe.set_user(BDE2)
		# a salesperson who can't see the lead gets nothing about it
		self.assertRaises(frappe.PermissionError, leads.get_lead_ai, self.lead)

	def test_the_overview_is_my_scope_with_the_ai_off(self):
		make_lead(BDE2, "Someone else's")
		frappe.set_user(BDE)
		overview = leads.get_overview(period="week")
		self.assertEqual((overview["scope"], overview["period"]), ("own", "week"))
		self.assertEqual(overview["totals"]["new"], 1)
		self.assertEqual(overview["ai"], {"enabled": False})
		ai = leads.get_lead_ai(self.lead)
		self.assertFalse(ai["enabled"])
		self.assertIn(ai["health"]["status"], ("healthy", "watch", "at_risk"))

	def test_a_person_moves_the_lead_to_the_suggested_stage(self):
		self.read_lead()
		frappe.set_user(BDE)
		self.assertEqual(leads.get_overview()["ai"]["stage_suggested"], 1)
		listed = crm.get_leads(metric="stage_suggested", period="month")["data"]
		self.assertEqual([row["name"] for row in listed], [self.lead])
		self.assertEqual(leads.get_lead_ai(self.lead)["insight"]["suggested_stage"], POSITIVE)
		lead = leads.apply_ai_stage(self.lead)
		# the app's own lead shape, now in the stage, moved by the person
		self.assertEqual((lead["name"], lead["status"]), (self.lead, POSITIVE))
		self.assertEqual(
			frappe.db.get_value("TBO Stage Change", {"parent": self.lead, "stage": POSITIVE}, "changed_by"),
			BDE,
		)
		self.assertIsNone(leads.get_lead_ai(self.lead)["insight"]["suggested_stage"])
		self.assertEqual(crm.get_leads(metric="stage_suggested")["data"], [])

	def test_dismiss_and_draft(self):
		self.read_lead()
		frappe.set_user(BDE)
		self.assertIsNone(leads.dismiss_ai_stage(self.lead)["insight"]["suggested_stage"])
		with mock_ai(openai_answer({"message": "Hello, shall we fix the demo? Anu"})):
			draft = leads.draft_lead_message(self.lead, "whatsapp")
		self.assertTrue(draft["url"].startswith("https://wa.me/919847012345"))
		# "I sent it" is the app's log_activity: a WhatsApp event on the lead, by the person
		timeline = crm.log_activity("lead", self.lead, "whatsapp", draft["text"])
		self.assertEqual(timeline["activities"][0]["kind"], "comment")

	def test_lead_list_filters_and_score(self):
		hot = make_lead(BDE, "Keen", source=SOURCE)
		set_lead_values(hot, tbo_score=72, tbo_temperature="Hot")
		won = make_lead(BDE, "Winner")
		change_lead_stage(won, CONTACTED)
		change_lead_stage(won, CLOSED_WON)
		make_deal(BDE, opportunity_from="Lead", party_name=won)
		frappe.set_user(BDE)

		def names(**filters) -> set:
			return {row["name"] for row in crm.get_leads(page_length=100, **filters)["data"]}

		self.assertEqual(names(temperature="Hot"), {hot})
		self.assertEqual(names(source=SOURCE), {hot})
		self.assertRaises(frappe.ValidationError, crm.get_leads, temperature="Lukewarm")
		# a won lead has its deal: hidden from the plain list, there in the Won number's list
		self.assertNotIn(won, names())
		self.assertEqual(names(metric="won", period="month"), {won})
		row = next(row for row in crm.get_leads(page_length=100)["data"] if row["name"] == hot)
		self.assertEqual((row["score"], row["temperature"]), (72, "Hot"))

	def test_the_fixtures_match_the_responses(self):
		hot = make_lead(BDE, "Keen")
		set_lead_values(hot, tbo_score=80, tbo_temperature="Hot")
		lost = make_lead(BDE, "Gone")
		change_lead_stage(lost, CLOSED_LOST, tbo_lost_reason="Other", tbo_lost_notes="Price")
		make_lead(BDE, "Anu again", company_name="Anu Textiles")
		make_lead_task(self.lead, add_days(nowdate(), -1), BDE)
		set_smart_lead_settings(enable_lead_scoring=1)
		self.read_lead()
		frappe.set_user(BDE)
		self.assertLessEqual(
			set(get_all_keys(get_fixture("leads.get_overview")["message"])),
			set(get_all_keys(leads.get_overview())),
		)
		self.assertLessEqual(
			set(get_all_keys(get_fixture("leads.get_lead_ai")["message"])),
			set(get_all_keys(leads.get_lead_ai(self.lead))),
		)
		self.assertLessEqual(
			set(get_all_keys(get_fixture("crm.get_leads")["message"]["data"])),
			set(get_all_keys(crm.get_leads()["data"])),
		)
