from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from tbo_smart.api import hub_leads as hub
from tbo_smart.sales.call_batches import clock, focus
from tbo_smart.sales.call_batches.fill import build_batch, campaign_of
from tbo_smart.sales.call_batches.reads import get_my_calls
from tbo_smart.test_utils import (
	delete_bd_focus,
	delete_call_batches,
	delete_lead_departments,
	delete_sales_records,
	disable_sales_hierarchy,
	make_batch_department,
	make_call_batch,
	make_callable_lead,
	make_user,
)

BD = "focus.bd@tbosmart.test"
OUTSIDER = "focus.outsider@tbosmart.test"
PEOPLE = (BD, OUTSIDER)
GROWTHX = "TBO Test Focus GrowthX"
STAYVEX = "TBO Test Focus StayVeX"
OFF = "TBO Test Focus Off"
DEPARTMENTS = (GROWTHX, STAYVEX, OFF)
PRODUCTS = [
	{"product_name": "PhoebiX", "is_active": 1, "intro_line": "PhoebiX - solar ERP"},
	{"product_name": "BillaX", "is_active": 1, "intro_line": "BillaX - billing"},
	{"product_name": "Foamia", "is_active": 0, "intro_line": "Foamia - old"},
]


class TestBDFocus(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		for user in PEOPLE:
			make_user(user, ["Sales User"])

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_sales_hierarchy(PEOPLE)
		for target in (
			"tbo_smart.sales.call_batches.next_batch.is_off_today",
			"tbo_smart.sales.call_batches.clock.is_off_today",
		):
			patcher = patch(target, return_value=None)
			patcher.start()
			self.addCleanup(patcher.stop)
		make_batch_department(GROWTHX, members=[BD], products=PRODUCTS)
		make_batch_department(STAYVEX, members=[BD])
		make_batch_department(OFF, members=[BD], call_batches_enabled=0)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.clean()

	def clean(self):
		delete_bd_focus(PEOPLE)
		delete_call_batches(PEOPLE)
		delete_sales_records(PEOPLE)
		for lead in frappe.get_all("Lead", filters={"tbo_department": ("in", DEPARTMENTS)}, pluck="name"):
			frappe.delete_doc("Lead", lead, force=True, ignore_permissions=True)
		delete_lead_departments(DEPARTMENTS)

	def lead(self, name: str, department: str = GROWTHX, **fields) -> str:
		return make_callable_lead(BD, name, department, **fields)

	def build(self, department_name: str, request: dict | None = None):
		department = frappe.get_doc("TBO Lead Department", department_name)
		now = now_datetime()
		return build_batch(
			department,
			BD,
			"Day",
			add_to_date(now, hours=-1),
			add_to_date(now, hours=3),
			50,
			campaign_of(department, request, focus.get_focus(BD)),
		)

	def leads_of(self, batch) -> list[str]:
		return [row.lead for row in batch.items]

	# ---------- the choices ----------

	def test_the_options_are_the_departments_with_batches_on_and_their_active_products(self):
		options = {row["department"]: row for row in focus.options_for(BD)}
		self.assertCountEqual(options, [GROWTHX, STAYVEX])
		self.assertEqual([p["product_name"] for p in options[GROWTHX]["products"]], ["PhoebiX", "BillaX"])
		self.assertEqual(options[STAYVEX]["products"], [])
		self.assertEqual(focus.options_for(OUTSIDER), [])

	def test_the_districts_offered_are_those_of_the_leads_the_person_could_get(self):
		self.lead("Focus Kochi 1", tbo_district="Kochi")
		self.lead("Focus Kochi 2", tbo_district="kochi")
		self.lead("Focus Thrissur", tbo_district="Thrissur")
		options = {row["department"]: row for row in focus.options_for(BD)}
		self.assertEqual([d.lower() for d in options[GROWTHX]["districts"]], ["kochi", "thrissur"])

	# ---------- remembering ----------

	def test_the_choice_is_remembered_and_replaced(self):
		self.assertIsNone(focus.get_focus(BD))
		focus.set_focus(BD, GROWTHX, "phoebix", "Kochi")
		self.assertEqual(
			focus.get_focus(BD), {"department": GROWTHX, "product": "PhoebiX", "district": "Kochi"}
		)
		focus.set_focus(BD, STAYVEX)
		self.assertEqual(focus.get_focus(BD), {"department": STAYVEX, "product": None, "district": None})
		self.assertEqual(frappe.db.count("TBO BD Focus", {"user": BD}), 1)
		focus.set_focus(BD, "")
		self.assertIsNone(focus.get_focus(BD))

	def test_the_choice_is_per_person(self):
		focus.set_focus(BD, STAYVEX)
		self.assertIsNone(focus.get_focus(OUTSIDER))

	def test_a_person_cannot_choose_outside_their_departments_or_products(self):
		with self.assertRaises(frappe.ValidationError):
			focus.set_focus(OUTSIDER, GROWTHX)
		with self.assertRaises(frappe.ValidationError):
			focus.set_focus(BD, OFF)
		with self.assertRaises(frappe.ValidationError):
			focus.set_focus(BD, GROWTHX, "Foamia")
		with self.assertRaises(frappe.ValidationError):
			focus.set_focus(BD, STAYVEX, "PhoebiX")

	def test_a_choice_that_no_longer_holds_is_dropped(self):
		focus.set_focus(BD, GROWTHX, "PhoebiX")
		frappe.db.set_value("TBO Lead Department", GROWTHX, "call_batches_enabled", 0)
		self.assertIsNone(focus.get_focus(BD))
		frappe.db.set_value("TBO Lead Department", GROWTHX, "call_batches_enabled", 1)
		department = frappe.get_doc("TBO Lead Department", GROWTHX)
		department.products[0].is_active = 0
		department.save()
		self.assertEqual(focus.get_focus(BD)["product"], None)

	# ---------- the filler ----------

	def test_the_focus_product_and_district_narrow_new_leads(self):
		solar_kochi = self.lead("Focus Solar Kochi", tbo_product="PhoebiX", tbo_district="Kochi")
		solar_tvm = self.lead("Focus Solar Tvm", tbo_product="PhoebiX", tbo_district="Trivandrum")
		billing = self.lead("Focus Billing Kochi", tbo_product="BillaX", tbo_district="Kochi")
		focus.set_focus(BD, GROWTHX, "PhoebiX", "Kochi")
		self.assertEqual(self.leads_of(self.build(GROWTHX)), [solar_kochi])
		self.assertNotIn(solar_tvm, [solar_kochi])
		self.assertNotIn(billing, [solar_kochi])

	def test_a_focus_for_another_department_does_not_narrow_this_one(self):
		first = self.lead("Focus Other 1", tbo_product="BillaX")
		focus.set_focus(BD, STAYVEX)
		self.assertEqual(self.leads_of(self.build(GROWTHX)), [first])

	def test_the_managers_campaign_is_a_limit_the_focus_and_a_request_cannot_widen(self):
		solar = self.lead("Focus Limit Solar", tbo_product="PhoebiX", tbo_district="Kochi")
		billing = self.lead("Focus Limit Billing", tbo_product="BillaX", tbo_district="Kochi")
		frappe.db.set_value("TBO Lead Department", GROWTHX, "campaign_product", "PhoebiX")
		option = focus.option_of(frappe.get_doc("TBO Lead Department", GROWTHX), BD)
		self.assertEqual([p["product_name"] for p in option["products"]], ["PhoebiX"])
		with self.assertRaises(frappe.ValidationError):
			focus.set_focus(BD, GROWTHX, "BillaX")
		focus.set_focus(BD, GROWTHX)
		self.assertEqual(self.leads_of(self.build(GROWTHX, {"product": "BillaX"})), [solar])
		self.assertNotIn(billing, [solar])

	def test_a_manager_district_is_the_only_district_choice(self):
		frappe.db.set_value("TBO Lead Department", GROWTHX, "campaign_district", "Kochi")
		self.assertEqual(
			focus.option_of(frappe.get_doc("TBO Lead Department", GROWTHX), BD)["districts"], ["Kochi"]
		)
		self.assertEqual(focus.set_focus(BD, GROWTHX, None, "Elsewhere")["district"], "Kochi")

	# ---------- the clock and the switch ----------

	def test_the_clock_only_opens_the_focus_department_for_the_person(self):
		hotel = self.lead("Focus Clock Hotel", STAYVEX)
		solar = self.lead("Focus Clock Solar", GROWTHX)
		focus.set_focus(BD, STAYVEX)
		clock.run_call_batch_clock()
		batches = frappe.get_all("TBO Call Batch", filters={"user": BD}, fields=["name", "department"])
		self.assertEqual([row.department for row in batches], [STAYVEX])
		self.assertEqual(
			[row.lead for row in frappe.get_doc("TBO Call Batch", batches[0].name).items], [hotel]
		)
		self.assertNotEqual(hotel, solar)

	def test_without_a_choice_the_clock_opens_every_department(self):
		self.lead("Focus All Hotel", STAYVEX)
		self.lead("Focus All Solar", GROWTHX)
		clock.run_call_batch_clock()
		departments = frappe.get_all("TBO Call Batch", filters={"user": BD}, pluck="department")
		self.assertCountEqual(departments, [GROWTHX, STAYVEX])

	def test_switching_starts_the_next_batch_for_the_choice_and_says_so(self):
		hotel = self.lead("Focus Switch Hotel", STAYVEX)
		self.lead("Focus Switch Solar", GROWTHX, tbo_product="PhoebiX")
		frappe.set_user(BD)
		result = hub.set_calling_focus(STAYVEX)
		self.assertEqual(result["focus"]["department"], STAYVEX)
		self.assertTrue(result["next_batch"]["created"])
		self.assertIn("ready", result["message"])
		batch = frappe.get_doc("TBO Call Batch", get_my_calls()["batch"]["name"])
		self.assertEqual([row.lead for row in batch.items], [hotel])

	def test_switching_while_calls_are_waiting_saves_the_choice_and_explains(self):
		waiting = self.lead("Focus Wait Solar", GROWTHX)
		make_call_batch(BD, GROWTHX, [waiting])
		self.lead("Focus Wait Hotel", STAYVEX)
		frappe.set_user(BD)
		result = hub.set_calling_focus(STAYVEX)
		self.assertFalse(result["next_batch"]["created"])
		self.assertIn("1 calls", result["message"])
		calls = get_my_calls()
		self.assertEqual(calls["focus"]["department"], STAYVEX)
		self.assertEqual({row["department"] for row in calls["focus_options"]}, {GROWTHX, STAYVEX})

	def test_the_next_batch_without_a_department_follows_the_choice(self):
		self.lead("Focus Next Solar", GROWTHX)
		hotel = self.lead("Focus Next Hotel", STAYVEX)
		focus.set_focus(BD, STAYVEX)
		frappe.set_user(BD)
		result = hub.get_next_batch()
		self.assertTrue(result["created"])
		self.assertEqual(
			[row.lead for row in frappe.get_doc("TBO Call Batch", result["batch"]).items], [hotel]
		)
