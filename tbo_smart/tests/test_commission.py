import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, get_first_day, getdate, today

from tbo_smart.api import hub_commission as hub
from tbo_smart.lead_pipeline import commission
from tbo_smart.test_utils import (
	delete_commission_records,
	delete_lead_departments,
	delete_lead_pipeline_records,
	make_commission_plan,
	make_commission_receipt,
	make_lead_department,
	make_management_user,
	make_project,
	make_project_commission,
	make_user,
)

HUB = "comm.integration@tbosmart.test"
BDE = "comm.bde@tbosmart.test"
BDE2 = "comm.bde2@tbosmart.test"
HEAD = "comm.head@tbosmart.test"
MANAGER = "comm.manager@tbosmart.test"
BOSS = "comm.management@tbosmart.test"
OWNERS = (BDE, BDE2, MANAGER)
DEPARTMENT = "TBO Commission Dept"
SLABS = [(0, 100000, 2), (100000, 250000, 3), (250000, None, 4)]


def this_month():
	return getdate(get_first_day(today()))


class TestSlabMaths(FrappeTestCase):
	def slabs(self):
		return commission.clean_slabs([{"from_amount": a, "to_amount": b, "rate": r} for a, b, r in SLABS])

	def test_progressive_slabs_without_a_target(self):
		result = commission.apply_slabs(300000, 0, self.slabs(), 6)
		self.assertEqual(result["commission"], 8500)
		self.assertEqual(result["reached"]["slab_no"], 3)

	def test_above_target_rate_replaces_a_lower_slab_rate(self):
		result = commission.apply_slabs(300000, 200000, self.slabs(), 6)
		self.assertEqual(result["commission"], 11000)
		self.assertEqual([row["above_target"] for row in result["rows"]], [0, 0, 1, 1])
		self.assertEqual(sum(row["amount"] for row in result["rows"]), 300000)

	def test_above_target_rate_never_lowers_a_rate(self):
		result = commission.apply_slabs(300000, 200000, self.slabs(), 1)
		self.assertEqual(result["commission"], 8500)

	def test_below_target_and_nothing_collected(self):
		self.assertEqual(commission.apply_slabs(50000, 200000, self.slabs(), 6)["commission"], 1000)
		for amount in (0, -500):
			self.assertEqual(commission.apply_slabs(amount, 0, self.slabs(), 6)["commission"], 0)

	def test_slabs_must_not_overlap_and_rates_stay_in_range(self):
		rows = [
			{"from_amount": 0, "to_amount": 100, "rate": 1},
			{"from_amount": 50, "to_amount": 200, "rate": 2},
		]
		self.assertRaises(frappe.ValidationError, commission.validate_scheme, rows, 0, 0)
		open_first = [{"from_amount": 0, "to_amount": None, "rate": 1}, {"from_amount": 50, "rate": 2}]
		self.assertRaises(frappe.ValidationError, commission.validate_scheme, open_first, 0, 0)
		self.assertRaises(
			frappe.ValidationError, commission.validate_scheme, [{"from_amount": 0, "rate": 101}], 0, 0
		)
		self.assertRaises(frappe.ValidationError, commission.validate_scheme, [], 120, 0)
		commission.validate_scheme(
			[{"from_amount": 0, "to_amount": 100, "rate": 1}, {"from_amount": 100, "rate": 2}], 5, 10
		)

	def test_the_most_specific_plan_wins(self):
		plans = [
			{"name": "A", "user": None, "department": "D", "lead_type": None, "project_type": None},
			{
				"name": "B",
				"user": None,
				"department": "D",
				"lead_type": "ERP / Software",
				"project_type": None,
			},
			{"name": "C", "user": "u@x", "department": None, "lead_type": None, "project_type": None},
			{"name": "D", "user": None, "department": None, "lead_type": None, "project_type": None},
		]
		match = commission.match_plan
		self.assertEqual(match(plans, "v@x", "D", "ERP / Software", None)["name"], "B")
		self.assertEqual(match(plans, "v@x", "D", "Content / Digital Marketing", None)["name"], "A")
		self.assertEqual(match(plans, "u@x", "D", "ERP / Software", None)["name"], "C")
		self.assertEqual(match(plans, "v@x", "Other", None, None)["name"], "D")
		self.assertIsNone(match(plans[:2], "v@x", "Other", None, None))


