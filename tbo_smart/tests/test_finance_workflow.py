from unittest.mock import patch

import erpnext
import frappe
from frappe.permissions import add_permission
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from tbo_smart.api import quotation_adjustment as adjustment_api
from tbo_smart.api.finance import deal as deal_api
from tbo_smart.api.finance import quotation as quotation_api
from tbo_smart.api.finance import report as report_api
from tbo_smart.finance.documents import FinanceDocument, QuotationApprovalPending
from tbo_smart.finance.stage import recompute_all, update_stage
from tbo_smart.quotation_control import AdjustmentApprovalRequired
from tbo_smart.sales import permissions
from tbo_smart.test_utils import (
	configure_quotation_approval,
	delete_sales_records,
	enable_finance_workflow,
	get_versions,
	make_customer,
	make_deal,
	make_deal_quotation,
	make_finance_deal,
	make_finance_item,
	make_invoice_from_order,
	make_lead,
	make_manual_order,
	make_order_from_quotation,
	make_organization,
	make_receipt,
	make_user,
	reset_finance_settings,
)

BDE = "fin.bde@tbosmart.test"
OTHER_BDE = "fin.bde2@tbosmart.test"
BDM = "fin.bdm@tbosmart.test"
ACCOUNTS = "fin.accounts@tbosmart.test"
ACCOUNTS_MANAGER = "fin.accmgr@tbosmart.test"
OWNERS = (BDE, OTHER_BDE, BDM)


class FinanceTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(BDE, ["Sales User", "Business Development Executive"])
		make_user(OTHER_BDE, ["Sales User", "Business Development Executive"])
		make_user(BDM, ["Sales User", "Business Development Manager"])
		make_user(ACCOUNTS, ["Accounts User"])
		make_user(ACCOUNTS_MANAGER, ["Accounts User", "Accounts Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		configure_quotation_approval(enabled=0)
		enable_finance_workflow()
		self.addCleanup(reset_finance_settings)
		self.addCleanup(frappe.set_user, "Administrator")


class TestDealLinks(FinanceTestCase):
	def test_deal_follows_quotation_order_invoice_and_payment(self):
		deal = make_finance_deal(BDE)
		quotation = make_deal_quotation(deal)
		order = make_order_from_quotation(quotation.name, keep_deal=False)
		self.assertEqual(order.opportunity, deal)
		invoice = make_invoice_from_order(order.name, keep_deal=False)
		self.assertEqual(invoice.opportunity, deal)
		self.assertEqual(make_receipt("Sales Invoice", invoice.name).opportunity, deal)
		self.assertEqual(
			make_receipt(
				"Sales Order", make_order_from_quotation(make_deal_quotation(deal).name).name, 100
			).opportunity,
			deal,
		)

	def test_payment_for_two_deals_has_no_deal(self):
		invoices = []
		for _i in range(2):
			deal = make_finance_deal(BDE)
			invoices.append(
				make_invoice_from_order(make_order_from_quotation(make_deal_quotation(deal).name).name)
			)
		entry = make_receipt("Sales Invoice", invoices[0].name, submit=False)
		entry.append(
			"references",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": invoices[1].name,
				"allocated_amount": 0,
			},
		)
		entry.opportunity = invoices[0].opportunity
		FinanceDocument(entry).validate()
		self.assertFalse(entry.opportunity)

	def test_payment_without_references_keeps_a_typed_deal(self):
		deal = make_finance_deal(BDE)
		entry = make_receipt(
			"Sales Order", make_order_from_quotation(make_deal_quotation(deal).name).name, 100, submit=False
		)
		entry.set("references", [])
		entry.opportunity = deal
		FinanceDocument(entry).validate()
		self.assertEqual(entry.opportunity, deal)

	def test_switched_off_leaves_links_alone(self):
		deal = make_finance_deal(BDE)
		quotation = make_deal_quotation(deal)
		frappe.db.set_single_value("TBO Smart Settings", "enable_finance_workflow", 0)
		order = make_manual_order(quotation.name)
		order.insert(ignore_permissions=True)
		self.assertFalse(order.opportunity)
		enable_finance_workflow()
		order.save(ignore_permissions=True)
		self.assertEqual(order.opportunity, deal)


class TestStage(FinanceTestCase):
	def stage(self, deal):
		return frappe.db.get_value("Opportunity", deal, "tbo_finance_stage")

	def test_stage_follows_the_documents(self):
		deal = make_finance_deal(BDE)
		self.assertEqual(update_stage(deal), "Onboarding")
		quotation = make_deal_quotation(deal, rate=1000)
		self.assertEqual(self.stage(deal), "Quotation")
		order = make_order_from_quotation(quotation.name)
		invoice = make_invoice_from_order(order.name)
		self.assertEqual(self.stage(deal), "Invoiced")
		make_receipt("Sales Invoice", invoice.name, 400)
		self.assertEqual(self.stage(deal), "Collecting")
		last = make_receipt("Sales Invoice", invoice.name)
		self.assertEqual(self.stage(deal), "Collected")
		last.cancel()
		self.assertEqual(self.stage(deal), "Collecting")
		stages = [
			change["tbo_finance_stage"][1]
			for change in get_versions("Opportunity", deal)
			if "tbo_finance_stage" in change
		]
		self.assertEqual(
			stages, ["Onboarding", "Quotation", "Invoiced", "Collecting", "Collected", "Collecting"]
		)

	def test_overdue_invoice_is_collecting(self):
		deal = make_finance_deal(BDE)
		order = make_order_from_quotation(make_deal_quotation(deal).name)
		from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

		invoice = make_sales_invoice(order.name)
		invoice.update(
			{
				"set_posting_time": 1,
				"posting_date": add_days(nowdate(), -40),
				"due_date": add_days(nowdate(), -10),
			}
		)
		invoice.payment_schedule = []
		invoice.insert(ignore_permissions=True)
		invoice.submit()
		self.assertEqual(self.stage(deal), "Collecting")

	def test_onboarding_and_closing_are_people_and_logged(self):
		deal = make_finance_deal(BDE)
		frappe.set_user(OTHER_BDE)
		with self.assertRaises(frappe.PermissionError):
			deal_api.set_onboarding_complete(deal)
		frappe.set_user(BDE)
		self.assertEqual(deal_api.set_onboarding_complete(deal)["stage"], "Quotation")
		with self.assertRaises(frappe.PermissionError):
			deal_api.set_finance_closed(deal)
		frappe.set_user(ACCOUNTS_MANAGER)
		with self.assertRaises(frappe.PermissionError):
			deal_api.set_finance_closed(deal)
		enable_finance_workflow(finance_crm_access=1)
		with self.assertRaises(frappe.ValidationError):
			deal_api.set_finance_closed(deal)
		frappe.set_user("Administrator")
		invoice = make_invoice_from_order(make_order_from_quotation(make_deal_quotation(deal).name).name)
		make_receipt("Sales Invoice", invoice.name)
		frappe.set_user(ACCOUNTS_MANAGER)
		self.assertEqual(deal_api.set_finance_closed(deal)["stage"], "Closed")
		frappe.set_user("Administrator")
		recompute_all()
		self.assertEqual(self.stage(deal), "Closed")
		versions = get_versions("Opportunity", deal)
		self.assertTrue(any("tbo_onboarding_complete" in change for change in versions))
		self.assertTrue(any("tbo_closed" in change for change in versions))
		comments = frappe.get_all(
			"Comment", filters={"reference_doctype": "Opportunity", "reference_name": deal}, pluck="content"
		)
		self.assertIn("Finance closed.", comments)

	def test_new_deal_gets_the_default_company(self):
		deal = make_deal(BDE, company=None)
		self.assertEqual(frappe.db.get_value("Opportunity", deal, "company"), erpnext.get_default_company())


class TestQuotationApi(FinanceTestCase):
	def test_dialog_prefills_from_the_deal(self):
		deal = make_finance_deal(BDE, items=[{"item_code": make_finance_item(), "qty": 3, "rate": 250}])
		frappe.set_user(BDE)
		form = quotation_api.get_quotation_form(deal)
		self.assertEqual(form["company"], erpnext.get_default_company())
		self.assertEqual(form["party"]["quotation_to"], "Customer")
		[item] = form["items"]
		self.assertEqual(
			(item["item_code"], item["qty"], item["price_list_rate"]), (make_finance_item(), 3, 250)
		)

	def test_save_edit_and_submit_a_draft(self):
		deal = make_finance_deal(BDE)
		frappe.set_user(BDE)
		values = {
			"company": erpnext.get_default_company(),
			"valid_till": add_days(nowdate(), 10),
			"commission": 2,
			"terms": "Net 30",
			"items": [
				{
					"item_code": make_finance_item(),
					"qty": 2,
					"price_list_rate": 500,
					"discount_percentage": 10,
				}
			],
		}
		saved = quotation_api.save_quotation(deal, values)
		doc = frappe.get_doc("Quotation", saved["name"])
		self.assertEqual((doc.opportunity, doc.docstatus, doc.tbo_commission_rate), (deal, 0, 2))
		self.assertEqual((doc.items[0].rate, doc.net_total), (450, 900))
		values["items"][0]["qty"] = 4
		self.assertEqual(quotation_api.save_quotation(deal, values, saved["name"])["net_total"], 1800)
		self.assertEqual(quotation_api.submit_quotation(deal, saved["name"])["docstatus"], 1)
		with self.assertRaises(frappe.ValidationError):
			quotation_api.save_quotation(deal, values, saved["name"])

	def test_a_deal_without_a_customer_quotes_to_its_lead(self):
		lead = make_lead(BDE, "Quote Lead", company_name=f"Lead Org {frappe.generate_hash(length=6)}")
		deal = make_deal(BDE, opportunity_from="Lead", party_name=lead)
		frappe.set_user(BDE)
		saved = quotation_api.save_quotation(
			deal, {"items": [{"item_code": make_finance_item(), "qty": 1, "price_list_rate": 10}]}
		)
		self.assertEqual((saved["party"]["quotation_to"], saved["party"]["party_name"]), ("Lead", lead))
		self.assertEqual(frappe.db.get_value("Quotation", saved["name"], "opportunity"), deal)
		self.assertEqual(quotation_api.get_quotation_form(deal, saved["name"])["name"], saved["name"])

	def test_refuses_other_deals_and_bad_input(self):
		deal = make_finance_deal(BDE)
		other = make_finance_deal(OTHER_BDE)
		frappe.set_user("Administrator")
		other_quotation = make_deal_quotation(other, submit=False)
		frappe.set_user(BDE)
		values = {"items": [{"item_code": make_finance_item(), "qty": 1, "price_list_rate": 10}]}
		with self.assertRaises(frappe.PermissionError):
			quotation_api.save_quotation(other, values)
		with self.assertRaises(frappe.PermissionError):
			quotation_api.save_quotation(deal, values, other_quotation.name)
		with self.assertRaises(frappe.ValidationError):
			quotation_api.save_quotation(deal, {"items": []})
		with self.assertRaises(frappe.ValidationError):
			quotation_api.save_quotation(deal, {"items": [{"item_code": make_finance_item(), "qty": 0}]})


class TestApprovalGuard(FinanceTestCase):
	def setUp(self):
		super().setUp()
		configure_quotation_approval(limits={"BDE": (5, 2), "BDM": (10, 4)})
		notifier = patch(
			"tbo_smart.tbo_smart.doctype.tbo_quotation_adjustment.tbo_quotation_adjustment.enqueue_create_notification"
		)
		notifier.start()
		self.addCleanup(notifier.stop)
		self.addCleanup(configure_quotation_approval, enabled=0)

	def test_pending_approval_blocks_order_until_approved(self):
		deal = make_finance_deal(BDE)
		frappe.set_user(BDE)
		quotation = make_deal_quotation(deal, submit=False, additional_discount_percentage=8)
		request = adjustment_api.request_adjustment(quotation.name, "Discount", 8, "Large account")
		frappe.set_user("Administrator")
		with self.assertRaises(QuotationApprovalPending):
			make_manual_order(quotation.name).insert(ignore_permissions=True)
		frappe.set_user(BDM)
		adjustment_api.decide_adjustment(request["name"], "approve")
		frappe.set_user(BDE)
		quotation.reload()
		quotation.submit()
		frappe.set_user("Administrator")
		order = make_order_from_quotation(quotation.name)
		self.assertEqual(order.docstatus, 1)

	def test_draft_above_limit_without_request_is_refused(self):
		deal = make_finance_deal(BDE)
		frappe.set_user(BDE)
		quotation = make_deal_quotation(deal, submit=False, additional_discount_percentage=8)
		with self.assertRaises(AdjustmentApprovalRequired):
			quotation.submit()
		frappe.set_user("Administrator")
		with self.assertRaises(QuotationApprovalPending):
			make_manual_order(quotation.name).insert(ignore_permissions=True)
		frappe.db.set_single_value("TBO Smart Settings", "enable_finance_workflow", 0)
		make_manual_order(quotation.name).insert(ignore_permissions=True)

	def test_quotation_from_before_enforcement_passes(self):
		configure_quotation_approval(enabled=0)
		deal = make_finance_deal(BDE)
		quotation = make_deal_quotation(deal, additional_discount_percentage=8)
		configure_quotation_approval(limits={"BDE": (5, 2)})
		self.assertEqual(make_order_from_quotation(quotation.name).docstatus, 1)


class TestFinanceTabAccess(FinanceTestCase):
	def make_invoiced_deal(self, owner):
		deal = make_finance_deal(owner)
		invoice = make_invoice_from_order(make_order_from_quotation(make_deal_quotation(deal).name).name)
		make_receipt("Sales Invoice", invoice.name, 300)
		return deal

	def test_bde_sees_own_deal_only(self):
		deal = self.make_invoiced_deal(BDE)
		frappe.set_user(BDE)
		data = deal_api.get_deal_finance(deal)
		self.assertEqual(data["stage"], "Collecting")
		[total] = data["totals"]
		self.assertEqual((total["invoiced"], total["received"], total["outstanding"]), (1000, 300, 700))
		self.assertEqual(len(data["payments"]), 1)
		frappe.set_user(OTHER_BDE)
		with self.assertRaises(frappe.PermissionError):
			deal_api.get_deal_finance(deal)

	def test_accounts_need_the_deal_access_setting(self):
		deal = self.make_invoiced_deal(BDE)
		frappe.set_user(ACCOUNTS)
		with self.assertRaises(frappe.PermissionError):
			deal_api.get_deal_finance(deal)
		self.assertFalse(permissions.unrestricted(ACCOUNTS, "Opportunity", "read"))
		enable_finance_workflow(finance_crm_access=1)
		self.assertTrue(permissions.unrestricted(ACCOUNTS, "Opportunity", "read"))
		self.assertFalse(permissions.unrestricted(ACCOUNTS, "Opportunity", "write"))
		self.assertFalse(permissions.unrestricted(ACCOUNTS, "Lead", "read"))
		self.assertFalse(permissions.unrestricted(BDE, "Opportunity", "read"))
		self.assertEqual(deal_api.get_deal_finance(deal)["deal"], deal)

	def test_amounts_hidden_from_bde_when_setting_off(self):
		deal = self.make_invoiced_deal(BDE)
		enable_finance_workflow(finance_bde_can_see_amounts=0, finance_crm_access=1)
		frappe.set_user(BDE)
		data = deal_api.get_deal_finance(deal)
		self.assertFalse(data["amounts_visible"])
		self.assertEqual(data["totals"], [])
		self.assertIsNone(data["invoices"][0]["outstanding_amount"])
		self.assertIsNone(data["payments"][0]["amount"])
		self.assertIsNotNone(data["quotations"][0]["grand_total"])
		frappe.set_user(ACCOUNTS)
		self.assertEqual(deal_api.get_deal_finance(deal)["invoices"][0]["outstanding_amount"], 700)

	def test_switched_off(self):
		deal = self.make_invoiced_deal(BDE)
		frappe.db.set_single_value("TBO Smart Settings", "enable_finance_workflow", 0)
		frappe.set_user(BDE)
		self.assertFalse(deal_api.get_finance_access()["enabled"])
		with self.assertRaises(frappe.ValidationError):
			deal_api.get_deal_finance(deal)
		with self.assertRaises(frappe.ValidationError):
			report_api.get_bde_conversion()

	def test_settings_grant_and_revoke_crm_read(self):
		frappe.db.delete("Custom DocPerm", {"parent": "Prospect", "role": "Accounts User"})
		settings = frappe.get_single("TBO Smart Settings")
		settings.update(
			{"enable_finance_workflow": 1, "finance_crm_access": 1, "finance_crm_roles": "Accounts User\n"}
		)
		settings.save(ignore_permissions=True)
		row = {"parent": "Prospect", "role": "Accounts User", "read": 1}
		self.assertTrue(frappe.db.exists("Custom DocPerm", row))
		settings.reload()
		settings.finance_crm_access = 0
		settings.save(ignore_permissions=True)
		self.assertFalse(frappe.db.exists("Custom DocPerm", row))
		# an existing row without read is widened, then put back
		add_permission("Prospect", "Accounts User", 0, "write")
		row_name = frappe.db.get_value("Custom DocPerm", {"parent": "Prospect", "role": "Accounts User"})
		frappe.db.set_value("Custom DocPerm", row_name, {"read": 0, "if_owner": 1})
		settings.reload()
		settings.finance_crm_access = 1
		settings.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Custom DocPerm", row_name, ["read", "if_owner"]), (1, 0))
		settings.reload()
		settings.finance_crm_access = 0
		settings.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Custom DocPerm", row_name, ["read", "if_owner"]), (0, 1))
		frappe.db.delete("Custom DocPerm", {"name": row_name})
		settings.finance_crm_roles = "No Such Role"
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)


