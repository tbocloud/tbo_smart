import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, now_datetime, nowdate

from tbo_smart.api.mobile import crm
from tbo_smart.test_utils import make_deal, make_lead, make_user

SELLER = "crm.seller@tbosmart.test"
OTHER_SELLER = "crm.other@tbosmart.test"
NON_SALES = "crm.staff@tbosmart.test"


class TestMobileCRM(FrappeTestCase):
	def setUp(self):
		make_user(SELLER, ["Sales User"])
		make_user(OTHER_SELLER, ["Sales User"])
		make_user(NON_SALES, ["Employee"])
		frappe.db.set_value("User", SELLER, "mobile_no", "+91 98470 00001")
		self.lead = make_lead(SELLER, "Faisal", company_name="Al Noor Trading", mobile_no="+971 50 418 2276")
		self.other_lead = make_lead(OTHER_SELLER, "Deepa")
		frappe.set_user(SELLER)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_leads_list_only_what_crm_lets_me_see(self):
		names = {row["name"] for row in crm.get_leads(page_length=100)["data"]}
		self.assertIn(self.lead, names)
		self.assertNotIn(self.other_lead, names)
		with self.assertRaises(frappe.PermissionError):
			crm.get_lead(self.other_lead)

	def test_mine_lists_the_leads_i_created_too_marked_so(self):
		frappe.set_user("Administrator")
		created = make_lead(OTHER_SELLER, "Rahim")
		frappe.db.set_value("Lead", created, "owner", SELLER, update_modified=False)
		frappe.set_user(SELLER)
		rows = {row["name"]: row for row in crm.get_leads(scope="mine", page_length=100)["data"]}
		self.assertTrue({self.lead, created} <= set(rows))
		self.assertNotIn(self.other_lead, rows)
		self.assertTrue(rows[created]["created_by_me"])
		self.assertFalse(rows[self.lead]["created_by_me"])
		only = {row["name"] for row in crm.get_leads(scope="mine", created_by_me=1, page_length=100)["data"]}
		self.assertIn(created, only)
		self.assertNotIn(self.lead, only)

	def test_called_lists_my_leads_last_called_first_with_their_counts(self):
		frappe.set_user("Administrator")
		older = make_lead(SELLER, "Called Before", mobile_no="+91 98470 11112")
		frappe.db.set_value("Lead", older, "tbo_last_call_on", add_days(now_datetime(), -3))
		frappe.db.set_value("Lead", self.lead, "tbo_last_call_on", now_datetime())
		frappe.set_user(SELLER)
		rows = crm.get_leads(scope="mine", called=1, page_length=100)["data"]
		names = [row["name"] for row in rows]
		self.assertLess(names.index(self.lead), names.index(older))
		self.assertNotIn(self.other_lead, names)
		self.assertIn("call_count", rows[0])
		today = crm.get_leads(
			scope="mine", called=1, called_from=nowdate(), called_to=nowdate(), page_length=100
		)
		today_names = {row["name"] for row in today["data"]}
		self.assertIn(self.lead, today_names)
		self.assertNotIn(older, today_names)

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
		self.assertEqual(str(getdate(crm.get_lead(self.lead)["next_follow_up"])), follow_up)

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
		deal = make_deal(SELLER, status="Negotiation", deal_value=940000)
		frappe.set_user(SELLER)
		deals = crm.get_deals(stage="Negotiation")["data"]
		self.assertIn(deal, [row["name"] for row in deals])
		row = next(row for row in deals if row["name"] == deal)
		self.assertEqual((row["stage"], row["amount"]), ("Negotiation", 940000))

		options = crm.get_options()
		self.assertIn("Lost", [stage["name"] for stage in options["deal_stages"]])
		self.assertIn("data", crm.get_organizations())
