import unittest

import frappe
from frappe.database import get_db
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import cutover as cutover_api
from tbo_smart.cutover import crm_to_erpnext as cutover
from tbo_smart.test_utils import (
	clear_crm_cutover,
	crm_installed,
	ensure_quotation_crm_lead_field,
	make_crm_records,
	make_lead_meeting,
	make_user,
)

OWNER = "cut.owner@tbosmart.test"
MANAGER = "cut.manager@tbosmart.test"


@unittest.skipUnless(crm_installed(), "The cutover reads Frappe CRM's tables: install the crm app to run it.")
class TestCRMCutover(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(OWNER, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		clear_crm_cutover((OWNER, MANAGER))
		self.crm = make_crm_records(OWNER, MANAGER)

	def tearDown(self):
		frappe.set_user("Administrator")
		clear_crm_cutover((OWNER, MANAGER))

	def test_dry_run_counts_and_writes_nothing(self):
		report = cutover.run(dry_run=True)
		self.assertEqual(report.counts["leads"]["crm"], 2)
		self.assertEqual(report.counts["deals"]["crm"], 1)
		self.assertFalse(frappe.db.exists("Lead", self.crm.lead))
		self.assertFalse(frappe.db.exists("Opportunity", self.crm.deal))

	def test_copies_keep_names_history_activity_and_links(self):
		report = cutover.run(dry_run=False)
		self.assertEqual(report.failed, [])
		lead = frappe.get_doc("Lead", self.crm.lead)
		self.assertEqual(
			(lead.crm_lead_ref, lead.tbo_stage, lead.lead_owner, lead.company_name, lead.tbo_score),
			(self.crm.lead, "Proposal Sent", OWNER, "Cutover Traders", 41),
		)
		self.assertEqual([row.stage for row in lead.tbo_stage_log], ["Contacted", "Proposal Sent"])
		# notes are Comments (the migration note keeps its title first), the call an Event with
		# its note, tasks ToDos with their names
		self.assertTrue(
			frappe.db.get_value("Comment", self.crm.migration_note, "content").startswith(
				"<p>Migrated from tboindia-old CRM-LEAD-2025-00777</p>"
			)
		)
		self.assertEqual(frappe.db.get_value("Comment", self.crm.note, "reference_name"), self.crm.lead)
		self.assertEqual(
			frappe.db.get_value(
				"Comment", self.crm.organization_note, ["reference_doctype", "reference_name"]
			),
			("Prospect", self.crm.organization),
		)
		call = frappe.db.get_value("Event", self.crm.call, ["event_category", "description"], as_dict=True)
		self.assertEqual((call.event_category, call.description), ("Call", "Rang about the demo"))
		self.assertEqual(
			frappe.db.get_value(
				"ToDo", str(self.crm.open_task), ["status", "reference_type", "allocated_to"]
			),
			("Open", "Lead", OWNER),
		)
		self.assertEqual(frappe.db.get_value("ToDo", str(self.crm.done_task), "status"), "Closed")
		# the deal: same name, from its lead, stage, value, finance fields
		deal = frappe.get_doc("Opportunity", self.crm.deal)
		self.assertEqual(
			(deal.opportunity_from, deal.party_name, deal.sales_stage, deal.opportunity_amount),
			("Lead", self.crm.won_lead, "Won", 120000),
		)
		self.assertEqual((deal.tbo_finance_stage, deal.tbo_onboarding_complete), ("Invoiced", 1))
		self.assertEqual(
			frappe.db.get_value("ToDo", str(self.crm.deal_task), "reference_type"), "Opportunity"
		)
		self.assertTrue(frappe.db.exists("Prospect", self.crm.organization))
		# links: quotations, the assignment, the hierarchy
		self.assertEqual(
			frappe.db.get_value("Quotation", self.crm.lead_quotation, ["quotation_to", "party_name"]),
			("Lead", self.crm.lead),
		)
		self.assertEqual(
			frappe.db.get_value(
				"Quotation", self.crm.deal_quotation, ["quotation_to", "party_name", "opportunity"]
			),
			("Lead", self.crm.won_lead, self.crm.deal),
		)
		self.assertFalse(frappe.db.exists("ToDo", {"reference_type": "CRM Lead"}))
		self.assertEqual(
			frappe.db.get_value("TBO Sales Hierarchy", {"user": OWNER}, "reports_to"),
			frappe.db.get_value("TBO Sales Hierarchy", {"user": MANAGER}, "name"),
		)
		# idempotent: a second run copies nothing again
		again = cutover.run(dry_run=False)
		self.assertEqual(again.failed, [])
		self.assertEqual(frappe.db.count("Lead", {"crm_lead_ref": self.crm.lead}), 1)
		self.assertEqual(frappe.db.count("Comment", {"name": self.crm.note}), 1)
		self.assertTrue(cutover.verify()["pass"])

	def test_a_resumed_run_relinks_renamed_copies_and_quotation_leads(self):
		cutover.run(dry_run=False)
		ensure_quotation_crm_lead_field()
		meeting = make_lead_meeting(self.crm.lead)
		try:
			# what a cut-off first run leaves: a TBO record still on the CRM name of a copy made
			# under another name, and a quotation moved to a Customer that names its lead only in
			# tbo_crm_lead
			frappe.db.set_value("TBO Lead Meeting", meeting, "lead", "CRM-LEAD-OLD-NAME")
			frappe.db.set_value(
				"Quotation", self.crm.deal_quotation, {"tbo_lead": None, "tbo_crm_lead": "CRM-LEAD-OLD-NAME"}
			)
			resumed = cutover.Cutover(dry_run=False)
			resumed.load_names()
			resumed.lead_names["CRM-LEAD-OLD-NAME"] = self.crm.lead
			resumed.relink_quotations()
			resumed.relink_deal_fields()
			self.assertEqual(resumed.report.renamed, {})
			self.assertEqual(frappe.db.get_value("TBO Lead Meeting", meeting, "lead"), self.crm.lead)
			self.assertEqual(
				frappe.db.get_value("Quotation", self.crm.deal_quotation, "tbo_lead"), self.crm.lead
			)
		finally:
			frappe.delete_doc("TBO Lead Meeting", meeting, force=True, ignore_permissions=True)

	def test_the_browser_methods_are_for_system_managers(self):
		frappe.set_user(OWNER)
		self.assertRaises(frappe.PermissionError, cutover_api.dry_run)
		self.assertRaises(frappe.PermissionError, cutover_api.start)
		self.assertRaises(frappe.PermissionError, cutover_api.verify)
		frappe.set_user("Administrator")
		self.assertEqual(cutover_api.dry_run()["counts"]["leads"]["crm"], 2)
		self.assertFalse(frappe.db.exists("Lead", self.crm.lead))
		self.assertEqual(cutover_api.status()["running"], False)
		# one run at a time: while another connection holds the run's lock, start queues nothing
		# and a run that slipped through exits without copying
		other = get_db(
			host=frappe.conf.db_host,
			port=frappe.conf.db_port,
			user=frappe.conf.db_name,
			password=frappe.conf.db_password,
			cur_db_name=frappe.conf.db_name,
		)
		other.connect()
		try:
			other.sql("SELECT GET_LOCK(%s, 0)", cutover_api.lock_name())
			self.assertTrue(cutover_api.status()["running"])
			self.assertFalse(cutover_api.start()["queued"])
			cutover_api.run_once()
			self.assertFalse(frappe.db.exists("Lead", {"crm_lead_ref": self.crm.lead}))
		finally:
			other.sql("SELECT RELEASE_LOCK(%s)", cutover_api.lock_name())
			other.close()
		self.assertFalse(cutover_api.status()["running"])

	def test_finalize_removes_the_old_links(self):
		cutover.run(dry_run=False)
		# the deal link tboindia's orders had
		if not frappe.db.exists("Custom Field", {"dt": "Sales Order", "fieldname": "crm_deal"}):
			frappe.get_doc(
				{
					"doctype": "Custom Field",
					"dt": "Sales Order",
					"fieldname": "crm_deal",
					"fieldtype": "Link",
					"options": "CRM Deal",
					"label": "CRM Deal",
				}
			).insert(ignore_permissions=True)
		result = cutover.finalize()
		self.assertIn("Sales Order.crm_deal", result["removed"])
		self.assertFalse(frappe.db.exists("Custom Field", {"dt": "Sales Order", "fieldname": "crm_deal"}))
