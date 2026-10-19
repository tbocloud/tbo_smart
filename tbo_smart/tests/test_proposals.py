import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from tbo_smart.api import hub_leads
from tbo_smart.api import hub_proposals as hub
from tbo_smart.api import proposal_page as page
from tbo_smart.api.quotation_adjustment import decide_adjustment, get_pending_approvals
from tbo_smart.lead_pipeline import proposals as p
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.lead_pipeline.statuses import CLOSED_WON, PROPOSAL_SENT
from tbo_smart.sales.opportunity import lead_opportunity
from tbo_smart.test_utils import (
	call_as_guest,
	configure_ai_providers,
	configure_quotation_approval,
	delete_owner_lead_records,
	disable_lead_quotations,
	disable_proposals,
	disable_smart_leads,
	enable_proposals,
	hours_from_now,
	make_hub_proposal,
	make_lead,
	make_lead_meeting,
	make_lead_note,
	make_outgoing_email_account,
	make_pdf_bytes,
	make_quotation_item,
	make_sent_proposal,
	make_user,
	mock_ai,
	openai_answer,
	price_hub_proposal,
	proposal_token,
	send_hub_proposal,
	set_leaf_customer_group,
	set_proposal_values,
	set_segment_rates,
)

HUB = "prop.hub@tbosmart.test"
BDE = "prop.bde@tbosmart.test"
BDE2 = "prop.bde2@tbosmart.test"
BDM = "prop.bdm@tbosmart.test"
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
		self.assertEqual(
			[row["section_type"] for row in proposal["sections"]],
			["Cover", "About us", "Challenge", "Solution", "Scope", "Timeline", "Pricing", "Terms"],
		)
		self.assertIn("Team Back Office", proposal["sections"][1]["body"])
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

	def test_editing_the_blocks(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		sections = [
			{"section_type": "Cover", "heading": "Hello", "body": "Para one.\n\n- first\n- second"},
			{"section_type": "Pricing", "heading": "Investment"},
		]
		saved = hub.save_proposal(
			proposal["name"],
			json.dumps({"title": "Spice ERP", "theme": "Graphite", "sections": sections}),
			acting_user=BDE,
		)
		self.assertEqual((saved["title"], saved["theme"]), ("Spice ERP", "Graphite"))
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

	def test_the_ai_drafts_the_blocks_but_pricing_and_invented_numbers(self):
		def answer(url, body):
			blocks = json.loads(body["messages"][1]["content"])["blocks"]
			self.assertNotIn("Pricing", [row["type"] for row in blocks])
			written = [
				{"key": row["key"], "heading": row["heading"], "body": f"About {row['type']}: 3 warehouses."}
				for row in blocks
			]
			written[0]["body"] = "You will save 40% of your time."
			return openai_answer({"blocks": written})

		with mock_ai(answer):
			result = hub.create_proposal(self.lead, TEMPLATE, 1, acting_user=BDE)
		bodies = {row["section_type"]: row["body"] for row in result["proposal"]["sections"]}
		self.assertEqual(bodies["Challenge"], "About Challenge: 3 warehouses.")
		self.assertIsNone(bodies["Cover"])
		self.assertIsNone(bodies["Pricing"])
		self.assertEqual(len(result["warnings"]), 1)
		self.assertEqual(frappe.db.count("TBO AI Usage Log", {"feature": "Proposal draft"}) > 0, True)

	def test_an_ai_failure_still_makes_the_proposal(self):
		with mock_ai():
			result = hub.create_proposal(self.lead, TEMPLATE, 1, acting_user=BDE)
		self.assertIn("The AI couldn't draft it", result["warnings"][0])
		self.assertIn("Team Back Office", result["proposal"]["sections"][1]["body"])

	def test_ai_off_by_default(self):
		frappe.set_user("Administrator")
		enable_proposals()
		frappe.set_user(HUB)
		self.assertFalse(hub.get_proposal_options(acting_user=BDE)["ai"])
		self.assertRaises(frappe.ValidationError, make_hub_proposal, self.lead, BDE, TEMPLATE, 1)


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
		self.assertEqual(frappe.db.get_value(p.DOCTYPE, name, "status"), p.SENT)
		self.assertNotEqual(frappe.db.get_value("Lead", self.lead, "tbo_stage"), CLOSED_WON)

	def test_views_are_counted_and_the_first_marks_it_viewed(self):
		name, token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		call_as_guest(page.record_view, token)
		call_as_guest(page.record_view, token)
		doc = frappe.get_doc(p.DOCTYPE, name)
		self.assertEqual((doc.status, doc.view_count), (p.VIEWED, 2))
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
