import json
from unittest.mock import patch

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from tbo_smart.api import hub_leads, lead_quotation
from tbo_smart.api import hub_proposals as hub
from tbo_smart.api import proposal_page as page
from tbo_smart.lead_pipeline import brd, srs
from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline import proposals as p
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.test_utils import (
	accept_as_client,
	brd_answer,
	call_as_guest,
	configure_ai_providers,
	configure_quotation_approval,
	delete_owner_lead_records,
	disable_client_documents,
	disable_lead_quotations,
	disable_proposals,
	disable_smart_leads,
	enable_client_documents,
	enable_proposals,
	make_accepted_proposal,
	make_accepted_quotation_document,
	make_approved_brd,
	make_approved_document,
	make_customised_proposal,
	make_gst_template,
	make_hub_document,
	make_hub_proposal,
	make_lead,
	make_outgoing_email_account,
	make_pdf_bytes,
	make_plan_document,
	make_quotation_item,
	make_signed_agreement,
	make_user,
	mock_ai,
	price_hub_proposal,
	send_hub_proposal,
	set_leaf_customer_group,
	set_segment_rates,
	set_smart_lead_settings,
	srs_answer,
)

HUB = "docs.hub@tbosmart.test"
BDE = "docs.bde@tbosmart.test"
OWNERS = (BDE,)
SERVICE = "TBO Smart Document Implementation"
# the customisation days' service item in ERPNext's Day, which takes fractions (Nos doesn't)
DAY_SERVICE = "TBO Smart Document Day"
TEMPLATE = "ERPNext Implementation"


class ClientDocumentTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User", "Business Development Executive"])
		set_leaf_customer_group()
		make_quotation_item(SERVICE)
		make_quotation_item(DAY_SERVICE, uom="Day")
		make_outgoing_email_account()
		cls.gst = make_gst_template(18, is_default=True)
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		frappe.db.set_value("Sales Taxes and Charges Template", cls.gst, "is_default", 0)
		disable_client_documents()
		disable_proposals()
		disable_smart_leads()
		disable_lead_quotations()
		configure_quotation_approval(enabled=0)
		set_segment_rates()
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		enable_proposals()
		enable_client_documents()
		set_smart_lead_settings(proposal_service_item=None, proposal_day_rate=0)
		configure_quotation_approval(enabled=0)
		set_segment_rates([{"segment": "Medium", "max_discount": 5}])
		self.lead = make_lead(
			BDE,
			"Asha Menon",
			company_name=f"Malabar Spices {frappe.generate_hash(length=6)}",
			email_id=f"asha.{frappe.generate_hash(length=6)}@malabar.example",
			tbo_segment="Medium",
		)
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")


class TestSwitches(ClientDocumentTestCase):
	def test_each_type_is_off_by_default(self):
		disable_client_documents()
		options = hub.get_proposal_options(acting_user=BDE)
		self.assertEqual([row["name"] for row in options["document_types"]], [kinds.PROPOSAL])
		proposal = make_accepted_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		self.assertRaises(frappe.ValidationError, make_hub_document, proposal, kinds.QUOTATION, BDE)
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual([step["document_type"] for step in chain["steps"]], [kinds.PROPOSAL])
		self.assertIsNone(chain["next"])
		# the agreement's switch needs the quotation's
		enable_client_documents(quotations=False, work_orders=True)
		self.assertEqual(
			[row["name"] for row in hub.get_proposal_options(acting_user=BDE)["document_types"]],
			[kinds.PROPOSAL],
		)

	def test_the_clause_library_is_seeded_and_grouped(self):
		library = hub.get_proposal_library(acting_user=BDE)
		self.assertIn("Agreement Clause", library["libraries"])
		self.assertEqual(
			library["clause_groups"],
			["Change Requests", "Intellectual Property", "Confidentiality", "Termination", "General"],
		)
		groups = {row["group"] for row in library["items"] if row["library"] == "Agreement Clause"}
		self.assertLessEqual(groups, set(library["clause_groups"]))
		self.assertTrue(groups)

	def test_a_rest_insert_cant_choose_the_type(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)["name"]
		frappe.set_user("Administrator")
		doc = frappe.get_doc(
			{
				"doctype": p.DOCTYPE,
				"lead": self.lead,
				"title": "Sneaky",
				"document_type": kinds.WORK_ORDER,
				"source_document": proposal,
				"client_profile": "{}",
			}
		).insert()
		self.assertEqual((doc.document_type, doc.source_document), (kinds.PROPOSAL, None))
		self.assertTrue(doc.name.startswith("PROP-"))


