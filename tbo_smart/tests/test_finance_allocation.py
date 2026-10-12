import erpnext
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, nowdate

from tbo_smart.api.finance import allocations as allocations_api
from tbo_smart.api.finance import deal as deal_api
from tbo_smart.finance.allocation import money
from tbo_smart.finance.transfers import flag_pending_transfers
from tbo_smart.test_utils import (
	delete_crm_records,
	enable_erpnext_crm,
	enable_fund_allocation,
	get_allocation,
	get_vat_account,
	make_deal_invoice,
	make_finance_deal,
	make_internal_payment,
	make_invoice_with_advances,
	make_partner_company,
	make_receipt,
	make_user,
	make_vat_order,
	reset_finance_settings,
)

BDE = "alc.bde@tbosmart.test"
ACCOUNTS = "alc.accounts@tbosmart.test"


class AllocationTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		enable_erpnext_crm()
		make_partner_company()
		get_vat_account()
		make_user(BDE, ["Sales User"])
		make_user(ACCOUNTS, ["Accounts User", "Accounts Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_crm_records((BDE,))
		reset_finance_settings()
		enable_fund_allocation()
		self.addCleanup(reset_finance_settings)
		self.addCleanup(frappe.set_user, "Administrator")
		self.company = erpnext.get_default_company()

	def shares(self, *entries) -> list[float]:
		return [flt(get_allocation(entry.name).share_amount) for entry in entries]


class TestAllocationFormula(AllocationTestCase):
	def test_example_a_advance_partial_balance(self):
		deal = make_finance_deal(BDE)
		order = make_vat_order(deal, 100000)
		self.assertEqual(order.grand_total, 115000)
		advance = make_receipt("Sales Order", order.name, 34500)
		self.assertEqual((get_allocation(advance.name).net_amount, self.shares(advance)[0]), (30000, 15000))

		invoice = make_invoice_with_advances(order.name)
		partial = make_receipt("Sales Invoice", invoice.name, 40250)
		closing = make_receipt("Sales Invoice", invoice.name)
		self.assertEqual(flt(closing.paid_amount), 40250)
		self.assertEqual(self.shares(advance, partial, closing), [15000, 17500, 17500])
		self.assertTrue(get_allocation(closing.name).lines[0].is_closing_receipt)
		self.assertEqual(get_allocation(advance.name).crm_deal, deal)

	def test_example_b_rounding(self):
		deal = make_finance_deal(BDE)
		order = make_vat_order(deal, 33333.33)
		invoice = make_invoice_with_advances(order.name)
		self.assertEqual((invoice.net_total, invoice.grand_total), (33333.33, 38333.33))
		first = make_receipt("Sales Invoice", invoice.name, 10000)
		second = make_receipt("Sales Invoice", invoice.name)
		self.assertEqual(get_allocation(first.name).net_amount, 8695.65)
		self.assertEqual(self.shares(first, second), [4347.83, 12318.84])
		self.assertEqual(round(sum(self.shares(first, second)), 2), 16666.67)

	def test_money_rounds_half_up(self):
		self.assertEqual(money(4347.825), 4347.83)
		self.assertEqual(money(0.125), 0.13)

	def test_outside_the_allocation(self):
		deal = make_finance_deal(BDE)
		invoice = make_deal_invoice(deal, rate=1000)
		frappe.db.set_single_value("TBO Smart Settings", "allocation_start_date", add_days(nowdate(), 1))
		self.assertIsNone(get_allocation(make_receipt("Sales Invoice", invoice.name, 100).name))
		frappe.db.set_single_value(
			"TBO Smart Settings", {"allocation_start_date": nowdate(), "enable_fund_allocation": 0}
		)
		self.assertIsNone(get_allocation(make_receipt("Sales Invoice", invoice.name, 100).name))
		enable_fund_allocation(invoicing_company=make_partner_company())
		self.assertIsNone(get_allocation(make_receipt("Sales Invoice", invoice.name, 100).name))

	def test_unallocated_receipt_uses_the_deal_ratio(self):
		deal = make_finance_deal(BDE)
		make_vat_order(deal, 1000)
		entry = make_receipt(
			"Sales Order",
			frappe.get_all("Sales Order", {"crm_deal": deal}, pluck="name")[0],
			1150,
			submit=False,
		)
		entry.set("references", [])
		entry.unallocated_amount = 1150
		entry.crm_deal = deal
		entry.submit()
		[line] = get_allocation(entry.name).lines
		self.assertEqual(
			(line.reference_doctype, line.net_amount, line.share_amount), ("Unallocated", 1000, 500)
		)


class TestReversals(AllocationTestCase):
	def test_cancel_before_and_after_transfer(self):
		deal = make_finance_deal(BDE)
		invoice = make_invoice_with_advances(make_vat_order(deal, 1000).name)
		first = make_receipt("Sales Invoice", invoice.name, 230)
		first.cancel()
		self.assertIsNone(get_allocation(first.name))
		self.assertEqual(
			frappe.db.get_value("TBO Fund Allocation", {"payment_entry": first.name}, "docstatus"), 2
		)

		second = make_receipt("Sales Invoice", invoice.name, 230)
		allocation = get_allocation(second.name)
		sent = make_internal_payment(self.company, 100)
		allocations_api.create_transfer(
			sent.name, nowdate(), [{"fund_allocation": allocation.name, "allocated_amount": 100}]
		)
		due_before = allocations_api.get_allocation_summary()["totals"][0]["due_from_partner"]
		second.reload()
		second.cancel()
		reversal = get_allocation(second.name, "Reversal")
		self.assertEqual((reversal.share_amount, reversal.reversal_of), (-100, allocation.name))
		self.assertEqual(frappe.db.get_value("TBO Fund Allocation", allocation.name, "status"), "Reversed")
		self.assertTrue(
			frappe.db.exists(
				"TBO Finance Exception",
				{"exception_type": "Receipt cancelled after transfer", "reference_name": reversal.name},
			)
		)
		summary = allocations_api.get_allocation_summary()
		self.assertEqual(summary["totals"][0]["due_from_partner"] - due_before, 100)

	def test_refund_reverses_its_share(self):
		deal = make_finance_deal(BDE)
		order = make_vat_order(deal, 10000)
		make_receipt("Sales Order", order.name, 11500)
		refund = self.make_refund(order.customer, deal, 1150)
		reversal = get_allocation(refund.name, "Reversal")
		self.assertEqual(reversal.share_amount, -500)

	def make_refund(self, customer: str, deal: str, amount: float):
		from erpnext.accounts.party import get_party_account

		entry = frappe.get_doc(
			{
				"doctype": "Payment Entry",
				"payment_type": "Pay",
				"company": self.company,
				"posting_date": nowdate(),
				"party_type": "Customer",
				"party": customer,
				"paid_from": frappe.get_cached_value("Company", self.company, "default_cash_account"),
				"paid_to": get_party_account("Customer", customer, self.company),
				"paid_amount": amount,
				"received_amount": amount,
				"source_exchange_rate": 1,
				"target_exchange_rate": 1,
				"crm_deal": deal,
			}
		)
		entry.insert(ignore_permissions=True)
		entry.submit()
		return entry

	def test_receipt_by_journal_entry_raises_an_exception(self):
		deal = make_finance_deal(BDE)
		customer = frappe.db.get_value("CRM Deal", deal, "erpnext_customer")
		from erpnext.accounts.party import get_party_account

		entry = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"company": self.company,
				"posting_date": nowdate(),
				"accounts": [
					{
						"account": frappe.get_cached_value("Company", self.company, "default_cash_account"),
						"debit_in_account_currency": 500,
					},
					{
						"account": get_party_account("Customer", customer, self.company),
						"party_type": "Customer",
						"party": customer,
						"credit_in_account_currency": 500,
					},
				],
			}
		)
		entry.insert(ignore_permissions=True)
		entry.submit()
		self.assertTrue(
			frappe.db.exists(
				"TBO Finance Exception",
				{"exception_type": "Receipt via Journal Entry", "reference_name": entry.name},
			)
		)


