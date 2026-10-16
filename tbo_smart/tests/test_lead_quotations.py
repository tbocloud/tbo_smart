import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from tbo_smart.api import lead_quotation as api
from tbo_smart.api import quotation_adjustment
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.lead_pipeline.statuses import CLOSED_LOST, CLOSED_WON, NEGOTIATION, PROPOSAL_SENT
from tbo_smart.test_utils import (
	configure_quotation_approval,
	delete_lead_deals,
	delete_lead_meetings,
	delete_lead_quotations,
	delete_smart_lead_records,
	disable_lead_quotations,
	enable_lead_quotations,
	make_gst_template,
	make_lead,
	make_lead_meeting,
	make_pdf_bytes,
	make_quotation_item,
	make_user,
	quotation_line,
	set_leaf_customer_group,
)

BDE = "lq.bde@tbosmart.test"
BDE2 = "lq.bde2@tbosmart.test"
BDM = "lq.bdm@tbosmart.test"
OWNERS = (BDE, BDE2, BDM)
SERVICE = "TBO Smart Pipeline Implementation"
SUPPORT = "TBO Smart Pipeline Support"
PACKAGE = "TBO Smart Test ERP Starter"


class LeadQuotationTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(BDE, ["Sales User", "Business Development Executive"])
		make_user(BDE2, ["Sales User", "Business Development Executive"])
		make_user(BDM, ["Sales User", "Business Development Manager"])
		set_leaf_customer_group()
		make_quotation_item(SERVICE)
		make_quotation_item(SUPPORT)
		cls.gst = make_gst_template(18)
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls.clean()
		disable_lead_quotations()
		configure_quotation_approval(enabled=0)
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	@classmethod
	def clean(cls):
		leads = frappe.get_all("Lead", filters={"lead_owner": ("in", OWNERS)}, pluck="name")
		delete_lead_quotations(leads)
		delete_lead_meetings(leads)
		delete_lead_deals(leads)
		frappe.db.delete("TBO Quotation Package Item", {"parent": PACKAGE})
		frappe.db.delete("TBO Quotation Package", {"name": PACKAGE})
		delete_smart_lead_records(OWNERS)

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		enable_lead_quotations()
		configure_quotation_approval(enabled=0)
		# a fresh organization each time: ERPNext reuses a Customer with the same name
		self.lead = make_lead(
			BDE,
			"Quote Me",
			company_name=f"Quote Traders {frappe.generate_hash(length=6)}",
			email_id="quote@traders.example",
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def save(self, items=None, quotation=None, **values):
		payload = {
			"valid_till": add_days(nowdate(), 20),
			"items": items or [quotation_line(SERVICE)],
			**values,
		}
		return api.save_quotation(self.lead, json.dumps(payload, default=str), quotation)

	def sent(self, **values):
		saved = self.save(**values)
		api.mark_sent(saved["name"], "Manual")
		return saved["name"]


class TestBuilder(LeadQuotationTestCase):
	def test_presets_fill_the_builder_and_totals_add_up(self):
		api.set_service_rate(SERVICE, 50000)
		api.set_service_rate(SUPPORT, 12000)
		api.save_package(
			json.dumps(
				{
					"package_name": PACKAGE,
					"project_duration": "6 weeks",
					"items": [
						{"item_code": SERVICE, "description": "Setup and training", "qty": 1},
						{"item_code": SUPPORT, "qty": 2, "rate": 10000},
					],
				}
			)
		)
		frappe.set_user(BDE)
		form = api.get_quotation_form(self.lead)
		self.assertEqual(form["party"]["quotation_to"], "Lead")
		self.assertEqual(getdate(form["valid_till"]), getdate(add_days(nowdate(), 30)))
		package = next(row for row in form["catalogue"]["packages"] if row["name"] == PACKAGE)
		self.assertEqual(
			[(row["item_code"], row["qty"], row["rate"]) for row in package["items"]],
			[(SERVICE, 1, 50000), (SUPPORT, 2, 10000)],
		)
		self.assertEqual(form["tax_rates"].get(self.gst), 18)

		saved = self.save(
			items=[
				quotation_line(SERVICE, 1, 50000, description="Setup and training"),
				quotation_line(SUPPORT, 2, 10000),
			],
			package=PACKAGE,
			taxes_and_charges=self.gst,
			additional_discount_percentage=10,
		)
		self.assertEqual((saved["total"], saved["discount_amount"], saved["net_total"]), (70000, 7000, 63000))
		self.assertEqual((saved["total_taxes_and_charges"], saved["grand_total"]), (11340, 74340))
		self.assertEqual(saved["quote_status"], q.DRAFT)
		self.assertEqual((saved["revision"], saved["family"], saved["is_latest"]), (0, saved["name"], 1))
		frappe.set_user("Administrator")
		self.assertRaises(
			frappe.DuplicateEntryError,
			api.save_package,
			json.dumps({"package_name": PACKAGE, "items": [{"item_code": SERVICE}]}),
		)
		frappe.set_user(BDE)
		doc = frappe.get_doc("Quotation", saved["name"])
		self.assertEqual((doc.quotation_to, doc.party_name, doc.tbo_package), ("Lead", self.lead, PACKAGE))
		self.assertEqual(doc.items[0].description, "Setup and training")

	def test_only_preset_roles_edit_prices_and_packages(self):
		frappe.set_user(BDE)
		self.assertRaises(frappe.PermissionError, api.set_service_rate, SERVICE, 1)
		self.assertRaises(
			frappe.PermissionError,
			api.save_package,
			json.dumps({"package_name": "x", "items": [{"item_code": SERVICE}]}),
		)
		self.assertFalse(api.get_catalogue()["can_edit"])

	def test_quotation_from_a_meeting(self):
		meeting = make_lead_meeting(self.lead)
		frappe.set_user(BDE)
		self.assertEqual(api.get_quotation_form(self.lead, meeting=meeting)["meeting"], meeting)
		saved = self.save(meeting=meeting)
		self.assertEqual(saved["meeting"], meeting)

	def test_permissions_and_switch(self):
		frappe.set_user(BDE2)
		self.assertRaises(frappe.PermissionError, api.get_lead_quotations, self.lead)
		frappe.set_user("Administrator")
		disable_lead_quotations()
		self.assertRaises(frappe.ValidationError, api.get_lead_quotations, self.lead)
		self.assertFalse(api.get_access()["enabled"])


class TestStatuses(LeadQuotationTestCase):
	def test_sent_negotiation_accepted_moves_the_lead_and_makes_the_deal(self):
		frappe.set_user(BDE)
		name = self.sent()
		doc = frappe.get_doc("Quotation", name)
		self.assertEqual((doc.docstatus, doc.tbo_quote_status, doc.tbo_sent_via), (1, q.SENT, "Manual"))
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), PROPOSAL_SENT)
		task = frappe.db.get_value(
			"ToDo",
			{"reference_name": self.lead, "description": ("like", f"%{name}%")},
			["date", "tbo_task_type", "allocated_to"],
			as_dict=True,
		)
		self.assertEqual(
			(getdate(task.date), task.tbo_task_type, task.allocated_to),
			(getdate(add_days(nowdate(), 3)), "Sales follow-up", BDE),
		)

		api.set_quote_status(name, q.NEGOTIATING, "Wants 5% off")
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), NEGOTIATION)
		self.assertRaises(frappe.ValidationError, api.set_quote_status, name, q.VIEWED)
		api.set_quote_status(name, q.ACCEPTED)
		deal = frappe.db.get_value(
			"Opportunity", {"opportunity_from": "Lead", "party_name": self.lead}, "name"
		)
		self.assertTrue(deal)
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), CLOSED_WON)
		self.assertEqual(
			frappe.db.get_value("Quotation", name, ["opportunity", "quotation_to", "party_name"]),
			(deal, "Lead", self.lead),
		)
		self.assertEqual(api.get_lead_quotations(self.lead)["deal"], deal)
		self.assertTrue(frappe.get_all("Version", filters={"ref_doctype": "Quotation", "docname": name}))

	def test_sending_is_blocked_while_approval_is_pending(self):
		configure_quotation_approval(limits={"BDE": (5, 2), "BDM": (10, 4)})
		frappe.set_user(BDE)
		saved = self.save(additional_discount_percentage=8)
		self.assertEqual(saved["needs_approval"][0]["kind"], "Discount")
		quotation_adjustment.request_adjustment(saved["name"], "Discount", 8, "Long-term client")
		self.assertEqual(frappe.db.get_value("Quotation", saved["name"], "tbo_quote_status"), q.PENDING)
		self.assertRaises(frappe.ValidationError, api.mark_sent, saved["name"], "Manual")
		self.assertEqual(frappe.db.get_value("Quotation", saved["name"], "docstatus"), 0)

		frappe.set_user(BDM)
		request = frappe.db.get_value("TBO Quotation Adjustment", {"quotation": saved["name"]}, "name")
		quotation_adjustment.decide_adjustment(request, "approve")
		self.assertEqual(frappe.db.get_value("Quotation", saved["name"], "tbo_quote_status"), q.APPROVED)
		frappe.set_user(BDE)
		api.mark_sent(saved["name"], "Manual")
		self.assertEqual(frappe.db.get_value("Quotation", saved["name"], "tbo_quote_status"), q.SENT)

	def test_a_lowered_discount_no_longer_waits_for_approval(self):
		configure_quotation_approval(limits={"BDE": (5, 2), "BDM": (10, 4)})
		frappe.set_user(BDE)
		saved = self.save(additional_discount_percentage=8)
		quotation_adjustment.request_adjustment(saved["name"], "Discount", 8, "Long-term client")
		lowered = self.save(quotation=saved["name"], additional_discount_percentage=3)
		self.assertEqual(lowered["quote_status"], q.DRAFT)
		api.mark_sent(saved["name"], "Manual")
		self.assertEqual(frappe.db.get_value("Quotation", saved["name"], "tbo_quote_status"), q.SENT)

	def test_rejected_asks_why_and_can_close_the_lead(self):
		frappe.set_user(BDE)
		name = self.sent()
		self.assertRaises(frappe.ValidationError, api.set_quote_status, name, q.REJECTED)
		self.assertRaises(frappe.ValidationError, api.set_quote_status, name, q.REJECTED, "Too costly", 1)
		api.set_quote_status(name, q.REJECTED, "Too costly", 1, "Price")
		self.assertEqual(
			frappe.db.get_value("Lead", self.lead, ["tbo_stage", "tbo_lost_reason", "tbo_lost_notes"]),
			(CLOSED_LOST, "Other", "Price"),
		)

	def test_expiry_after_valid_till(self):
		frappe.set_user(BDE)
		name = self.sent()
		frappe.set_user("Administrator")
		frappe.db.set_value("Quotation", name, "valid_till", add_days(nowdate(), -1))
		self.assertGreaterEqual(q.expire_quotations(), 1)
		self.assertEqual(frappe.db.get_value("Quotation", name, "tbo_quote_status"), q.EXPIRED)

	def test_whatsapp_and_email(self):
		frappe.db.set_value("Lead", self.lead, "mobile_no", "9847000555")
		frappe.set_user(BDE)
		saved = self.save()
		result = api.mark_sent(saved["name"], "WhatsApp", message="Here is the quote")
		self.assertTrue(result["whatsapp_url"].startswith("https://wa.me/919847000555"))
		second = self.save()
		with patch("frappe.core.doctype.communication.email.make") as make:
			api.mark_sent(second["name"], "Email", follow_up_days=0)
		self.assertEqual(make.call_args.kwargs["recipients"], "quote@traders.example")
		self.assertEqual(make.call_args.kwargs["print_format"], "TBO Quotation")