class TestQuotation(ClientDocumentTestCase):
	def test_made_from_the_accepted_proposal(self):
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual(chain["next"]["action"], "create_proposal")
		proposal = make_accepted_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		accepted = frappe.db.get_value(p.DOCTYPE, proposal, "quotation")
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual(
			(chain["next"]["action"], chain["next"]["document_type"], chain["next"]["source"]),
			("create", kinds.QUOTATION, proposal),
		)

		result = make_hub_document(proposal, kinds.QUOTATION, BDE)
		quote = result["proposal"]
		self.assertEqual(result["warnings"], [])
		self.assertTrue(quote["name"].startswith("QUOT-"))
		self.assertEqual((quote["document_type"], quote["source_document"]), (kinds.QUOTATION, proposal))
		self.assertTrue(quote["title"].startswith("Quotation: ERPNext Implementation for Malabar Spices"))
		self.assertEqual(
			[row["section_type"] for row in quote["sections"]],
			[kind for kind, _h in kinds.QUOTATION_SECTIONS],
		)
		self.assertTrue(quote["can_price"])
		# its own ERPNext Quotation: a revision of the accepted one, with the default taxes
		revision = frappe.get_doc("Quotation", quote["quotation"]["name"])
		self.assertNotEqual(revision.name, accepted)
		self.assertEqual(
			revision.tbo_quote_family, frappe.db.get_value("Quotation", accepted, "tbo_quote_family")
		)
		self.assertEqual((revision.docstatus, revision.taxes_and_charges), (0, self.gst))
		self.assertTrue(revision.taxes)
		self.assertEqual(frappe.db.get_value("Quotation", accepted, "tbo_quote_status"), q.ACCEPTED)
		bodies = {row["section_type"]: row["body"] for row in quote["sections"]}
		self.assertIn("valid till", bodies["Terms & Conditions"])
		self.assertIn("Advance on signing", bodies["Payment Terms"])
		# one quotation in play at a time
		self.assertRaises(frappe.DuplicateEntryError, make_hub_document, proposal, kinds.QUOTATION, BDE)
		self.assertEqual(hub.get_document_chain(self.lead, acting_user=BDE)["next"]["name"], quote["name"])

		send_hub_proposal(quote["name"], BDE)
		self.assertEqual(frappe.db.get_value("Quotation", revision.name, "tbo_quote_status"), q.SENT)
		self.assertEqual(
			hub.get_document_chain(self.lead, acting_user=BDE)["steps"][1]["quotation_status"], q.SENT
		)
		accept_as_client(quote["name"])
		self.assertEqual(frappe.db.get_value(p.DOCTYPE, quote["name"], "status"), p.ACCEPTED)
		self.assertEqual(frappe.db.get_value("Quotation", revision.name, "tbo_quote_status"), q.ACCEPTED)
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual([step["done"] for step in chain["steps"]][:3], [True, True, False])
		self.assertEqual(chain["next"]["document_type"], kinds.WORK_ORDER)

	def test_only_from_an_accepted_proposal(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)["name"]
		price_hub_proposal(proposal, BDE, SERVICE)
		send_hub_proposal(proposal, BDE)
		self.assertRaises(frappe.ValidationError, make_hub_document, proposal, kinds.QUOTATION, BDE)
		accept_as_client(proposal)
		# a work order isn't made from a proposal
		self.assertRaises(frappe.ValidationError, make_hub_document, proposal, kinds.WORK_ORDER, BDE)

	def test_an_unpriced_proposal_is_priced_from_its_modules(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)["name"]
		send_hub_proposal(proposal, BDE)
		accept_as_client(proposal)
		result = make_hub_document(proposal, kinds.QUOTATION, BDE)
		quote = result["proposal"]
		self.assertTrue(result["warnings"][0].startswith("Not priced yet"))
		self.assertIsNone(quote["quotation"])
		self.assertIn("Price the quotation first.", quote["send_blockers"])
		set_smart_lead_settings(proposal_service_item=SERVICE)
		quote = hub.price_from_modules(quote["name"], acting_user=BDE)
		self.assertEqual(quote["quotation"]["taxes_and_charges"], self.gst)
		self.assertNotIn("Price the quotation first.", quote["send_blockers"])

	def test_a_discount_above_the_limit_needs_approval(self):
		frappe.set_user("Administrator")
		configure_quotation_approval(enabled=1, limits={"BDM": (20, 0)})
		frappe.set_user(HUB)
		proposal = make_accepted_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		quote = make_hub_document(proposal, kinds.QUOTATION, BDE)["proposal"]
		values = {
			"valid_till": add_days(nowdate(), 20),
			"items": [{"item_code": SERVICE, "qty": 1, "price_list_rate": 100000, "discount_percentage": 0}],
			"additional_discount_percentage": 12,
			"taxes_and_charges": self.gst,
		}
		quote = hub.save_pricing(quote["name"], json.dumps(values), acting_user=BDE)
		self.assertTrue(any("above your limit" in reason for reason in quote["send_blockers"]))