class TestBdeConversion(FinanceTestCase):
	def test_counts_per_bde_and_filters(self):
		organization = make_organization(f"TBO Report {frappe.generate_hash(length=6)}")
		for owner, leads, deals in ((BDE, 3, 2), (OTHER_BDE, 2, 1)):
			names = [make_lead(owner, company_name=organization) for _i in range(leads)]
			for lead in names[:deals]:
				make_deal(owner, opportunity_from="Lead", party_name=lead)
		won = make_deal(BDE, "Won", 5000, opportunity_from="Customer", party_name=make_customer(organization))
		make_deal_quotation(won)
		make_deal_quotation(won, submit=False)

		report = report_api.get_bde_conversion(organization=organization)
		rows = {row["user"]: row for row in report["rows"]}
		self.assertEqual(
			{key: rows[BDE][key] for key in ("leads", "deals", "quotations", "won", "won_value")},
			{"leads": 3, "deals": 2, "quotations": 1, "won": 1, "won_value": 5000},
		)
		self.assertEqual(rows[BDE]["conversion_rate"], 66.7)
		self.assertEqual((rows[OTHER_BDE]["leads"], rows[OTHER_BDE]["deals"]), (2, 1))
		self.assertEqual(report["total"]["leads"], 5)

		other_company = report_api.get_bde_conversion(organization=organization, company="No Such Company")
		self.assertEqual(sum(row["deals"] for row in other_company["rows"]), 0)

		frappe.set_user(BDE)
		self.assertEqual(
			[row["user"] for row in report_api.get_bde_conversion(organization=organization)["rows"]], [BDE]
		)
		frappe.set_user(ACCOUNTS)
		with self.assertRaises(frappe.PermissionError):
			report_api.get_bde_conversion()


