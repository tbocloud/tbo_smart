"""Changes the system makes to a lead (stage moves, statuses, owner changes, a caller's new lead)
save even when a mandatory field is empty on an old or imported lead (Source made required on the
site); a person editing the lead's own fields is still asked for it (sales.stages.system_save)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads
from tbo_smart.api import hub_proposals as hub
from tbo_smart.api import smart_leads as smart_leads_api
from tbo_smart.lead_pipeline import proposals as p
from tbo_smart.lead_pipeline import quotations
from tbo_smart.lead_pipeline.statuses import CLOSED_WON, CONTACTED, PROPOSAL_SENT
from tbo_smart.lead_pipeline.transitions import advance_lead
from tbo_smart.smart_leads import notes
from tbo_smart.telephony.calls import new_caller_lead
from tbo_smart.test_utils import (
	delete_owner_lead_records,
	disable_lead_quotations,
	disable_proposals,
	enable_proposals,
	lead_source_required,
	make_hub_proposal,
	make_lead,
	make_user,
	set_leaf_customer_group,
)

HUB = "saves.hub@tbosmart.test"
BDE = "saves.bde@tbosmart.test"
BDE2 = "saves.bde2@tbosmart.test"
OWNERS = (BDE, BDE2)
TEMPLATE = "ERPNext Implementation"


class TestLeadSystemSaves(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User", "Business Development Executive"])
		make_user(BDE2, ["Sales User", "Business Development Executive"])
		set_leaf_customer_group()
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		disable_proposals()
		# enable_proposals switched the pipeline and quotations on too: later modules expect them off
		disable_lead_quotations()
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		enable_proposals()
		# an imported lead: no Source
		self.lead = make_lead(BDE, "Vasthu Builders", company_name=f"Vasthu {frappe.generate_hash(length=6)}")
		frappe.db.set_value("Lead", self.lead, "source", None)
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def stage(self):
		return frappe.db.get_value("Lead", self.lead, "tbo_stage")

	def test_a_stage_move_from_the_hub(self):
		with lead_source_required():
			hub_leads.set_stage(self.lead, CONTACTED, acting_user=BDE)
		self.assertEqual(self.stage(), CONTACTED)

	def test_sharing_a_proposal_moves_the_lead(self):
		proposal = make_hub_proposal(self.lead, BDE, TEMPLATE)
		with lead_source_required():
			shared = hub.share_proposal(proposal["name"], acting_user=BDE)
		self.assertEqual(shared["proposal"]["status"], p.SENT)
		self.assertEqual(self.stage(), PROPOSAL_SENT)

	def test_the_pipeline_moves_the_lead(self):
		# e.g. a quotation sent or accepted, a meeting held, a proposal accepted (as the person
		# or the system)
		frappe.set_user(BDE)
		with lead_source_required():
			self.assertTrue(advance_lead(self.lead, CLOSED_WON, "accepted"))
		self.assertEqual(self.stage(), CLOSED_WON)

	def test_a_quotation_moves_the_lead(self):
		# a quotation (or the agreement chain after it) moves its lead through move_lead
		frappe.set_user(BDE)
		quote = frappe._dict(quotation_to="Lead", party_name=self.lead, name="SAL-QTN-TEST")
		with lead_source_required():
			quotations.move_lead(quote, quotations.ACCEPTED)
		self.assertEqual(self.stage(), CLOSED_WON)

	def test_a_smart_leads_status(self):
		frappe.set_user(BDE)
		with lead_source_required():
			notes.set_lead_status(self.lead, CONTACTED)
		self.assertEqual(self.stage(), CONTACTED)

	def test_reassigning_the_lead(self):
		frappe.set_user("Administrator")
		with lead_source_required():
			smart_leads_api.reassign_leads([self.lead], BDE2)
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "lead_owner"), BDE2)

	def test_a_new_lead_from_a_call(self):
		frappe.set_user("Administrator")
		with lead_source_required():
			name = new_caller_lead("+919800000001", BDE)
		self.assertTrue(frappe.db.exists("Lead", name))
		frappe.db.set_value("Lead", name, "lead_owner", BDE)

	def test_a_person_editing_the_lead_is_still_asked_for_source(self):
		with lead_source_required():
			self.assertRaises(
				frappe.MandatoryError,
				hub_leads.update_lead,
				self.lead,
				{"company_name": "Vasthu Builders Pvt Ltd"},
				acting_user=BDE,
			)
