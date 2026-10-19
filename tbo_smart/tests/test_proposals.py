import io
import json
import socket
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from tbo_smart.api import hub_leads
from tbo_smart.api import hub_proposals as hub
from tbo_smart.api import proposal_page as page
from tbo_smart.api.quotation_adjustment import decide_adjustment, get_pending_approvals
from tbo_smart.lead_pipeline import proposal_builder as builder
from tbo_smart.lead_pipeline import proposal_media as media
from tbo_smart.lead_pipeline import proposals as p
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.lead_pipeline.statuses import CLOSED_WON, PROPOSAL_SENT
from tbo_smart.sales.opportunity import lead_opportunity
from tbo_smart.test_utils import (
	CLIENT_PROFILE,
	call_as_guest,
	clear_default_letter_heads,
	configure_ai_providers,
	configure_quotation_approval,
	delete_owner_lead_records,
	disable_lead_quotations,
	disable_proposals,
	disable_smart_leads,
	enable_proposals,
	hours_from_now,
	make_hub_proposal,
	make_image_bytes,
	make_lead,
	make_lead_meeting,
	make_lead_note,
	make_letter_head,
	make_library_image,
	make_outgoing_email_account,
	make_pdf_bytes,
	make_private_file,
	make_quotation_item,
	make_sent_proposal,
	make_user,
	mock_ai,
	openai_answer,
	page_response,
	price_hub_proposal,
	proposal_token,
	save_lead_quotation,
	send_hub_proposal,
	set_leaf_customer_group,
	set_proposal_values,
	set_segment_rates,
	set_smart_lead_settings,
	upload_hub_image,
)

HUB = "prop.hub@tbosmart.test"
BDE = "prop.bde@tbosmart.test"
BDE2 = "prop.bde2@tbosmart.test"
BDM = "prop.bdm@tbosmart.test"
EDITOR = "prop.editor@tbosmart.test"
OWNERS = (BDE, BDE2, BDM)
SERVICE = "TBO Smart Proposal Implementation"
TEMPLATE = "ERPNext Implementation"
EMAIL = "frappe.core.doctype.communication.email.make"


class ProposalTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User", "Business Development Executive"])
		make_user(BDE2, ["Sales User", "Business Development Executive"])
		make_user(BDM, ["Sales User", "Business Development Manager"])
		make_user(EDITOR, ["Sales User", "Sales Manager"])
		set_leaf_customer_group()
		make_quotation_item(SERVICE)
		make_outgoing_email_account()
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		disable_proposals()
		disable_lead_quotations()
		disable_smart_leads()
		configure_quotation_approval(enabled=0)
		set_segment_rates()
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		enable_proposals()
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


