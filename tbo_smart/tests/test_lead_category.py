import json

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline import lead_category as category
from tbo_smart.lead_pipeline.setup import seed_industries
from tbo_smart.test_utils import (
	delete_industries,
	delete_sales_records,
	disable_sales_hierarchy,
	make_lead,
	make_user,
)

HUB = "cat.integration@tbosmart.test"
BDE = "cat.bde@tbosmart.test"
BDE2 = "cat.bde2@tbosmart.test"
MANAGER = "cat.manager@tbosmart.test"
OWNERS = (BDE, BDE2)
NEW_INDUSTRY = "Tbo Test Salons"


class TestLeadCategory(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		make_user(MANAGER, ["System Manager", "Sales Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		disable_sales_hierarchy(OWNERS)
		delete_industries([NEW_INDUSTRY])
		self.resort = make_lead(
			BDE,
			"Cat Resort",
			company_name="Backwater Resort",
			industry="Resorts",
			state="Kerala",
			tbo_district="Alappuzha",
			no_of_employees="51-200",
		)
		self.builder = make_lead(
			BDE,
			"Cat Builder",
			company_name="Skyline Builders",
			industry="Builders & Developers",
			state="Tamil Nadu",
			tbo_district="Chennai",
		)
		self.bare = make_lead(BDE2, "Cat Bare", company_name="No Category Co")
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")
		delete_industries([NEW_INDUSTRY])

	def names(self, **filters):
		return {row["name"] for row in hub.list_leads(acting_user=MANAGER, **filters)["rows"]}

	def test_the_starting_industries_are_seeded_once_and_kept(self):
		frappe.set_user("Administrator")
		before = frappe.db.count("Industry Type")
		seed_industries()
		self.assertEqual(frappe.db.count("Industry Type"), before)
		for name in category.INDUSTRIES:
			self.assertTrue(frappe.db.exists("Industry Type", name), name)

	def test_the_state_list_has_india_and_the_gulf(self):
		self.assertEqual(len(category.INDIAN_STATES), 36)
		for region in ("Kerala", "Delhi", "Dubai", "Saudi Arabia", "Qatar", "Oman", "Bahrain", "Kuwait"):
			self.assertIn(region, category.STATES)
		self.assertEqual(len(category.KERALA_DISTRICTS), 14)

	def test_options_list_the_categories(self):
		options = hub.get_options(acting_user=BDE)
		self.assertIn("Resorts", options["industries"])
		self.assertEqual(options["org_sizes"], ["1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"])
		self.assertEqual(options["states"][0], "Andhra Pradesh")
		self.assertEqual(options["kerala_districts"][0], "Thiruvananthapuram")
		# only the acting user's leads: BDE has two, each with a state
		self.assertEqual(options["districts_by_state"]["Kerala"], ["Alappuzha"])
		self.assertEqual(options["districts_by_state"]["Tamil Nadu"], ["Chennai"])

	def test_list_filters_by_each_category_and_by_several_values(self):
		self.assertEqual(self.names(industry="Resorts") & {self.resort, self.builder}, {self.resort})
		self.assertEqual(
			self.names(industry=["Resorts", "Builders & Developers"]) & {self.resort, self.builder},
			{self.resort, self.builder},
		)
		self.assertEqual(
			self.names(industry=json.dumps(["Resorts"])) & {self.resort, self.builder}, {self.resort}
		)
		self.assertEqual(self.names(state="kerala") & {self.resort, self.builder}, {self.resort})
		self.assertEqual(self.names(district=["Chennai"]) & {self.resort, self.builder}, {self.builder})
		self.assertEqual(self.names(org_size="51-200") & {self.resort, self.builder}, {self.resort})
		self.assertEqual(self.names(state="Kerala", district="Chennai"), set())
		self.assertIn(self.bare, self.names())

	def test_rows_and_the_lead_carry_the_categories(self):
		row = next(r for r in hub.list_leads(acting_user=BDE)["rows"] if r["name"] == self.resort)
		self.assertEqual(
			(row["industry"], row["state"], row["district"], row["org_size"]),
			("Resorts", "Kerala", "Alappuzha", "51-200"),
		)
		lead = hub.get_lead(self.resort, acting_user=BDE)["lead"]
		self.assertEqual(
			(lead["industry"], lead["state"], lead["district"], lead["org_size"]),
			("Resorts", "Kerala", "Alappuzha", "51-200"),
		)
		self.assertNotIn("tbo_district", lead)

	def test_create_and_update_clean_the_values(self):
		lead = hub.create_lead(
			{
				"company_name": "Palm Hotels",
				"industry": "resorts",
				"state": "  KERALA ",
				"district": "kozhikode",
				"org_size": "11-50",
			},
			acting_user=BDE,
		)["lead"]
		self.assertEqual(
			(lead["industry"], lead["state"], lead["district"], lead["org_size"]),
			("Resorts", "Kerala", "Kozhikode", "11-50"),
		)
		changed = hub.update_lead(
			lead["name"], {"state": "orissa", "district": "  some   new  place"}, acting_user=BDE
		)["lead"]
		self.assertEqual((changed["state"], changed["district"]), ("Odisha", "Some New Place"))
		cleared = hub.update_lead(lead["name"], {"district": "", "org_size": ""}, acting_user=BDE)["lead"]
		self.assertFalse(cleared["district"] or cleared["org_size"])

	def test_a_state_off_the_list_is_kept(self):
		lead = hub.update_lead(self.bare, {"state": "Sri Lanka"}, acting_user=BDE2)["lead"]
		self.assertEqual(lead["state"], "Sri Lanka")

	def test_unknown_industry_and_size_are_refused(self):
		self.assertRaises(
			frappe.ValidationError, hub.update_lead, self.bare, {"industry": "Nope Inc"}, acting_user=BDE2
		)
		self.assertRaises(
			frappe.ValidationError, hub.update_lead, self.bare, {"org_size": "7"}, acting_user=BDE2
		)

	def test_a_manager_adds_an_industry_and_it_is_idempotent(self):
		added = hub.add_industry(NEW_INDUSTRY, acting_user=MANAGER)
		self.assertEqual(added, {"name": NEW_INDUSTRY, "created": True})
		again = hub.add_industry(NEW_INDUSTRY.lower(), acting_user=MANAGER)
		self.assertEqual(again, {"name": NEW_INDUSTRY, "created": False})
		self.assertIn(NEW_INDUSTRY, hub.get_options(acting_user=BDE)["industries"])
		self.assertRaises(frappe.ValidationError, hub.add_industry, "  ", acting_user=MANAGER)

	def test_a_salesperson_cannot_add_an_industry_or_bulk_set(self):
		self.assertRaises(frappe.PermissionError, hub.add_industry, NEW_INDUSTRY, acting_user=BDE)
		self.assertRaises(
			frappe.PermissionError,
			hub.bulk_set_lead_category,
			[self.resort],
			{"industry": "Resorts"},
			acting_user=BDE,
		)

	def test_bulk_set_categorises_many_leads(self):
		result = hub.bulk_set_lead_category(
			[self.bare, self.builder, "NO-SUCH-LEAD"],
			{"industry": "Hotels & Restaurants", "state": "dubai", "district": ""},
			acting_user=MANAGER,
		)
		self.assertEqual(result["updated"], 2)
		self.assertEqual(result["skipped"], [{"name": "NO-SUCH-LEAD", "reason": "missing"}])
		for name in (self.bare, self.builder):
			lead = hub.get_lead(name, acting_user=MANAGER)["lead"]
			self.assertEqual((lead["industry"], lead["state"]), ("Hotels & Restaurants", "Dubai"))
			self.assertFalse(lead["district"])
		# what the call did not name is left as it was
		self.assertEqual(hub.get_lead(self.builder, acting_user=MANAGER)["lead"]["org_size"], None)

	def test_bulk_set_checks_its_input(self):
		for leads, values in (([], {"industry": "Resorts"}), ([self.bare], {}), ([self.bare], {"x": "y"})):
			self.assertRaises(
				frappe.ValidationError, hub.bulk_set_lead_category, leads, values, acting_user=MANAGER
			)
		self.assertRaises(
			frappe.ValidationError,
			hub.bulk_set_lead_category,
			[self.bare],
			{"industry": "Nope Inc"},
			acting_user=MANAGER,
		)

	def test_counts_are_the_acting_users(self):
		counts = hub.get_category_counts(acting_user=BDE)
		self.assertEqual(counts["industry"]["Resorts"], 1)
		self.assertEqual(counts["state"]["Kerala"], 1)
		self.assertEqual(counts["district"]["Chennai"], 1)
		self.assertEqual(counts["org_size"]["51-200"], 1)
		self.assertNotIn("No Category Co", json.dumps(counts))
