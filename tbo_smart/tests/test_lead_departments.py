import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads as hub
from tbo_smart.test_utils import (
	delete_lead_departments,
	delete_sales_records,
	disable_sales_hierarchy,
	make_lead,
	make_lead_department,
	make_user,
)

HUB = "dept.integration@tbosmart.test"
HEAD = "dept.head@tbosmart.test"
MEMBER = "dept.member@tbosmart.test"
MEMBER2 = "dept.member2@tbosmart.test"
OUTSIDER = "dept.outsider@tbosmart.test"
MANAGER = "dept.manager@tbosmart.test"
OWNERS = (HEAD, MEMBER, MEMBER2, OUTSIDER, MANAGER)
GROWTHX = "TBO Test GrowthX"
CONTENT = "TBO Test Content"


class TestLeadDepartments(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		for user in (HEAD, MEMBER, MEMBER2, OUTSIDER):
			make_user(user, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		delete_lead_departments((GROWTHX, CONTENT))
		disable_sales_hierarchy(OWNERS)
		make_lead_department(GROWTHX, heads=[HEAD], members=[MEMBER, MEMBER2])

	def tearDown(self):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		delete_lead_departments((GROWTHX, CONTENT))

	def visible(self, user: str) -> set[str]:
		frappe.set_user(user)
		try:
			return set(frappe.get_list("Lead", pluck="name", limit_page_length=0))
		finally:
			frappe.set_user("Administrator")

	def test_head_sees_the_department_but_a_member_only_their_own(self):
		theirs = make_lead(MEMBER2, "Dept Theirs", tbo_department=GROWTHX)
		mine = make_lead(MEMBER, "Dept Mine", tbo_department=GROWTHX)
		self.assertTrue({mine, theirs} <= self.visible(HEAD))
		self.assertTrue(frappe.has_permission("Lead", "read", theirs, user=HEAD))
		seen = self.visible(MEMBER)
		self.assertIn(mine, seen)
		self.assertNotIn(theirs, seen)
		self.assertFalse(frappe.has_permission("Lead", "read", theirs, user=MEMBER))

	def test_leads_without_a_department_keep_the_old_rule(self):
		plain = make_lead(OUTSIDER, "No Dept")
		self.assertNotIn(plain, self.visible(HEAD))
		self.assertIn(plain, self.visible(OUTSIDER))
		self.assertFalse(frappe.has_permission("Lead", "read", plain, user=HEAD))
		other = make_lead(MEMBER, "Dept Other", tbo_department=GROWTHX)
		self.assertNotIn(other, self.visible(OUTSIDER))

	def test_a_head_sees_only_their_own_departments(self):
		make_lead_department(CONTENT, "Content / Digital Marketing", heads=[OUTSIDER], members=[MEMBER2])
		elsewhere = make_lead(MEMBER2, "Content Lead", tbo_department=CONTENT)
		self.assertNotIn(elsewhere, self.visible(HEAD))
		self.assertIn(elsewhere, self.visible(OUTSIDER))

	def test_a_department_switched_off_opens_nothing(self):
		lead = make_lead(MEMBER, "Soon Off", tbo_department=GROWTHX)
		self.assertIn(lead, self.visible(HEAD))
		frappe.db.set_value("TBO Lead Department", GROWTHX, "is_active", 0)
		self.assertNotIn(lead, self.visible(HEAD))

	def test_the_lead_type_follows_the_department_unless_set(self):
		lead = frappe.get_doc("Lead", make_lead(MEMBER, "Typed", tbo_department=GROWTHX))
		self.assertEqual(lead.tbo_lead_type, "ERP / Software")
		other = frappe.get_doc(
			"Lead",
			make_lead(
				MEMBER, "Typed Over", tbo_department=GROWTHX, tbo_lead_type="Content / Digital Marketing"
			),
		)
		self.assertEqual(other.tbo_lead_type, "Content / Digital Marketing")

	def test_a_lead_cannot_join_an_inactive_department(self):
		frappe.db.set_value("TBO Lead Department", GROWTHX, "is_active", 0)
		self.assertRaises(frappe.ValidationError, make_lead, MEMBER, "Off Dept", tbo_department=GROWTHX)

	def test_the_owner_is_one_of_the_departments_people(self):
		self.assertRaises(frappe.ValidationError, make_lead, OUTSIDER, "Wrong Owner", tbo_department=GROWTHX)
		make_lead(HEAD, "Head Owner", tbo_department=GROWTHX)
		lead = frappe.get_doc("Lead", make_lead(MEMBER, "Move Owner", tbo_department=GROWTHX))
		lead.lead_owner = OUTSIDER
		self.assertRaises(frappe.ValidationError, lead.save)
		# a department with nobody in it asks nothing of the owner
		make_lead_department(CONTENT)
		make_lead(OUTSIDER, "Open Dept", tbo_department=CONTENT)

	def test_a_lead_stays_editable_after_its_department_is_switched_off(self):
		lead = frappe.get_doc("Lead", make_lead(MEMBER, "Stays", tbo_department=GROWTHX))
		frappe.db.set_value("TBO Lead Department", GROWTHX, "is_active", 0)
		lead.reload()
		lead.job_title = "Buyer"
		lead.save()

	def test_the_options_list_active_departments_for_the_acting_user(self):
		make_lead_department(CONTENT, "Content / Digital Marketing", is_active=0)
		frappe.set_user(HUB)
		rows = {row["name"]: row for row in hub.get_options(acting_user=HEAD)["departments"]}
		self.assertNotIn(CONTENT, rows)
		self.assertEqual(
			rows[GROWTHX],
			{
				"name": GROWTHX,
				"lead_type": "ERP / Software",
				"is_active": 1,
				"is_head": True,
				"is_member": False,
			},
		)
		rows = {row["name"]: row for row in hub.get_options(acting_user=MEMBER)["departments"]}
		self.assertEqual((rows[GROWTHX]["is_head"], rows[GROWTHX]["is_member"]), (False, True))
		self.assertEqual(
			hub.get_options(acting_user=MEMBER)["lead_types"],
			["ERP / Software", "Content / Digital Marketing"],
		)

	def test_the_list_filters_by_department_and_returns_it(self):
		inside = make_lead(MEMBER, "List Inside", tbo_department=GROWTHX)
		outside = make_lead(MEMBER, "List Outside")
		frappe.set_user(HUB)
		rows = hub.list_leads(department=GROWTHX, acting_user=HEAD)["rows"]
		self.assertEqual([row["name"] for row in rows], [inside])
		self.assertEqual((rows[0]["tbo_department"], rows[0]["tbo_lead_type"]), (GROWTHX, "ERP / Software"))
		self.assertNotIn(outside, [row["name"] for row in hub.list_leads(acting_user=HEAD)["rows"]])

	def test_the_hub_creates_and_moves_a_lead_in_a_department(self):
		frappe.set_user(HUB)
		lead = hub.create_lead(
			{"first_name": "Hub Dept", "department": GROWTHX, "lead_type": "Content / Digital Marketing"},
			acting_user=MEMBER,
		)["lead"]
		self.assertEqual(
			(lead["tbo_department"], lead["tbo_lead_type"]), (GROWTHX, "Content / Digital Marketing")
		)
		self.assertRaises(
			frappe.ValidationError,
			hub.update_lead,
			lead["name"],
			{"lead_type": "Nonsense"},
			acting_user=MEMBER,
		)

	def test_only_managers_save_departments(self):
		frappe.set_user(HUB)
		values = {"department_name": CONTENT, "lead_type": "Content / Digital Marketing", "heads": [HEAD]}
		self.assertRaises(frappe.PermissionError, hub.save_lead_department, values, acting_user=MEMBER)
		self.assertRaises(
			frappe.PermissionError, hub.set_lead_department_active, GROWTHX, 0, acting_user=MEMBER
		)
		saved = hub.save_lead_department(values, acting_user=MANAGER)
		self.assertEqual(saved["name"], CONTENT)
		self.assertEqual([row["user"] for row in saved["heads"]], [HEAD])
		updated = hub.save_lead_department({"members": [MEMBER, MEMBER2]}, name=CONTENT, acting_user=MANAGER)
		self.assertEqual(len(updated["members"]), 2)
		self.assertEqual(hub.set_lead_department_active(CONTENT, 0, acting_user=MANAGER)["is_active"], 0)
		listing = hub.get_lead_departments(acting_user=MANAGER)
		self.assertTrue(listing["can_edit"])
		self.assertIn(CONTENT, [row["name"] for row in listing["departments"]])
		self.assertFalse(hub.get_lead_departments(acting_user=MEMBER)["can_edit"])