class TestBuilding(ProposalTestCase):
	def test_a_proposal_starts_from_its_template(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		self.assertEqual(proposal["status"], p.DRAFT)
		self.assertTrue(proposal["title"].startswith("ERPNext Implementation for Malabar Spices"))
		# the Universal IT Proposal: all 23 sections, in order, every one on
		self.assertEqual(
			[row["section_type"] for row in proposal["sections"]], [kind for kind, _h in p.MASTER_SECTIONS]
		)
		self.assertTrue(all(row["enabled"] for row in proposal["sections"]))
		bodies = {row["section_type"]: row["body"] for row in proposal["sections"]}
		self.assertIn("Team Back Office", bodies["Company Overview"])
		# the template's modules, assembled from the library with no AI
		self.assertIn("Accounts", [row["module_name"] for row in proposal["modules"]])
		self.assertIn("GST settings, tax templates, e-invoice and e-way bill", bodies["Scope of Work"])
		self.assertIn("Accounts configured with GST", bodies["Deliverables"])
		self.assertIsNone(bodies["Executive Summary"])
		self.assertEqual(proposal["client_profile"]["industry"], "Spice export")
		# the timeline and payment terms templates are picked for it
		self.assertTrue(proposal["scoping"]["timeline_template"].endswith("days"))
		self.assertEqual(proposal["scoping"]["payment_terms"], "30 / 30 / 30 / 10")
		self.assertEqual((proposal["revision_no"], proposal["is_latest"]), (0, 1))
		self.assertIsNone(proposal["public_url"])
		self.assertRegex(proposal_token(proposal["name"]), p.TOKEN)
		self.assertEqual(frappe.session.user, HUB)
		options = hub.get_proposal_options(acting_user=BDE)
		self.assertTrue(options["enabled"])
		self.assertFalse(options["ai"])
		self.assertIn(TEMPLATE, [row["name"] for row in options["templates"]])
		self.assertIsInstance(options["packages"], list)

	def test_only_people_who_may_work_the_lead(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		self.assertRaises(frappe.PermissionError, hub.get_proposal, proposal["name"], acting_user=BDE2)
		self.assertRaises(frappe.PermissionError, make_hub_proposal, self.lead, BDE2, TEMPLATE)
		self.assertRaises(frappe.PermissionError, hub.save_proposal, proposal["name"], "{}", acting_user=BDE2)
		self.assertEqual(frappe.session.user, HUB)
		frappe.set_user(BDE2)
		self.assertEqual(frappe.get_list(p.DOCTYPE, filters={"lead": self.lead}), [])

	def test_switched_off(self):
		disable_proposals()
		self.assertEqual(hub.get_proposal_options(acting_user=BDE), {"enabled": False})
		self.assertRaises(frappe.ValidationError, make_hub_proposal, self.lead, BDE, TEMPLATE)

	def test_workflow_fields_cant_be_set_directly(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		other = make_lead(BDE, "Other Client", company_name=f"Other {frappe.generate_hash(length=6)}")
		frappe.set_user(BDE)
		elsewhere = save_lead_quotation(other, SERVICE, 5000)["name"]
		doc = frappe.get_doc(p.DOCTYPE, proposal["name"])
		doc.quotation = elsewhere
		self.assertRaises(frappe.PermissionError, doc.save)
		for field, value in (("status", p.ACCEPTED), ("view_count", 9), ("public_token", "0" * 32)):
			doc = frappe.get_doc(p.DOCTYPE, proposal["name"])
			doc.set(field, value)
			self.assertRaises(frappe.PermissionError, doc.save)
		frappe.set_user(HUB)
		send_hub_proposal(proposal["name"], BDE)
		frappe.set_user(BDE)
		doc = frappe.get_doc(p.DOCTYPE, proposal["name"])
		doc.title = "Changed after sending"
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_a_new_proposal_starts_as_a_fresh_draft(self):
		frappe.set_user(BDE)
		doc = frappe.get_doc(
			{
				"doctype": p.DOCTYPE,
				"lead": self.lead,
				"title": "Sneaky",
				"status": p.ACCEPTED,
				"view_count": 7,
				"decided_by_name": "Nobody",
				"revision_no": 4,
			}
		).insert()
		self.assertEqual(
			(doc.status, doc.view_count, doc.decided_by_name, doc.revision_no), (p.DRAFT, 0, None, 0)
		)
		self.assertRegex(doc.public_token, p.TOKEN)

	def test_editing_the_blocks(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		sections = [
			{"section_type": "Cover", "heading": "Hello", "body": "Para one.\n\n- first\n- second"},
			{"section_type": "Investment", "heading": "Investment"},
		]
		saved = hub.save_proposal(
			proposal["name"],
			json.dumps({"title": "Spice ERP", "theme": "Graphite", "sections": sections}),
			acting_user=BDE,
		)
		# one theme: a theme sent is ignored
		self.assertEqual((saved["title"], saved["theme"]), ("Spice ERP", "Ivory"))
		self.assertEqual([row["heading"] for row in saved["sections"]], ["Hello", "Investment"])
		self.assertRaises(
			frappe.ValidationError,
			hub.save_proposal,
			proposal["name"],
			json.dumps({"sections": [{"section_type": "Weird"}]}),
			acting_user=BDE,
		)
		self.assertEqual(
			p.text_parts(sections[0]["body"]),
			[{"kind": "text", "lines": ["Para one."]}, {"kind": "list", "items": ["first", "second"]}],
		)
		# two subheadings in a row keep the page's wrappers balanced
		sections[0]["body"] = "Intro.\n\n## One\n\n## Two\n\nText."
		hub.save_proposal(proposal["name"], json.dumps({"sections": sections}), acting_user=BDE)
		html = p.render_html(frappe.get_doc(p.DOCTYPE, proposal["name"]), preview=True)
		self.assertEqual(html.count("<div"), html.count("</div>"))
		parts = p.text_parts("## Phases\nPhase | Days\nAnalysis | 5\n\nLead → Order → Invoice")
		self.assertEqual([part["kind"] for part in parts], ["heading", "table", "chain"])
		self.assertEqual(parts[1]["rows"], [["Analysis", "5"]])
		self.assertEqual(parts[2]["steps"], ["Lead", "Order", "Invoice"])

	def test_pricing_is_the_leads_quotation_with_the_band(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		form = hub.get_pricing_form(proposal["name"], acting_user=BDE)
		self.assertEqual(form["pricing"]["discount_band"], 5)
		priced = price_hub_proposal(proposal["name"], BDE, SERVICE, discount=4)
		quotation = priced["quotation"]
		self.assertEqual((quotation["lead"], quotation["grand_total"]), (self.lead, 96000))
		self.assertEqual(quotation["needs_approval"], [])
		again = price_hub_proposal(proposal["name"], BDE, SERVICE, discount=2)
		self.assertEqual(again["quotation"]["name"], quotation["name"])
		self.assertEqual(again["quotation"]["grand_total"], 98000)


class TestAIDraft(ProposalTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		make_lead_note(self.lead, "Asha wants GST invoicing and stock across 3 warehouses.")
		meeting = make_lead_meeting(self.lead, scheduled_at=hours_from_now(-3))
		frappe.db.set_value(
			"TBO Lead Meeting",
			meeting,
			{
				"status": "MOM Submitted",
				"discussion_points": "Stock is tracked in spreadsheets.",
				"client_requirements": "Warehouse transfers",
			},
		)
		frappe.set_user(HUB)

	def narrative(self, invent: bool = False):
		"""An AI answer for the narrative call; it checks the brief is compact and complete."""

		def answer(url, body):
			brief = json.loads(body["messages"][1]["content"])
			self.assertLessEqual({row["key"] for row in brief["sections"]}, set(builder.AI_SECTIONS))
			self.assertIn("Spice export", brief["client_profile"])
			self.assertIn("Accounts", brief["modules"])
			self.assertNotIn("features", json.dumps(brief))  # module names only, no catalogue text
			self.assertLessEqual(len(brief["notes"]), builder.NOTES_CHARS)
			sections = {row["key"]: f"About {row['key']}: 3 warehouses." for row in brief["sections"]}
			if invent:
				sections["Executive Summary"] = "You will save 40% of your time."
			return openai_answer(
				{
					"sections": sections,
					"customisations": {
						item: f"{item}, done properly." for item in brief.get("customisations", [])
					},
				}
			)

		return answer

	def test_one_call_writes_the_narrative_and_the_customisations(self):
		scoping = {"customisation": 1, "customisations": [{"item": "Salary slip in our format", "days": 2}]}
		with mock_ai(self.narrative(invent=True)) as post:
			proposal = make_hub_proposal(self.lead, BDE, TEMPLATE, 1, scoping)
		self.assertEqual(post.call_count, 1)
		sent = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
		self.assertEqual([row["key"] for row in sent["sections"]], list(builder.AI_SECTIONS))
		bodies = {row["section_type"]: row["body"] for row in proposal["sections"]}
		self.assertEqual(bodies["Business Challenges"], "About Business Challenges: 3 warehouses.")
		self.assertIsNone(bodies["Executive Summary"])  # an invented number is left out
		# the benefits follow the data's component table
		self.assertIn("Component | What it does", bodies["Proposed Solution"])
		self.assertIn("About Proposed Solution", bodies["Proposed Solution"])
		# the customisation bullet becomes a description in the Scope of Work; its effort stays
		self.assertEqual(
			proposal["scoping"]["customisations"][0]["detail"], "Salary slip in our format, done properly."
		)
		self.assertIn(
			"Salary slip in our format | Salary slip in our format, done properly. | 2",
			bodies["Scope of Work"],
		)
		self.assertTrue(
			frappe.db.exists(
				"TBO AI Usage Log", {"feature": "Proposal draft", "reference_name": proposal["name"]}
			)
		)
		self.assertIsNone(hub.get_proposal(proposal["name"], acting_user=BDE)["ai"])  # editors only
		self.assertEqual(hub.get_proposal(proposal["name"], acting_user=EDITOR)["ai"]["runs"], 1)

	def test_regenerate_asks_before_writing_over_an_edit(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		index = next(
			i for i, row in enumerate(proposal["sections"]) if row["section_type"] == "Executive Summary"
		)
		sections = proposal["sections"]
		sections[index]["body"] = "Written by presales."
		saved = hub.save_proposal(proposal["name"], json.dumps({"sections": sections}), acting_user=BDE)
		self.assertEqual(saved["sections"][index]["edited"], 1)
		self.assertEqual(sum(row["edited"] for row in saved["sections"]), 1)
		asked = hub.regenerate_section(proposal["name"], index, acting_user=BDE)
		self.assertTrue(asked["confirm"])
		self.assertEqual(asked["proposal"]["sections"][index]["body"], "Written by presales.")
		with mock_ai(self.narrative()) as post:
			done = hub.regenerate_section(proposal["name"], index, 1, acting_user=BDE)
		self.assertEqual(post.call_count, 1)
		self.assertEqual(
			done["proposal"]["sections"][index]["body"], "About Executive Summary: 3 warehouses."
		)
		self.assertEqual(done["proposal"]["sections"][index]["edited"], 0)
		# a data section resets to the library with no AI
		scope = next(i for i, row in enumerate(sections) if row["section_type"] == "Scope of Work")
		with mock_ai() as post:
			reset = hub.regenerate_section(proposal["name"], scope, acting_user=BDE)
		post.assert_not_called()
		self.assertIn("## Business Analysis", reset["proposal"]["sections"][scope]["body"])

	def test_regenerate_all_keeps_the_hand_edits(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		sections = proposal["sections"]
		terms = next(i for i, row in enumerate(sections) if row["section_type"] == "Terms & Conditions")
		sections[terms]["body"] = "Our own terms."
		hub.save_proposal(
			proposal["name"],
			json.dumps(
				{"sections": sections, "scoping": {**proposal["scoping"], "modules": ["HR & Payroll"]}}
			),
			acting_user=BDE,
		)
		with mock_ai(self.narrative()):
			result = hub.regenerate_proposal(proposal["name"], acting_user=BDE)
		self.assertIn("Terms & Conditions", result["kept"])
		bodies = {row["section_type"]: row["body"] for row in result["proposal"]["sections"]}
		self.assertEqual(bodies["Terms & Conditions"], "Our own terms.")
		self.assertEqual(bodies["Executive Summary"], "About Executive Summary: 3 warehouses.")

	def test_the_requirements_box_fills_the_form(self):
		def answer(url, body):
			context = json.loads(body["messages"][1]["content"])
			self.assertIn("Accounts", context["modules"])
			return openai_answer(
				{
					"template": TEMPLATE,
					"modules": ["Accounts", "HR & Payroll", "Not A Module"],
					"customisations": ["Salary slip in our format", "Expense approval above 10k"],
					"users": 40,
					"hosting": "Frappe Cloud",
					"amc": True,
				}
			)

		frappe.set_user("Administrator")
		self.assertIn("Asha wants GST invoicing", builder.requirements_from_lead(self.lead))
		frappe.set_user(HUB)
		with mock_ai(answer):
			form = hub.extract_requirements(self.lead, "Need HR and Accounts, 40 users", acting_user=BDE)
		self.assertEqual(form["template"], TEMPLATE)
		self.assertEqual(form["scoping"]["modules"], ["Accounts", "HR & Payroll"])
		self.assertEqual(
			[row["item"] for row in form["scoping"]["customisations"]],
			["Salary slip in our format", "Expense approval above 10k"],
		)
		self.assertEqual((form["scoping"]["users"], form["scoping"]["hosting"]), (40, "Frappe Cloud"))

	def test_an_ai_failure_still_makes_the_proposal(self):
		with mock_ai():
			result = hub.create_proposal(
				self.lead, TEMPLATE, 1, None, None, json.dumps(CLIENT_PROFILE), acting_user=BDE
			)
		self.assertTrue(any("The AI couldn't write the narrative" in w for w in result["warnings"]))
		self.assertIn(
			"Team Back Office",
			next(
				row["body"]
				for row in result["proposal"]["sections"]
				if row["section_type"] == "Company Overview"
			),
		)

	def test_the_daily_limit(self):
		used = frappe.db.count(
			"TBO AI Usage Log",
			{"feature": ("in", list(builder.AI_FEATURES)), "creation": (">=", nowdate())},
		)
		frappe.set_user("Administrator")
		enable_proposals(ai=True, proposal_ai_daily_limit=used + 1)
		frappe.set_user(HUB)
		with mock_ai(self.narrative()):
			make_hub_proposal(self.lead, BDE, TEMPLATE, 1)
		self.assertRaises(frappe.ValidationError, make_hub_proposal, self.lead, BDE, TEMPLATE, 1)

	def test_ai_off_by_default(self):
		frappe.set_user("Administrator")
		enable_proposals()
		frappe.set_user(HUB)
		self.assertFalse(hub.get_proposal_options(acting_user=BDE)["ai"])
		self.assertRaises(frappe.ValidationError, make_hub_proposal, self.lead, BDE, TEMPLATE, 1)
		self.assertRaises(frappe.ValidationError, hub.extract_requirements, self.lead, "x", acting_user=BDE)


class TestSending(ProposalTestCase):
	def test_send_is_blocked_until_the_pricing_is_approved(self):
		frappe.set_user("Administrator")
		configure_quotation_approval(enabled=1, limits={"BDM": (20, 0)})
		frappe.set_user(HUB)
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		priced = price_hub_proposal(proposal["name"], BDE, SERVICE, discount=12)
		self.assertEqual(priced["quotation"]["needs_approval"][0]["limit"], 5)
		self.assertTrue(priced["send_blockers"])
		with patch(EMAIL) as make:
			self.assertRaises(frappe.ValidationError, hub.send_proposal, proposal["name"], acting_user=BDE)
		make.assert_not_called()
		waiting = hub.request_approval(proposal["name"], "Three-year contract", acting_user=BDE)
		self.assertIn("waiting for approval", waiting["send_blockers"][0])
		frappe.set_user(BDM)
		request = next(
			row for row in get_pending_approvals() if row["quotation"] == priced["quotation"]["name"]
		)
		decide_adjustment(request["name"], "approve")
		frappe.set_user(HUB)
		sent, make = send_hub_proposal(proposal["name"], BDE, attach_pdf=0)
		self.assertEqual(sent["status"], p.SENT)
		self.assertEqual(make.call_args.kwargs["doctype"], "Lead")
		self.assertEqual(make.call_args.kwargs["name"], self.lead)
		self.assertIn(sent["public_url"], make.call_args.kwargs["content"])
		quotation = frappe.db.get_value(
			"Quotation", priced["quotation"]["name"], ["docstatus", "tbo_quote_status"]
		)
		self.assertEqual(quotation, (1, q.SENT))
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), PROPOSAL_SENT)

	def test_send_without_pricing_moves_the_lead_and_can_attach_the_pdf(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		with patch("frappe.utils.pdf.get_pdf", return_value=make_pdf_bytes()):
			sent, make = send_hub_proposal(proposal["name"], BDE, attach_pdf=1, message="Hi Asha")
		self.assertEqual(make.call_args.kwargs["attachments"][0]["fname"], f"{proposal['name']}.pdf")
		self.assertIn("Hi Asha", make.call_args.kwargs["content"])
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), PROPOSAL_SENT)
		self.assertEqual(sent["tracking"]["sent_to"], frappe.db.get_value("Lead", self.lead, "email_id"))
		# a sent proposal is changed through a revision
		self.assertRaises(
			frappe.ValidationError, hub.save_proposal, proposal["name"], '{"title": "x"}', acting_user=BDE
		)
		lead = hub_leads.get_lead(self.lead, acting_user=BDE)
		self.assertEqual(lead["proposals"][0]["name"], proposal["name"])
		self.assertIn("proposal", [item["kind"] for item in lead["timeline"]])

	def test_send_needs_an_outgoing_email_account(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		with patch("tbo_smart.lead_pipeline.proposals.has_outgoing_email", return_value=False):
			blocked = hub.get_proposal(proposal["name"], acting_user=BDE)
			self.assertIn("outgoing email account", blocked["send_blockers"][0])
			self.assertRaises(frappe.ValidationError, send_hub_proposal, proposal["name"], BDE)
		self.assertEqual(frappe.db.get_value(p.DOCTYPE, proposal["name"], "status"), p.DRAFT)

	def test_revisions_keep_the_previous_version(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		price_hub_proposal(proposal["name"], BDE, SERVICE)
		send_hub_proposal(proposal["name"], BDE)
		old_token = proposal_token(proposal["name"])
		revision = hub.create_revision(proposal["name"], acting_user=BDE)
		self.assertEqual(
			(revision["revision_no"], revision["is_latest"], revision["status"]), (1, 1, p.DRAFT)
		)
		self.assertNotEqual(proposal_token(revision["name"]), old_token)
		self.assertEqual(
			frappe.db.get_value(p.DOCTYPE, proposal["name"], ["is_latest", "status"]), (0, p.SENT)
		)
		self.assertEqual([row["name"] for row in revision["revisions"]], [proposal["name"], revision["name"]])
		# repricing the revision revises the sent quotation
		repriced = price_hub_proposal(revision["name"], BDE, SERVICE, discount=2)
		self.assertNotEqual(repriced["quotation"]["name"], revision["quotation"]["name"])
		self.assertEqual(repriced["quotation"]["revision"], 1)
		self.assertEqual(
			frappe.db.get_value("Quotation", revision["quotation"]["name"], "tbo_quote_status"), q.REVISION
		)
		self.assertRaises(frappe.ValidationError, hub.send_proposal, proposal["name"], acting_user=BDE)


class TestClientPage(ProposalTestCase):
	def test_a_draft_or_bad_link_shows_nothing(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		token = proposal_token(proposal["name"])
		for bad in (token, "x" * 32, "../etc", None):
			self.assertIsNone(p.public_doc(bad))
			self.assertRaises(frappe.DoesNotExistError, call_as_guest, page.record_view, bad)
		self.assertIsNotNone(p.public_doc(token, preview=True))
		frappe.set_user("Administrator")
		disable_proposals()
		self.assertIsNone(p.public_doc(token, preview=True))

	def test_the_page_shows_the_proposal_and_nothing_private(self):
		frappe.set_user("Administrator")
		make_lead_note(self.lead, "Internal: they pay late, push for advance")
		frappe.set_user(HUB)
		name, _token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		html = p.render_html(frappe.get_doc(p.DOCTYPE, name))
		self.assertIn("ERPNext Implementation for Malabar Spices", html)
		self.assertIn(SERVICE, html)
		lead = frappe.db.get_value("Lead", self.lead, ["email_id", "lead_owner"], as_dict=True)
		for private in (lead.email_id, lead.lead_owner, "pay late", self.lead):
			self.assertNotIn(private, html)

	def test_line_descriptions_are_plain_text(self):
		name, _token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		quotation = frappe.db.get_value(p.DOCTYPE, name, "quotation")
		frappe.db.set_value(
			"Quotation Item", {"parent": quotation}, "description", "<p>Setup&nbsp;&amp; training</p>"
		)
		lines = p.pricing_table(quotation)["lines"]
		self.assertEqual(lines[0]["description"], "Setup\xa0& training")
		frappe.db.set_value("Quotation Item", {"parent": quotation}, "description", f"<div>{SERVICE}</div>")
		self.assertIsNone(p.pricing_table(quotation)["lines"][0]["description"])

	def test_an_expired_price_cant_be_accepted(self):
		name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		quotation = frappe.db.get_value(p.DOCTYPE, name, "quotation")
		frappe.db.set_value("Quotation", quotation, "tbo_quote_status", q.EXPIRED)
		self.assertRaises(frappe.ValidationError, call_as_guest, page.accept, token, "Asha", "a@b.example")
		# also before the nightly job marks it: its valid till has passed
		frappe.db.set_value(
			"Quotation", quotation, {"tbo_quote_status": q.SENT, "valid_till": add_days(nowdate(), -1)}
		)
		self.assertRaises(frappe.ValidationError, call_as_guest, page.accept, token, "Asha", "a@b.example")
		self.assertIn("was valid till", hub.get_proposal(name, acting_user=BDE)["send_blockers"][0])
		self.assertRaises(frappe.ValidationError, call_as_guest, page.decline, token, "no")
		context = p.page_context(frappe.get_doc(p.DOCTYPE, name))
		self.assertEqual((context["can_decide"], context["price_unavailable"]), (False, True))
		self.assertEqual(frappe.db.get_value(p.DOCTYPE, name, "status"), p.SENT)
		self.assertNotEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), CLOSED_WON)

	def test_views_are_counted_and_the_first_marks_it_viewed(self):
		name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		call_as_guest(page.record_view, token)
		call_as_guest(page.record_view, token)
		doc = frappe.get_doc(p.DOCTYPE, name)
		self.assertEqual((doc.status, doc.view_count), (p.VIEWED, 2))
		# someone signed in here (staff checking the link) counts nothing
		frappe.set_user(BDE)
		page.record_view(token)
		frappe.set_user(HUB)
		self.assertEqual(frappe.db.get_value(p.DOCTYPE, name, "view_count"), 2)
		self.assertTrue(doc.first_viewed_on and doc.last_viewed_on)
		self.assertEqual(frappe.db.get_value("Quotation", doc.quotation, "tbo_quote_status"), q.VIEWED)
		self.assertTrue(
			frappe.db.exists(
				"Comment", {"reference_name": self.lead, "content": ("like", "%opened the proposal%")}
			)
		)

	def test_accept_wins_the_lead_once(self):
		name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		self.assertRaises(frappe.ValidationError, call_as_guest, page.accept, token, "", "")
		self.assertRaises(frappe.ValidationError, call_as_guest, page.accept, token, "Asha", "not-an-email")
		call_as_guest(page.accept, token, "Asha Menon", "asha@malabar.example")
		doc = frappe.get_doc(p.DOCTYPE, name)
		self.assertEqual((doc.status, doc.decided_by_name), (p.ACCEPTED, "Asha Menon"))
		self.assertEqual(frappe.db.get_value("Quotation", doc.quotation, "tbo_quote_status"), q.ACCEPTED)
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), CLOSED_WON)
		self.assertTrue(lead_opportunity(self.lead))
		self.assertEqual(frappe.session.user, HUB)
		# the same answer again changes nothing; the other one is refused
		call_as_guest(page.accept, token, "Someone Else", "else@malabar.example")
		self.assertEqual(frappe.db.get_value(p.DOCTYPE, name, "decided_by_name"), "Asha Menon")
		self.assertRaises(frappe.ValidationError, call_as_guest, page.decline, token, "changed mind")
		# the PDF says so too
		self.assertIn("Accepted", p.render_html(frappe.get_doc(p.DOCTYPE, name), pdf=True))

	def test_decline_rejects_the_quotation_but_keeps_the_lead_open(self):
		name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		call_as_guest(page.decline, token, "Budget is next year")
		call_as_guest(page.decline, token, "again")
		doc = frappe.get_doc(p.DOCTYPE, name)
		self.assertEqual((doc.status, doc.decline_reason), (p.DECLINED, "Budget is next year"))
		self.assertEqual(frappe.db.get_value("Quotation", doc.quotation, "tbo_quote_status"), q.REJECTED)
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), PROPOSAL_SENT)
		self.assertRaises(frappe.ValidationError, call_as_guest, page.accept, token, "Asha", "a@b.example")

	def test_expired_and_older_versions_cant_be_answered(self):
		name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		frappe.set_user("Administrator")
		set_proposal_values(name, valid_till=add_days(nowdate(), -1))
		frappe.set_user(HUB)
		self.assertRaises(frappe.ValidationError, call_as_guest, page.accept, token, "Asha", "a@b.example")
		self.assertEqual(p.expire_proposals(), 1)
		self.assertEqual(frappe.db.get_value(p.DOCTYPE, name, "status"), p.EXPIRED)

		other, other_token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		hub.create_revision(other, acting_user=BDE)
		self.assertRaises(frappe.ValidationError, call_as_guest, page.decline, other_token, "no")
		self.assertTrue(p.page_context(frappe.get_doc(p.DOCTYPE, other))["superseded"])

	def test_pdf(self):
		_name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		with patch("frappe.utils.pdf.get_pdf", return_value=make_pdf_bytes()) as get_pdf:
			call_as_guest(page.download_pdf, token)
		self.assertEqual(frappe.local.response.type, "pdf")
		self.assertIn("Malabar Spices", get_pdf.call_args.args[0])


class TestImages(ProposalTestCase):
	def add_image(self, name, block="Image", **row):
		"""Upload a PNG and put it in a new block of `block` type; returns (payload, file)."""
		uploaded = upload_hub_image(name, BDE, make_image_bytes())
		sections = hub.get_proposal(name, acting_user=BDE)["sections"]
		sections.append(
			{"section_type": block, "image": uploaded["file"], "image_alt": "Our team at work", **row}
		)
		saved = hub.save_proposal(name, json.dumps({"sections": sections}), acting_user=BDE)
		return saved, uploaded["file"]

	def test_upload_checks_size_type_and_permission(self):
		name = make_hub_proposal(self.lead, BDE, TEMPLATE)["name"]
		uploaded = upload_hub_image(name, BDE, make_image_bytes("JPEG"), "team photo.png")
		file = frappe.get_doc("File", uploaded["file"])
		self.assertEqual((file.is_private, file.attached_to_name), (1, name))
		self.assertEqual(file.file_name, "team photo.jpg")  # named by its bytes, not its name
		self.assertIn("proposal_page.image?token=", uploaded["url"])
		upload_hub_image(name, BDE, make_image_bytes("WEBP"), "a.webp")
		svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
		for bad in (svg, make_image_bytes("GIF"), b"not an image", b""):
			self.assertRaises(frappe.ValidationError, upload_hub_image, name, BDE, bad, "x.png")
		with patch.object(media, "MAX_IMAGE_BYTES", 10):
			self.assertRaises(frappe.ValidationError, upload_hub_image, name, BDE, make_image_bytes())
		self.assertRaises(
			frappe.ValidationError,
			hub.upload_proposal_image,
			name,
			"x.png",
			"%%%not-base64",
			acting_user=BDE,
		)
		# write on the lead is needed
		self.assertRaises(frappe.PermissionError, upload_hub_image, name, BDE2, make_image_bytes())
		# a sent proposal takes no new images
		send_hub_proposal(name, BDE)
		self.assertRaises(frappe.ValidationError, upload_hub_image, name, BDE, make_image_bytes())

	def test_blocks_carry_only_their_own_images_with_alt_text(self):
		name = make_hub_proposal(self.lead, BDE, TEMPLATE)["name"]
		saved, file = self.add_image(name, image_caption="Kochi office", image_size="Half")
		row = saved["sections"][-1]
		self.assertEqual(
			(row["image"], row["image_size"], row["image_caption"]), (file, "Half", "Kochi office")
		)
		self.assertIn(file, row["image_url"])
		sections = saved["sections"]
		# alt text is required
		sections[-1]["image_alt"] = " "
		self.assertRaises(
			frappe.ValidationError,
			hub.save_proposal,
			name,
			json.dumps({"sections": sections}),
			acting_user=BDE,
		)
		# another proposal's image or any other file is refused
		other = make_hub_proposal(self.lead, BDE, TEMPLATE)["name"]
		foreign = upload_hub_image(other, BDE, make_image_bytes())["file"]
		frappe.set_user("Administrator")
		loose = make_private_file(make_image_bytes())
		frappe.set_user(HUB)
		for bad in (foreign, loose):
			sections[-1].update(image=bad, image_alt="x")
			self.assertRaises(
				frappe.PermissionError,
				hub.save_proposal,
				name,
				json.dumps({"sections": sections}),
				acting_user=BDE,
			)
		# the Investment (the price) and the Acceptance carry none
		sections[-1].update(section_type="Investment", image=file)
		saved = hub.save_proposal(name, json.dumps({"sections": sections}), acting_user=BDE)
		self.assertIsNone(saved["sections"][-1]["image"])

	def test_the_guest_endpoint_serves_only_this_proposals_images(self):
		name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		draft = hub.create_revision(name, acting_user=BDE)["name"]
		_saved, file = self.add_image(draft, block="Cover")
		draft_token = proposal_token(draft)
		other = make_hub_proposal(self.lead, BDE, TEMPLATE)["name"]
		_other_saved, other_file = self.add_image(other)
		frappe.set_user("Administrator")
		loose = make_private_file(make_image_bytes())
		frappe.set_user(HUB)

		call_as_guest(page.image, draft_token, file)
		self.assertEqual(frappe.local.response.type, "download")
		self.assertEqual(frappe.local.response.content_type, "image/png")
		self.assertEqual(frappe.local.response.display_content_as, "inline")
		self.assertEqual(frappe.local.response.filecontent[:4], b"\x89PNG")
		for bad_token, bad_file in (
			(draft_token, other_file),  # another proposal's image
			(token, file),  # the image of another revision, with the earlier one's link
			(draft_token, loose),  # a private file attached to nothing
			(draft_token, "no-such-file"),
			("0" * 32, file),  # an unknown link
		):
			self.assertRaises(frappe.DoesNotExistError, call_as_guest, page.image, bad_token, bad_file)

	def test_images_on_the_page_in_a_revision_and_in_the_pdf(self):
		name = make_hub_proposal(self.lead, BDE, TEMPLATE)["name"]
		_saved, cover = self.add_image(name, block="Cover")
		_saved, file = self.add_image(name)
		unused = upload_hub_image(name, BDE, make_image_bytes())["file"]
		html = p.render_html(frappe.get_doc(p.DOCTYPE, name), preview=True)
		self.assertIn(f"file={file}", html)
		self.assertIn('alt="Our team at work"', html)
		self.assertIn('class="sr-only">Image</h2>', html)  # no heading of its own
		self.assertNotIn("/private/files/", html)
		pdf_html = p.render_html(frappe.get_doc(p.DOCTYPE, name), pdf=True)
		self.assertIn("data:image/png;base64,", pdf_html)
		self.assertNotIn("proposal_page.image", pdf_html)
		# sending deletes the uploads the proposal doesn't show
		send_hub_proposal(name, BDE)
		self.assertFalse(frappe.db.exists("File", unused))
		self.assertTrue(frappe.db.exists("File", file))
		# a revision shows the same images, as its own files
		revision = hub.create_revision(name, acting_user=BDE)
		copies = [row["image"] for row in revision["sections"] if row["image"]]
		self.assertEqual(len(copies), 2)
		self.assertFalse({cover, file} & set(copies))
		for copy in copies:
			copied = frappe.get_doc("File", copy)
			self.assertEqual((copied.attached_to_name, copied.is_private), (revision["name"], 1))
			self.assertEqual(
				copied.file_url,
				frappe.db.get_value("File", file if copy == copies[-1] else cover, "file_url"),
			)
			call_as_guest(page.image, proposal_token(revision["name"]), copy)
		with patch("frappe.utils.pdf.get_pdf", return_value=make_pdf_bytes()) as get_pdf:
			call_as_guest(page.download_pdf, proposal_token(revision["name"]), "1")
		self.assertIn("data:image/png;base64,", get_pdf.call_args.args[0])


class TestLetterHead(ProposalTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		clear_default_letter_heads()
		self.site_default = make_letter_head(
			"Prop LH Default", "<p>Site default header</p>", "<p>Site default footer</p>", is_default=1
		)
		self.chosen = make_letter_head(
			"Prop LH Chosen",
			'<p onclick="steal()">TBO letter head<script>alert(1)</script></p>',
			"<p>Kochi · www.teambackoffice.com</p>",
		)
		self.other = make_letter_head("Prop LH Other", "<p>Other header</p>")
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_default_letter_heads()
		for name in (self.site_default, self.chosen, self.other):
			frappe.delete_doc("Letter Head", name, force=True)
		super().tearDown()

	def context(self, name):
		return p.page_context(frappe.get_doc(p.DOCTYPE, name))["letter_head"]

	def test_fallback_order_and_override(self):
		name = make_hub_proposal(self.lead, BDE, TEMPLATE)["name"]
		# the Letter Head marked default
		self.assertEqual(self.context(name)["name"], self.site_default)
		# the setting wins over it; editors set it from the hub
		self.assertRaises(frappe.PermissionError, hub.set_default_letter_head, self.chosen, acting_user=BDE)
		options = hub.set_default_letter_head(self.chosen, acting_user=EDITOR)
		self.assertEqual(options["default_letter_head"], self.chosen)
		self.assertIn(self.other, [row["name"] for row in options["letter_heads"]])
		self.assertEqual(self.context(name)["name"], self.chosen)
		# the proposal's own pick wins over both
		saved = hub.save_proposal(name, json.dumps({"letter_head": self.other}), acting_user=BDE)
		self.assertEqual(saved["letter_head_preview"]["name"], self.other)
		# blank: back to the default
		saved = hub.save_proposal(name, json.dumps({"letter_head": None}), acting_user=BDE)
		self.assertEqual(saved["letter_head_preview"]["name"], self.chosen)
		# a disabled pick falls back; an unknown one is refused; no Letter Head at all is the logo
		hub.save_proposal(name, json.dumps({"letter_head": self.other}), acting_user=BDE)
		frappe.db.set_value("Letter Head", self.other, "disabled", 1)
		self.assertEqual(self.context(name)["name"], self.chosen)
		self.assertRaises(
			frappe.ValidationError,
			hub.save_proposal,
			name,
			json.dumps({"letter_head": "No Such Head"}),
			acting_user=BDE,
		)
		frappe.set_user("Administrator")
		clear_default_letter_heads()
		frappe.db.set_value("Letter Head", self.site_default, "is_default", 0)
		frappe.set_user(HUB)
		self.assertIsNone(self.context(name))
		self.assertIn('class="brand', p.render_html(frappe.get_doc(p.DOCTYPE, name), preview=True))
		self.assertIsNone(hub.get_proposal_options(acting_user=BDE)["default_letter_head"])

	def test_the_html_is_sanitised_and_shown_on_the_page_and_pdf(self):
		hub.set_default_letter_head(self.chosen, acting_user=EDITOR)
		name, _token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		html = p.render_html(frappe.get_doc(p.DOCTYPE, name))
		self.assertIn("TBO letter head", html)
		self.assertIn("www.teambackoffice.com", html)
		for unsafe in ("<script>alert", "onclick", "steal()"):
			self.assertNotIn(unsafe, html)
		self.assertNotIn('class="brand', html)
		pdf_html = p.render_html(frappe.get_doc(p.DOCTYPE, name), pdf=True)
		self.assertIn('id="header-html"', pdf_html)
		self.assertIn('id="footer-html"', pdf_html)

	def test_a_private_letter_head_image_is_served_through_the_token(self):
		frappe.set_user("Administrator")
		file = make_private_file(make_image_bytes(), "header.png")
		url = frappe.db.get_value("File", file, "file_url")
		head = make_letter_head("Prop LH Image", source="Image", image=url)
		frappe.set_user(HUB)
		hub.set_default_letter_head(head, acting_user=EDITOR)
		try:
			name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
			# served by a File of that URL (Letter Head may attach its own copy)
			served = self.context(name)["header"]["image"].rsplit("file=", 1)[1]
			self.assertEqual(frappe.db.get_value("File", served, "file_url"), url)
			call_as_guest(page.image, token, served)
			self.assertEqual(frappe.local.response.content_type, "image/png")
			pdf_header = p.page_context(frappe.get_doc(p.DOCTYPE, name), pdf=True)["letter_head"]["header"]
			self.assertTrue(pdf_header["image"].startswith("data:image/png;base64,"))
			# a square image at the content's width would be too tall: it is narrowed instead
			self.assertEqual(pdf_header["height"], media.HEADER_MAX_MM)
			# once the proposal uses another letter head, that file is no longer served
			hub.set_default_letter_head(self.chosen, acting_user=EDITOR)
			self.assertRaises(frappe.DoesNotExistError, call_as_guest, page.image, token, served)
		finally:
			frappe.set_user("Administrator")
			frappe.delete_doc("Letter Head", head, force=True)
			frappe.delete_doc("File", file, force=True)


class TestMaster(ProposalTestCase):
	"""The Universal IT Proposal: modules, templates, sharing, the page, the libraries, the client
	profile."""

	def test_modules_price_the_quotation(self):
		frappe.set_user("Administrator")
		set_smart_lead_settings(proposal_service_item=SERVICE, proposal_day_rate=6000)
		for module, price in (("Accounts", 120000), ("HR & Payroll", 90000)):
			self.addCleanup(
				frappe.db.set_value,
				"TBO Proposal Module",
				module,
				"default_price",
				frappe.db.get_value("TBO Proposal Module", module, "default_price"),
			)
			frappe.db.set_value("TBO Proposal Module", module, "default_price", price)
		frappe.set_user(HUB)
		scoping = {
			"modules": ["Accounts", "HR & Payroll"],
			"customisation": 1,
			"customisations": [{"item": "Salary slip in our format", "days": 2}],
		}
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE, 0, scoping)
		lines = [
			(row["description"], row["qty"], row["price_list_rate"]) for row in proposal["quotation"]["items"]
		]
		self.assertEqual(
			lines,
			[("Accounts", 1, 120000), ("HR & Payroll", 1, 90000), ("Customisation (2 person-days)", 2, 6000)],
		)
		# a price set by hand stays; the others take the list price again
		modules = proposal["modules"]
		modules[0].update(rate=100000, rate_overridden=1)
		modules[1].update(rate=1)
		saved = hub.save_proposal(proposal["name"], json.dumps({"modules": modules}), acting_user=BDE)
		self.assertEqual([row["rate"] for row in saved["modules"]], [100000, 90000])
		priced = hub.price_from_modules(proposal["name"], acting_user=BDE)
		self.assertEqual(priced["quotation"]["items"][0]["price_list_rate"], 100000)
		html = p.render_html(frappe.get_doc(p.DOCTYPE, proposal["name"]))
		self.assertIn("HR &amp; Payroll", html)  # a service-item line is named by its module

	def test_templates_timeline_payment_terms_and_amc(self):
		proposal = make_hub_proposal(
			self.lead, BDE, TEMPLATE, 0, {"timeline_template": "45 days", "amc": 1, "amc_plan": "Premium"}
		)
		bodies = {row["section_type"]: row["body"] for row in proposal["sections"]}
		self.assertIn("Planned over 45 days.", bodies["Project Timeline"])
		self.assertIn("We recommend Premium.", bodies["AMC"])
		context = p.page_context(frappe.get_doc(p.DOCTYPE, proposal["name"]))
		timeline = next(
			block["timeline"] for block in context["blocks"] if block["type"] == "Project Timeline"
		)
		self.assertEqual((timeline["total"], timeline["unit"]), ("45", "Days"))
		self.assertEqual(timeline["bars"][0]["left"], 0)
		# the milestones add up to 100
		sections = proposal["sections"]
		terms = next(row for row in sections if row["section_type"] == "Payment Terms")
		terms["body"] = "Milestone | Share\nAdvance | 50%\nGo-live | 40%"
		self.assertRaises(
			frappe.ValidationError,
			hub.save_proposal,
			proposal["name"],
			json.dumps({"sections": sections}),
			acting_user=BDE,
		)
		# a timeline template's phases add up to its days, payment terms to 100%
		frappe.set_user("Administrator")
		for library, body, days in (
			("Timeline", "A | 10\nB | 10", 30),
			("Payment Terms", "A | 60\nB | 30", 0),
		):
			doc = frappe.get_doc(
				{
					"doctype": "TBO Proposal Library Item",
					"library": library,
					"title": "Bad",
					"body": body,
					"weeks": days,
				}
			)
			self.assertRaises(frappe.ValidationError, doc.insert)

	def test_the_suggested_timeline_follows_the_effort(self):
		small = make_hub_proposal(self.lead, BDE, TEMPLATE, 0, {"modules": ["Training"]})
		large = make_hub_proposal(
			self.lead, BDE, TEMPLATE, 0, {"modules": [m.module_name for m in builder.catalogue()]}
		)
		self.assertEqual(small["scoping"]["timeline_template"], "45 days")
		self.assertEqual(large["scoping"]["timeline_template"], "120 days")

	def test_the_client_profile_is_required(self):
		self.assertRaises(
			frappe.ValidationError,
			hub.create_proposal,
			self.lead,
			TEMPLATE,
			0,
			None,
			None,
			json.dumps({"industry": "Spice export"}),
			acting_user=BDE,
		)
		defaults = hub.get_client_profile(self.lead, acting_user=BDE)
		self.assertTrue(defaults["profile"]["name"].startswith("Malabar Spices"))

	def test_share_link_sends_a_draft_only_when_it_may_go(self):
		frappe.set_user("Administrator")
		configure_quotation_approval(enabled=1, limits={"BDM": (20, 0)})
		frappe.set_user(HUB)
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		token = proposal_token(proposal["name"])
		self.assertIsNone(p.public_doc(token))  # a draft's link shows nothing
		price_hub_proposal(proposal["name"], BDE, SERVICE, discount=12)
		self.assertRaises(frappe.ValidationError, hub.share_proposal, proposal["name"], acting_user=BDE)
		price_hub_proposal(proposal["name"], BDE, SERVICE, discount=2)
		with (
			patch("tbo_smart.lead_pipeline.proposals.has_outgoing_email", return_value=False),
			patch(EMAIL) as make,
		):
			shared = hub.share_proposal(proposal["name"], acting_user=BDE)
		make.assert_not_called()  # no email, and no email account needed
		self.assertEqual(shared["proposal"]["status"], p.SENT)
		self.assertEqual(shared["url"], shared["proposal"]["public_url"])
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), PROPOSAL_SENT)
		self.assertIsNotNone(p.public_doc(token))
		# sharing again just gives the link
		self.assertEqual(hub.share_proposal(proposal["name"], acting_user=BDE)["url"], shared["url"])

	def test_the_page_numbers_parts_contents_and_acceptance(self):
		name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		context = p.page_context(frappe.get_doc(p.DOCTYPE, name))
		numbered = [block for block in context["blocks"] if block["number"]]
		self.assertEqual([block["number"] for block in numbered], list(range(1, len(numbered) + 1)))
		self.assertEqual(numbered[0]["part"], "Part A — Business Proposal")
		self.assertEqual(
			[group["part"] for group in context["toc"]],
			[
				"Part A — Business Proposal",
				"Part B — Technical & Implementation Proposal",
				"Part C — Commercial Proposal",
			],
		)
		scope = next(block for block in context["blocks"] if block["type"] == "Scope of Work")
		self.assertTrue(scope["parts"][0]["number"].endswith(".1"))
		cards = next(block["modules"] for block in context["blocks"] if block["type"] == "Modules & Features")
		self.assertEqual(cards[0]["name"], "Accounts")
		payment = next(block for block in context["blocks"] if block["type"] == "Payment Terms")
		self.assertEqual(payment["parts"][0]["header"][-1], "Amount")
		# the share card: title and client only, never a price
		share = context["share"]
		self.assertIn("Malabar Spices", share["description"])
		for card in (share["title"], share["description"]):
			self.assertNotIn("₹", card)
		html = p.render_html(frappe.get_doc(p.DOCTYPE, name))
		self.assertIn('rel="icon"', html)
		self.assertIn('property="og:title"', html)
		self.assertIn("tbo-proposal-icon", html)
		# accepting fills the acceptance block
		call_as_guest(page.accept, token, "Asha Menon", "asha@malabar.example")
		acceptance = p.page_context(frappe.get_doc(p.DOCTYPE, name))["acceptance"]
		self.assertEqual((acceptance["name"], acceptance["accepted"]), ("Asha Menon", True))
		self.assertEqual(acceptance["email"], "a•••@malabar.example")  # never the full address
		self.assertNotIn("asha@malabar.example", p.render_html(frappe.get_doc(p.DOCTYPE, name)))

	def test_the_pdf_is_ivory_with_page_numbers(self):
		from pypdf import PdfReader, PdfWriter

		writer = PdfWriter()
		for _n in range(3):
			writer.add_blank_page(595, 842)
		buffer = io.BytesIO()
		writer.write(buffer)
		pages = PdfReader(io.BytesIO(p.on_ivory(buffer.getvalue()))).pages
		self.assertEqual(len(pages), 3)
		self.assertNotIn("1 / 3", pages[0].extract_text())  # not on the cover
		self.assertIn("2 / 3", pages[1].extract_text())
		fonts = [font.get_object()["/BaseFont"] for font in pages[1]["/Resources"]["/Font"].values()]
		self.assertIn("/Geist-Regular", fonts)  # the page's own font, embedded

	def test_the_pdf_page_is_a4_with_18_mm_margins(self):
		self.assertEqual(
			p.pdf_page({"header": {"image": "data:", "height": 14.5}}),
			{"header": 14.5, "top": 40.5, "bottom": 27, "cover": 227.5},
		)
		self.assertEqual(p.pdf_page(None)["top"], p.MARGIN_MM + p.BRAND_HEADER_MM + p.HEADER_GAP_MM)
		options = p.pdf_options(p.pdf_page(None))
		self.assertEqual(
			(options["page-size"], options["margin-bottom"], options["margin-left"]), ("A4", "27mm", "0mm")
		)

	def test_the_contents_take_each_page_after_the_contents(self):
		toc = [
			{"part": None, "items": [{"number": 1, "heading": "Summary"}, {"number": 2, "heading": "Scope"}]}
		]
		texts = ["1 Summary", "Contents 1 Summary . . . 2 Scope", "1  Summary of it", "", "2\nScope"]
		reader = MagicMock(pages=[MagicMock(**{"extract_text.return_value": text}) for text in texts])
		with patch("pypdf.PdfReader", return_value=reader):
			self.assertTrue(p.number_contents(toc, b""))
		self.assertEqual([item["page"] for item in toc[0]["items"]], [3, 5])

	def test_the_pdf_contents_have_page_numbers_and_dot_leaders(self):
		doc = frappe.get_doc(p.DOCTYPE, make_hub_proposal(self.lead, BDE, TEMPLATE)["name"])

		def found(toc, _pdf):
			toc[0]["items"][0]["page"] = 3
			return True

		with (
			patch("frappe.utils.pdf.get_pdf", return_value=make_pdf_bytes()) as get_pdf,
			patch.object(p, "number_contents", side_effect=found),
		):
			self.assertTrue(p.render_pdf(doc).startswith(b"%PDF"))
		first, second = (call.args[0] for call in get_pdf.call_args_list)
		self.assertNotIn('<span class="pg">', first)
		self.assertIn('<ol class="leaders">', second)
		self.assertIn('<span class="pg">3</span>', second)
		self.assertNotIn('href="#block-', second)  # in the PDF a link would open the web page
		self.assertIn("data:font/ttf;base64,", second)  # the font travels inside the PDF
		self.assertEqual(get_pdf.call_args.args[1]["margin-right"], "0mm")
		self.assertNotIn("fonts.googleapis.com", p.render_html(doc))

	def test_libraries_are_for_editors(self):
		data = json.dumps({"library": "Assumption", "title": "The client gives VPN access."})
		self.assertRaises(frappe.PermissionError, hub.save_library_record, "item", data, acting_user=BDE)
		library = hub.save_library_record("item", data, acting_user=EDITOR)
		item = next(row for row in library["items"] if row["title"] == "The client gives VPN access.")
		self.assertIn("Timeline", library["libraries"])
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		bodies = {row["section_type"]: row["body"] for row in proposal["sections"]}
		self.assertIn("The client gives VPN access.", bodies["Assumptions"])
		self.assertRaises(
			frappe.PermissionError, hub.delete_library_record, "item", item["name"], acting_user=BDE
		)
		hub.delete_library_record("item", item["name"], acting_user=EDITOR)
		self.assertRaises(frappe.ValidationError, hub.save_library_record, "weird", data, acting_user=EDITOR)

	def test_draft_with_ai_fills_a_module_once_and_saves_nothing(self):
		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		frappe.set_user(HUB)
		draft = {
			"description": "Fixed assets.",
			"features": ["Register", "Depreciation"],
			"scope": ["Development & Configuration | Asset categories"],
		}
		self.assertRaises(frappe.PermissionError, hub.draft_library_module, "Assets", acting_user=BDE)
		with mock_ai(openai_answer(draft)):
			result = hub.draft_library_module("Assets", acting_user=EDITOR)
		self.assertEqual(result["draft"]["features"], "Register\nDepreciation")
		self.assertNotEqual(
			frappe.db.get_value("TBO Proposal Module", "Assets", "description"), "Fixed assets."
		)

	def test_library_images_are_picked_by_tag(self):
		frappe.set_user("Administrator")
		image = make_library_image("Kochi team", "Cover\nProject Team")
		frappe.set_user(HUB)
		try:
			proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
			cover = proposal["sections"][0]
			self.assertTrue(cover["image"])
			self.assertEqual(cover["image_alt"], "Kochi team picture")
			self.assertEqual(
				frappe.db.get_value("File", cover["image"], "attached_to_name"), proposal["name"]
			)
			call_as_guest(page.image, proposal_token(proposal["name"]), cover["image"])
			picked = hub.use_library_image(proposal["name"], image, acting_user=BDE)
			self.assertIn("proposal_page.image", picked["url"])
			frappe.db.set_value("TBO Proposal Image", image, "enabled", 0)
			options = hub.get_proposal_options(acting_user=BDE)
			self.assertNotIn(image, [row["name"] for row in options["images"]])
		finally:
			frappe.set_user("Administrator")
			frappe.delete_doc("TBO Proposal Image", image, force=True)

	def test_reading_the_website_is_guarded_and_kept_on_the_lead(self):
		from tbo_smart.lead_pipeline import proposal_profile as profile

		for url in (
			"ftp://client.example",
			"http://127.0.0.1/",
			"http://10.0.0.5/",
			"http://169.254.169.254/",
		):
			self.assertRaises(profile.SiteUnreachable, profile.checked_url, url)
		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		frappe.set_user(HUB)
		html = "<html><body><script>ignore()</script><p>We export spices.</p><a href='/about'>About</a></body></html>"
		public = [(2, 1, 6, "", ("93.184.216.34", 0))]
		summary = {"about": "Spice exporter", "industry": "Food export", "locations": "Kochi"}
		with (
			patch("socket.getaddrinfo", return_value=public),
			patch(
				"tbo_smart.lead_pipeline.proposal_profile.requests.get", return_value=page_response(html)
			) as get,
			mock_ai(openai_answer(summary)) as post,
		):
			read = hub.read_client_website(self.lead, "client.example", acting_user=BDE)
			again = hub.read_client_website(self.lead, None, acting_user=BDE)
			hub.read_client_website(self.lead, "client.example", acting_user=BDE)  # no scheme: still kept
		self.assertEqual(read["profile"]["industry"], "Food export")
		self.assertEqual(again["profile"], read["profile"])  # kept on the lead: read once
		self.assertEqual(get.call_count, 2)  # the home page and its About page
		self.assertEqual(post.call_count, 1)
		sent = json.loads(post.call_args.kwargs["json"]["messages"][1]["content"])
		self.assertNotIn("ignore()", sent["site_text"])
		self.assertIn("untrusted", post.call_args.kwargs["json"]["messages"][0]["content"])
		defaults = hub.get_client_profile(self.lead, acting_user=BDE)
		self.assertTrue(defaults["from_web"])
		# a redirect to a private address is refused
		redirect = page_response("")
		redirect.is_redirect = True
		redirect.status_code = 302
		redirect.headers = {"Location": "http://192.168.1.1/admin"}

		def resolve(host, *args, **kwargs):
			return [(2, 1, 6, "", ("192.168.1.1" if host == "192.168.1.1" else "93.184.216.34", 0))]

		with (
			patch("socket.getaddrinfo", side_effect=resolve),
			patch("tbo_smart.lead_pipeline.proposal_profile.requests.get", return_value=redirect),
		):
			self.assertRaises(profile.SiteUnreachable, profile.fetch, "https://client.example/")

	def test_old_proposals_move_to_the_master_sections(self):
		from tbo_smart.patches.v1 import proposal_master_sections

		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		frappe.db.set_value(
			"TBO Proposal Section",
			{"parent": proposal["name"], "section_type": "Investment"},
			"section_type",
			"Pricing",
		)
		set_proposal_values(proposal["name"], theme="Graphite")
		proposal_master_sections.execute()
		self.assertTrue(
			frappe.db.exists(
				"TBO Proposal Section", {"parent": proposal["name"], "section_type": "Investment"}
			)
		)
		self.assertEqual(frappe.db.get_value(p.DOCTYPE, proposal["name"], "theme"), "Ivory")

	def test_the_assistant_edits_in_words_with_structured_changes(self):
		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		frappe.set_user(HUB)
		chat = [
			{"role": "assistant", "text": "What does the client need?"},
			{"role": "presales", "text": "HR"},
		]
		created = hub.create_proposal(
			self.lead, TEMPLATE, 0, None, None, json.dumps(CLIENT_PROFILE), json.dumps(chat), acting_user=BDE
		)["proposal"]
		self.assertEqual([row["text"] for row in created["chat"]], ["What does the client need?", "HR"])
		terms = next(
			i for i, row in enumerate(created["sections"]) if row["section_type"] == "Terms & Conditions"
		)
		created["sections"][terms]["body"] = "Our own terms."
		hub.save_proposal(created["name"], json.dumps({"sections": created["sections"]}), acting_user=BDE)

		def answer(url, body):
			context = json.loads(body["messages"][1]["content"])
			self.assertNotIn("chat", context["state"])  # the state, never the transcript
			self.assertIn("HR & Payroll", context["state"]["catalogue"])
			return openai_answer(
				{
					"add_modules": ["HR & Payroll", "Not A Module"],
					"timeline_template": "90 days",
					"amc": False,
					"payment_terms": "No Such Terms",
				}
			)

		frappe.db.set_value(
			"TBO Proposal Section",
			{"parent": created["name"], "section_type": "Proposed Solution"},
			"body",
			"Component | What it does\nDatabase | MariaDB\n\n## Business benefits\n- Faster payroll",
		)
		with mock_ai(answer) as post:
			result = hub.chat_edit(
				created["name"], "add Payroll, make it 90 days, remove AMC", acting_user=BDE
			)
		self.assertEqual(post.call_count, 1)
		proposal = result["proposal"]
		self.assertIn("HR & Payroll", [row["module_name"] for row in proposal["modules"]])
		self.assertEqual(proposal["scoping"]["timeline_template"], "90 days")
		self.assertEqual(proposal["scoping"]["payment_terms"], "30 / 30 / 30 / 10")  # not a choice: ignored
		sections = {row["section_type"]: row for row in proposal["sections"]}
		self.assertEqual(sections["AMC"]["enabled"], 0)
		self.assertIn("Planned over 90 days.", sections["Project Timeline"]["body"])
		self.assertEqual(sections["Terms & Conditions"]["body"], "Our own terms.")  # edited: kept
		self.assertIn("- Faster payroll", sections["Proposed Solution"]["body"])  # the AI's benefits kept
		self.assertTrue(result["reply"].startswith("Done:"))
		self.assertEqual(proposal["chat"][-2]["text"], "add Payroll, make it 90 days, remove AMC")
		with mock_ai(openai_answer({})):
			nothing = hub.chat_edit(created["name"], "make it nicer", acting_user=BDE)
		self.assertEqual(nothing["changes"], [])
		frappe.set_user("Administrator")
		enable_proposals()
		frappe.set_user(HUB)
		self.assertRaises(
			frappe.ValidationError, hub.chat_edit, created["name"], "add Payroll", acting_user=BDE
		)