class TestWorkOrder(ClientDocumentTestCase):
	def test_the_agreement_from_the_accepted_quotation(self):
		quote = make_accepted_quotation_document(self.lead, BDE, TEMPLATE, SERVICE)
		order = make_hub_document(quote["name"], kinds.WORK_ORDER, BDE)["proposal"]
		self.assertTrue(order["name"].startswith("WO-"))
		self.assertEqual(order["document_label"], "Project Agreement")
		self.assertEqual(order["quotation"]["name"], quote["quotation"]["name"])
		self.assertFalse(order["can_price"])
		self.assertTrue(order["signature"])
		self.assertEqual(
			[row["section_type"] for row in order["sections"]],
			[kind for kind, _h in kinds.WORK_ORDER_SECTIONS],
		)
		bodies = {row["section_type"]: row["body"] for row in order["sections"]}
		self.assertIn("Service provider", bodies["Parties"])
		self.assertIn("Malabar Spices", bodies["Parties"])
		self.assertIn("Represented by Asha Menon", bodies["Parties"])
		self.assertIn(quote["name"], bodies["Purpose"])
		self.assertIn("written notice", bodies["Termination"])
		self.assertIn("confidential", bodies["Confidentiality"])
		self.assertNotIn("Source code", bodies["Intellectual Property"])
		self.assertIn("Change Request", order["sections"][9]["heading"])
		# its price is the accepted quotation's: not changed here
		self.assertRaises(frappe.ValidationError, hub.price_from_modules, order["name"], acting_user=BDE)
		self.assertTrue(any("start date" in reason for reason in order["send_blockers"]))

		# a new start date dates the timeline and the purpose, with the hub's copy of the sections
		start = add_days(nowdate(), 7)
		data = {"start_date": start, "sections": order["sections"]}
		order = hub.save_proposal(order["name"], json.dumps(data), acting_user=BDE)
		timeline = order["sections"][6]["body"]
		self.assertIn("From | To", timeline)
		self.assertIn(frappe.utils.formatdate(getdate(start), "d MMM yyyy"), timeline)
		self.assertIn("The project starts on", order["sections"][2]["body"])
		self.assertEqual(order["send_blockers"], [])
		# a purpose edited by hand is kept when the date moves again
		order["sections"][2]["body"] = "Our own purpose."
		order = hub.save_proposal(order["name"], json.dumps({"sections": order["sections"]}), acting_user=BDE)
		later = add_days(nowdate(), 14)
		order = hub.save_proposal(order["name"], json.dumps({"start_date": later}), acting_user=BDE)
		self.assertEqual(order["sections"][2]["body"], "Our own purpose.")
		self.assertIn(frappe.utils.formatdate(getdate(later), "d MMM yyyy"), order["sections"][6]["body"])

		send_hub_proposal(order["name"], BDE)
		doc = frappe.get_doc(p.DOCTYPE, order["name"])
		html = p.render_html(doc)
		self.assertIn("Sign the agreement", html)
		self.assertIn('id="accept-designation"', html)
		with patch("frappe.utils.pdf.get_pdf", return_value=make_pdf_bytes()) as get_pdf:
			self.assertTrue(p.render_pdf(doc).startswith(b"%PDF"))
		self.assertIn("Project Agreement / Work Order", get_pdf.call_args.args[0])
		# signing needs the designation, and changes nothing on the quotation
		self.assertRaises(frappe.ValidationError, accept_as_client, order["name"])
		accept_as_client(order["name"], "Director")
		doc.reload()
		self.assertEqual((doc.status, doc.decided_by_designation), (p.ACCEPTED, "Director"))
		self.assertEqual(frappe.db.get_value("Quotation", doc.quotation, "tbo_quote_status"), q.ACCEPTED)
		self.assertIn("Signed", p.render_html(doc))
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual(chain["steps"][2]["status_label"], "Signed")
		self.assertEqual([step["done"] for step in chain["steps"]][:4], [True, True, True, False])
		self.assertEqual((chain["next"]["document_type"], chain["next"]["source"]), (kinds.BRD, doc.name))
		self.assertEqual(hub_leads.get_lead(self.lead, acting_user=BDE)["documents"], chain)

	def test_source_code_clause_follows_the_scoping(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE, scoping={"source_code": 1})["name"]
		price_hub_proposal(proposal, BDE, SERVICE)
		send_hub_proposal(proposal, BDE)
		accept_as_client(proposal)
		quote = make_hub_document(proposal, kinds.QUOTATION, BDE)["proposal"]
		send_hub_proposal(quote["name"], BDE)
		accept_as_client(quote["name"])
		order = make_hub_document(quote["name"], kinds.WORK_ORDER, BDE)["proposal"]
		bodies = {row["section_type"]: row["body"] for row in order["sections"]}
		self.assertIn("source code of the custom work", bodies["Intellectual Property"])

	def test_a_revision_stays_an_agreement(self):
		quote = make_accepted_quotation_document(self.lead, BDE, TEMPLATE, SERVICE)
		order = make_hub_document(quote["name"], kinds.WORK_ORDER, BDE)["proposal"]
		order = hub.save_proposal(order["name"], json.dumps({"start_date": nowdate()}), acting_user=BDE)
		send_hub_proposal(order["name"], BDE)
		revision = hub.create_revision(order["name"], acting_user=BDE)
		self.assertTrue(revision["name"].startswith("WO-"))
		self.assertEqual(
			(revision["document_type"], revision["source_document"], revision["revision_no"]),
			(kinds.WORK_ORDER, quote["name"], 1),
		)