class TestRevisionsAndOrders(LeadQuotationTestCase):
	def test_revision_chain_and_diff(self):
		frappe.set_user(BDE)
		first = self.sent(items=[quotation_line(SERVICE, 1, 1000), quotation_line(SUPPORT, 1, 500)])
		revision = api.create_revision(first)
		self.assertEqual(
			(revision["revision"], revision["family"], revision["quote_status"]), (1, first, q.DRAFT)
		)
		self.assertEqual(
			frappe.db.get_value("Quotation", first, ["tbo_quote_status", "tbo_is_latest"]), (q.REVISION, 0)
		)
		# only the latest revision goes out
		self.assertRaises(frappe.ValidationError, api.mark_sent, first, "Manual")
		self.save(
			quotation=revision["name"],
			items=[quotation_line(SERVICE, 2, 900, description="Now with training")],
			terms="New terms",
		)
		rows = api.get_revisions(revision["name"])
		self.assertEqual([row["name"] for row in rows], [first, revision["name"]])
		changes = rows[1]["changes"]
		self.assertEqual(changes["grand_total"], [1500, 1800])
		self.assertEqual(changes["removed"], [SUPPORT])
		self.assertEqual(changes["changed"][0]["qty"], [1, 2])
		self.assertTrue(changes["terms_changed"])
		self.assertTrue(changes["changed"][0]["description_changed"])
		self.assertEqual([row["is_latest"] for row in rows], [0, 1])

	def test_accepted_quotation_becomes_a_sales_order_and_pdf_on_the_lead(self):
		frappe.set_user(BDE)
		name = self.sent()
		with patch("tbo_smart.api.lead_quotation.frappe.get_print", return_value=make_pdf_bytes()):
			pdf = api.save_pdf(name)
		self.assertTrue(pdf["file_name"].startswith(f"{name}--"))
		self.assertEqual(api.get_lead_quotations(self.lead)["quotations"][0]["pdf"], pdf["file_url"])
		# only an accepted quotation, and only for people who may create Sales Orders
		self.assertRaises(frappe.ValidationError, api.make_sales_order, name)
		api.set_quote_status(name, q.ACCEPTED)
		frappe.set_user(BDE2)
		self.assertRaises(frappe.PermissionError, api.make_sales_order, name)
		frappe.set_user(BDE)
		order = api.make_sales_order(name)["sales_order"]
		frappe.set_user("Administrator")
		deal = frappe.db.get_value(
			"Opportunity", {"opportunity_from": "Lead", "party_name": self.lead}, "name"
		)
		customer, opportunity = frappe.db.get_value("Sales Order", order, ["customer", "opportunity"])
		# ERPNext made the Customer from the lead; the deal knows it
		self.assertEqual(frappe.db.get_value("Customer", customer, "lead_name"), self.lead)
		self.assertEqual(opportunity, deal)
		self.assertEqual(frappe.db.get_value("Opportunity", deal, "tbo_customer"), customer)

	def test_print_format_renders(self):
		frappe.set_user(BDE)
		name = self.save(taxes_and_charges=self.gst)["name"]
		frappe.set_user("Administrator")
		html = frappe.get_print("Quotation", name, print_format="TBO Quotation")
		self.assertIn("Quote Traders", html)
		self.assertIn(name, html)
