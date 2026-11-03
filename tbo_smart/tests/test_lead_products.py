import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, getdate, now_datetime

from tbo_smart.api import hub_leads as hub
from tbo_smart.outreach import whatsapp
from tbo_smart.prospecting import importer
from tbo_smart.sales import products
from tbo_smart.sales.call_batches.fill import build_batch, campaign_of
from tbo_smart.sales.call_batches.reads import get_my_calls
from tbo_smart.smart_leads import autopilot
from tbo_smart.test_utils import (
	delete_call_batches,
	delete_lead_departments,
	delete_leads_matching,
	delete_sales_records,
	disable_sales_hierarchy,
	make_call_batch,
	make_callable_lead,
	make_lead,
	make_lead_department,
	make_lead_task,
	make_user,
)

HUB = "prod.integration@tbosmart.test"
MANAGER = "prod.manager@tbosmart.test"
HEAD = "prod.head@tbosmart.test"
OTHER_HEAD = "prod.otherhead@tbosmart.test"
BD = "prod.bd@tbosmart.test"
PEOPLE = (MANAGER, HEAD, OTHER_HEAD, BD)
GROWTHX = "TBO Test GrowthX Products"
OTHER = "TBO Test Other Products"
PHOEBIX = "PhoebiX"
INTRO = "PhoebiX - solar ERP for installers"
PRODUCTS = [
	{"product_name": PHOEBIX, "is_active": 1, "intro_line": INTRO},
	{"product_name": "BillaX", "is_active": 1, "intro_line": "BillaX - billing for shops"},
	{"product_name": "Foamia", "is_active": 0, "intro_line": "Foamia - old brand"},
]
DRAFT = {"subject": "Solar ERP", "text": "Hi, PhoebiX helps installers. Could we talk? Sam"}


class ProductCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(MANAGER, ["Sales Manager"])
		for user in (HEAD, OTHER_HEAD, BD):
			make_user(user, ["Sales User"])

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_sales_hierarchy(PEOPLE)
		make_lead_department(GROWTHX, heads=[HEAD], members=[BD], products=PRODUCTS)
		make_lead_department(OTHER, heads=[OTHER_HEAD], members=[BD])

	def tearDown(self):
		frappe.set_user("Administrator")
		self.clean()

	def clean(self):
		delete_call_batches(PEOPLE)
		delete_leads_matching(tbo_department=("in", [GROWTHX, OTHER]))
		delete_sales_records(PEOPLE)
		delete_lead_departments([GROWTHX, OTHER])

	def lead(self, name: str = "Prod Lead", **fields) -> str:
		return make_lead(BD, name, tbo_department=GROWTHX, **fields)

	def product_of(self, lead: str):
		return frappe.db.get_value("Lead", lead, "tbo_product")

	def as_hub(self, method, user: str, *args, **kwargs):
		frappe.set_user(HUB)
		try:
			return method(*args, acting_user=user, **kwargs)
		finally:
			frappe.set_user("Administrator")


