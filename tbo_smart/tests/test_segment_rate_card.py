import json

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads as hub
from tbo_smart.api import lead_quotation as api
from tbo_smart.api import quotation_adjustment
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.lead_pipeline import rate_card
from tbo_smart.quotation_control import AdjustmentApprovalRequired
from tbo_smart.test_utils import (
	configure_quotation_approval,
	delete_owner_lead_records,
	disable_lead_quotations,
	enable_lead_quotations,
	get_item_rate,
	make_lead,
	make_quotation_item,
	make_selling_price_list,
	make_user,
	save_lead_quotation,
	set_leaf_customer_group,
	set_segment_rates,
)

HUB = "rc.hub@tbosmart.test"
BDE = "rc.bde@tbosmart.test"
BDM = "rc.bdm@tbosmart.test"
EDITOR = "rc.manager@tbosmart.test"
OWNERS = (BDE, BDM, EDITOR)
SERVICE = "TBO Smart Rate Card Implementation"
PREMIUM_LIST = "TBO Smart Premium Rates"
BUYING_LIST = "TBO Smart Buying Rates"


class SegmentRateCardTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User", "Business Development Executive"])
		make_user(BDM, ["Sales User", "Business Development Manager"])
		make_user(EDITOR, ["Sales Manager"])
		set_leaf_customer_group()
		make_quotation_item(SERVICE)
		make_selling_price_list(PREMIUM_LIST)
		make_selling_price_list(BUYING_LIST, selling=0)
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		set_segment_rates()
		disable_lead_quotations()
		configure_quotation_approval(enabled=0)
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_owner_lead_records(OWNERS)
		enable_lead_quotations()
		configure_quotation_approval(enabled=0)
		set_segment_rates(
			[
				{"segment": "Premium", "price_list": PREMIUM_LIST, "max_discount": 10},
				{"segment": "Retail", "max_discount": 5},
			]
		)
		api.set_service_rate(SERVICE, 50000)
		rate_card.set_segment_rate(SERVICE, 80000, "Premium")
		self.premium = make_lead(
			BDE,
			"Premium Client",
			company_name=f"Premium {frappe.generate_hash(length=6)}",
			tbo_segment="Premium",
		)
		self.medium = make_lead(
			BDE,
			"Medium Client",
			company_name=f"Medium {frappe.generate_hash(length=6)}",
			tbo_segment="Medium",
		)

	def tearDown(self):
		frappe.set_user("Administrator")

	def save(self, lead, discount=0):
		return save_lead_quotation(lead, SERVICE, 100000, discount)


