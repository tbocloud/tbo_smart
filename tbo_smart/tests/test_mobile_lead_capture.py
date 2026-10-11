import base64
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, nowdate

from tbo_smart.api.mobile import crm
from tbo_smart.test_utils import (
	delete_crm_records,
	disable_sales_hierarchy,
	enable_sales_hierarchy,
	find_match,
	get_note_texts,
	make_card_image_base64,
	make_contact,
	make_crm_lead,
	make_crm_lead_source,
	make_crm_organization,
	make_customer,
	make_sales_hierarchy_node,
	make_test_mobile,
	make_user,
)

SELLER = "capture.seller@tbosmart.test"
OTHER_SELLER = "capture.other@tbosmart.test"
MANAGER = "capture.manager@tbosmart.test"
STAFF = "capture.staff@tbosmart.test"
TEAM_LEAD = "capture.teamlead@tbosmart.test"
TEAM_REP = "capture.teamrep@tbosmart.test"
CRM_PEOPLE = (SELLER, OTHER_SELLER, MANAGER, TEAM_LEAD, TEAM_REP)


class TestMobileLeadCapture(FrappeTestCase):
	def setUp(self):
		make_user(SELLER, ["Sales User"])
		make_user(OTHER_SELLER, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])
		make_user(STAFF, ["Employee"])
		frappe.response.pop("matches", None)
		frappe.set_user(SELLER)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.response.pop("matches", None)
		disable_sales_hierarchy((TEAM_LEAD, TEAM_REP))
		delete_crm_records(CRM_PEOPLE)

	# ---------- check_duplicates ----------

	def test_phone_matches_in_any_format(self):
		mobile = make_test_mobile()
		lead = make_crm_lead(SELLER, "Phone Match", mobile_no=f"+971 {mobile[:2]} {mobile[2:5]} {mobile[5:]}")
		for phone in (
			f"0{mobile}",
			f"+971{mobile}",
			f"00971 {mobile[:2]} {mobile[2:5]} {mobile[5:]}",
			f"({mobile[:2]}) {mobile[2:5]}-{mobile[5:]}",
		):
			matches = crm.check_duplicates(phone=phone)["data"]["matches"]
			row = find_match(matches, "lead", lead)
			self.assertEqual(row["match_on"], ["phone"], phone)
			self.assertTrue(row["can_open"])
			self.assertEqual(row["title"], "Phone Match")

		other = make_test_mobile()
		matches = crm.check_duplicates(phone=f"0{other}")["data"]["matches"]
		self.assertNotIn(lead, [row["name"] for row in matches])
		self.assertEqual(crm.check_duplicates(phone="12 34")["data"]["matches"], [])

	def test_email_and_organization_match_case_insensitively_across_doctypes(self):
		tag = frappe.generate_hash(length=8)
		organization = f"Gulf Star {tag}"
		frappe.set_user("Administrator")
		lead = make_crm_lead(SELLER, "Ravi Kumar", email=f"Ravi.{tag}@Example.com", organization=organization)
		make_crm_organization(organization)
		make_customer(organization)
		make_contact("Ravi", email=f"ravi.{tag}@example.com")
		frappe.set_user(SELLER)

		by_email = crm.check_duplicates(email=f"  RAVI.{tag}@example.COM ")["data"]["matches"]
		self.assertEqual(find_match(by_email, "lead", lead)["match_on"], ["email"])
		contact_row = find_match(by_email, "contact")
		self.assertEqual(contact_row["match_on"], ["email"])
		self.assertFalse(contact_row["can_open"])

		by_organization = crm.check_duplicates(organization=f"  gulf star {tag.upper()} ")["data"]["matches"]
		self.assertEqual(find_match(by_organization, "lead", lead)["match_on"], ["organization"])
		self.assertTrue(find_match(by_organization, "organization")["can_open"])
		self.assertEqual(find_match(by_organization, "customer")["title"], organization)

		both = crm.check_duplicates(email=f"ravi.{tag}@example.com", organization=organization)
		self.assertEqual(
			find_match(both["data"]["matches"], "lead", lead)["match_on"], ["email", "organization"]
		)

	def test_hidden_matches_say_a_match_exists_without_details(self):
		mobile = make_test_mobile()
		hidden = make_crm_lead(OTHER_SELLER, "Secret Prospect", mobile_no=mobile, organization="Hidden Org")
		matches = crm.check_duplicates(phone=f"0{mobile}")["data"]["matches"]
		self.assertEqual(
			matches,
			[
				{
					"kind": "lead",
					"name": None,
					"title": "Someone on the team already has this lead",
					"owner_name": frappe.db.get_value("User", OTHER_SELLER, "full_name"),
					"status": None,
					"match_on": ["phone"],
					"can_open": False,
				}
			],
		)
		self.assertNotIn("Secret Prospect", json.dumps(matches))

		frappe.set_user(MANAGER)
		row = find_match(crm.check_duplicates(phone=mobile)["data"]["matches"], "lead", hidden)
		self.assertEqual(row["title"], "Secret Prospect")

	def test_check_duplicates_needs_crm(self):
		frappe.set_user(STAFF)
		with self.assertRaises(frappe.PermissionError):
			crm.check_duplicates(phone=make_test_mobile())

	# ---------- create_lead ----------

	def test_duplicate_is_refused_with_409_until_forced(self):
		mobile = make_test_mobile()
		make_crm_lead(OTHER_SELLER, "First Capture", mobile_no=mobile)
		with self.assertRaises(crm.DuplicateLead) as refused:
			crm.create_lead("Second Capture", mobile_no=f"0{mobile}")
		self.assertEqual(refused.exception.http_status_code, 409)
		self.assertEqual([row["kind"] for row in frappe.response["matches"]], ["lead"])
		self.assertIsNone(frappe.response["matches"][0]["name"])
		self.assertFalse(frappe.db.exists("CRM Lead", {"lead_name": "Second Capture"}))

		lead = crm.create_lead("Second Capture", mobile_no=f"0{mobile}", force=True)
		self.assertEqual(lead["owner_id"], SELLER)
		note = next(text for text in get_note_texts(lead) if text.startswith("Created despite"))
		self.assertIn("a lead owned by", note)
		self.assertNotIn("First Capture", note)

	def test_follow_up_task_and_note_at_creation(self):
		due = f"{add_days(nowdate(), 3)} 10:30:00"
		lead = crm.create_lead("Follow Capture", follow_up_on=due, follow_up_note="Send the price list")
		self.assertEqual(len(lead["tasks"]), 1)
		task = lead["tasks"][0]
		self.assertEqual(task["title"], "Follow up with Follow Capture")
		self.assertEqual(get_datetime(task["due"]), get_datetime(due))
		self.assertEqual(task["assigned_to"], SELLER)
		self.assertEqual(frappe.db.get_value("CRM Task", task["id"], "description"), "Send the price list")
		self.assertIn("Send the price list", get_note_texts(lead))
		self.assertEqual(get_datetime(lead["next_follow_up"]), get_datetime(due))

	def test_staff_cannot_assign_and_have_nobody_to_assign_to(self):
		self.assertEqual(crm.get_assignable_users(), {"data": []})
		with self.assertRaises(frappe.PermissionError):
			crm.create_lead("Staff Assigned", assign_to=OTHER_SELLER)
		self.assertFalse(frappe.db.exists("CRM Lead", {"lead_name": "Staff Assigned"}))
		lead = crm.create_lead("Self Assigned", assign_to=SELLER)
		self.assertEqual(lead["owner_id"], SELLER)

	def test_manager_assigns_with_crm_assignment_and_task(self):
		frappe.set_user(MANAGER)
		assignable = {row["user"] for row in crm.get_assignable_users()["data"]}
		self.assertTrue({SELLER, OTHER_SELLER, MANAGER} <= assignable)
		self.assertNotIn(STAFF, assignable)

		due = f"{add_days(nowdate(), 1)} 09:00:00"
		lead = crm.create_lead("Manager Capture", assign_to=SELLER, follow_up_on=due)
		self.assertTrue(
			frappe.db.exists(
				"ToDo",
				{
					"reference_type": "CRM Lead",
					"reference_name": lead["name"],
					"allocated_to": SELLER,
					"status": "Open",
				},
			)
		)
		self.assertEqual(lead["tasks"][0]["assigned_to"], SELLER)
		with self.assertRaises(frappe.ValidationError):
			crm.create_lead("Not A Seller", assign_to=STAFF)

		frappe.set_user(SELLER)
		self.assertEqual(crm.get_lead(lead["name"])["lead_name"], "Manager Capture")

	def test_team_lead_assigns_only_inside_the_team(self):
		frappe.set_user("Administrator")
		make_user(TEAM_LEAD, ["Sales Manager"])
		make_user(TEAM_REP, ["Sales User"])
		enable_sales_hierarchy()
		make_sales_hierarchy_node(TEAM_REP, reports_to=make_sales_hierarchy_node(TEAM_LEAD))

		frappe.set_user(TEAM_LEAD)
		assignable = {row["user"] for row in crm.get_assignable_users()["data"]}
		self.assertEqual(assignable, {TEAM_LEAD, TEAM_REP})
		lead = crm.create_lead("Team Capture", assign_to=TEAM_REP)
		self.assertTrue(
			frappe.db.exists(
				"ToDo", {"reference_name": lead["name"], "allocated_to": TEAM_REP, "status": "Open"}
			)
		)
		with self.assertRaises(frappe.PermissionError):
			crm.create_lead("Outside Capture", assign_to=SELLER)

	def test_card_image_is_attached_privately(self):
		lead = crm.create_lead("Card Capture", card_image=make_card_image_base64())
		file = frappe.db.get_value(
			"File",
			{"attached_to_doctype": "CRM Lead", "attached_to_name": lead["name"]},
			["is_private", "file_name"],
			as_dict=True,
		)
		self.assertEqual(file.is_private, 1)
		self.assertTrue(file.file_name.endswith("-card.jpg"))

		with self.assertRaises(frappe.ValidationError):
			crm.create_lead("Bad Card", card_image=base64.b64encode(b"GIF89a not a jpeg").decode())
		self.assertFalse(frappe.db.exists("CRM Lead", {"lead_name": "Bad Card"}))

	def test_source_channel_is_stored(self):
		make_crm_lead_source("Business Card")
		lead = crm.create_lead("Scanned Capture", source_channel="card_scan")
		self.assertEqual(lead["source"], "Business Card")
		self.assertIn("Captured in TBO Smart: Business card scan (card_scan)", get_note_texts(lead))

		typed = crm.create_lead("Typed Capture", source_channel="manual")
		self.assertIsNone(typed["source"])
		self.assertIn("Captured in TBO Smart: Typed in (manual)", get_note_texts(typed))

		with self.assertRaises(frappe.ValidationError):
			crm.create_lead("Odd Capture", source_channel="carrier_pigeon")
