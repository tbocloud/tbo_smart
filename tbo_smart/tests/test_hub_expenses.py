import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate

from tbo_smart.api import hub_expenses as hub
from tbo_smart.test_utils import make_expense_claim_type, make_staff, make_user

HUB = "exp.integration@tbosmart.test"
SELLER = "exp.seller@tbosmart.test"
APPROVER = "exp.approver@tbosmart.test"
NO_SALES = "exp.nosales@tbosmart.test"
RECEIPT = "cmVjZWlwdA=="  # "receipt"


class TestHubExpenses(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_staff(APPROVER, ["Expense Approver", "Sales Manager"])
		cls.seller = make_staff(SELLER, ["Sales User"], expense_approver=APPROVER)
		make_staff(NO_SALES)
		cls.expense_type = make_expense_claim_type()
		frappe.db.commit()  # nosemgrep

	def setUp(self):
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def claim(self, **kwargs) -> dict:
		values = {"receipt_filename": "bill.png", "receipt_base64": RECEIPT, **kwargs}
		return hub.create_expense_claim(
			self.expense_type, 480, str(getdate()), "Lunch", acting_user=SELLER, **values
		)

	def test_a_salesperson_claims_and_sees_their_own(self):
		claim = self.claim()
		self.assertEqual((claim["status"], claim["amount"]), ("Pending", 480))
		self.assertEqual((claim["approver_id"], claim["receipt"]), (APPROVER, "bill.png"))
		self.assertEqual(frappe.session.user, HUB)

		page = hub.get_expenses(acting_user=SELLER)
		self.assertTrue(page["employee"])
		self.assertFalse(page["can_approve"])
		self.assertIn(self.expense_type, page["types"])
		self.assertIn(claim["name"], [row["name"] for row in page["claims"]["data"]])
		self.assertEqual(page["approvals"], [])
		self.assertTrue(page["currency"])

	def test_the_approver_sees_the_claim_with_its_receipt_and_approves_it(self):
		claim = self.claim()
		page = hub.get_expenses(acting_user=APPROVER)
		self.assertTrue(page["can_approve"])
		waiting = {row["ref"]: row for row in page["approvals"]}
		self.assertEqual(waiting[claim["name"]]["receipt"], "bill.png")
		self.assertEqual(waiting[claim["name"]]["employee"], self.seller)

		hub.get_expense_receipt(claim["name"], acting_user=APPROVER)
		self.assertEqual(frappe.local.response.filecontent, b"receipt")
		self.assertEqual(frappe.local.response.content_type, "image/png")

		result = hub.decide_expense(claim["name"], 1, acting_user=APPROVER)
		self.assertEqual((result["status"], result["docstatus"]), ("Approved", 1))
		self.assertEqual(frappe.session.user, HUB)

	def test_a_rejection_needs_a_reason(self):
		claim = self.claim()
		with self.assertRaises(frappe.ValidationError):
			hub.decide_expense(claim["name"], 0, acting_user=APPROVER)
		result = hub.decide_expense(claim["name"], 0, "No bill for this", acting_user=APPROVER)
		self.assertEqual(result["status"], "Rejected")

	def test_only_pdfs_and_pictures_up_to_10_mb_are_receipts(self):
		with self.assertRaises(frappe.ValidationError):
			self.claim(receipt_filename="bill.html")
		with self.assertRaises(frappe.ValidationError):
			self.claim(receipt_base64="A" * (hub.MAX_FILE_BYTES // 3 * 4 + 8))

	def test_a_claim_without_a_receipt_serves_none(self):
		claim = self.claim(receipt_filename=None, receipt_base64=None)
		self.assertIsNone(claim["receipt"])
		with self.assertRaises(frappe.DoesNotExistError):
			hub.get_expense_receipt(claim["name"], acting_user=SELLER)

	def test_only_people_with_a_sales_role_are_acted_for(self):
		with self.assertRaises(frappe.PermissionError):
			hub.get_expenses(acting_user=NO_SALES)