class TestCommission(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		for user in (BDE, BDE2, HEAD):
			make_user(user, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])
		make_management_user(BOSS)
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_commission_records()
		delete_lead_pipeline_records(OWNERS)
		delete_lead_departments([DEPARTMENT])
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_commission_records()
		delete_lead_pipeline_records(OWNERS)
		delete_lead_departments([DEPARTMENT])
		make_lead_department(DEPARTMENT, heads=[HEAD], members=[BDE, BDE2, MANAGER])
		self.project = make_project(f"TBO Commission A {frappe.generate_hash(length=6)}")
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def month(self, as_user=BOSS, **filters):
		return hub.get_commission(str(this_month()), acting_user=as_user, **filters)

	def person(self, user, as_user=BOSS):
		return next((r for r in self.month(as_user)["rows"] if r["user"] == user), None)

	def receipt(self, owner, amount, project=...):
		project = self.project if project is ... else project
		frappe.set_user("Administrator")
		found = make_commission_receipt(owner, amount, project, DEPARTMENT)
		frappe.set_user(HUB)
		return found

	def test_a_receipt_earns_its_owner_the_matching_plans_slabs(self):
		make_commission_plan(
			"Dept plan", SLABS, department=DEPARTMENT, monthly_target=200000, above_target_rate=6
		)
		self.receipt(BDE, 300000)
		row = self.person(BDE)
		self.assertEqual((row["collected"], row["commission"]), (300000, 11000))
		self.assertEqual(row["plans"], ["Dept plan"])
		self.assertEqual(row["achievement"], 150)
		[project] = row["projects"]
		self.assertEqual((project["project"], project["share_percent"]), (self.project, 100))
		self.assertEqual(len(row["payments"]), 1)
		self.assertTrue(row["breakdown"][-1]["above_target"])

	def test_a_user_plan_beats_a_department_plan(self):
		make_commission_plan("Dept plan", [(0, None, 1)], department=DEPARTMENT)
		make_commission_plan("Own plan", [(0, None, 5)], user=BDE)
		self.receipt(BDE, 10000)
		self.receipt(BDE2, 10000)
		self.assertEqual(self.person(BDE)["commission"], 500)
		self.assertEqual(self.person(BDE2)["commission"], 100)

	def test_a_project_can_split_its_commission_between_earners(self):
		make_commission_plan(
			"Dept plan", SLABS, department=DEPARTMENT, monthly_target=200000, above_target_rate=6
		)
		make_project_commission(self.project, [(BDE, 70), (HEAD, 30)])
		self.receipt(BDE2, 300000)
		self.assertIsNone(self.person(BDE2))
		self.assertEqual(self.person(BDE)["commission"], 7700)
		self.assertEqual(self.person(HEAD)["commission"], 3300)
		self.assertEqual(self.person(BDE)["projects"][0]["credited"], 210000)

	def test_earner_shares_must_add_up_to_100(self):
		self.assertRaises(
			frappe.ValidationError, make_project_commission, self.project, [(BDE, 70), (HEAD, 20)]
		)

	def test_a_project_can_use_its_own_slabs(self):
		make_commission_plan("Dept plan", [(0, None, 1)], department=DEPARTMENT)
		make_project_commission(
			self.project,
			use_override=1,
			override_slabs=[{"from_amount": 0, "rate": 10}],
		)
		self.receipt(BDE, 10000)
		self.assertEqual(self.person(BDE)["commission"], 1000)

	def test_all_collections_basis_pools_the_projects(self):
		other = make_project(f"TBO Commission B {frappe.generate_hash(length=6)}")
		make_commission_plan("Pool", SLABS, basis="Collected (all)")
		self.receipt(BDE, 60000)
		self.receipt(BDE, 90000, project=other)
		row = self.person(BDE)
		# 150,000 through the slabs once: 100,000 x 2% + 50,000 x 3%
		self.assertEqual(row["commission"], 3500)
		self.assertEqual(sum(r["commission"] for r in row["projects"]), 3500)

	def test_project_basis_runs_each_project_on_its_own(self):
		other = make_project(f"TBO Commission B {frappe.generate_hash(length=6)}")
		make_commission_plan("Each", SLABS)
		self.receipt(BDE, 60000)
		self.receipt(BDE, 90000, project=other)
		self.assertEqual(self.person(BDE)["commission"], 3000)

	def test_money_without_a_project_is_unattributed(self):
		make_commission_plan("Each", SLABS)
		before = (self.month()["unattributed"] or {}).get("amount", 0)
		self.receipt(BDE, 40000, project=None)
		result = self.month()
		self.assertEqual(result["unattributed"]["amount"] - before, 40000)
		self.assertIn(commission.NO_PROJECT, result["unattributed"]["reasons"])
		self.assertIsNone(self.person(BDE))
		self.assertIsNone(self.month(BDE)["unattributed"])

	def test_a_receipt_cancelled_later_counts_negatively_then(self):
		make_commission_plan("Each", [(0, None, 10)])
		_lead, entry = self.receipt(BDE, 10000)
		frappe.set_user("Administrator")
		entry.cancel()
		last_month = add_months(this_month(), -1)
		frappe.db.set_value(
			"Payment Entry", entry.name, "posting_date", add_months(today(), -1), update_modified=False
		)
		frappe.set_user(HUB)
		earlier = hub.get_commission(str(last_month), acting_user=BOSS)
		earned = next(r for r in earlier["rows"] if r["user"] == BDE)
		self.assertEqual(earned["collected"], 10000)
		self.assertEqual(self.person(BDE)["collected"], -10000)
		self.assertEqual(self.person(BDE)["commission"], 0)

	def test_statements_are_drafts_until_approved_and_then_frozen(self):
		make_commission_plan("Each", [(0, None, 10)])
		self.receipt(BDE, 10000)
		[made] = [r for r in hub.generate_statements(str(this_month()), acting_user=BOSS) if r["user"] == BDE]
		self.assertEqual((made["status"], made["commission"], made["skipped"]), ("Draft", 1000, 0))
		self.assertEqual(self.person(BDE)["status"], "Draft")
		hub.approve_statement(made["name"], acting_user=BOSS)
		doc = frappe.get_doc("TBO Commission Statement", made["name"])
		self.assertEqual((doc.status, doc.approved_by), ("Approved", BOSS))
		self.receipt(BDE, 10000)
		again = [r for r in hub.generate_statements(str(this_month()), acting_user=BOSS) if r["user"] == BDE]
		self.assertEqual(again[0]["skipped"], 1)
		row = self.person(BDE)
		self.assertEqual((row["commission"], row["frozen"]), (1000, 1))
		doc.reload()
		doc.commission = 5
		self.assertRaises(frappe.ValidationError, doc.save)
		self.assertRaises(frappe.ValidationError, hub.approve_statement, made["name"], acting_user=BOSS)
		hub.mark_paid(made["name"], acting_user=BOSS)
		self.assertEqual(self.person(BDE)["status"], "Paid")
		self.assertRaises(frappe.ValidationError, doc.delete)

	def test_a_draft_refreshes(self):
		make_commission_plan("Each", [(0, None, 10)])
		self.receipt(BDE, 10000)
		hub.generate_statements(str(this_month()), acting_user=BOSS)
		self.receipt(BDE, 5000)
		hub.generate_statements(str(this_month()), acting_user=BOSS)
		[doc] = frappe.get_all("TBO Commission Statement", filters={"user": BDE}, pluck="name")
		self.assertEqual(frappe.db.get_value("TBO Commission Statement", doc, "commission"), 1500)

	def test_nobody_approves_their_own_commission_but_a_system_manager(self):
		make_commission_plan("Each", [(0, None, 10)])
		self.receipt(MANAGER, 10000)
		[made] = [
			r for r in hub.generate_statements(str(this_month()), acting_user=MANAGER) if r["user"] == MANAGER
		]
		self.assertRaises(frappe.PermissionError, hub.approve_statement, made["name"], acting_user=MANAGER)
		self.assertRaises(frappe.PermissionError, hub.approve_statement, made["name"], acting_user=BDE)

	def test_who_sees_whose_commission(self):
		make_commission_plan("Each", [(0, None, 10)])
		self.receipt(BDE, 10000)
		self.receipt(BDE2, 20000)
		mine = self.month(BDE)
		self.assertEqual((mine["scope"], [r["user"] for r in mine["rows"]]), ("own", [BDE]))
		self.assertRaises(frappe.PermissionError, self.month, BDE, user=BDE2)
		head = self.month(HEAD)
		self.assertEqual(head["scope"], "department")
		self.assertEqual({BDE, BDE2} - {r["user"] for r in head["rows"]}, set())
		self.assertFalse(head["can_manage"])
		everyone = self.month(MANAGER)
		self.assertEqual(everyone["scope"], "all")
		self.assertEqual([r["user"] for r in self.month(BOSS, user=BDE2)["rows"]], [BDE2])
		self.assertRaises(
			frappe.PermissionError, hub.generate_statements, str(this_month()), acting_user=HEAD
		)

	def test_plans_are_saved_by_managers_only(self):
		values = {
			"plan_name": "Saved",
			"department": DEPARTMENT,
			"monthly_target": 1000,
			"slabs": [{"from_amount": 0, "rate": 2}],
		}
		self.assertRaises(frappe.PermissionError, hub.save_commission_plan, values, acting_user=BDE)
		self.assertRaises(frappe.PermissionError, hub.get_commission_plans, acting_user=HEAD)
		saved = hub.save_commission_plan(values, acting_user=BOSS)
		self.assertEqual(saved["slabs"], [{"from_amount": 0, "to_amount": None, "rate": 2}])
		updated = hub.save_commission_plan({"above_target_rate": 5}, name="Saved", acting_user=MANAGER)
		self.assertEqual(updated["above_target_rate"], 5)
		self.assertRaises(
			frappe.ValidationError,
			hub.save_commission_plan,
			{**values, "plan_name": "Twin"},
			acting_user=BOSS,
		)
		self.assertEqual([p["name"] for p in hub.get_commission_plans(acting_user=BOSS)["plans"]], ["Saved"])

	def test_project_set_up_round_trip_and_collected_so_far(self):
		make_commission_plan("Each", [(0, None, 10)])
		self.receipt(BDE, 10000)
		saved = hub.save_project_commission(
			{
				"project": self.project,
				"earners": [{"user": BDE, "share_percent": 60}, {"user": HEAD, "share_percent": 40}],
			},
			acting_user=BOSS,
		)
		self.assertEqual(saved["collected"], 10000)
		self.assertEqual([e["share_percent"] for e in saved["setup"]["earners"]], [60, 40])
		self.assertRaises(frappe.PermissionError, hub.get_project_commission, self.project, acting_user=BDE)
		self.assertIsNone(hub.get_project_commission("No such project", acting_user=BOSS)["setup"])
