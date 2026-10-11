import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api.mobile import crm, ess, manager
from tbo_smart.api.mobile.capabilities import get_capabilities, get_my_capabilities
from tbo_smart.test_utils import (
	capabilities_as,
	disable_sales_hierarchy,
	enable_sales_hierarchy,
	make_crm_lead,
	make_sales_hierarchy_node,
	make_staff,
	make_user,
	set_hr_settings,
)

PLAIN = "cap.plain@tbosmart.test"
LEAVE_APPROVER = "cap.leave@tbosmart.test"
EXPENSE_APPROVER = "cap.expense@tbosmart.test"
SALES_USER = "cap.seller@tbosmart.test"
SALES_MANAGER = "cap.salesmgr@tbosmart.test"
HR_MANAGER = "cap.hr@tbosmart.test"
NO_EMPLOYEE = "cap.noemployee@tbosmart.test"
TEAM_LEAD = "cap.teamlead@tbosmart.test"
TEAM_REP = "cap.teamrep@tbosmart.test"


class TestMobileCapabilities(FrappeTestCase):
	def setUp(self):
		set_hr_settings(allow_employee_checkin_from_mobile_app=1, allow_geolocation_tracking=0)
		make_user(LEAVE_APPROVER, ["Leave Approver"])
		make_user(EXPENSE_APPROVER, ["Expense Approver"])
		self.plain = make_staff(PLAIN, leave_approver=LEAVE_APPROVER, expense_approver=EXPENSE_APPROVER)
		make_staff(SALES_USER, ["Sales User"])
		make_staff(SALES_MANAGER, ["Sales Manager"])
		make_staff(HR_MANAGER, ["HR Manager"])
		make_user(NO_EMPLOYEE, ["Sales User"])

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_plain_employee(self):
		caps = capabilities_as(PLAIN)
		self.assertTrue(caps.ess)
		self.assertTrue(caps.checkin_allowed)
		self.assertFalse(caps.geolocation_required)
		for flag in (
			"approve_leave",
			"approve_expense",
			"team_today",
			"team_insights",
			"crm",
			"crm_manager",
			"sales_insights",
		):
			self.assertFalse(caps[flag], flag)
		self.assertIsNone(caps.crm_scope)
		self.assertEqual(caps.crm_scopes, [])

	def test_settings_drive_checkin_and_geolocation(self):
		set_hr_settings(allow_geolocation_tracking=1)
		self.assertTrue(capabilities_as(PLAIN).geolocation_required)
		set_hr_settings(allow_employee_checkin_from_mobile_app=0)
		caps = capabilities_as(PLAIN)
		self.assertFalse(caps.checkin_allowed)
		self.assertFalse(caps.geolocation_required)

	def test_named_leave_approver_approves_leave_only(self):
		caps = capabilities_as(LEAVE_APPROVER)
		self.assertTrue(caps.approve_leave)
		self.assertFalse(caps.approve_expense)
		self.assertFalse(caps.ess)

	def test_named_expense_approver_approves_expense_only(self):
		caps = capabilities_as(EXPENSE_APPROVER)
		self.assertTrue(caps.approve_expense)
		self.assertFalse(caps.approve_leave)

	def test_approver_role_without_anyone_to_approve_is_not_an_approver(self):
		make_user("cap.roleonly@tbosmart.test", ["Leave Approver", "Expense Approver"])
		caps = capabilities_as("cap.roleonly@tbosmart.test")
		self.assertFalse(caps.approve_leave)
		self.assertFalse(caps.approve_expense)
		with self.assertRaises(frappe.PermissionError):
			manager.get_approvals()

	def test_sales_user_is_crm_own(self):
		caps = capabilities_as(SALES_USER)
		self.assertTrue(caps.crm)
		self.assertFalse(caps.crm_manager)
		self.assertFalse(caps.sales_insights)
		self.assertEqual((caps.crm_scope, caps.crm_scopes), ("own", ["mine"]))

	def test_sales_manager_outside_the_hierarchy_sees_all(self):
		caps = capabilities_as(SALES_MANAGER)
		self.assertTrue(caps.crm_manager)
		self.assertTrue(caps.sales_insights)
		self.assertEqual((caps.crm_scope, caps.crm_scopes), ("all", ["mine", "all"]))

	def test_hr_manager_approves_and_sees_company_insights(self):
		caps = capabilities_as(HR_MANAGER)
		self.assertTrue(caps.approve_leave)
		self.assertTrue(caps.approve_expense)
		self.assertTrue(caps.team_insights)
		self.assertFalse(caps.team_today)
		self.assertEqual(caps.team_scope, "company")
		self.assertFalse(caps.crm)

	def test_manager_with_direct_reports(self):
		lead = make_staff("cap.manager@tbosmart.test")
		make_staff("cap.report@tbosmart.test", reports_to=lead)
		caps = capabilities_as("cap.manager@tbosmart.test")
		self.assertTrue(caps.team_today)
		self.assertTrue(caps.team_insights)
		self.assertEqual(caps.team_scope, "direct_reports")
		self.assertTrue(ess.get_me()["is_manager"])

	def test_user_without_employee_keeps_crm(self):
		frappe.set_user(NO_EMPLOYEE)
		caps = get_my_capabilities()
		self.assertFalse(caps.ess)
		self.assertFalse(caps.checkin_allowed)
		self.assertFalse(caps.team_insights)
		self.assertTrue(caps.crm)

	def test_get_me_carries_the_same_capabilities(self):
		frappe.set_user(SALES_MANAGER)
		me = ess.get_me()
		self.assertEqual(me["capabilities"], get_capabilities())
		self.assertTrue(me["is_sales_manager"])
		self.assertFalse(me["is_approver"])

	def test_crm_endpoints_refuse_users_without_crm(self):
		frappe.set_user(PLAIN)
		for call in (crm.get_options, crm.get_leads, crm.get_deals, crm.get_my_follow_ups):
			with self.assertRaises(frappe.PermissionError):
				call()