class TestProposalIsQuotation(ClientDocumentTestCase):
	"""TBO Smart Settings → The proposal is the quotation (docs/client-documents.md §1)."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		enable_client_documents(proposal_is_quotation=True)
		set_smart_lead_settings(proposal_service_item=SERVICE, proposal_day_rate=10000)
		frappe.set_user(HUB)

	def test_the_chain_skips_the_quotation(self):
		options = hub.get_proposal_options(acting_user=BDE)
		self.assertTrue(options["proposal_is_quotation"])
		types = {row["name"]: row for row in options["document_types"]}
		self.assertEqual(list(types), [kinds.PROPOSAL, kinds.WORK_ORDER, kinds.BRD, kinds.SRS, kinds.PLAN])
		self.assertEqual(types[kinds.WORK_ORDER]["follows"], kinds.PROPOSAL)
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual(
			[step["document_type"] for step in chain["steps"]],
			[kinds.PROPOSAL, kinds.WORK_ORDER, kinds.BRD, kinds.SRS, kinds.PLAN, "Project", "UAT"],
		)

	def test_the_proposal_carries_the_final_price(self):
		proposal = make_customised_proposal(self.lead, BDE, TEMPLATE)
		doc = frappe.get_doc(p.DOCTYPE, proposal["name"])
		quotation = frappe.get_doc("Quotation", doc.quotation)
		# the default GST and the proposal's validity on its own ERPNext Quotation
		self.assertEqual(quotation.taxes_and_charges, self.gst)
		self.assertTrue(quotation.taxes)
		self.assertEqual(getdate(quotation.valid_till), getdate(doc.valid_till))
		self.assertEqual(quotation.tbo_proposal, doc.name)
		# 3.5 days on Nos (whole numbers only): one unit at 3.5 times the day rate
		line = quotation.items[-1]
		self.assertEqual((line.qty, line.rate), (1, 35000))
		self.assertIn("3.5 person-days", line.description)
		self.assertEqual(quotation.grand_total, 41300)

		context = p.page_context(doc)
		self.assertEqual(context["pricing"]["grand_total_value"], 41300)
		self.assertEqual([row["label"] for row in context["pricing"]["taxes"]], ["GST 18%"])
		html = p.render_html(doc, preview=True)
		self.assertIn(context["pricing"]["grand_total"], html)
		self.assertIn("GST 18%", html)

	def test_the_customer_never_sees_the_quotation_number(self):
		proposal = make_customised_proposal(self.lead, BDE, TEMPLATE)["name"]
		quotation = frappe.db.get_value(p.DOCTYPE, proposal, "quotation")
		_result, make = send_hub_proposal(proposal, BDE, attach_pdf=0)
		self.assertNotIn(quotation, make.call_args.kwargs["content"])
		self.assertNotIn(quotation, make.call_args.kwargs["subject"])
		accept_as_client(proposal)
		doc = frappe.get_doc(p.DOCTYPE, proposal)
		html = p.render_html(doc)
		self.assertIn(proposal, html)
		self.assertNotIn(quotation, html)
		self.assertNotIn(quotation, p.render_html(doc, pdf=True))
		with patch("frappe.utils.pdf.get_pdf", return_value=make_pdf_bytes()) as get_pdf:
			p.render_pdf(doc)
		self.assertNotIn(quotation, get_pdf.call_args.args[0])
		# staff see it in the hub, with its desk link
		payload = hub.get_proposal(proposal, acting_user=BDE)
		self.assertEqual(payload["quotation"]["name"], quotation)
		self.assertIn(f"/app/quotation/{quotation}", payload["quotation"]["desk_url"])
		rows = hub_leads.get_lead(self.lead, acting_user=BDE)["quotations"]
		row = next(row for row in rows if row["name"] == quotation)
		self.assertEqual(row["tbo_proposal"], proposal)
		self.assertTrue(row["url"].endswith(f"/app/quotation/{quotation}"))

	def test_the_agreement_from_the_accepted_proposal(self):
		proposal = make_customised_proposal(self.lead, BDE, TEMPLATE)["name"]
		send_hub_proposal(proposal, BDE)
		accept_as_client(proposal)
		quotation = frappe.db.get_value(p.DOCTYPE, proposal, "quotation")
		self.assertEqual(frappe.db.get_value("Quotation", quotation, "tbo_quote_status"), q.ACCEPTED)
		# no quotation document: the agreement is next, made from the proposal
		self.assertRaises(frappe.ValidationError, make_hub_document, proposal, kinds.QUOTATION, BDE)
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual(
			(chain["next"]["action"], chain["next"]["document_type"], chain["next"]["source"]),
			("create", kinds.WORK_ORDER, proposal),
		)
		order = make_hub_document(proposal, kinds.WORK_ORDER, BDE)["proposal"]
		self.assertEqual((order["source_document"], order["quotation"]["name"]), (proposal, quotation))
		self.assertFalse(order["can_price"])
		bodies = {row["section_type"]: row["body"] for row in order["sections"]}
		self.assertIn(f"the proposal {proposal}", bodies["Investment"])
		self.assertIn(proposal, bodies["Purpose"])
		self.assertNotIn(quotation, bodies["Investment"] + bodies["Purpose"])
		with self.assertRaisesRegex(frappe.ValidationError, "revised proposal"):
			hub.price_from_modules(order["name"], acting_user=BDE)
		order = hub.save_proposal(order["name"], json.dumps({"start_date": nowdate()}), acting_user=BDE)
		self.assertEqual(order["send_blockers"], [])
		send_hub_proposal(order["name"], BDE)
		accept_as_client(order["name"], "Director")
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual([step["done"] for step in chain["steps"]][:3], [True, True, False])
		self.assertEqual(
			(chain["next"]["document_type"], chain["next"]["source"]), (kinds.BRD, order["name"])
		)
		brd_doc = make_hub_document(order["name"], kinds.BRD, BDE)["proposal"]
		self.assertEqual(brd_doc["source_document"], order["name"])

		# the Sales Order made from the accepted quotation carries the proposal
		frappe.set_user("Administrator")
		order_name = lead_quotation.make_sales_order(quotation)["sales_order"]
		self.assertEqual(frappe.db.get_value("Sales Order", order_name, "tbo_proposal"), proposal)
		# and on to the Sales Invoice made from the order
		sales_order = frappe.get_doc("Sales Order", order_name)
		sales_order.submit()
		self.assertEqual(make_sales_invoice(order_name).tbo_proposal, proposal)

	def test_off_keeps_the_quotation_step(self):
		frappe.set_user("Administrator")
		enable_client_documents()
		frappe.set_user(HUB)
		options = hub.get_proposal_options(acting_user=BDE)
		self.assertFalse(options["proposal_is_quotation"])
		types = {row["name"]: row for row in options["document_types"]}
		self.assertEqual(list(types)[:3], [kinds.PROPOSAL, kinds.QUOTATION, kinds.WORK_ORDER])
		self.assertEqual(types[kinds.WORK_ORDER]["follows"], kinds.QUOTATION)
		# the proposal's quotation is priced as before: no default taxes added
		proposal = make_customised_proposal(self.lead, BDE, TEMPLATE)["name"]
		quotation = frappe.get_doc("Quotation", frappe.db.get_value(p.DOCTYPE, proposal, "quotation"))
		self.assertFalse(quotation.taxes_and_charges)
		send_hub_proposal(proposal, BDE)
		accept_as_client(proposal)
		self.assertRaises(frappe.ValidationError, make_hub_document, proposal, kinds.WORK_ORDER, BDE)
		self.assertEqual(
			hub.get_document_chain(self.lead, acting_user=BDE)["next"]["document_type"], kinds.QUOTATION
		)


class TestFractionalDays(ClientDocumentTestCase):
	"""Customisation days on the service item's UOM (docs/proposals.md §4.1)."""

	def test_create_quotation_with_half_days_on_nos(self):
		# the owner's case: an unpriced accepted proposal, then Create quotation with 3.5 days
		proposal = make_customised_proposal(self.lead, BDE, TEMPLATE)["name"]
		send_hub_proposal(proposal, BDE)
		accept_as_client(proposal)
		frappe.set_user("Administrator")
		set_smart_lead_settings(proposal_service_item=SERVICE, proposal_day_rate=10000)
		frappe.set_user(HUB)
		result = make_hub_document(proposal, kinds.QUOTATION, BDE)
		self.assertEqual(result["warnings"], [])
		line = result["proposal"]["quotation"]["items"][-1]
		self.assertEqual((line["qty"], line["price_list_rate"]), (1, 35000))
		self.assertIn("3.5 person-days at", line["description"])

	def test_a_day_uom_keeps_the_days_as_the_quantity(self):
		frappe.set_user("Administrator")
		set_smart_lead_settings(proposal_service_item=DAY_SERVICE, proposal_day_rate=10000)
		frappe.set_user(HUB)
		proposal = make_customised_proposal(self.lead, BDE, TEMPLATE)
		line = proposal["quotation"]["items"][-1]
		self.assertEqual((line["qty"], line["price_list_rate"]), (3.5, 10000))
		self.assertEqual(line["description"], "Customisation (3.5 person-days)")


