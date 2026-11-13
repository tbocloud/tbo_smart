import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline.statuses import NEW_LEAD, WRONG_NUMBER
from tbo_smart.outreach import whatsapp
from tbo_smart.patches.v1 import lead_contact_numbers
from tbo_smart.sales import SETTINGS, numbers
from tbo_smart.telephony import calls
from tbo_smart.test_utils import (
	delete_sales_records,
	disable_sales_hierarchy,
	enable_lead_pipeline,
	get_lead_notes,
	get_lead_numbers,
	make_lead,
	make_lead_without_number_rows,
	make_user,
)

HUB = "numbers.integration@tbosmart.test"
BD = "numbers.bd@tbosmart.test"
BD2 = "numbers.bd2@tbosmart.test"
OWNERS = (BD, BD2)
FIRST = "98470 12345"
SECOND = "0484 2345678"
THIRD = "+91 98950 11111"


class TestLeadNumbers(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BD, ["Sales User"])
		make_user(BD2, ["Sales User"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		disable_sales_hierarchy(OWNERS)
		enable_lead_pipeline()
		self.lead = make_lead(BD, "Numbers Lead", mobile_no=FIRST)
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def add(self, number, **values):
		return hub.add_lead_number(self.lead, number, acting_user=BD, **values)

	def test_a_new_lead_gets_a_row_for_each_number_it_has(self):
		frappe.set_user("Administrator")
		lead = make_lead(BD, "Two Numbers", mobile_no=FIRST, phone=SECOND, whatsapp_no="+919847012345")
		self.assertEqual(
			get_lead_numbers(lead), [(FIRST, "Active", 1), (SECOND, "Active", 0)]
		)  # the WhatsApp number is the mobile again
		self.assertEqual(frappe.db.get_value("Lead", lead, "mobile_no"), FIRST)

	def test_the_migration_gives_old_leads_rows_and_can_run_again(self):
		frappe.set_user("Administrator")
		legacy = make_lead_without_number_rows(BD, mobile_no=FIRST, phone=SECOND, whatsapp_no="+919847012345")
		bare = make_lead_without_number_rows(BD, "No Phone")
		lead_contact_numbers.execute()
		lead_contact_numbers.execute()
		self.assertEqual(get_lead_numbers(legacy), [(FIRST, "Active", 1), (SECOND, "Active", 0)])
		self.assertEqual(get_lead_numbers(bare), [])
		self.assertEqual(frappe.db.get_value("Lead", legacy, "mobile_no"), FIRST)
		rows = numbers.rows_of(legacy)
		self.assertEqual({row.source for row in rows}, {"Import"})
		self.assertEqual([row.label for row in rows], ["Mobile", "Office"])

	def test_adding_a_number_keeps_the_old_one_and_the_main_number(self):
		result = self.add(THIRD, label="Decision-maker", person="Anil", note="Owner, after 5 pm")
		self.assertEqual(get_lead_numbers(self.lead), [(FIRST, "Active", 1), (THIRD, "Active", 0)])
		row = result["numbers"][1]
		self.assertEqual(
			(row["label"], row["person"], row["source"], row["added_by"], row["e164"]),
			("Decision-maker", "Anil", "Manual", BD, "+919895011111"),
		)
		self.assertEqual(hub.get_lead(self.lead, acting_user=BD)["lead"]["numbers"], result["numbers"])
		self.assertIn(f"Number added: {THIRD}", get_lead_notes(self.lead)[-1])
		self.assertRaises(frappe.ValidationError, self.add, "9847 012345")
		self.assertRaises(frappe.ValidationError, self.add, "call me")

	def test_a_number_added_on_a_call_says_so(self):
		self.add(THIRD, on_call=1)
		self.assertEqual(numbers.rows_of(self.lead)[1].source, "Added on a call")

	def test_changing_the_main_number_keeps_the_old_one_and_logs_it(self):
		self.add(THIRD, make_primary=1)
		self.assertEqual(get_lead_numbers(self.lead), [(THIRD, "Active", 1), (FIRST, "Old", 0)])
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "mobile_no"), THIRD)
		self.assertIn(f"Primary changed from {FIRST} to {THIRD} by", " ".join(get_lead_notes(self.lead)))

	def test_typing_a_new_mobile_in_the_lead_does_the_same(self):
		self.add(THIRD)
		hub.update_lead(self.lead, {"mobile_no": SECOND}, acting_user=BD)
		self.assertEqual(
			get_lead_numbers(self.lead), [(SECOND, "Active", 1), (THIRD, "Active", 0), (FIRST, "Old", 0)]
		)

	def test_the_duplicate_check_covers_every_number_of_every_lead(self):
		self.add(THIRD)
		hub.update_lead_number(self.lead, numbers.rows_of(self.lead)[1].name, status="Old", acting_user=BD)
		for number in (FIRST, THIRD, "098950 11111"):
			found = hub.find_duplicate_leads(phone=number, acting_user=BD2)
			self.assertEqual([match["name"] for match in found], [self.lead], number)
			self.assertEqual(found[0]["matched_on"], ["phone"])
		self.assertEqual(hub.find_duplicate_leads(phone=THIRD, exclude=self.lead, acting_user=BD2), [])
		with self.assertRaises(frappe.DuplicateEntryError):
			hub.create_lead({"lead_name": "Same Person", "mobile_no": THIRD}, acting_user=BD2)
		other = make_lead(BD2, "Other Lead", mobile_no="98765 43210")
		with self.assertRaises(frappe.DuplicateEntryError):
			hub.add_lead_number(other, "9895011111", acting_user=BD2)

	def test_a_new_lead_with_a_number_another_lead_has_is_refused_however_it_comes_in(self):
		frappe.set_user("Administrator")
		desk = frappe.get_doc(
			{"doctype": "Lead", "first_name": "Desk Lead", "lead_owner": BD2, "phone": "+91 98470-12345"}
		)
		with self.assertRaises(frappe.DuplicateEntryError) as refused:
			desk.insert(ignore_permissions=True)
		self.assertIn(f"Possible duplicate of {self.lead} (", str(refused.exception))
		self.assertNotIn(BD, str(refused.exception))  # the owner by name, never their user id
		self.assertFalse(frappe.db.exists("Lead", {"first_name": "Desk Lead"}))

	def test_a_number_another_lead_has_cannot_be_added_or_typed_in(self):
		frappe.set_user("Administrator")
		other = make_lead(BD2, "Other Lead", mobile_no="98765 43210")
		frappe.set_user(HUB)
		with self.assertRaises(frappe.DuplicateEntryError):
			hub.add_lead_number(other, "098470 12345", acting_user=BD2)
		for field in ("mobile_no", "phone", "whatsapp_no"):
			with self.assertRaises(frappe.DuplicateEntryError, msg=field):
				hub.update_lead(other, {field: "+91 98470 12345"}, acting_user=BD2)
		self.assertEqual(get_lead_numbers(other), [("98765 43210", "Active", 1)])

	def test_a_duplicate_made_before_the_rule_can_still_be_changed(self):
		frappe.set_user("Administrator")
		twin = make_lead(BD2, "Twin Lead", mobile_no="+91 98470 12345")
		frappe.set_user(HUB)
		hub.update_lead(twin, {"company_name": "Twin Traders"}, acting_user=BD2)
		self.assertEqual(frappe.db.get_value("Lead", twin, "company_name"), "Twin Traders")

	def test_a_number_many_businesses_share_may_be_on_many_leads(self):
		frappe.set_user("Administrator")
		before = frappe.db.get_single_value(SETTINGS, "shared_numbers")
		self.addCleanup(frappe.db.set_single_value, SETTINGS, "shared_numbers", before)
		frappe.db.set_single_value(SETTINGS, "shared_numbers", "+91 80 4780 8605\n080 1111 2222")
		for name in ("Portal One", "Portal Two"):
			frappe.get_doc(
				{"doctype": "Lead", "first_name": name, "lead_owner": BD2, "phone": "080 4780 8605"}
			).insert(ignore_permissions=True)
		frappe.set_user(HUB)
		self.assertEqual(hub.find_duplicate_leads(phone="08047808605", acting_user=BD2), [])

	def test_a_call_or_chat_finds_the_lead_by_any_of_its_numbers(self):
		self.add(THIRD)
		hub.update_lead_number(self.lead, numbers.rows_of(self.lead)[1].name, status="Old", acting_user=BD)
		for number in (FIRST, "+91 98950 11111", "0 98950 11111"):
			self.assertEqual(calls.find_lead(number), self.lead, number)
			self.assertEqual(whatsapp.find_lead(number), self.lead, number)
		self.assertIsNone(calls.find_lead("98000 00000"))
		self.assertEqual(numbers.leads_with("9895011111"), [self.lead])

	def test_the_leads_search_finds_a_lead_by_any_of_its_numbers(self):
		self.add(THIRD)
		hub.update_lead_number(self.lead, numbers.rows_of(self.lead)[1].name, status="Old", acting_user=BD)
		for search in ("98950 11111", "+91 9895011111", "98950", "098950 11111"):
			found = [row["name"] for row in hub.list_leads(search=search, acting_user=BD)["rows"]]
			self.assertEqual(found, [self.lead], search)
			self.assertEqual(hub.list_leads(search=search, acting_user=BD)["total"], 1, search)
		self.assertEqual(numbers.leads_with_digits("9895"), [])  # too few digits
		self.assertEqual(numbers.leads_with_digits("Room 98950"), [])  # not a number

	def test_click_to_call_accepts_any_number_of_the_lead(self):
		self.add(THIRD)
		self.assertEqual(calls.lead_number(self.lead, None), "+919847012345")
		self.assertEqual(calls.lead_number(self.lead, "9895011111"), "+919895011111")
		self.assertRaises(frappe.ValidationError, calls.lead_number, self.lead, "9000000000")

	def test_a_salesperson_marks_a_number_but_only_a_manager_removes_it(self):
		self.add(THIRD)
		other = numbers.rows_of(self.lead)[1].name
		hub.update_lead_number(self.lead, other, status="Wrong number", acting_user=BD)
		self.assertEqual(get_lead_numbers(self.lead)[1], (THIRD, "Wrong number", 0))
		self.assertRaises(frappe.PermissionError, hub.remove_lead_number, self.lead, other, acting_user=BD)
		self.assertEqual(len(get_lead_numbers(self.lead)), 2)
		self.assertRaises(
			frappe.PermissionError,
			hub.update_lead_number,
			self.lead,
			other,
			status="Active",
			acting_user=BD2,
		)
		frappe.set_user("Administrator")
		answer = numbers.remove_lead_number(self.lead, other)
		self.assertEqual([row["number"] for row in answer["numbers"]], [FIRST])
		self.assertIn(f"Number removed: {THIRD}", get_lead_notes(self.lead)[-1])

	def test_a_wrong_number_hands_over_to_the_next_and_the_lead_stays_open(self):
		self.add(THIRD)
		hub.log_call(self.lead, "Wrong number", number=FIRST, acting_user=BD)
		self.assertEqual(get_lead_numbers(self.lead), [(THIRD, "Active", 1), (FIRST, "Wrong number", 0)])
		self.assertEqual(
			frappe.db.get_value("Lead", self.lead, ["tbo_stage", "mobile_no"]), (NEW_LEAD, THIRD)
		)
		self.assertEqual(frappe.db.get_value("TBO Call", {"lead": self.lead}, "customer_number"), FIRST)

	def test_the_lead_is_a_wrong_number_when_none_is_left(self):
		self.add(THIRD)
		hub.log_call(self.lead, "Wrong number", number=FIRST, acting_user=BD)
		made = hub.log_call(self.lead, "Wrong number", acting_user=BD)
		self.assertEqual(made["stage"], WRONG_NUMBER)
		self.assertEqual(
			get_lead_numbers(self.lead), [(THIRD, "Wrong number", 1), (FIRST, "Wrong number", 0)]
		)
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), WRONG_NUMBER)

	def test_a_call_on_another_number_records_it(self):
		self.add(THIRD)
		hub.log_call(self.lead, "Connected", number="9895011111", acting_user=BD)
		self.assertEqual(frappe.db.get_value("TBO Call", {"lead": self.lead}, "customer_number"), THIRD)
		self.assertRaises(
			frappe.ValidationError, hub.log_call, self.lead, "Connected", number="9000000000", acting_user=BD
		)
