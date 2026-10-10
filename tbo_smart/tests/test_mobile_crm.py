import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime, nowdate

from tbo_smart.api.mobile import crm
from tbo_smart.test_utils import make_crm_deal, make_crm_lead, make_user

SELLER = "crm.seller@tbosmart.test"
OTHER_SELLER = "crm.other@tbosmart.test"
NON_SALES = "crm.staff@tbosmart.test"


class TestMobileCRM(FrappeTestCase):
	def setUp(self):
		make_user(SELLER, ["Sales User"])
		make_user(OTHER_SELLER, ["Sales User"])
		make_user(NON_SALES, ["Employee"])
		frappe.db.set_value("User", SELLER, "mobile_no", "+91 98470 00001")
		self.lead = make_crm_lead(
			SELLER, "Faisal", organization="Al Noor Trading", mobile_no="+971 50 418 2276"
		)
		self.other_lead = make_crm_lead(OTHER_SELLER, "Deepa")
		frappe.set_user(SELLER)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_leads_list_only_what_crm_lets_me_see(self):
		names = {row["name"] for row in crm.get_leads(page_length=100)["data"]}
		self.assertIn(self.lead, names)
		self.assertNotIn(self.other_lead, names)
		with self.assertRaises(frappe.PermissionError):
			crm.get_lead(self.other_lead)

	def test_non_sales_staff_cannot_read_leads(self):
		frappe.set_user(NON_SALES)
		with self.assertRaises(frappe.PermissionError):
			crm.get_leads()

	def test_create_lead_is_owned_by_me_with_note(self):
		lead = crm.create_lead("Salim Al Harthy", organization="Sahelmed", note="Warehouse module")
		self.assertEqual(lead["owner_id"], SELLER)
		self.assertEqual(lead["lead_name"], "Salim Al Harthy")
		self.assertEqual([note["text"] for note in lead["notes"]], ["Warehouse module"])

	def test_log_call_with_follow_up(self):
		follow_up = add_days(nowdate(), 1)
		timeline = crm.log_activity("lead", self.lead, "call", "Intro call, wants a demo", follow_up)
		self.assertEqual(timeline["activities"][0]["kind"], "call")
		self.assertEqual(timeline["activities"][0]["text"], "Intro call, wants a demo")
		self.assertEqual(len(timeline["tasks"]), 1)
		self.assertEqual(crm.get_lead(self.lead)["next_follow_up"].date().isoformat(), follow_up)

	def test_meeting_closes_due_follow_ups_and_shows_in_my_list(self):
		crm.add_follow_up("lead", self.lead, str(now_datetime()))
		due = crm.get_my_follow_ups()
		self.assertEqual([row["ref"] for row in due], [{"doctype": "lead", "name": self.lead}])
		crm.log_activity("lead", self.lead, "meeting", "Met at the expo")
		self.assertEqual(crm.get_my_follow_ups(), [])

	def test_cannot_log_on_someone_elses_lead(self):
		with self.assertRaises(frappe.PermissionError):
			crm.log_activity("lead", self.other_lead, "note", "Not mine")

	def test_convert_lead_and_move_deal_to_lost_with_free_text(self):
		deal = crm.convert_lead(self.lead)
		self.assertEqual(crm.convert_lead(self.lead)["name"], deal["name"])

		lost = crm.move_deal_stage(deal["name"], "Lost", "Went with a local partner on price")
		self.assertEqual(lost["stage_type"], "Lost")
		self.assertEqual(lost["lost_reason"], "Went with a local partner on price")
		with self.assertRaises(frappe.ValidationError):
			crm.move_deal_stage(deal["name"], "Lost")

	def test_deals_by_stage_and_organization_shapes(self):
		frappe.set_user("Administrator")
		deal = make_crm_deal(SELLER, status="Negotiation", deal_value=940000)
		frappe.set_user(SELLER)
		deals = crm.get_deals(stage="Negotiation")["data"]
		self.assertIn(deal, [row["name"] for row in deals])
		row = next(row for row in deals if row["name"] == deal)
		self.assertEqual((row["stage"], row["amount"]), ("Negotiation", 940000))

		options = crm.get_options()
		self.assertIn("Lost", [stage["name"] for stage in options["deal_stages"]])
		self.assertIn("data", crm.get_organizations())