class TestBRD(ClientDocumentTestCase):
	def make_brd(self, ai_on: bool = False, *answers) -> dict:
		order = make_signed_agreement(self.lead, BDE, TEMPLATE, SERVICE)
		if not ai_on:
			return make_hub_document(order, kinds.BRD, BDE)
		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		frappe.set_user(HUB)
		with mock_ai(*answers) as post:
			result = make_hub_document(order, kinds.BRD, BDE)
		result["post"] = post
		return result

	def test_built_from_the_agreement_without_ai(self):
		result = self.make_brd()
		doc = result["proposal"]
		self.assertTrue(doc["name"].startswith("BRD-"))
		self.assertEqual(
			[row["section_type"] for row in doc["sections"]], [kind for kind, _h in kinds.BRD_SECTIONS]
		)
		bodies = {row["section_type"]: row["body"] for row in doc["sections"]}
		# only what the client told us, the rest to confirm
		self.assertIn("Payroll by hand", bodies["Pain Points"])
		self.assertIn("Tally and Excel", bodies["Current Process"])
		self.assertIn("To confirm with client", bodies["Business Objectives"])
		self.assertIn("## Accounts", bodies["Desired Process"])
		questions = doc["questionnaire"]["questions"]
		self.assertTrue(any("Accounts: how is this done today" in row["question"] for row in questions))
		self.assertEqual({row["asked_by"] for row in questions}, {"Library"})
		self.assertIn("Section | Question | Answer", bodies["Open Questions"])
		token = frappe.db.get_value(p.DOCTYPE, doc["name"], "questions_token")
		self.assertRegex(token, p.TOKEN)
		self.assertNotEqual(token, frappe.db.get_value(p.DOCTYPE, doc["name"], "public_token"))
		self.assertIsNone(doc["questionnaire"]["url"])

	def test_only_from_a_signed_agreement(self):
		quote = make_accepted_quotation_document(self.lead, BDE, TEMPLATE, SERVICE)
		order = make_hub_document(quote["name"], kinds.WORK_ORDER, BDE)["proposal"]["name"]
		self.assertRaises(frappe.ValidationError, make_hub_document, order, kinds.BRD, BDE)

	def test_the_ai_writes_from_the_clients_words_and_asks_for_the_gaps(self):
		sections = {
			"Pain Points": "- Salary takes 37 hours a month",
			"Business Objectives": "- To confirm with client: the objectives",
			"Success Criteria": "- Close the books in 12 days",
		}
		result = self.make_brd(
			True,
			brd_answer(
				sections,
				[{"section": "Success Criteria", "question": "What does a good month-end look like?"}],
			),
		)
		self.assertEqual(result["post"].call_count, 1)
		sent = json.loads(result["post"].call_args.kwargs["json"]["messages"][1]["content"])
		self.assertEqual(set(sent["sections"]), set(brd.AI_SECTIONS))
		self.assertEqual(sent["modules"], [row["module_name"] for row in result["proposal"]["modules"]])
		self.assertIn("never invent", result["post"].call_args.kwargs["json"]["messages"][0]["content"])
		bodies = {row["section_type"]: row["body"] for row in result["proposal"]["sections"]}
		self.assertEqual(bodies["Business Objectives"], sections["Business Objectives"])
		# "12 days" isn't in anything the client said: left as the data built it, with a warning
		self.assertNotIn("12 days", bodies["Success Criteria"])
		self.assertTrue(result["warnings"])
		# "37 hours" isn't either
		self.assertIn("Payroll by hand", bodies["Pain Points"])
		asked = {row["question"]: row["asked_by"] for row in result["proposal"]["questionnaire"]["questions"]}
		self.assertEqual(asked["What does a good month-end look like?"], "AI")
		self.assertTrue(
			frappe.db.exists(
				"TBO AI Usage Log", {"feature": "BRD draft", "reference_name": result["proposal"]["name"]}
			)
		)

	def test_the_client_answers_and_only_those_sections_change(self):
		doc = self.make_brd()["proposal"]
		url = hub.share_questionnaire(doc["name"], acting_user=BDE)["url"]
		token = url.rsplit("/", 1)[1]
		frappe.set_user("Guest")
		frappe.form_dict.token = token
		from tbo_smart.www import questions as page_view

		context = page_view.get_context(frappe._dict())
		frappe.set_user(HUB)
		self.assertEqual(context.q["total"], len(doc["questionnaire"]["questions"]))
		html = frappe.render_template("tbo_smart/www/questions.html", {**context, "_": frappe._})
		self.assertIn("Accounts: how is this done today", html)
		self.assertNotIn("Payroll by hand", html)

		questions = doc["questionnaire"]["questions"]
		current = next(row for row in questions if row["section_type"] == "Current Process")
		goal = next(row for row in questions if row["section_type"] == "Business Objectives")
		saved = call_as_guest(
			page.save_answers,
			token,
			json.dumps({current["name"]: "Tally, entered by two accountants", goal["name"]: ""}),
		)
		self.assertEqual(saved["saved"], 1)
		self.assertRaises(
			frappe.DoesNotExistError, call_as_guest, page.save_answers, token, json.dumps({"nope": "x"})
		)
		doc = hub.get_proposal(doc["name"], acting_user=BDE)
		self.assertEqual(doc["questionnaire"]["new_answers"], 1)
		self.assertTrue(any("new answers" in reason for reason in doc["send_blockers"]))

		before = {row["section_type"]: row["body"] for row in doc["sections"]}
		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		frappe.set_user(HUB)
		with mock_ai(
			brd_answer({"Current Process": "## Accounts\n- Tally, entered by two accountants"})
		) as post:
			result = hub.apply_answers(doc["name"], acting_user=BDE)
		sent = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
		self.assertEqual(sent["sections"], ["Current Process"])
		self.assertEqual(sent["client_answers"][0]["answer"], "Tally, entered by two accountants")
		after = {row["section_type"]: row["body"] for row in result["proposal"]["sections"]}
		self.assertEqual(after["Current Process"], "## Accounts\n- Tally, entered by two accountants")
		self.assertIn("Tally, entered by two accountants", after["Open Questions"])
		for kind in ("Pain Points", "Desired Process", "Business Objectives", "Stakeholders"):
			self.assertEqual(after[kind], before[kind])
		self.assertEqual(result["proposal"]["questionnaire"]["new_answers"], 0)
		self.assertRaises(frappe.ValidationError, hub.apply_answers, doc["name"], acting_user=BDE)

	def test_approved_closes_the_questionnaire(self):
		doc = self.make_brd()["proposal"]
		token = hub.share_questionnaire(doc["name"], acting_user=BDE)["url"].rsplit("/", 1)[1]
		send_hub_proposal(doc["name"], BDE)
		html = p.render_html(frappe.get_doc(p.DOCTYPE, doc["name"]))
		self.assertIn("Approve the BRD", html)
		accept_as_client(doc["name"])
		self.assertEqual(
			hub.get_document_chain(self.lead, acting_user=BDE)["steps"][3]["status_label"], "Approved"
		)
		question = doc["questionnaire"]["questions"][0]["name"]
		self.assertRaises(
			frappe.ValidationError, call_as_guest, page.save_answers, token, json.dumps({question: "late"})
		)
		self.assertRaises(frappe.DoesNotExistError, call_as_guest, page.save_answers, "0" * 32, "{}")
		# with BRDs switched off the link shows nothing
		frappe.set_user("Administrator")
		enable_client_documents(brds=False)
		frappe.set_user(HUB)
		self.assertIsNone(brd.by_questions_token(token))

	def test_presales_edits_the_questions(self):
		doc = self.make_brd()["proposal"]
		mine = [{"section_type": "Integrations", "question": "Which bank do you use?"}]
		doc = hub.save_questions(doc["name"], json.dumps(mine), acting_user=BDE)
		self.assertEqual(
			[(row["question"], row["asked_by"]) for row in doc["questionnaire"]["questions"]],
			[("Which bank do you use?", "Presales")],
		)


