import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, get_first_day, getdate, nowdate

from tbo_smart.api import hub_leads as hub
from tbo_smart.api import lead_quotation
from tbo_smart.lead_pipeline import money
from tbo_smart.lead_pipeline.quotations import ACCEPTED, SENT
from tbo_smart.test_utils import (
	age_lead,
	configure_quotation_approval,
	delete_owner_lead_records,
	disable_lead_quotations,
	disable_proposals,
	disable_sales_hierarchy,
	enable_lead_quotations,
	enable_proposals,
	make_lead,
	make_outgoing_email_account,
	make_payment_terms_template,
	make_quotation_item,
	make_receipt,
	make_sent_proposal,
	make_user,
	save_lead_quotation,
	set_leaf_customer_group,
	set_smart_lead_settings,
)

HUB = "money.hub@tbosmart.test"
BDE = "money.bde@tbosmart.test"
BDE2 = "money.bde2@tbosmart.test"
MANAGER = "money.manager@tbosmart.test"
OWNERS = (BDE, BDE2, MANAGER)
SERVICE = "TBO Smart Money Implementation"
TEMPLATE = "ERPNext Implementation"
TERMS = "TBO Smart Money 40-60"
ADVANCE, SECOND = "TBO Smart Money Advance", "TBO Smart Money Second split"


class LeadMoneyTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User", "Business Development Executive"])
		make_user(BDE2, ["Sales User", "Business Development Executive"])
		make_user(MANAGER, ["Sales Manager"])
		disable_sales_hierarchy(OWNERS)
		set_leaf_customer_group()
		make_quotation_item(SERVICE)
		make_outgoing_email_account()
		make_payment_terms_template(TERMS, [(ADVANCE, 40, 0), (SECOND, 60, 45)])
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		disable_proposals()
		disable_lead_quotations()
		set_smart_lead_settings(quotation_payment_terms=None)
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		enable_lead_quotations(quotation_payment_terms=None)
		configure_quotation_approval(enabled=0)
		self.lead = make_lead(
			BDE,
			"Asha Menon",
			company_name=f"Malabar Spices {frappe.generate_hash(length=6)}",
			email_id=f"asha.{frappe.generate_hash(length=6)}@malabar.example",
		)
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def money(self, acting_user: str = BDE, **filters) -> dict:
		return hub.get_proposal_money(acting_user=acting_user, **filters)

	def splits_of(self, result: dict, lead: str) -> list[dict]:
		return [row for row in result["splits"] if row["lead"] == lead]