class TestWithoutERPNext(FrappeTestCase):
	"""tbo_smart must migrate on a site without ERPNext: no fields on its doctypes, and every
	phase stays off."""

	def test_setup_skips_without_erpnext(self):
		from tbo_smart.finance import setup as finance_setup

		with (
			patch("frappe.get_installed_apps", return_value=["frappe", "tbo_smart"]),
			patch.object(finance_setup, "create_custom_fields") as create_fields,
			patch.object(finance_setup, "set_allocation_start_date") as allocation_start,
		):
			finance_setup.setup_finance()

		# Phase 4's Bank Transaction fields are part of these finance fields
		create_fields.assert_not_called()
		allocation_start.assert_not_called()

	def test_finance_off_without_erpnext(self):
		from tbo_smart.finance import (
			allocation_enabled,
			dashboard_enabled,
			exceptions_enabled,
			finance_enabled,
			reconciliation_enabled,
		)
		from tbo_smart.finance.dashboard import get_cards

		frappe.db.set_single_value(
			"TBO Smart Settings",
			{
				"enable_finance_workflow": 1,
				"enable_fund_allocation": 1,
				"enable_reconciliation": 1,
				"enable_finance_dashboard": 1,
			},
		)
		with patch("frappe.get_installed_apps", return_value=["frappe", "tbo_smart"]):
			self.assertFalse(finance_enabled())
			self.assertFalse(allocation_enabled())
			self.assertFalse(reconciliation_enabled())
			self.assertFalse(dashboard_enabled())
			self.assertEqual(get_cards(nowdate(), nowdate()), [])
			self.assertFalse(exceptions_enabled())
		frappe.db.rollback()