class TestSRS(ClientDocumentTestCase):
	def test_built_from_the_approved_brd_without_ai(self):
		brd_name = make_approved_brd(self.lead, BDE, TEMPLATE, SERVICE)
		spec = make_hub_document(brd_name, kinds.SRS, BDE)["proposal"]
		self.assertTrue(spec["name"].startswith("SRS-"))
		kinds_shown = [row["section_type"] for row in spec["sections"]]
		modules = [row["module_name"] for row in spec["modules"]]
		self.assertEqual(kinds_shown[:3], ["Cover", "Introduction", "User Roles"])
		self.assertEqual(kinds_shown[-2:], ["Non-functional Requirements", "Acceptance"])
		self.assertEqual(
			[row["heading"] for row in spec["sections"] if row["section_type"] == srs.MODULE_SECTION], modules
		)
		bodies = {row["heading"]: row["body"] for row in spec["sections"]}
		self.assertIn(brd_name, bodies["Introduction"])
		self.assertIn("FR-ACC-01 | Keep the chart of accounts", bodies["Accounts"])
		self.assertIn("## Validations", bodies["Accounts"])
		self.assertIn("Accounts Manager", bodies["User Roles & Permissions"])
		self.assertIn("Daily backups", bodies["Non-functional Requirements"])
		found = srs.functional_requirements(frappe.get_doc(p.DOCTYPE, spec["name"]))
		self.assertEqual(found[0]["id"], "FR-ACC-01")
		self.assertTrue(found[0]["acceptance"])
		self.assertEqual(len({row["id"] for row in found}), len(found))
		# a section a module is rebuilt from the library with no AI
		index = kinds_shown.index(srs.MODULE_SECTION)
		with mock_ai() as post:
			reset = hub.regenerate_section(spec["name"], index, acting_user=BDE)
		post.assert_not_called()
		self.assertEqual(reset["proposal"]["sections"][index]["body"], spec["sections"][index]["body"])

	def test_only_from_an_approved_brd(self):
		order = make_signed_agreement(self.lead, BDE, TEMPLATE, SERVICE)
		brd_name = make_hub_document(order, kinds.BRD, BDE)["proposal"]["name"]
		self.assertRaises(frappe.ValidationError, make_hub_document, brd_name, kinds.SRS, BDE)

	def test_one_ai_call_a_module_the_brd_speaks_of(self):
		brd_name = make_approved_brd(self.lead, BDE, TEMPLATE, SERVICE)
		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		frappe.set_user(HUB)
		answers = [
			srs_answer(
				[
					(
						"Post GST credit notes against invoices",
						"A credit note reduces the invoice's GST in GSTR-1",
					)
				]
			),
			*[srs_answer([]) for _i in range(20)],
		]
		with mock_ai(*answers) as post:
			result = make_hub_document(brd_name, kinds.SRS, BDE)
		spec = result["proposal"]
		calls = [json.loads(call.kwargs["json"]["messages"][1]["content"]) for call in post.call_args_list]
		self.assertEqual(len(calls), len({call["module"] for call in calls}), "one call a module")
		self.assertLessEqual(len(calls), len(spec["modules"]))
		first = calls[0]["module"]
		body = next(row["body"] for row in spec["sections"] if row["heading"] == first)
		self.assertIn("Post GST credit notes against invoices", body)
		self.assertTrue(
			frappe.db.exists("TBO AI Usage Log", {"feature": "SRS draft", "reference_name": spec["name"]})
		)

	def test_an_invented_number_is_left_out(self):
		brd_name = make_approved_brd(self.lead, BDE, TEMPLATE, SERVICE)
		spec = make_hub_document(brd_name, kinds.SRS, BDE)["proposal"]
		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		frappe.set_user(HUB)
		index = next(i for i, row in enumerate(spec["sections"]) if row["heading"] == "Accounts")
		with mock_ai(srs_answer([("Close payroll in 37 minutes", "Payroll closes in 37 minutes")])) as post:
			result = hub.regenerate_section(spec["name"], index, 1, acting_user=BDE)
		self.assertEqual(post.call_count, 1)
		self.assertNotIn("37 minutes", result["proposal"]["sections"][index]["body"])
		self.assertTrue(result["warnings"])