class TestChatFeedback(ProposalTestCase):
	"""The live test's findings: the website step's errors in words; users and companies can be
	Unlimited."""

	def test_every_website_failure_comes_back_in_words(self):
		from tbo_smart.lead_pipeline import proposal_profile as profile

		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		frappe.set_user(HUB)
		public = [(2, 1, 6, "", ("93.184.216.34", 0))]
		not_html = page_response("")
		not_html.headers = {"Content-Type": "application/pdf"}
		cases = (
			("build propsol", None, None, "doesn't look like a website"),
			("no-such-site.example", "dns", None, "Couldn't find the website"),
			("client.example", public, requests.Timeout(), "took too long"),
			("client.example", public, requests.ConnectionError(), "Couldn't reach the website"),
			("client.example", public, not_html, "isn't a web page"),
			("10.0.0.5", None, None, "isn't a public site"),
		)
		for url, resolved, answer, words in cases:
			frappe.clear_messages()
			dns = (
				patch("socket.getaddrinfo", side_effect=socket.gaierror())
				if resolved == "dns"
				else patch("socket.getaddrinfo", return_value=resolved or [(2, 1, 6, "", (url, 0))])
			)
			get = patch(
				"tbo_smart.lead_pipeline.proposal_profile.requests.get",
				**({"side_effect": answer} if isinstance(answer, Exception) else {"return_value": answer}),
			)
			with dns, get, mock_ai() as post:
				with self.assertRaises(profile.SiteUnreachable) as caught:
					hub.read_client_website(self.lead, url, 1, acting_user=BDE)
			self.assertIn(words, str(caught.exception), url)
			# through frappe.throw: the message reaches the hub, not a bare 417
			self.assertTrue(frappe.message_log, url)
			post.assert_not_called()

	def test_users_and_companies_can_be_unlimited(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE, 0, {"users": "Unlimited", "companies": 3})
		self.assertEqual((proposal["scoping"]["users"], proposal["scoping"]["companies"]), ("Unlimited", 3))
		bodies = {row["section_type"]: row["body"] for row in proposal["sections"]}
		self.assertIn("Users and companies | Unlimited users, Up to 3 companies", bodies["Proposed Solution"])
		self.assertIn("The price covers unlimited users, up to 3 companies.", bodies["Investment"])
		self.assertIn("The set-up is sized for unlimited users, up to 3 companies.", bodies["Assumptions"])
		one = make_hub_proposal(self.lead, BDE, TEMPLATE, 0, {"users": 40, "companies": "unlimited"})
		self.assertEqual(one["scoping"]["companies"], "Unlimited")
		self.assertEqual(builder.scale_text(one["scoping"]), "40 users, Unlimited companies")

	def test_the_requirements_read_knows_unlimited_and_multi_company(self):
		frappe.set_user("Administrator")
		enable_proposals(ai=True)
		configure_ai_providers()
		frappe.set_user(HUB)

		def answer(url, body):
			self.assertIn("multi-company", body["messages"][0]["content"])
			return openai_answer({"template": TEMPLATE, "users": "Unlimited", "companies": 2})

		with mock_ai(answer):
			form = hub.extract_requirements(self.lead, "Unlimited users, multi-company", acting_user=BDE)
		self.assertEqual((form["scoping"]["users"], form["scoping"]["companies"]), ("Unlimited", 2))

	def test_old_scoping_gains_companies(self):
		from tbo_smart.patches.v1 import proposal_scoping_companies

		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		set_proposal_values(proposal["name"], scoping=json.dumps({"modules": ["Accounts"], "users": 25}))
		proposal_scoping_companies.execute()
		scoping = frappe.parse_json(frappe.db.get_value(p.DOCTYPE, proposal["name"], "scoping"))
		self.assertEqual((scoping["users"], scoping["companies"]), (25, 0))

	def test_malformed_and_international_addresses(self):
		from tbo_smart.lead_pipeline import proposal_profile as profile

		frappe.clear_messages()
		with self.assertRaises(profile.SiteUnreachable) as caught:
			profile.checked_url("http://[bad")
		self.assertIn("doesn't look like a website", str(caught.exception))
		public = [(2, 1, 6, "", ("93.184.216.34", 0))]
		with patch("socket.getaddrinfo", return_value=public) as resolve:
			for url in ("https://münchen.de", "https://xn--80aswg.xn--p1ai", "https://пример.рф/about"):
				self.assertEqual(profile.checked_url(url), url)
		# DNS is asked for the IDNA (ASCII) form
		self.assertEqual(resolve.call_args_list[0].args[0], "xn--mnchen-3ya.de")

	def test_a_stream_that_breaks_comes_back_in_words(self):
		from tbo_smart.lead_pipeline import proposal_profile as profile

		broken = page_response("")
		broken.iter_content.side_effect = requests.exceptions.ChunkedEncodingError()
		stalled = page_response("")
		stalled.iter_content.side_effect = requests.exceptions.ReadTimeout()
		public = [(2, 1, 6, "", ("93.184.216.34", 0))]
		for response, words in ((broken, "Couldn't reach the website"), (stalled, "took too long")):
			frappe.clear_messages()
			with (
				patch("socket.getaddrinfo", return_value=public),
				patch("tbo_smart.lead_pipeline.proposal_profile.requests.get", return_value=response),
			):
				with self.assertRaises(profile.SiteUnreachable) as caught:
					profile.fetch("https://client.example/")
			self.assertIn(words, str(caught.exception))
			self.assertTrue(frappe.message_log)

	def test_templates_ask_for_companies(self):
		from tbo_smart.lead_pipeline.proposal_templates import templates
		from tbo_smart.patches.v1 import proposal_scoping_companies

		self.assertIn("companies", p.QUESTIONS)
		for template in templates():
			keys = template["questions"].split("\n")
			if "users" in keys:
				self.assertEqual(keys[keys.index("users") + 1], "companies", template["template_name"])
		frappe.set_user("Administrator")
		original = frappe.db.get_value(p.TEMPLATE, TEMPLATE, "questions")
		self.addCleanup(frappe.db.set_value, p.TEMPLATE, TEMPLATE, "questions", original)
		frappe.db.set_value(p.TEMPLATE, TEMPLATE, "questions", "modules\nusers\nhosting")
		proposal_scoping_companies.execute()
		self.assertEqual(
			frappe.db.get_value(p.TEMPLATE, TEMPLATE, "questions"), "modules\nusers\ncompanies\nhosting"
		)
		options = hub.get_proposal_options(acting_user=BDE)
		self.assertIn("companies", options["questions"])