class TestProductRules(ProductCase):
	def test_only_managers_and_the_departments_head_change_a_product(self):
		lead = self.lead()
		for user in (BD, OTHER_HEAD):
			with self.assertRaises(frappe.PermissionError, msg=user):
				self.as_hub(hub.update_lead, user, lead, {"product": PHOEBIX})
			self.assertIsNone(self.product_of(lead))
		for user in (HEAD, MANAGER):
			self.as_hub(hub.update_lead, user, lead, {"product": "billax"})
			self.assertEqual(self.product_of(lead), "BillaX", "any case is accepted, stored as listed")
			self.as_hub(hub.update_lead, user, lead, {"product": ""})
			self.assertIsNone(self.product_of(lead))

	def test_the_owner_sees_the_product_but_cannot_clear_or_set_it_on_a_new_lead(self):
		lead = self.lead("Prod Tagged", tbo_product=PHOEBIX)
		self.assertEqual(self.as_hub(hub.get_lead, BD, lead)["lead"]["product"], PHOEBIX)
		with self.assertRaises(frappe.PermissionError):
			self.as_hub(hub.update_lead, BD, lead, {"product": ""})
		self.assertEqual(self.product_of(lead), PHOEBIX)
		with self.assertRaises(frappe.PermissionError):
			self.as_hub(
				hub.create_lead,
				BD,
				{"first_name": "New", "department": GROWTHX, "product": PHOEBIX, "lead_owner": BD},
			)
		# editing something else on a tagged lead is fine for the owner
		self.as_hub(hub.update_lead, BD, lead, {"job_title": "Owner"})
		self.assertEqual(self.product_of(lead), PHOEBIX)

	def test_a_head_can_create_a_lead_with_a_product(self):
		made = self.as_hub(
			hub.create_lead, HEAD, {"first_name": "Solar Co", "department": GROWTHX, "product": PHOEBIX}
		)
		self.assertEqual(made["lead"]["product"], PHOEBIX)

	def test_the_product_must_be_an_active_product_of_the_leads_department(self):
		lead = self.lead()
		for value in ("Nope", "Foamia"):
			with self.assertRaises(frappe.ValidationError, msg=value):
				self.as_hub(hub.update_lead, MANAGER, lead, {"product": value})
		other = make_lead(BD, "Prod Other", tbo_department=OTHER)
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(hub.update_lead, MANAGER, other, {"product": PHOEBIX})
		nodept = make_lead(BD, "Prod None")
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(hub.update_lead, MANAGER, nodept, {"product": PHOEBIX})

	def test_moving_a_lead_to_a_department_without_its_product_is_refused(self):
		lead = self.lead("Prod Move", tbo_product=PHOEBIX)
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(hub.update_lead, MANAGER, lead, {"department": OTHER})
		self.as_hub(hub.update_lead, MANAGER, lead, {"department": OTHER, "product": ""})
		self.assertIsNone(self.product_of(lead))

	def test_a_lead_stays_editable_after_its_product_is_switched_off(self):
		lead = self.lead("Prod Off", tbo_product=PHOEBIX)
		department = frappe.get_doc("TBO Lead Department", GROWTHX)
		department.products[0].is_active = 0
		department.save()
		self.as_hub(hub.update_lead, BD, lead, {"job_title": "Still fine"})
		self.assertEqual(self.product_of(lead), PHOEBIX)

	def test_a_department_without_products_works_as_before(self):
		lead = make_lead(BD, "Prod Plain", tbo_department=OTHER)
		self.as_hub(hub.update_lead, BD, lead, {"job_title": "Owner"})
		row = self.as_hub(hub.list_leads, BD, department=OTHER)["rows"][0]
		self.assertIsNone(row["product"])

	def test_bulk_set_follows_the_same_rules(self):
		mine, other = self.lead("Prod Bulk 1"), make_lead(BD, "Prod Bulk 2", tbo_department=OTHER)
		result = self.as_hub(hub.bulk_set_lead_category, BD, [mine], {"product": PHOEBIX})
		self.assertEqual(result["updated"], 0)
		self.assertEqual(result["skipped"][0]["name"], mine)
		result = self.as_hub(hub.bulk_set_lead_category, HEAD, [mine, other], {"product": PHOEBIX})
		self.assertEqual(result["updated"], 1, "the head manages GrowthX only")
		self.assertEqual({row["name"] for row in result["skipped"]}, {other})
		self.assertEqual(self.product_of(mine), PHOEBIX)
		result = self.as_hub(hub.bulk_set_lead_category, MANAGER, [mine, other], {"product": "BillaX"})
		self.assertEqual((result["updated"], [row["name"] for row in result["skipped"]]), (1, [other]))
		self.assertEqual(self.product_of(mine), "BillaX")
		self.as_hub(hub.bulk_set_lead_category, MANAGER, [mine], {"product": "", "state": "Kerala"})
		self.assertEqual(frappe.db.get_value("Lead", mine, ["tbo_product", "state"]), (None, "Kerala"))
		# a head may not change the other categories
		with self.assertRaises(frappe.PermissionError):
			self.as_hub(hub.bulk_set_lead_category, HEAD, [mine], {"state": "Goa"})


