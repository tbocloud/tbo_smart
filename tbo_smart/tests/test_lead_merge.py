import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads as hub
from tbo_smart.sales.activities import add_call
from tbo_smart.test_utils import (
	delete_call_batches,
	delete_calls_of,
	delete_lead_deals,
	delete_lead_departments,
	delete_lead_meetings,
	delete_lead_quotations,
	delete_sales_records,
	disable_sales_hierarchy,
	get_batch_items,
	get_lead_notes,
	get_lead_numbers,
	make_call,
	make_call_batch,
	make_deal,
	make_lead,
	make_lead_department,
	make_lead_email,
	make_lead_meeting,
	make_lead_note,
	make_lead_quotation,
	make_lead_task,
	make_private_file,
	make_user,
)

HUB = "merge.integration@tbosmart.test"
BD = "merge.bd@tbosmart.test"
HEAD = "merge.head@tbosmart.test"
OTHER_HEAD = "merge.otherhead@tbosmart.test"
MANAGER = "merge.manager@tbosmart.test"
OWNERS = (BD, HEAD, OTHER_HEAD, MANAGER)
GROWTHX = "TBO Test Merge GrowthX"
CONTENT = "TBO Test Merge Content"
FIRST = "98470 12345"
SECOND = "0484 2345678"
THIRD = "+91 98950 11111"
SAME_AS_FIRST = "+919847012345"