class TestTransfers(AllocationTestCase):
	def make_allocations(self):
		deal = make_finance_deal(BDE)
		invoice = make_invoice_with_advances(make_vat_order(deal, 100000).name)
		first = get_allocation(make_receipt("Sales Invoice", invoice.name, 34500).name)
		second = get_allocation(make_receipt("Sales Invoice", invoice.name, 40250).name)
		return deal, first, second

	def test_transfer_settles_fifo_and_india_confirms(self):
		deal, first, second = self.make_allocations()
		form = allocations_api.get_transfer_form()
		names = [row.name for row in form["allocations"]]
		self.assertLess(names.index(first.name), names.index(second.name))
		sent = make_internal_payment(self.company, 32500)
		result = allocations_api.create_transfer(
			sent.name,
			nowdate(),
			[
				{"fund_allocation": first.name, "allocated_amount": 15000},
				{"fund_allocation": second.name, "allocated_amount": 17500},
			],
			reference_no="SWIFT123",
		)
		self.assertEqual((result["status"], result["difference"]), ("Sent", 0))
		self.assertEqual(
			frappe.db.get_value("TBO Fund Allocation", first.name, ["status", "outstanding_amount"]),
			("Settled", 0),
		)
		received = make_internal_payment(make_partner_company(), 760500, "Receive")
		confirmed = allocations_api.confirm_receipt(result["name"], received.name)
		self.assertEqual(confirmed["status"], "Received")
		self.assertEqual(confirmed["effective_rate"], 23.4)
		frappe.set_user(ACCOUNTS)
		self.assertEqual(deal_api.get_deal_finance(deal)["allocation"]["pending"], 0)

	def test_short_transfer_and_over_settlement(self):
		_deal, first, second = self.make_allocations()
		sent = make_internal_payment(self.company, 32400)
		result = allocations_api.create_transfer(
			sent.name,
			nowdate(),
			[
				{"fund_allocation": first.name, "allocated_amount": 15000},
				{"fund_allocation": second.name, "allocated_amount": 17500},
			],
		)
		self.assertEqual((result["difference"], result["status"]), (-100, "Difference"))
		self.assertTrue(
			frappe.db.exists(
				"TBO Finance Exception",
				{"exception_type": "Short transfer", "reference_name": result["name"]},
			)
		)
		again = make_internal_payment(self.company, 100)
		with self.assertRaises(frappe.ValidationError):
			allocations_api.create_transfer(
				again.name, nowdate(), [{"fund_allocation": first.name, "allocated_amount": 100}]
			)

	def test_pending_transfer_is_flagged(self):
		_deal, first, _second = self.make_allocations()
		sent = make_internal_payment(self.company, 15000)
		result = allocations_api.create_transfer(
			sent.name, add_days(nowdate(), -10), [{"fund_allocation": first.name, "allocated_amount": 15000}]
		)
		flag_pending_transfers()
		flag_pending_transfers()
		self.assertEqual(
			frappe.db.count(
				"TBO Finance Exception",
				{"exception_type": "Transfer pending", "reference_name": result["name"]},
			),
			1,
		)

	def test_allocations_are_never_typed(self):
		_deal, first, _second = self.make_allocations()
		doc = frappe.copy_doc(first)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)