class TestImplementationPlan(ClientDocumentTestCase):
	def test_dated_phases_deliverables_raci_and_the_clients_part(self):
		plan_doc = make_plan_document(self.lead, BDE, TEMPLATE, SERVICE)
		self.assertTrue(plan_doc["name"].startswith("PIP-"))
		self.assertEqual(plan_doc["start_date"], getdate(nowdate()))
		bodies = {row["section_type"]: row["body"] for row in plan_doc["sections"]}
		start = frappe.utils.formatdate(nowdate(), "d MMM yyyy")
		self.assertIn("Phase | Days | From | To | Milestone", bodies["Project Timeline"])
		self.assertIn("Analysis | ", bodies["Project Timeline"])
		self.assertIn(start, bodies["Project Timeline"])
		self.assertIn("Analysis complete", bodies["Project Timeline"])
		self.assertIn("## Development & Configuration", bodies["Phase Deliverables"])
		self.assertIn(
			"Accounts: GST settings, tax templates, e-invoice and e-way bill", bodies["Phase Deliverables"]
		)
		self.assertIn("Phase | Project Manager", bodies["RACI Matrix"])
		self.assertIn("Testing & UAT | A", bodies["RACI Matrix"])
		self.assertIn(
			f"Name the project owner and the key user of each department | {start}",
			bodies["Client Responsibilities"],
		)
		self.assertIn("goes live by", bodies["Plan Overview"])
		# a draft plan can't make a project yet
		self.assertRaises(frappe.ValidationError, hub.get_project_plan, plan_doc["name"], acting_user=BDE)

	def test_the_hub_makes_the_project_and_records_the_uat(self):
		plan_doc = make_plan_document(self.lead, BDE, TEMPLATE, SERVICE)
		# a draft plan can't be linked to a project
		self.assertRaises(
			frappe.ValidationError, hub.link_project, plan_doc["name"], "PROJ-0041", acting_user=BDE
		)
		# what the client approves is what the project gets: an edited deliverable stands
		index = next(
			i for i, row in enumerate(plan_doc["sections"]) if row["section_type"] == "Phase Deliverables"
		)
		plan_doc["sections"][index]["body"] = "## Analysis\n- Our own workshop plan"
		hub.save_proposal(plan_doc["name"], json.dumps({"sections": plan_doc["sections"]}), acting_user=BDE)
		send_hub_proposal(plan_doc["name"], BDE)
		accept_as_client(plan_doc["name"])
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual(
			[step["document_type"] for step in chain["steps"]][-3:], [kinds.PLAN, "Project", "UAT"]
		)
		self.assertEqual(
			(chain["next"]["action"], chain["next"]["source"]), ("create_project", plan_doc["name"])
		)
		project = hub.get_project_plan(plan_doc["name"], acting_user=BDE)
		self.assertEqual(project["start"], str(getdate(nowdate())))
		self.assertEqual(project["phases"][0]["phase"], "Analysis")
		self.assertEqual(
			project["phases"][0]["tasks"][-1],
			{
				"subject": "Analysis complete",
				"start": project["phases"][0]["end"],
				"end": project["phases"][0]["end"],
				"milestone": 1,
			},
		)
		self.assertEqual(
			[task["subject"] for task in project["phases"][0]["tasks"]],
			["Our own workshop plan", "Analysis complete"],
		)
		chain = hub.link_project(
			plan_doc["name"], "PROJ-0042", "https://hub.example/helpdesk/projects/PROJ-0042", acting_user=BDE
		)
		self.assertEqual(chain["steps"][-2]["name"], "PROJ-0042")
		self.assertEqual(chain["next"]["action"], "uat")
		self.assertRaises(
			frappe.ValidationError, hub.link_project, plan_doc["name"], "PROJ-0043", acting_user=BDE
		)
		chain = hub.record_uat(plan_doc["name"], "Signed", "Asha Menon", acting_user=BDE)
		self.assertTrue(chain["steps"][-1]["done"])
		self.assertIsNone(chain["next"])
		self.assertEqual(
			frappe.db.get_value(p.DOCTYPE, plan_doc["name"], "warranty_starts_on"),
			getdate(add_days(nowdate(), 1)),
		)
		# Signed again (a retried call) keeps the date; Reopened withdraws the signature
		frappe.db.set_value(p.DOCTYPE, plan_doc["name"], "warranty_starts_on", "2026-01-02")
		hub.record_uat(plan_doc["name"], "Signed", "Someone Else", acting_user=BDE)
		self.assertEqual(
			frappe.db.get_value(p.DOCTYPE, plan_doc["name"], ["uat_signed_by", "warranty_starts_on"]),
			("Asha Menon", getdate("2026-01-02")),
		)
		hub.record_uat(plan_doc["name"], "Reopened", acting_user=BDE)
		self.assertEqual(
			frappe.db.get_value(p.DOCTYPE, plan_doc["name"], ["uat_signed_by", "warranty_starts_on"]),
			(None, None),
		)

	def test_a_template_with_its_own_phase_names_keeps_every_modules_work(self):
		from tbo_smart.lead_pipeline import implementation_plan as plan

		names = ["Discovery", "Blueprint", "Build", "Prove", "Launch", "Hypercare"]
		found = plan.phase_of_group(names)
		self.assertEqual(found["Business Analysis"], "Discovery")
		self.assertEqual(found["Development & Configuration"], "Build")
		self.assertEqual(found["Training & Handover"], "Hypercare")
		self.assertLessEqual(set(found.values()), set(names))


