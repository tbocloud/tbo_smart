import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from tbo_smart.api import hub_leads
from tbo_smart.api import hub_proposals as hub
from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline import proposals as p
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.test_utils import (
	accept_as_client,
	configure_quotation_approval,
	delete_owner_lead_records,
	disable_client_documents,
	disable_lead_quotations,
	disable_proposals,
	enable_client_documents,
	enable_proposals,
	make_accepted_proposal,
	make_accepted_quotation_document,
	make_gst_template,
	make_hub_document,
	make_hub_proposal,
	make_lead,
	make_outgoing_email_account,
	make_quotation_item,
	make_user,
	price_hub_proposal,
	send_hub_proposal,
	set_leaf_customer_group,
	set_segment_rates,
	set_smart_lead_settings,
)

HUB = "docs.hub@tbosmart.test"
BDE = "docs.bde@tbosmart.test"
OWNERS = (BDE,)
SERVICE = "TBO Smart Document Implementation"
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
		self.assertEqual([step["done"] for step in chain["steps"]], [True, True, False])
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
		self.assertTrue(p.render_pdf(doc).startswith(b"%PDF"))
		# signing needs the designation, and changes nothing on the quotation
		self.assertRaises(frappe.ValidationError, accept_as_client, order["name"])
		accept_as_client(order["name"], "Director")
		doc.reload()
		self.assertEqual((doc.status, doc.decided_by_designation), (p.ACCEPTED, "Director"))
		self.assertEqual(frappe.db.get_value("Quotation", doc.quotation, "tbo_quote_status"), q.ACCEPTED)
		self.assertIn("Signed", p.render_html(doc))
		chain = hub.get_document_chain(self.lead, acting_user=BDE)
		self.assertEqual(chain["steps"][2]["status_label"], "Signed")
		self.assertTrue(all(step["done"] for step in chain["steps"]))
		self.assertIsNone(chain["next"])
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
