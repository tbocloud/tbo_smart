import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_first_day, get_last_day, nowdate

from tbo_smart.finance import dashboard
from tbo_smart.test_utils import (
	delete_sales_records,
	enable_finance_workflow,
	get_dashboard_cards,
	make_deal_invoice,
	make_finance_deal,
	make_lead,
	make_receipt,
	make_user,
	reset_finance_settings,
)

BDE = "dash.bde@tbosmart.test"
OTHER_BDE = "dash.bde2@tbosmart.test"
ACCOUNTS = "dash.accounts@tbosmart.test"


class TestFinanceDashboard(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(BDE, ["Sales User"])
		make_user(OTHER_BDE, ["Sales User"])
		make_user(ACCOUNTS, ["Accounts User", "Accounts Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records((BDE, OTHER_BDE))
		reset_finance_settings()
		enable_finance_workflow(enable_finance_dashboard=1, enable_fund_allocation=1, enable_reconciliation=1)
		self.addCleanup(reset_finance_settings)
		self.addCleanup(frappe.set_user, "Administrator")
		self.window = (get_first_day(nowdate()), get_last_day(nowdate()))

	def test_bde_sees_own_sales_figures_only(self):
		own = make_finance_deal(BDE)
		other = make_finance_deal(OTHER_BDE)
		invoice = make_deal_invoice(own, rate=700)
		make_deal_invoice(other, rate=900)
		make_receipt("Sales Invoice", invoice.name, 200)
		make_deal_invoice(own, rate=50, due_in=-3)
		make_lead(BDE)
		frappe.set_user(BDE)
		cards = get_dashboard_cards(self.window, BDE)
		self.assertEqual(cards["tbo_invoiced"]["value"], 750)
		self.assertEqual(cards["tbo_collected"]["value"], 200)
		self.assertEqual(cards["tbo_outstanding"]["value"], 550)
		self.assertEqual(cards["tbo_overdue"]["value"], 50)
		self.assertNotIn("tbo_payable", cards)
		self.assertNotIn("tbo_lines_to_verify", cards)

	def test_everyone_view_counts_deal_invoices_only(self):
		deal = make_finance_deal(BDE)
		make_deal_invoice(deal, rate=300)
		before = get_dashboard_cards(self.window)["tbo_invoiced"]["value"]
		plain = make_deal_invoice(deal, rate=1000, submit=False)
		plain.opportunity = None
		plain.submit()
		self.assertFalse(frappe.db.get_value("Sales Invoice", plain.name, "opportunity"))
		self.assertEqual(get_dashboard_cards(self.window)["tbo_invoiced"]["value"], before)

	def test_accounts_see_finance_cards(self):
		frappe.set_user(ACCOUNTS)
		cards = get_dashboard_cards(self.window)
		for name in ("tbo_payable", "tbo_transfers_waiting", "tbo_lines_to_verify", "tbo_open_exceptions"):
			self.assertIn(name, cards)

	def test_switches_and_hidden_amounts(self):
		enable_finance_workflow(finance_bde_can_see_amounts=0)
		frappe.set_user(BDE)
		cards = get_dashboard_cards(self.window, BDE)
		self.assertIn("tbo_quotations", cards)
		self.assertNotIn("tbo_invoiced", cards)
		self.assertIsNone(dashboard.get_tbo_invoiced(*self.window, BDE))
		frappe.set_user("Administrator")
		frappe.db.set_single_value("TBO Smart Settings", "enable_finance_dashboard", 0)
		self.assertEqual(dashboard.get_cards(*self.window), [])
		self.assertIsNone(dashboard.get_tbo_quotations(*self.window))