class TestLeadMerge(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		for user in (BD, HEAD, OTHER_HEAD):
			make_user(user, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_sales_hierarchy(OWNERS)
		make_lead_department(GROWTHX, heads=[HEAD], members=[BD])
		make_lead_department(CONTENT, heads=[OTHER_HEAD], members=[BD])
		self.keep = make_lead(
			BD, "Ashraf", company_name="Al Tamayuz", mobile_no=FIRST, tbo_department=GROWTHX
		)
		self.merge = make_lead(
			BD,
			"Ashraf",
			company_name="Amana UAE",
			mobile_no=SAME_AS_FIRST,
			phone=SECOND,
			email_id="ashraf@amana.test",
			website="https://amana.test",
			tbo_department=GROWTHX,
		)
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.clean()

	def clean(self):
		leads = frappe.get_all("Lead", filters={"lead_owner": ("in", OWNERS)}, pluck="name")
		delete_call_batches(OWNERS)
		delete_calls_of(OWNERS)
		delete_lead_meetings(leads)
		delete_lead_deals(leads)
		delete_lead_quotations(leads)
		delete_sales_records(OWNERS)
		delete_lead_departments((GROWTHX, CONTENT))

	def merge_as(self, user, keep=None, merge=None):
		return hub.merge_leads(keep or self.keep, merge or self.merge, acting_user=user)

	def test_the_merged_lead_is_gone_and_the_payload_is_the_kept_lead(self):
		result = self.merge_as(MANAGER)
		self.assertEqual(result["lead"]["name"], self.keep)
		self.assertFalse(frappe.db.exists("Lead", self.merge))

	def test_numbers_are_copied_without_doubles_and_one_stays_primary(self):
		self.merge_as(MANAGER)
		self.assertEqual(get_lead_numbers(self.keep), [(FIRST, "Active", 1), (SECOND, "Active", 0)])
		self.assertEqual(frappe.db.get_value("Lead", self.keep, "mobile_no"), FIRST)

	def test_a_number_keeps_its_status_label_and_person(self):
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Lead", self.merge)
		doc.append(
			"tbo_numbers",
			{
				"number": THIRD,
				"digits": "919895011111",
				"label": "Decision-maker",
				"person": "Mr Ali",
				"status": "Wrong number",
			},
		)
		doc.save(ignore_permissions=True)
		frappe.set_user(HUB)
		self.merge_as(MANAGER)
		row = next(
			r for r in hub.get_lead(self.keep, acting_user=MANAGER)["lead"]["numbers"] if r["number"] == THIRD
		)
		self.assertEqual(
			(row["status"], row["label"], row["person"], row["is_primary"]),
			("Wrong number", "Decision-maker", "Mr Ali", 0),
		)

	def test_empty_fields_are_filled_and_values_are_never_overwritten(self):
		self.merge_as(MANAGER)
		lead = frappe.db.get_value(
			"Lead", self.keep, ["company_name", "email_id", "website", "phone"], as_dict=True
		)
		self.assertEqual(lead.company_name, "Al Tamayuz")
		self.assertEqual(
			(lead.email_id, lead.website, lead.phone), ("ashraf@amana.test", "https://amana.test", SECOND)
		)

	def test_everything_linked_moves_to_the_kept_lead(self):
		frappe.set_user("Administrator")
		make_lead_note(self.merge, "Asked for a quote")
		add_call("Lead", self.merge, "Spoke to Ashraf")
		call = make_call(BD, self.merge)
		task = make_lead_task(self.merge, frappe.utils.add_days(frappe.utils.nowdate(), 2), BD)
		meeting = make_lead_meeting(self.merge)
		deal = make_deal(BD, opportunity_from="Lead", party_name=self.merge)
		quotation = make_lead_quotation(self.merge)
		email = make_lead_email(self.merge, "Hello", "Hi")
		file = make_private_file(
			b"test", "merge-test.txt", attached_to_doctype="Lead", attached_to_name=self.merge
		)
		batch = make_call_batch(BD, GROWTHX, [self.merge])
		frappe.set_user(HUB)
		self.merge_as(MANAGER)
		self.assertEqual(frappe.db.get_value("TBO Call", call, "lead"), self.keep)
		self.assertEqual(frappe.db.get_value("ToDo", task, "reference_name"), self.keep)
		self.assertEqual(frappe.db.get_value("TBO Lead Meeting", meeting, "lead"), self.keep)
		self.assertEqual(frappe.db.get_value("TBO Lead Meeting", meeting, "lead_name"), "Ashraf")
		self.assertEqual(
			frappe.db.get_value("Opportunity", deal, ["party_name", "tbo_lead"]), (self.keep, self.keep)
		)
		self.assertEqual(
			frappe.db.get_value("Quotation", quotation, ["party_name", "tbo_lead"]), (self.keep, self.keep)
		)
		self.assertEqual(frappe.db.get_value("Communication", email, "reference_name"), self.keep)
		self.assertEqual(frappe.db.get_value("File", file, "attached_to_name"), self.keep)
		self.assertEqual([item.lead for item in get_batch_items(batch)], [self.keep])
		participants = frappe.get_all(
			"Event Participants",
			filters={"reference_doctype": "Lead", "reference_docname": self.keep},
			pluck="name",
		)
		self.assertEqual(len(participants), 1)
		self.assertIn("Asked for a quote", get_lead_notes(self.keep)[0])

	def test_the_kept_lead_records_who_was_merged_in(self):
		frappe.set_user("Administrator")
		make_lead_note(self.merge, "Asked for a quote")
		frappe.set_user(HUB)
		self.merge_as(MANAGER)
		notes = get_lead_notes(self.keep)
		merged = [text for text in notes if "Merged" in text]
		self.assertEqual(len(merged), 1)
		self.assertIn(self.merge, merged[0])
		self.assertIn("Ashraf, Amana UAE", merged[0])
		self.assertIn("1 note", merged[0])

	def test_a_lead_cannot_be_merged_into_itself(self):
		with self.assertRaises(frappe.ValidationError):
			self.merge_as(MANAGER, self.keep, self.keep)

	def test_a_salesperson_cannot_merge(self):
		with self.assertRaises(frappe.PermissionError):
			self.merge_as(BD)
		self.assertTrue(frappe.db.exists("Lead", self.merge))

	def test_the_head_of_the_department_can_merge_its_leads(self):
		self.merge_as(HEAD)
		self.assertFalse(frappe.db.exists("Lead", self.merge))

	def test_another_departments_head_cannot(self):
		with self.assertRaises(frappe.PermissionError):
			self.merge_as(OTHER_HEAD)

	def test_leads_of_two_departments_need_a_manager_and_keep_the_kept_ones(self):
		frappe.set_user("Administrator")
		elsewhere = make_lead(BD, "Ashraf", company_name="Amana", mobile_no=THIRD, tbo_department=CONTENT)
		frappe.set_user(HUB)
		with self.assertRaises(frappe.PermissionError):
			self.merge_as(HEAD, self.keep, elsewhere)
		self.merge_as(MANAGER, self.keep, elsewhere)
		self.assertEqual(frappe.db.get_value("Lead", self.keep, "tbo_department"), GROWTHX)

	def test_the_preview_counts_what_will_move(self):
		frappe.set_user("Administrator")
		make_lead_note(self.merge, "One")
		make_lead_note(self.merge, "Two")
		frappe.set_user(HUB)
		first, second = hub.get_merge_preview(self.keep, self.merge, acting_user=MANAGER)["leads"]
		self.assertEqual(second["counts"]["notes"], 2)
		self.assertEqual(second["new_numbers"], 1)
		self.assertEqual(first["new_numbers"], 0)

	def test_get_lead_says_who_may_merge(self):
		self.assertEqual(hub.get_lead(self.keep, acting_user=HEAD)["lead"]["can_merge"], True)
		self.assertEqual(hub.get_lead(self.keep, acting_user=BD)["lead"]["can_merge"], False)