class TestLeadIntake(LeadMoneyTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.second = make_lead(BDE, "Ravi Kumar")
		self.yesterday = make_lead(BDE, "Yesterday")
		age_lead(self.yesterday, 1)
		# copied in from the old CRM today: nobody took it today
		self.imported = make_lead(BDE, "Imported", crm_lead_ref="tboindia-old:CRM-LEAD-2025-00001")
		self.theirs = make_lead(BDE2, "Elsewhere")
		frappe.set_user(HUB)

	def intake(self, acting_user: str, **filters) -> dict:
		return hub.get_lead_intake(add_days(nowdate(), -1), nowdate(), acting_user=acting_user, **filters)

	def counts(self, day: dict) -> dict:
		"""This test's owners' counts (other tests' leads of today may be on the site too)."""
		return {row["user"]: row["count"] for row in day["by_owner"] if row["user"] in OWNERS}

	def test_counts_each_day_by_owner_without_imports(self):
		today, yesterday = self.intake(MANAGER)["days"]
		self.assertEqual(getdate(today["date"]), getdate(nowdate()))
		self.assertEqual(self.counts(today), {BDE: 2, BDE2: 1})
		self.assertEqual(today["total"], sum(row["count"] for row in today["by_owner"]))
		self.assertEqual(self.counts(yesterday), {BDE: 1})
		mine = next(row for row in today["by_owner"] if row["user"] == BDE)
		self.assertEqual(mine["full_name"], frappe.utils.get_fullname(BDE))

	def test_follows_the_scope_and_owner(self):
		mine = self.intake(BDE)
		self.assertEqual(self.counts(mine["days"][0]), {BDE: 2})
		self.assertEqual([row["user"] for row in mine["people"]], [BDE])
		everyone = self.intake(MANAGER)
		self.assertTrue({BDE, BDE2} <= {row["user"] for row in everyone["people"]})
		# the manager holds no Sales User role: not a salesperson with a target
		self.assertNotIn(MANAGER, {row["user"] for row in everyone["people"]})
		self.assertEqual(self.counts(self.intake(MANAGER, owner=BDE2)["days"][0]), {BDE2: 1})
		self.assertRaises(frappe.PermissionError, self.intake, BDE, owner=BDE2)
		self.assertRaises(
			frappe.ValidationError, hub.get_lead_intake, "2026-01-01", "2026-06-01", acting_user=MANAGER
		)

	def test_the_intake_metric_lists_the_leads_taken(self):
		rows = hub.list_leads(
			metric="intake",
			period="custom",
			from_date=nowdate(),
			to_date=nowdate(),
			acting_user=MANAGER,
			page_length=100,
		)["rows"]
		names = {row["name"] for row in rows}
		self.assertTrue({self.lead, self.second, self.theirs} <= names)
		self.assertFalse({self.imported, self.yesterday} & names)


class TestRowStatus(LeadMoneyTestCase):
	def test_a_row_shows_its_latest_quotation_and_proposal(self):
		enable_proposals()
		set_smart_lead_settings(proposal_service_item=None, proposal_day_rate=0)
		bare = make_lead(BDE, "No proposal yet")
		proposal, _token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		rows = {row["name"]: row for row in hub.list_leads(acting_user=BDE)["rows"]}
		quotation = rows[self.lead]["latest_quotation"]
		self.assertEqual(quotation["name"], frappe.db.get_value("TBO Proposal", proposal, "quotation"))
		self.assertEqual(quotation["tbo_quote_status"], SENT)
		self.assertTrue(quotation["tbo_sent_on"])
		self.assertEqual(
			flt(quotation["grand_total"]),
			flt(frappe.db.get_value("Quotation", quotation["name"], "grand_total")),
		)
		self.assertEqual(
			{key: rows[self.lead]["latest_proposal"][key] for key in ("name", "document_type", "status")},
			{"name": proposal, "document_type": "Proposal", "status": "Sent"},
		)
		self.assertIsNone(rows[bare]["latest_quotation"])
		self.assertIsNone(rows[bare]["latest_proposal"])


class TestCollections(LeadMoneyTestCase):
	def test_a_sent_proposal_splits_by_its_payment_milestones(self):
		enable_proposals()
		set_smart_lead_settings(proposal_service_item=None, proposal_day_rate=0)
		proposal, _token = make_sent_proposal(self.lead, BDE, TEMPLATE, SERVICE)
		quotation = frappe.db.get_value("TBO Proposal", proposal, "quotation")
		total = flt(frappe.db.get_value("Quotation", quotation, "base_grand_total"))
		result = self.money()
		splits = self.splits_of(result, self.lead)
		# the quotation has only ERPNext's own 100% row: the proposal's milestones say the splits
		self.assertEqual([row["percent"] for row in splits], [30, 30, 30, 10])
		self.assertEqual({row["source"] for row in splits}, {"proposal"})
		self.assertEqual(
			{(row["document_type"], row["document"]) for row in splits}, {("Quotation", quotation)}
		)
		self.assertAlmostEqual(sum(row["amount"] for row in splits), total, places=2)
		self.assertEqual({row["due_date"] for row in splits}, {None})
		self.assertEqual({row["status"] for row in splits}, {money.UPCOMING})
		self.assertTrue(all(row["label"] for row in splits))
		self.assertEqual(splits[0]["lead_name"], "Asha Menon")
		self.assertEqual(splits[0]["lead_owner"], BDE)
		totals = result["totals"]
		self.assertEqual((totals["proposals_sent"], totals["proposals_value"]), (1, total))
		self.assertEqual(totals["to_collect"], total)
		self.assertEqual(result["by_month"]["undated"], total)
		self.assertEqual(totals["advances"], 0)
		# someone else's scope doesn't see it
		self.assertEqual(self.splits_of(self.money(BDE2), self.lead), [])

	def test_an_order_settles_its_schedule_with_what_came_in(self):
		set_smart_lead_settings(quotation_payment_terms=TERMS)
		frappe.set_user(BDE)
		name = save_lead_quotation(self.lead, SERVICE, 100000)["name"]
		lead_quotation.mark_sent(name, "Manual", follow_up_days=0)
		frappe.set_user(HUB)
		quoted = self.splits_of(self.money(), self.lead)
		self.assertEqual([(row["label"], row["percent"]) for row in quoted], [(ADVANCE, 40), (SECOND, 60)])
		# a quotation isn't a commitment yet: its first split is due, the second comes later
		self.assertEqual([row["status"] for row in quoted], [money.DUE, money.UPCOMING])

		frappe.set_user(BDE)
		lead_quotation.set_quote_status(name, ACCEPTED)
		order = lead_quotation.make_sales_order(name)["sales_order"]
		frappe.set_user("Administrator")
		doc = frappe.get_doc("Sales Order", order)
		doc.submit()
		advance, second = (flt(row.base_payment_amount) for row in doc.payment_schedule)
		make_receipt("Sales Order", order, advance)
		frappe.set_user(HUB)

		result = self.money()
		splits = self.splits_of(result, self.lead)
		self.assertEqual({row["document"] for row in splits}, {order})
		self.assertEqual(
			[(row["label"], row["received"], row["outstanding"], row["status"]) for row in splits],
			[(ADVANCE, advance, 0, money.RECEIVED), (SECOND, 0, second, money.UPCOMING)],
		)
		self.assertEqual(getdate(splits[1]["due_date"]), getdate(add_days(nowdate(), 45)))
		totals = result["totals"]
		self.assertEqual((totals["advances"], totals["to_collect"], totals["overdue"]), (advance, second, 0))
		month = getdate(get_first_day(add_days(nowdate(), 45)))
		by_month = {getdate(row["month"]): row["amount"] for row in result["by_month"]["months"]}
		self.assertEqual(by_month[month], second)

		# the second split's day passes unpaid: the order's split is overdue
		frappe.db.set_value(
			"Payment Schedule",
			{"parenttype": "Sales Order", "parent": order, "payment_term": SECOND},
			"due_date",
			add_days(nowdate(), -3),
		)
		result = self.money()
		self.assertEqual(self.splits_of(result, self.lead)[1]["status"], money.OVERDUE)
		self.assertEqual((result["totals"]["overdue"], result["by_month"]["overdue"]), (second, second))

		# paid in full: the order drops off the list
		make_receipt("Sales Order", order, second)
		self.assertEqual(self.splits_of(self.money(), self.lead), [])

	def test_splits_settle_in_due_order(self):
		splits = money.ProposalMoney.document_splits("Sales Order", "SO-1", 1000, [], [("A", 30), ("B", 70)])
		money.settle(splits, 500)
		self.assertEqual([(row["received"], row["outstanding"]) for row in splits], [(300, 0), (200, 500)])
		whole = money.ProposalMoney.document_splits("Quotation", "Q-1", 1000, [], [])
		self.assertEqual(
			[(row["percent"], row["amount"], row["due_date"]) for row in whole], [(100, 1000, None)]
		)
