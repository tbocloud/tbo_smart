import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline.statuses import CLOSED_LOST, CONTACTED, STATUS_NAMES
from tbo_smart.test_utils import (
	delete_sales_records,
	disable_sales_hierarchy,
	make_lead,
	make_lead_task,
	make_user,
	set_user_enabled,
)

HUB = "hub.integration@tbosmart.test"
BDE = "hub.bde@tbosmart.test"
BDE2 = "hub.bde2@tbosmart.test"
STAFF = "hub.staff@tbosmart.test"
GONE = "hub.gone@tbosmart.test"
OWNERS = (BDE, BDE2, GONE)


class TestHubLeads(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		make_user(STAFF, ["Employee"])
		make_user(GONE, ["Sales User"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		disable_sales_hierarchy(OWNERS)
		set_user_enabled(GONE, False)
		self.mine = make_lead(BDE, "Hub Mine", company_name="Mine Traders", tbo_segment="Small")
		self.theirs = make_lead(BDE2, "Hub Theirs", company_name="Their Traders")
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")
		set_user_enabled(GONE, True)

	def test_the_integration_user_must_name_a_real_sales_user(self):
		self.assertRaises(frappe.PermissionError, hub.list_leads)
		for who in ("nobody@tbosmart.test", GONE, STAFF, HUB):
			self.assertRaises(frappe.PermissionError, hub.list_leads, acting_user=who)
		self.assertEqual(frappe.session.user, HUB)

	def test_anyone_else_acts_as_themselves_only(self):
		frappe.set_user(BDE)
		self.assertRaises(frappe.PermissionError, hub.list_leads, acting_user=BDE2)
		names = {row["name"] for row in hub.list_leads(acting_user=BDE)["rows"]}
		self.assertEqual(names, {self.mine})
		self.assertEqual({row["name"] for row in hub.list_leads()["rows"]}, {self.mine})

	def test_visibility_follows_the_acting_user(self):
		result = hub.list_leads(acting_user=BDE)
		self.assertEqual([row["name"] for row in result["rows"]], [self.mine])
		self.assertEqual(result["total"], 1)
		row = result["rows"][0]
		self.assertEqual(
			(row["company_name"], row["segment"], row["tbo_stage"], row["owner_name"]),
			("Mine Traders", "Small", "New Lead", "hub.bde"),
		)
		self.assertRaises(frappe.PermissionError, hub.get_lead, self.theirs, acting_user=BDE)
		self.assertEqual(frappe.session.user, HUB)
		# filters and views
		self.assertEqual(hub.list_leads(segment="Large", acting_user=BDE)["rows"], [])
		self.assertEqual(hub.list_leads(search="Mine", acting_user=BDE)["total"], 1)
		self.assertEqual(hub.list_leads(view="mine", acting_user=BDE2)["total"], 1)
		self.assertRaises(frappe.ValidationError, hub.list_leads, stage="Nope", acting_user=BDE)

	def test_follow_up_view(self):
		frappe.set_user("Administrator")
		make_lead_task(self.mine, add_days(today(), -1), BDE)
		later = make_lead(BDE, "Hub Later")
		make_lead_task(later, add_days(today(), 5), BDE)
		frappe.set_user(HUB)
		rows = hub.list_leads(view="follow_up", acting_user=BDE)["rows"]
		self.assertEqual([row["name"] for row in rows], [self.mine])
		self.assertEqual(str(rows[0]["next_follow_up"]), add_days(today(), -1))

	def test_writes_are_the_acting_users(self):
		created = hub.create_lead(
			json.dumps(
				{
					"lead_name": "Hub Created",
					"company_name": "Created Co",
					"mobile_no": "9847000777",
					"segment": "Large",
					"note": "From the hub's form",
				}
			),
			acting_user=BDE,
		)
		self.assertEqual(frappe.session.user, HUB)
		name = created["lead"]["name"]
		owner, lead_owner, segment = frappe.db.get_value("Lead", name, ["owner", "lead_owner", "tbo_segment"])
		self.assertEqual((owner, lead_owner, segment), (BDE, BDE, "Large"))
		[note] = [item for item in created["timeline"] if item["kind"] == "note"]
		self.assertEqual((note["who"], note["text"]), (BDE, "From the hub's form"))
		self.assertEqual(created["allowed_stages"], [stage for stage in STATUS_NAMES if stage != "New Lead"])

		updated = hub.update_lead(name, {"job_title": "Owner", "segment": "Premium"}, acting_user=BDE)
		self.assertEqual((updated["lead"]["job_title"], updated["lead"]["segment"]), ("Owner", "Premium"))
		self.assertRaises(frappe.PermissionError, hub.update_lead, name, {"job_title": "x"}, acting_user=BDE2)
		self.assertRaises(frappe.ValidationError, hub.update_lead, name, {"status": "Open"}, acting_user=BDE)
		self.assertRaises(frappe.ValidationError, hub.update_lead, name, {"segment": "Huge"}, acting_user=BDE)

		moved = hub.set_stage(name, CONTACTED, "Spoke to the owner", acting_user=BDE)
		self.assertEqual(moved["lead"]["tbo_stage"], CONTACTED)
		kinds = [item["kind"] for item in moved["timeline"]]
		self.assertIn("stage", kinds)
		self.assertEqual(kinds.count("note"), 2)
		self.assertRaises(frappe.ValidationError, hub.set_stage, name, CLOSED_LOST, acting_user=BDE)
		lost = hub.set_stage(name, CLOSED_LOST, "Bought elsewhere", acting_user=BDE)
		self.assertEqual(
			(lost["lead"]["tbo_lost_reason"], lost["lead"]["tbo_lost_notes"]), ("Other", "Bought elsewhere")
		)

		entry = hub.add_note(name, "Called back", acting_user=BDE)
		self.assertEqual((entry["who"], entry["text"]), (BDE, "Called back"))
		self.assertEqual(frappe.db.get_value("Comment", entry["ref"], "owner"), BDE)
		self.assertRaises(frappe.PermissionError, hub.add_note, self.theirs, "Not mine", acting_user=BDE)
		self.assertEqual(frappe.session.user, HUB)

	def test_owner_and_fields_are_checked(self):
		self.assertRaises(
			frappe.PermissionError,
			hub.create_lead,
			{"lead_name": "Given Away", "lead_owner": BDE2},
			acting_user=BDE,
		)
		self.assertRaises(
			frappe.ValidationError, hub.create_lead, {"lead_name": "Odd", "status": "x"}, acting_user=BDE
		)
		self.assertRaises(frappe.ValidationError, hub.create_lead, {"note": "No name"}, acting_user=BDE)

	def test_options(self):
		options = hub.get_options(acting_user=BDE)
		self.assertEqual([row["name"] for row in options["stages"]], list(STATUS_NAMES))
		self.assertEqual(set(options["stages"][0]), {"name", "label", "type", "color"})
		self.assertEqual(options["segments"], ["Retail", "Small", "Medium", "Large", "Premium"])
		self.assertIn({"email": BDE, "full_name": "hub.bde"}, options["owners"])
		self.assertNotIn(STAFF, [row["email"] for row in options["owners"]])
		self.assertTrue({"sources", "territories", "industries"} <= set(options))
