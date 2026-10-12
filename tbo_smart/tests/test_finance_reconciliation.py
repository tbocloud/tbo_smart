import frappe
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from tbo_smart.api.finance import deal as deal_api
from tbo_smart.api.finance import exceptions as exceptions_api
from tbo_smart.api.finance import reconciliation as reconciliation_api
from tbo_smart.finance.reconciliation import flag_unmatched_lines
from tbo_smart.finance.stage import update_stage
from tbo_smart.test_utils import (
	delete_crm_records,
	enable_erpnext_crm,
	enable_reconciliation,
	make_bank_line,
	make_company_bank_account,
	make_deal_invoice,
	make_finance_deal,
	make_partner_company,
	make_user,
	match_bank_line,
	reset_finance_settings,
)

BDE = "rec.bde@tbosmart.test"
MAKER = "rec.maker@tbosmart.test"
CHECKER = "rec.checker@tbosmart.test"
CLERK = "rec.clerk@tbosmart.test"


class ReconciliationTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		enable_erpnext_crm()
		make_partner_company()
		cls.bank_account = make_company_bank_account()
		make_user(BDE, ["Sales User"])
		make_user(MAKER, ["Accounts User", "Accounts Manager", "TBO Finance Approver"])
		make_user(CHECKER, ["Accounts User", "Accounts Manager", "TBO Finance Approver"])
		make_user(CLERK, ["Accounts User"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_crm_records((BDE,))
		reset_finance_settings()
		enable_reconciliation()
		self.addCleanup(reset_finance_settings)
		self.addCleanup(frappe.set_user, "Administrator")

	def receipt(self, invoice: str, amount: float):
		entry = get_payment_entry(
			"Sales Invoice",
			invoice,
			party_amount=amount,
			bank_account=frappe.db.get_value("Bank Account", self.bank_account, "account"),
		)
		entry.reference_no = frappe.generate_hash(length=8)
		entry.reference_date = nowdate()
		entry.insert(ignore_permissions=True)
		entry.submit()
		return entry

	def reference(self) -> str:
		return frappe.generate_hash(length=10)


class TestBankLines(ReconciliationTestCase):
	def test_duplicate_lines_are_flagged_not_blocked(self):
		ref = self.reference()
		first = make_bank_line(self.bank_account, 1234.5, ref)
		second = make_bank_line(self.bank_account, 1234.5, ref)
		self.assertEqual(second.tbo_possible_duplicate_of, first.name)
		self.assertTrue(
			frappe.db.exists(
				"TBO Finance Exception",
				{"exception_type": "Possible duplicate", "reference_name": second.name},
			)
		)
		self.assertFalse(
			make_bank_line(self.bank_account, 1234.5, self.reference()).tbo_possible_duplicate_of
		)
		frappe.db.set_single_value("TBO Smart Settings", "enable_reconciliation", 0)
		self.assertFalse(make_bank_line(self.bank_account, 1234.5, ref).tbo_dedupe_key)

	def test_match_verify_and_lock(self):
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, rate=500)
		entry = self.receipt(invoice.name, 500)
		line = make_bank_line(self.bank_account, 500, self.reference())
		frappe.set_user(MAKER)
		match_bank_line(line.name, entry.name, 500)
		line.reload()
		self.assertEqual(
			(line.status, line.tbo_verification, line.tbo_matched_by), ("Reconciled", "Unverified", MAKER)
		)
		with self.assertRaises(frappe.PermissionError):
			reconciliation_api.verify_bank_line(line.name)
		frappe.set_user(CLERK)
		with self.assertRaises(frappe.PermissionError):
			reconciliation_api.verify_bank_line(line.name)
		frappe.set_user(CHECKER)
		self.assertIn(line.name, [row.name for row in reconciliation_api.get_bank_queue()["waiting"]])
		reconciliation_api.verify_bank_line(line.name)
		line.reload()
		self.assertEqual((line.tbo_verification, line.tbo_verified_by), ("Verified", CHECKER))
		frappe.set_user("Administrator")
		line.reload()
		with self.assertRaises(frappe.ValidationError):
			line.remove_payment_entries()

	def test_amount_mismatch(self):
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, rate=34500)
		entry = self.receipt(invoice.name, 34500)
		line = make_bank_line(self.bank_account, 34450, self.reference())
		match_bank_line(line.name, entry.name, 34450)
		exception = frappe.get_all(
			"TBO Finance Exception",
			filters={"exception_type": "Amount mismatch", "reference_name": line.name},
			fields=["expected_amount", "actual_amount", "difference"],
		)
		self.assertEqual(len(exception), 1)
		self.assertEqual(exception[0].difference, -50)

	def test_unmatched_lines_age_into_one_exception(self):
		line = make_bank_line(self.bank_account, 77, self.reference(), days_ago=5)
		flag_unmatched_lines()
		flag_unmatched_lines()
		self.assertEqual(
			frappe.db.count(
				"TBO Finance Exception",
				{"exception_type": "Unmatched bank line", "reference_name": line.name},
			),
			1,
		)


class TestSecondApproval(ReconciliationTestCase):
	def test_two_approvers_above_the_amount(self):
		enable_reconciliation(enable_finance_receivables=1, second_approval_amount=100)
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, rate=500, due_in=-5)
		frappe.set_user(BDE)
		exception = exceptions_api.request_exception(
			invoice.name, "Pause request", "Holiday", pause_until=nowdate()
		)
		frappe.set_user(MAKER)
		first = exceptions_api.decide_exception(exception["name"], "approve")
		self.assertEqual((first["status"], first["first_approved_by"]), ("Pending Approval", MAKER))
		with self.assertRaises(frappe.PermissionError):
			exceptions_api.decide_exception(exception["name"], "approve")
		frappe.set_user(CHECKER)
		self.assertEqual(exceptions_api.decide_exception(exception["name"], "approve")["status"], "Approved")


class TestReconciledStage(ReconciliationTestCase):
	def test_stage_runs_to_reconciled_and_closes(self):
		enable_reconciliation(finance_crm_access=1)
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, rate=800)
		entry = self.receipt(invoice.name, 800)
		self.assertEqual(update_stage(deal), "Collected")
		frappe.set_user(CHECKER)
		with self.assertRaises(frappe.ValidationError):
			deal_api.set_finance_closed(deal)
		frappe.set_user("Administrator")
		line = make_bank_line(self.bank_account, 800, self.reference())
		frappe.set_user(MAKER)
		match_bank_line(line.name, entry.name, 800)
		frappe.set_user(CHECKER)
		reconciliation_api.verify_bank_line(line.name)
		frappe.set_user("Administrator")
		self.assertEqual(update_stage(deal), "Reconciled")
		frappe.set_user(CHECKER)
		self.assertEqual(deal_api.set_finance_closed(deal)["stage"], "Closed")

	def test_open_exception_puts_the_deal_on_exception(self):
		enable_reconciliation(enable_finance_receivables=1)
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, rate=500, due_in=-5)
		frappe.set_user(BDE)
		exceptions_api.request_exception(invoice.name, "Write-off request", "Goodwill")
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("CRM Deal", deal, "tbo_stage"), "On Exception")

	def test_bank_queue_is_for_accounts_only(self):
		frappe.set_user(BDE)
		with self.assertRaises(frappe.PermissionError):
			reconciliation_api.get_bank_queue()
