from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from tbo_smart.api import hub_leads as hub
from tbo_smart.sales import departments
from tbo_smart.sales.call_batches import clock, daily_cap, focus, handout, plans
from tbo_smart.sales.call_batches.next_batch import create_next_batch
from tbo_smart.sales.call_batches.state import CallQueue
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

BD = "plan.bd@tbosmart.test"
OTHER_BD = "plan.other@tbosmart.test"
HEAD = "plan.head@tbosmart.test"
MANAGER = "plan.manager@tbosmart.test"
OUTSIDER = "plan.outsider@tbosmart.test"
PEOPLE = (BD, OTHER_BD, HEAD, MANAGER, OUTSIDER)
GROWTHX = "TBO Test Plan GrowthX"
STAYVEX = "TBO Test Plan StayVeX"
DEPARTMENTS = (GROWTHX, STAYVEX)
PRODUCTS = [
	{"product_name": "PhoebiX", "is_active": 1, "intro_line": "PhoebiX - solar ERP"},
	{"product_name": "BillaX", "is_active": 1, "intro_line": "BillaX - billing"},
	{"product_name": "Foamia", "is_active": 0, "intro_line": "Foamia - old"},
]


class TestCallPlans(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		for user in (BD, OTHER_BD, HEAD, OUTSIDER):
			make_user(user, ["Sales User"])
		make_user(MANAGER, ["Sales User", "Sales Manager"])

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
		make_batch_department(
			GROWTHX, members=[BD, OTHER_BD], heads=[HEAD], products=PRODUCTS, pull_unassigned=1
		)
		make_batch_department(STAYVEX, members=[BD])

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

	def plan(self, users=(BD,), product=None, sizes=None, department=GROWTHX, **kwargs):
		return plans.save_call_plan(department, list(users), product, sizes, **kwargs)

	def unassigned(self, count: int, product: str | None = None) -> list[str]:
		return [
			make_callable_lead(None, f"Plan Pool {product or 'any'} {i}", GROWTHX, tbo_product=product)
			for i in range(count)
		]

	def batches_of(self, user: str, department: str = GROWTHX) -> list:
		return frappe.get_all(
			"TBO Call Batch", filters={"user": user, "department": department}, pluck="name"
		)

	def leads_in(self, batch: str) -> list[str]:
		return [row.lead for row in frappe.get_doc("TBO Call Batch", batch).items]

	# ---------- saving and reading ----------

	def test_a_plan_is_saved_on_the_members_and_read_back(self):
		result = self.plan([BD, OTHER_BD], "phoebix", {"Day": 30})
		members = {row["user"]: row for row in result["members"]}
		self.assertEqual(members[BD]["product"], "PhoebiX")
		self.assertEqual(members[BD]["sizes"], {"Day": 30})
		self.assertEqual(members[OTHER_BD]["total"], 30)
		self.assertTrue(members[BD]["has_plan"])
		self.assertEqual([p["product_name"] for p in result["products"]], ["PhoebiX", "BillaX"])
		self.assertEqual(result["slots"][0]["label"], "Day")

	def test_clearing_a_plan_hands_the_department_defaults_again(self):
		self.plan([BD], "PhoebiX", {"Day": 5})
		members = {row["user"]: row for row in self.plan([BD], clear=1)["members"]}
		self.assertIsNone(members[BD]["product"])
		self.assertEqual(members[BD]["sizes"], {"Day": 50})
		self.assertFalse(members[BD]["has_plan"])

	def test_wrong_plans_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.plan([OUTSIDER], sizes={"Day": 5})
		with self.assertRaises(frappe.ValidationError):
			self.plan([BD], "Foamia")
		with self.assertRaises(frappe.ValidationError):
			self.plan([BD], sizes={"Night": 5})
		with self.assertRaises(frappe.ValidationError):
			self.plan([BD], sizes={"Day": 501})
		with self.assertRaises(frappe.ValidationError):
			self.plan([], sizes={"Day": 5})

	def test_the_campaign_product_is_the_only_product_a_plan_can_take(self):
		frappe.db.set_value("TBO Lead Department", GROWTHX, "campaign_product", "PhoebiX")
		with self.assertRaises(frappe.ValidationError):
			self.plan([BD], "BillaX")
		self.assertEqual(self.plan([BD], "PhoebiX")["members"][0]["product"], "PhoebiX")

	def test_saving_the_department_keeps_the_plans(self):
		self.plan([BD], "PhoebiX", {"Day": 7})
		departments.save_department({"members": [BD, OTHER_BD]}, GROWTHX)
		department = frappe.get_doc("TBO Lead Department", GROWTHX)
		self.assertEqual(plans.plan_product(department, BD), "PhoebiX")
		self.assertEqual(plans.sizes_of(plans.member_row(department, BD)), {"Day": 7})

	# ---------- who may plan ----------

	def test_a_head_plans_only_their_departments_and_others_may_not(self):
		frappe.set_user(HEAD)
		self.assertEqual([d["department"] for d in plans.get_call_plans()["departments"]], [GROWTHX])
		self.plan([BD], sizes={"Day": 5})
		with self.assertRaises(frappe.PermissionError):
			self.plan([BD], sizes={"Day": 5}, department=STAYVEX)
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			plans.get_call_plans()
		frappe.set_user(MANAGER)
		self.assertCountEqual(
			[d["department"] for d in plans.get_call_plans()["departments"]], [GROWTHX, STAYVEX]
		)

	def test_the_hub_endpoints_take_json(self):
		result = hub.save_call_plan(
			GROWTHX, frappe.as_json([BD]), "BillaX", frappe.as_json({"Day": 3}), acting_user=MANAGER
		)
		self.assertEqual({row["user"]: row["product"] for row in result["members"]}[BD], "BillaX")
		listed = {d["department"]: d for d in hub.get_call_plans(acting_user=MANAGER)["departments"]}
		self.assertIn(GROWTHX, listed)

	# ---------- what the batches do ----------

	def test_the_clock_hands_the_planned_number_of_the_planned_product(self):
		solar = self.unassigned(4, "PhoebiX")
		self.unassigned(3, "BillaX")
		self.plan([BD], "PhoebiX", {"Day": 3})
		clock.run_call_batch_clock()
		[batch] = self.batches_of(BD)
		leads = self.leads_in(batch)
		self.assertEqual(len(leads), 3)
		self.assertTrue(set(leads) <= set(solar))

	def test_a_slot_planned_at_zero_hands_out_nothing(self):
		self.unassigned(2)
		self.plan([BD], sizes={"Day": 0})
		clock.run_call_batch_clock()
		self.assertEqual(self.batches_of(BD), [])
		self.assertTrue(self.batches_of(OTHER_BD))

	def test_a_member_without_a_plan_is_handed_the_slot_size(self):
		self.unassigned(3)
		self.plan([BD], sizes={"Day": 1})
		clock.run_call_batch_clock()
		self.assertEqual(len(self.leads_in(self.batches_of(OTHER_BD)[0])), 2)

	def test_the_plan_wins_over_the_persons_focus(self):
		self.unassigned(2, "BillaX")
		make_callable_lead(BD, "Plan Hotel", STAYVEX)
		focus.set_focus(BD, STAYVEX)
		self.plan([BD], "BillaX", {"Day": 2})
		clock.run_call_batch_clock()
		self.assertEqual(len(self.batches_of(BD, GROWTHX)), 1)
		option = focus.option_of(frappe.get_doc("TBO Lead Department", GROWTHX), BD)
		self.assertEqual(option["locked_product"], "BillaX")
		with self.assertRaises(frappe.ValidationError):
			focus.set_focus(BD, GROWTHX, "PhoebiX")

	def test_the_days_cap_grows_to_the_plan(self):
		frappe.db.set_value("TBO Lead Department", GROWTHX, "daily_call_cap", 10)
		self.plan([BD], sizes={"Day": 40})
		department = frappe.get_doc("TBO Lead Department", GROWTHX)
		self.assertEqual(daily_cap(department, BD), 40)
		self.assertEqual(daily_cap(department, OTHER_BD), 10)

	def test_the_next_batch_follows_the_plan(self):
		solar = self.unassigned(3, "PhoebiX")
		self.unassigned(2, "BillaX")
		self.plan([BD], "PhoebiX", {"Day": 2})
		frappe.set_user(BD)
		result = create_next_batch(BD, CallQueue(BD, now_datetime()), GROWTHX, {"product": "BillaX"})
		frappe.set_user("Administrator")
		self.assertTrue(result["created"])
		self.assertEqual(result["size"], 2)
		self.assertTrue(set(self.leads_in(result["batch"])) <= set(solar))

	def test_a_slot_planned_at_zero_says_so_when_asked_for_a_batch(self):
		self.unassigned(1)
		self.plan([BD], sizes={"Day": 0})
		result = create_next_batch(BD, CallQueue(BD, now_datetime()), GROWTHX, {})
		self.assertFalse(result["created"])
		self.assertIn("no calls in the Day slot", result["reason"])

	def test_the_page_counts_what_is_left_to_pull_and_what_was_handed_today(self):
		self.unassigned(2, "PhoebiX")
		self.unassigned(1)
		self.plan([BD], sizes={"Day": 1})
		clock.run_call_batch_clock()
		department = {d["department"]: d for d in plans.get_call_plans()["departments"]}[GROWTHX]
		self.assertEqual(sum(department["unassigned"].values()), 0)
		members = {row["user"]: row for row in department["members"]}
		self.assertEqual(members[BD]["today"], {"size": 1, "called": 0})
		self.assertEqual(members[OTHER_BD]["today"]["size"], 2)

	# ---------- handing leads by hand (§12) ----------

	def owner_of(self, lead: str) -> str | None:
		return frappe.db.get_value("Lead", lead, "lead_owner")

	def open_assignees(self, lead: str) -> list[str]:
		return frappe.get_all(
			"ToDo",
			filters={"reference_type": "Lead", "reference_name": lead, "status": "Open"},
			pluck="allocated_to",
		)

	def test_a_members_leads_and_the_pool_are_listed_with_product_counts(self):
		mine = make_callable_lead(BD, "Plan Mine", GROWTHX, tbo_product="PhoebiX")
		pool = self.unassigned(2, "BillaX")
		page = handout.department_leads(GROWTHX, BD)
		self.assertEqual([row["name"] for row in page["rows"]], [mine])
		self.assertEqual(page["rows"][0]["product"], "PhoebiX")
		free = handout.department_leads(GROWTHX, "")
		self.assertCountEqual([row["name"] for row in free["rows"]], pool)
		self.assertEqual(free["products"], {"BillaX": 2})
		self.assertEqual(handout.department_leads(GROWTHX, "", product="PhoebiX")["total"], 0)
		self.assertEqual(handout.department_leads(GROWTHX, "", search="BillaX 1")["total"], 1)
		listed = {d["department"]: d for d in plans.get_call_plans()["departments"]}
		members = {row["user"]: row for row in listed[GROWTHX]["members"]}
		self.assertEqual(members[BD]["leads"], 1)

	def test_picked_leads_move_to_the_member_with_their_assignment(self):
		lead = make_callable_lead(OTHER_BD, "Plan Move", GROWTHX)
		make_call_batch(OTHER_BD, GROWTHX, [lead])
		result = handout.assign_leads(GROWTHX, BD, [lead, lead])
		self.assertEqual(result["assigned"], 1)
		self.assertEqual(self.owner_of(lead), BD)
		self.assertIn(BD, self.open_assignees(lead))
		self.assertNotIn(OTHER_BD, self.open_assignees(lead))
		self.assertFalse(
			frappe.db.exists("TBO Call Batch Item", {"lead": lead, "outcome": ("is", "not set")})
		)
		again = handout.assign_leads(GROWTHX, BD, [lead])
		self.assertEqual(again["skipped"][0]["name"], lead)

	def test_unassigning_clears_the_old_owners_overdue_calls_too(self):
		lead = make_callable_lead(BD, "Plan Overdue", GROWTHX)
		done = make_callable_lead(BD, "Plan Called", GROWTHX)
		make_call_batch(BD, GROWTHX, [lead, done], days_ago=1, status="Closed", outcomes={done: "Connected"})
		make_call_batch(BD, GROWTHX, [lead])
		handout.unassign_leads(GROWTHX, [lead])
		self.assertFalse(frappe.db.exists("TBO Call Batch Item", {"lead": lead}))
		self.assertTrue(frappe.db.exists("TBO Call Batch Item", {"lead": done, "outcome": "Connected"}))
		self.assertNotIn(lead, [row.lead for row in CallQueue(BD, now_datetime()).overdue])

	def test_the_next_unassigned_leads_of_a_product_are_given_by_number(self):
		solar = self.unassigned(3, "PhoebiX")
		self.unassigned(2, "BillaX")
		result = handout.assign_leads(GROWTHX, BD, count=2, product="phoebix")
		self.assertEqual(result["assigned"], 2)
		owned = [lead for lead in solar if self.owner_of(lead) == BD]
		self.assertEqual(len(owned), 2)

	def test_leads_go_back_to_the_pool(self):
		lead = make_callable_lead(BD, "Plan Back", GROWTHX)
		result = handout.unassign_leads(GROWTHX, [lead])
		self.assertEqual(result["unassigned"], 1)
		self.assertFalse(self.owner_of(lead))
		self.assertNotIn(BD, self.open_assignees(lead))
		self.assertEqual(handout.unassign_leads(GROWTHX, [lead])["skipped"][0]["name"], lead)

	def test_handing_out_is_refused_outside_the_department_and_its_planners(self):
		hotel = make_callable_lead(BD, "Plan Hotel Elsewhere", STAYVEX)
		with self.assertRaises(frappe.ValidationError):
			handout.assign_leads(GROWTHX, OUTSIDER, count=1)
		self.assertEqual(handout.assign_leads(GROWTHX, OTHER_BD, [hotel])["assigned"], 0)
		with self.assertRaises(frappe.ValidationError):
			handout.assign_leads(GROWTHX, BD, count=1, product="Foamia")
		frappe.set_user(HEAD)
		with self.assertRaises(frappe.PermissionError):
			handout.unassign_leads(STAYVEX, [hotel])
		frappe.set_user(OUTSIDER)
		with self.assertRaises(frappe.PermissionError):
			handout.department_leads(GROWTHX)