class TestRateCard(SegmentRateCardTestCase):
	def test_the_card_lists_every_segment_with_its_list_band_and_rates(self):
		frappe.set_user(HUB)
		card = hub.get_rate_card(acting_user=BDE)
		self.assertEqual(frappe.session.user, HUB)
		segments = {row["segment"]: row for row in card["segments"]}
		self.assertEqual(list(segments), ["Retail", "Small", "Medium", "Large", "Premium"])
		self.assertEqual(
			(segments["Premium"]["price_list"], segments["Premium"]["max_discount"]), (PREMIUM_LIST, 10)
		)
		self.assertEqual((segments["Retail"]["price_list"], segments["Retail"]["max_discount"]), (None, 5))
		self.assertEqual(segments["Medium"]["max_discount"], 0)
		service = next(row for row in card["services"] if row["item_code"] == SERVICE)
		self.assertEqual((service["rate"], service["rates"]), (50000, {"Premium": 80000}))
		self.assertIn(PREMIUM_LIST, card["price_lists"])
		self.assertNotIn(BUYING_LIST, card["price_lists"])
		self.assertFalse(card["can_edit"])
		self.assertTrue(hub.get_rate_card(acting_user=EDITOR)["can_edit"])

	def test_only_preset_editors_change_it(self):
		frappe.set_user(HUB)
		rows = json.dumps([{"segment": "Large", "max_discount": 7}])
		self.assertRaises(frappe.PermissionError, hub.save_rate_card, rows, acting_user=BDE)
		self.assertRaises(
			frappe.PermissionError, hub.set_segment_rate, SERVICE, 1, "Premium", acting_user=BDE
		)
		card = hub.save_rate_card(rows, acting_user=EDITOR)
		self.assertEqual(
			[(row["segment"], row["max_discount"]) for row in card["segments"] if row["max_discount"]],
			[("Large", 7)],
		)
		self.assertEqual(frappe.session.user, HUB)

	def test_rows_are_checked(self):
		frappe.set_user(EDITOR)
		for rows in (
			[{"segment": "Gold", "max_discount": 5}],
			[{"segment": "Large", "max_discount": 120}],
			[{"segment": "Large", "price_list": BUYING_LIST}],
			[{"segment": "Large", "max_discount": 5}, {"segment": "Large", "max_discount": 6}],
		):
			self.assertRaises(frappe.ValidationError, rate_card.save_rate_card, rows)

	def test_a_disabled_list_is_refused_and_falls_back(self):
		frappe.db.set_value("Price List", PREMIUM_LIST, "enabled", 0)
		try:
			self.assertEqual(rate_card.price_list_for("Premium"), rate_card.presets.price_list())
			frappe.set_user(EDITOR)
			self.assertRaises(
				frappe.ValidationError,
				rate_card.save_rate_card,
				[{"segment": "Premium", "price_list": PREMIUM_LIST}],
			)
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("Price List", PREMIUM_LIST, "enabled", 1)

	def test_segment_rates_go_on_their_own_list(self):
		frappe.set_user(EDITOR)
		rate_card.set_segment_rate(SERVICE, 90000, "Premium")
		self.assertEqual(get_item_rate(SERVICE, PREMIUM_LIST), 90000)
		self.assertEqual(get_item_rate(SERVICE, rate_card.presets.price_list()), 50000)
		# a segment without a list of its own has no rates of its own
		self.assertRaises(frappe.ValidationError, rate_card.set_segment_rate, SERVICE, 1, "Retail")
		rate_card.set_segment_rate(SERVICE, 55000)
		self.assertEqual(get_item_rate(SERVICE, rate_card.presets.price_list()), 55000)


class TestPricing(SegmentRateCardTestCase):
	def test_the_builder_prices_the_lead_by_its_segment(self):
		frappe.set_user(BDE)
		premium = api.get_quotation_form(self.premium)
		service = next(row for row in premium["catalogue"]["services"] if row["item_code"] == SERVICE)
		self.assertEqual(service["rate"], 80000)
		self.assertEqual(
			premium["pricing"], {"segment": "Premium", "price_list": PREMIUM_LIST, "discount_band": 10}
		)
		medium = api.get_quotation_form(self.medium)
		service = next(row for row in medium["catalogue"]["services"] if row["item_code"] == SERVICE)
		self.assertEqual((service["rate"], medium["pricing"]["discount_band"]), (50000, None))
		saved = self.save(self.premium)
		self.assertEqual(frappe.db.get_value("Quotation", saved["name"], "selling_price_list"), PREMIUM_LIST)

	def test_no_rate_card_while_quotations_are_off(self):
		disable_lead_quotations()
		self.assertEqual(
			(rate_card.price_list_for("Premium"), rate_card.band_for("Premium")),
			(rate_card.presets.price_list(), None),
		)


