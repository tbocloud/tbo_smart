from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api.mobile import ess, manager
from tbo_smart.test_utils import (
	capabilities_as,
	delete_advance_workflow,
	ensure_employee_advance_account,
	make_advance_workflow,
	make_employee_advance,
	make_staff,
	make_user,
)

STAFF = "adv.staff@tbosmart.test"
OTHER = "adv.other@tbosmart.test"
APPROVER = "adv.approver@tbosmart.test"
IDLE_APPROVER = "adv.idle@tbosmart.test"
HR_USER = "adv.hr.office@tbosmart.test"


class TestMobileAdvance(FrappeTestCase):
	def setUp(self):
		delete_advance_workflow()
		ensure_employee_advance_account()
		self.approver = make_staff(APPROVER, ["Expense Approver"])
		self.staff = make_staff(STAFF, expense_approver=APPROVER)
		self.other = make_staff(OTHER)
		make_staff(IDLE_APPROVER, ["Expense Approver"])
		# No Employee, so no User Permission narrows what HR reads.
		make_user(HR_USER, ["HR User", "Expense Approver"])
		frappe.set_user(STAFF)

	def tearDown(self):
		frappe.set_user("Administrator")

	# ---------- Employee side ----------

	def test_create_defaults_currency_and_advance_account(self):
		advance = ess.create_advance("Site visit to Galom, Kakkanad", 2500)
		company = frappe.db.get_value("Employee", self.staff, "company")
		saved = frappe.db.get_value(
			"Employee Advance",
			advance["name"],
			["employee", "currency", "exchange_rate", "advance_account", "docstatus"],
			as_dict=True,
		)
		self.assertEqual(saved.employee, self.staff)
		self.assertEqual(saved.currency, frappe.get_cached_value("Company", company, "default_currency"))
		self.assertEqual(saved.exchange_rate, 1)
		self.assertEqual(
			saved.advance_account,
			frappe.db.get_value("Company", company, "default_employee_advance_account"),
		)
		self.assertEqual(saved.docstatus, 0)
		self.assertEqual(advance["status"], "Draft")
		self.assertEqual(advance["pending_amount"], 2500)
		self.assertEqual(advance["balance_amount"], 0)

	def test_amount_must_be_positive_and_purpose_given(self):
		for amount in (0, -10):
			with self.assertRaises(frappe.ValidationError):
				ess.create_advance("Travel", amount)
		with self.assertRaises(frappe.ValidationError):
			ess.create_advance("   ", 100)

	def test_advance_account_must_belong_to_the_company(self):
		with self.assertRaises(frappe.ValidationError):
			ess.create_advance("Travel", 100, advance_account="Not An Account - XX")

	def test_list_shows_own_advances_only(self):
		mine = ess.create_advance("Hotel deposit", 1200)["name"]
		theirs = make_employee_advance(self.other, 900)
		names = {row["name"] for row in ess.get_advances(page_length=100)["data"]}
		self.assertIn(mine, names)
		self.assertNotIn(theirs, names)

	def test_list_reports_hrms_amounts(self):
		name = make_employee_advance(self.staff, 1000, submit=True)
		frappe.db.set_value(
			"Employee Advance", name, {"paid_amount": 1000, "claimed_amount": 600, "return_amount": 100}
		)
		row = next(row for row in ess.get_advances(page_length=100)["data"] if row["name"] == name)
		self.assertEqual(row["status"], "Unpaid")
		self.assertEqual(row["docstatus"], 1)
		self.assertEqual(row["paid_amount"], 1000)
		self.assertEqual(row["pending_amount"], 300)
		self.assertEqual(row["balance_amount"], 300)

	def test_cancel_withdraws_own_draft(self):
		name = ess.create_advance("Conference fee", 800)["name"]
		result = ess.cancel_advance(name)
		self.assertEqual(result, {"name": name, "status": "Cancelled", "withdrawn": True})
		self.assertFalse(frappe.db.exists("Employee Advance", name))

	def test_cancel_refuses_someone_elses_advance(self):
		theirs = make_employee_advance(self.other, 500)
		with self.assertRaises(frappe.PermissionError):
			ess.cancel_advance(theirs)
		self.assertTrue(frappe.db.exists("Employee Advance", theirs))

	def test_cancel_needs_the_advances_capability(self):
		name = ess.create_advance("Taxi to the airport", 400)["name"]
		with patch("tbo_smart.api.mobile.capabilities.can_request_advance", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				ess.cancel_advance(name)
		self.assertTrue(frappe.db.exists("Employee Advance", name))

	def test_cancel_refuses_a_paid_advance(self):
		name = make_employee_advance(self.staff, 500, submit=True)
		frappe.db.set_value("Employee Advance", name, "paid_amount", 500)
		with self.assertRaises(frappe.ValidationError):
			ess.cancel_advance(name)

	# ---------- Capabilities ----------

	def test_capability_flags(self):
		staff = capabilities_as(STAFF)
		self.assertTrue(staff.advances)
		self.assertFalse(staff.approve_advance)
		self.assertTrue(capabilities_as(APPROVER).approve_advance)
		self.assertTrue(capabilities_as(HR_USER).approve_advance)
		# The Expense Approver role alone, with nobody to approve, is not enough.
		self.assertFalse(capabilities_as(IDLE_APPROVER).approve_advance)
		self.assertFalse(capabilities_as(make_user("adv.nobody@tbosmart.test")).advances)

	# ---------- Approvals ----------

	def test_approvals_refuse_non_approvers(self):
		with self.assertRaises(frappe.PermissionError):
			manager.get_approvals()
		name = make_employee_advance(self.other, 300)
		with self.assertRaises(frappe.PermissionError):
			manager.decide_approval("advance", name, True)

	def test_named_expense_approver_sees_and_approves_by_submitting(self):
		mine = ess.create_advance("Client workshop travel", 4000)["name"]
		theirs = make_employee_advance(self.other, 700)
		frappe.set_user(APPROVER)
		waiting = {row["ref"]: row for row in manager.get_approvals() if row["kind"] == "advance"}
		self.assertIn(mine, waiting)
		self.assertNotIn(theirs, waiting)
		self.assertEqual(waiting[mine]["amount"], 4000)
		self.assertFalse(waiting[mine]["can_reject"])
		# Routed by sharing: the approver could not read a subordinate's advance otherwise.
		self.assertTrue(frappe.has_permission("Employee Advance", "submit", doc=mine, user=APPROVER))

		result = manager.decide_approval("advance", mine, True, "Go ahead")
		self.assertEqual((result["status"], result["docstatus"]), ("Unpaid", 1))
		with self.assertRaises(frappe.PermissionError):
			manager.decide_approval("advance", theirs, True)

	def test_rejection_needs_a_workflow(self):
		name = ess.create_advance("Laptop repair", 600)["name"]
		frappe.set_user(APPROVER)
		with self.assertRaises(frappe.ValidationError):
			manager.decide_approval("advance", name, False, "Not in budget")
		self.assertEqual(frappe.db.get_value("Employee Advance", name, "docstatus"), 0)

	def test_hr_user_sees_every_draft(self):
		theirs = make_employee_advance(self.other, 450)
		frappe.set_user(HR_USER)
		self.assertIn(theirs, {row["ref"] for row in manager.get_approvals()})

	def test_approver_cannot_decide_own_advance(self):
		frappe.set_user(APPROVER)
		own = ess.create_advance("Own trip", 100)["name"]
		with self.assertRaises(frappe.PermissionError):
			manager.decide_approval("advance", own, True)

	def test_workflow_approves_and_rejects(self):
		frappe.set_user("Administrator")
		make_advance_workflow()
		self.addCleanup(delete_advance_workflow)
		frappe.set_user(STAFF)
		approve = ess.create_advance("Workflow approve", 1000)["name"]
		reject = ess.create_advance("Workflow reject", 1000)["name"]
		self.assertTrue(capabilities_as(APPROVER).approve_advance)
		self.assertFalse(capabilities_as(STAFF).approve_advance)

		frappe.set_user(APPROVER)
		waiting = {row["ref"]: row for row in manager.get_approvals() if row["kind"] == "advance"}
		self.assertTrue(waiting[approve]["can_reject"])
		self.assertEqual(manager.decide_approval("advance", approve, True)["docstatus"], 1)
		manager.decide_approval("advance", reject, False, "Use the corporate card")
		self.assertEqual(frappe.db.get_value("Employee Advance", reject, "workflow_state"), "Rejected")