class TestProductReads(ProductCase):
	def setUp(self):
		super().setUp()
		self.solar = self.lead("Prod Solar", tbo_product=PHOEBIX)
		self.billing = self.lead("Prod Billing", tbo_product="BillaX")
		self.plain = self.lead("Prod Plain Lead")

	def test_the_list_filters_by_one_product_or_a_list_and_rows_carry_it(self):
		one = self.as_hub(hub.list_leads, MANAGER, department=GROWTHX, product=PHOEBIX)
		self.assertEqual([row["name"] for row in one["rows"]], [self.solar])
		self.assertEqual(one["rows"][0]["product"], PHOEBIX)
		both = self.as_hub(
			hub.list_leads, MANAGER, department=GROWTHX, product=json.dumps([PHOEBIX, "BillaX"])
		)
		self.assertEqual({row["name"] for row in both["rows"]}, {self.solar, self.billing})
		self.assertEqual(both["total"], 2)

	def test_counts_include_the_products(self):
		counts = self.as_hub(hub.get_category_counts, MANAGER)
		self.assertEqual(counts["product"][PHOEBIX], 1)
		self.assertEqual(counts["product"]["BillaX"], 1)

	def test_the_options_list_only_the_active_products(self):
		options = self.as_hub(hub.get_options, BD)
		department = next(row for row in options["departments"] if row["name"] == GROWTHX)
		self.assertEqual([row["product_name"] for row in department["products"]], [PHOEBIX, "BillaX"])
		self.assertEqual(department["products"][0]["intro_line"], INTRO)
		plain = next(row for row in options["departments"] if row["name"] == OTHER)
		self.assertEqual(plain["products"], [])


class TestSavingProducts(ProductCase):
	def test_managers_save_products_and_names_are_unique_in_the_department(self):
		rows = [*PRODUCTS, {"product_name": "WilpeX", "intro_line": "WilpeX - wills"}]
		saved = self.as_hub(hub.save_lead_department, MANAGER, {"products": rows}, GROWTHX)
		self.assertEqual([row["product_name"] for row in saved["products"]][-1], "WilpeX")
		self.assertEqual(saved["products"][-1]["is_active"], 1, "a new product is on")
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(
				hub.save_lead_department,
				MANAGER,
				{"products": [{"product_name": "Avera"}, {"product_name": " avera "}]},
				GROWTHX,
			)
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(hub.save_lead_department, MANAGER, {"products": [{"product_name": ""}]}, GROWTHX)
		for user in (BD, HEAD):
			with self.assertRaises(frappe.PermissionError):
				self.as_hub(hub.save_lead_department, user, {"products": rows}, GROWTHX)
		listed = next(row for row in self.as_hub(hub.get_lead_departments, BD) if row["name"] == GROWTHX)
		self.assertEqual(len(listed["products"]), 4, "switched-off ones are listed for the editor")

	def test_the_campaign_product_must_be_an_active_product(self):
		saved = self.as_hub(hub.save_lead_department, MANAGER, {"campaign_product": PHOEBIX}, GROWTHX)
		self.assertEqual(saved["campaign_product"], PHOEBIX)
		for value in ("Foamia", "Nope"):
			with self.assertRaises(frappe.ValidationError, msg=value):
				self.as_hub(hub.save_lead_department, MANAGER, {"campaign_product": value}, GROWTHX)