class TestUAT(ClientDocumentTestCase):
	def test_each_fr_becomes_a_test_case(self):
		plan_doc = make_plan_document(self.lead, BDE, TEMPLATE, SERVICE)
		# a draft plan has no tests yet
		self.assertRaises(frappe.ValidationError, hub.get_uat_cases, plan_doc["name"], acting_user=BDE)
		send_hub_proposal(plan_doc["name"], BDE)
		accept_as_client(plan_doc["name"])
		plan_name = plan_doc["name"]
		srs_name = frappe.db.get_value(p.DOCTYPE, plan_name, "source_document")
		found = hub.get_uat_cases(plan_name, acting_user=BDE)
		self.assertEqual(found["srs"], srs_name)
		frs = srs.functional_requirements(frappe.get_doc(p.DOCTYPE, srs_name))
		self.assertEqual([case["fr"] for case in found["cases"]], [row["id"] for row in frs])
		first = found["cases"][0]
		self.assertTrue(first["title"].startswith("FR-ACC-01: Keep the chart of accounts"))
		self.assertIn("2. Keep the chart of accounts", first["steps"])
		self.assertEqual(first["expected"], frs[0]["acceptance"])
		self.assertRaises(frappe.ValidationError, hub.get_uat_cases, srs_name, acting_user=BDE)