class TestMobileCrmScope(FrappeTestCase):
	def setUp(self):
		make_user(SALES_USER, ["Sales User"])
		make_user(SALES_MANAGER, ["Sales Manager"])
		self.seller_lead = make_crm_lead(SALES_USER, "Scope Seller Lead")
		self.manager_lead = make_crm_lead(SALES_MANAGER, "Scope Manager Lead")

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_sales_hierarchy((TEAM_LEAD, TEAM_REP))

	def test_staff_are_always_mine(self):
		frappe.set_user(SALES_USER)
		for scope in ("team", "all"):
			with self.assertRaises(frappe.PermissionError):
				crm.get_leads(scope=scope)
			with self.assertRaises(frappe.PermissionError):
				crm.get_deals(scope=scope)
			with self.assertRaises(frappe.PermissionError):
				crm.get_my_follow_ups(scope=scope)
		names = {row["name"] for row in crm.get_leads(scope="mine", page_length=100)["data"]}
		self.assertIn(self.seller_lead, names)
		self.assertNotIn(self.manager_lead, names)

	def test_manager_switches_between_mine_and_all(self):
		frappe.set_user(SALES_MANAGER)
		mine = {row["name"] for row in crm.get_leads(scope="mine", page_length=100)["data"]}
		self.assertIn(self.manager_lead, mine)
		self.assertNotIn(self.seller_lead, mine)
		everything = {row["name"] for row in crm.get_leads(scope="all", page_length=100)["data"]}
		self.assertTrue({self.manager_lead, self.seller_lead} <= everything)
		with self.assertRaises(frappe.PermissionError):
			crm.get_leads(scope="team")
		with self.assertRaises(frappe.PermissionError):
			crm.get_leads(scope="everyone")

	def test_sales_hierarchy_gives_a_team_scope(self):
		make_user(TEAM_LEAD, ["Sales Manager"])
		make_user(TEAM_REP, ["Sales User"])
		enable_sales_hierarchy()
		make_sales_hierarchy_node(TEAM_REP, reports_to=make_sales_hierarchy_node(TEAM_LEAD))
		lead_own = make_crm_lead(TEAM_LEAD, "Team Lead Own")
		rep_lead = make_crm_lead(TEAM_REP, "Team Rep Lead")

		caps = capabilities_as(TEAM_LEAD)
		self.assertEqual((caps.crm_scope, caps.crm_scopes), ("team", ["mine", "team"]))
		with self.assertRaises(frappe.PermissionError):
			crm.get_leads(scope="all")
		team = {row["name"] for row in crm.get_leads(scope="team", page_length=100)["data"]}
		self.assertTrue({lead_own, rep_lead} <= team)
		self.assertNotIn(self.seller_lead, team)
		mine = {row["name"] for row in crm.get_leads(scope="mine", page_length=100)["data"]}
		self.assertIn(lead_own, mine)
		self.assertNotIn(rep_lead, mine)

		self.assertEqual(capabilities_as(TEAM_REP).crm_scope, "own")