class TestAllocationAccess(AllocationTestCase):
	def test_only_accounts_and_management(self):
		deal = make_finance_deal(BDE)
		invoice = make_invoice_with_advances(make_vat_order(deal, 1000).name)
		make_receipt("Sales Invoice", invoice.name, 575)
		frappe.set_user(BDE)
		with self.assertRaises(frappe.PermissionError):
			allocations_api.get_allocation_summary()
		self.assertIsNone(deal_api.get_deal_finance(deal)["allocation"])
		frappe.set_user(ACCOUNTS)
		summary = allocations_api.get_allocation_summary()
		self.assertEqual(summary["accounting_option"], "Memo only")
		self.assertGreaterEqual(summary["totals"][0]["pending"], 250)
		self.assertEqual(deal_api.get_deal_finance(deal)["allocation"]["share"], 250)

	def test_preview_records_nothing(self):
		deal = make_finance_deal(BDE)
		invoice = make_invoice_with_advances(make_vat_order(deal, 1000).name)
		frappe.db.set_single_value("TBO Smart Settings", "allocation_start_date", add_days(nowdate(), 1))
		entry = make_receipt("Sales Invoice", invoice.name, 575)
		count = frappe.db.count("TBO Fund Allocation")
		preview = allocations_api.preview_allocations(add_days(nowdate(), -1), add_days(nowdate(), 5))
		row = next(row for row in preview["rows"] if row["payment_entry"] == entry.name)
		self.assertEqual(row["share"], 250)
		self.assertEqual(frappe.db.count("TBO Fund Allocation"), count)

	def test_settings_validation(self):
		settings = frappe.get_single("TBO Smart Settings")
		settings.partner_company = settings.invoicing_company
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)