class TestCallingUnderAProduct(ProductCase):
	def setUp(self):
		super().setUp()
		for target in (
			"tbo_smart.sales.call_batches.next_batch.is_off_today",
			"tbo_smart.sales.call_batches.clock.is_off_today",
		):
			patcher = patch(target, return_value=None)
			patcher.start()
			self.addCleanup(patcher.stop)

	def build(self):
		department = frappe.get_doc("TBO Lead Department", GROWTHX)
		now = now_datetime()
		return build_batch(
			department,
			BD,
			"Day",
			add_to_date(now, hours=-1),
			add_to_date(now, hours=3),
			50,
			campaign_of(department),
		)

	def callable(self, name: str, **fields) -> str:
		return make_callable_lead(BD, name, GROWTHX, **fields)

	def test_the_campaign_product_filters_new_leads_but_not_follow_ups(self):
		solar = self.callable("Prod Call Solar", tbo_product=PHOEBIX)
		billing = self.callable("Prod Call Billing", tbo_product="BillaX")
		plain = self.callable("Prod Call Plain")
		due = self.callable("Prod Call Due", tbo_product="BillaX")
		make_lead_task(due, getdate(), BD)
		frappe.db.set_value("TBO Lead Department", GROWTHX, "campaign_product", PHOEBIX)
		batch = self.build()
		leads = [row.lead for row in batch.items]
		self.assertCountEqual(leads, [solar, due])
		self.assertNotIn(billing, leads)
		self.assertNotIn(plain, leads)

	def test_without_a_campaign_product_every_lead_is_batched(self):
		leads = [self.callable("Prod Any 1", tbo_product=PHOEBIX), self.callable("Prod Any 2")]
		self.assertCountEqual([row.lead for row in self.build().items], leads)

	def test_a_call_carries_the_product_and_its_intro_line(self):
		solar = self.callable("Prod Call Solar", tbo_product=PHOEBIX)
		plain = self.callable("Prod Call Plain")
		make_call_batch(BD, GROWTHX, [solar, plain])
		frappe.db.set_value("TBO Lead Department", GROWTHX, "campaign_product", PHOEBIX)
		frappe.set_user(BD)
		calls = {row["lead"]: row for row in get_my_calls()["pending"]}
		self.assertEqual(
			(calls[solar]["product"], calls[solar]["intro_line"], calls[solar]["calling_for"]),
			(PHOEBIX, INTRO, PHOEBIX),
		)
		self.assertEqual(
			(calls[plain]["product"], calls[plain]["intro_line"], calls[plain]["calling_for"]),
			(None, None, GROWTHX),
			"a lead with no product shows the department",
		)
		self.assertEqual(get_my_calls()["batch"]["campaign_product"], PHOEBIX)

	def test_a_switched_off_product_still_shows_its_intro_on_calls_already_handed_out(self):
		solar = self.callable("Prod Call Off", tbo_product=PHOEBIX)
		make_call_batch(BD, GROWTHX, [solar])
		department = frappe.get_doc("TBO Lead Department", GROWTHX)
		department.products[0].is_active = 0
		department.save()
		frappe.set_user(BD)
		self.assertEqual(get_my_calls()["pending"][0]["intro_line"], INTRO)

	def test_the_hub_can_ask_for_one_product_for_the_next_batch(self):
		department = frappe.get_doc("TBO Lead Department", GROWTHX)
		self.assertEqual(campaign_of(department, {"product": "BillaX"})["product"], "billax")


class TestProductsInOutreach(ProductCase):
	def setUp(self):
		super().setUp()
		self.solar = self.lead("Prod Msg Solar", tbo_product=PHOEBIX, email_id="solar@prodmsg.test")
		self.plain = self.lead("Prod Msg Plain", email_id="plain@prodmsg.test")

	def draft(self, lead: str):
		frappe.set_user(BD)
		try:
			with patch("tbo_smart.ai.call_ai", return_value={"response": DRAFT}) as call:
				autopilot.draft_message(lead, "email")
			return call
		finally:
			frappe.set_user("Administrator")

	def test_the_draft_is_written_for_the_leads_product(self):
		call = self.draft(self.solar)
		self.assertEqual(json.loads(call.call_args.args[1])["selling"], {"name": PHOEBIX, "intro": INTRO})
		self.assertIn("selling", call.call_args.args[0])

	def test_a_lead_without_a_product_is_drafted_from_tbo_as_before(self):
		payload = json.loads(self.draft(self.plain).call_args.args[1])
		self.assertNotIn("selling", payload)

	def test_the_whatsapp_queue_row_names_the_product_for_the_template(self):
		self.assertEqual(whatsapp.selling_fields(self.solar), {"product": PHOEBIX, "product_intro": INTRO})
		self.assertEqual(whatsapp.selling_fields(self.plain), {"product": None, "product_intro": None})


class TestProductsInFindProspects(ProductCase):
	def test_a_run_takes_one_of_the_departments_products_and_its_leads_get_it(self):
		self.assertEqual(products.clean_for(GROWTHX, "phoebix"), PHOEBIX)
		with self.assertRaises(frappe.ValidationError):
			products.clean_for(OTHER, PHOEBIX)
		run = frappe._dict(
			name="RUN-X",
			industry="Resorts",
			department=GROWTHX,
			product=PHOEBIX,
			provider="OpenStreetMap",
			state="Kerala",
			district="Idukki",
		)
		values = importer.lead_values({"name": "Palm Resort"}, run)
		self.assertEqual(values["tbo_product"], PHOEBIX)
		self.assertEqual(values["tbo_department"], GROWTHX)