class TestBandAndApproval(SegmentRateCardTestCase):
	def setUp(self):
		super().setUp()
		configure_quotation_approval(enabled=1, limits={"BDM": (15, 0)})

	def test_within_the_band_needs_no_approval(self):
		frappe.set_user(BDE)
		saved = self.save(self.premium, discount=8)
		self.assertEqual(saved["needs_approval"], [])
		api.mark_sent(saved["name"], "Manual")
		self.assertEqual(frappe.db.get_value("Quotation", saved["name"], "tbo_quote_status"), q.SENT)
		status = quotation_adjustment.get_quotation_adjustment_status(saved["name"])
		self.assertEqual(status["kinds"][0]["limit"], 10)

	def test_a_segment_without_a_band_keeps_the_levels_limits(self):
		frappe.set_user(BDE)
		saved = self.save(self.medium, discount=8)
		self.assertEqual([row["kind"] for row in saved["needs_approval"]], ["Discount"])
		self.assertRaises(AdjustmentApprovalRequired, api.mark_sent, saved["name"], "Manual")

	def test_above_the_band_goes_through_the_approval_chain(self):
		frappe.set_user(BDE)
		saved = self.save(self.premium, discount=12)
		self.assertEqual(saved["needs_approval"], [{"kind": "Discount", "percent": 12, "limit": 10}])
		self.assertRaises(AdjustmentApprovalRequired, api.mark_sent, saved["name"], "Manual")
		# within the band there is nothing to ask for
		self.assertRaises(
			frappe.ValidationError,
			quotation_adjustment.request_adjustment,
			saved["name"],
			"Discount",
			9,
			"Big client",
		)
		request = quotation_adjustment.request_adjustment(saved["name"], "Discount", 12, "Three-year deal")
		self.assertRaises(frappe.ValidationError, api.mark_sent, saved["name"], "Manual")
		frappe.set_user(BDM)
		quotation_adjustment.decide_adjustment(request["name"], "approve")
		frappe.set_user(BDE)
		api.mark_sent(saved["name"], "Manual")
		self.assertEqual(frappe.db.get_value("Quotation", saved["name"], "tbo_quote_status"), q.SENT)

	def test_a_band_cut_takes_back_what_it_allowed(self):
		frappe.set_user(BDE)
		saved = self.save(self.premium, discount=8)
		self.assertEqual(saved["needs_approval"], [])
		frappe.set_user("Administrator")
		set_segment_rates([{"segment": "Premium", "price_list": PREMIUM_LIST, "max_discount": 5}])
		frappe.set_user(BDE)
		status = quotation_adjustment.get_quotation_adjustment_status(saved["name"])["kinds"][0]
		self.assertEqual((status["limit"], status["within_limit"], status["approved"]), (5, False, False))
		self.assertRaises(AdjustmentApprovalRequired, api.mark_sent, saved["name"], "Manual")

	def test_a_level_with_a_higher_limit_keeps_it(self):
		lead = make_lead(
			BDM, "BDM Client", company_name=f"BDM {frappe.generate_hash(length=6)}", tbo_segment="Premium"
		)
		frappe.set_user(BDM)
		saved = self.save(lead, discount=14)
		self.assertEqual(saved["needs_approval"], [])


class TestSegmentGuard(SegmentRateCardTestCase):
	def test_only_editors_move_a_lead_to_another_segment(self):
		frappe.set_user(HUB)
		self.assertRaises(
			frappe.ValidationError,
			hub.update_lead,
			self.medium,
			json.dumps({"segment": "Premium"}),
			acting_user=BDE,
		)
		self.assertEqual(frappe.db.get_value("Lead", self.medium, "tbo_segment"), "Medium")
		hub.update_lead(self.medium, json.dumps({"segment": "Premium"}), acting_user=EDITOR)
		self.assertEqual(frappe.db.get_value("Lead", self.medium, "tbo_segment"), "Premium")
		# a lead without a segment gets its first one from the salesperson, as on creation
		frappe.set_user("Administrator")
		blank = make_lead(BDE, "No Segment", company_name=f"Blank {frappe.generate_hash(length=6)}")
		frappe.set_user(HUB)
		hub.update_lead(blank, json.dumps({"segment": "Small"}), acting_user=BDE)

	def test_the_status_batch_reads_each_quotations_band(self):
		configure_quotation_approval(enabled=1)
		frappe.set_user(BDE)
		premium = self.save(self.premium, discount=8)["name"]
		medium = self.save(self.medium, discount=3)["name"]
		statuses = quotation_adjustment.get_quotations_adjustment_status(json.dumps([premium, medium]))
		self.assertEqual(statuses[premium]["kinds"][0]["limit"], 10)
		self.assertEqual(statuses[medium]["kinds"][0]["limit"], 0)
