from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import quotation_adjustment as api
from tbo_smart.quotation_control import AdjustmentApprovalRequired, get_discount_percent
from tbo_smart.test_utils import (
	configure_quotation_approval,
	get_adjustments,
	make_quotation,
	make_user,
	save_quotation,
)

BDE = "qa.bde@tbosmart.test"
OTHER_BDE = "qa.bde2@tbosmart.test"
BDM = "qa.bdm@tbosmart.test"
HEAD = "qa.head@tbosmart.test"
DIRECTOR = "qa.director@tbosmart.test"
CEO = "qa.ceo@tbosmart.test"
NO_LEVEL = "qa.nolevel@tbosmart.test"

# (max discount %, max commission %) per level; the CEO level has no limit
LIMITS = {
	"BDE": (5, 2),
	"BDM": (10, 4),
	"Sales & Marketing Head": (15, 6),
	"Director": (20, 8),
}


class TestQuotationAdjustment(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		configure_quotation_approval(limits=LIMITS)
		for user, role in (
			(BDE, "Business Development Executive"),
			(OTHER_BDE, "Business Development Executive"),
			(BDM, "Business Development Manager"),
			(HEAD, "Sales & Marketing Head"),
			(DIRECTOR, "Director"),
			(CEO, "CEO"),
		):
			make_user(user, ["Sales User", role])
		make_user(NO_LEVEL, ["Sales User"])
		notifier = patch(
			"tbo_smart.tbo_smart.doctype.tbo_quotation_adjustment.tbo_quotation_adjustment.enqueue_create_notification"
		)
		self.notify = notifier.start()
		self.addCleanup(notifier.stop)

	def tearDown(self):
		frappe.set_user("Administrator")

	def notified_users(self) -> set[str]:
		return {user for call in self.notify.call_args_list for user in call.args[0]}

	def test_within_limit_is_recorded_and_submits(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=4)
		self.assertEqual(get_discount_percent(quotation), 4)
		[applied] = get_adjustments(quotation.name, "Discount")
		self.assertEqual((applied.status, applied.within_limit, applied.approved_by), ("Approved", 1, BDE))
		self.assertEqual(applied.actions, ["Applied"])
		quotation.submit()
		self.assertEqual(quotation.docstatus, 1)

	def test_item_and_additional_discounts_add_up(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=3)
		# the form sets the rate from the line discount; the server keeps what it is sent
		quotation.items[0].update({"price_list_rate": 1000, "discount_percentage": 3, "rate": 970})
		quotation.save(ignore_permissions=True)
		# 3% off the line, then 3% off the total: 1000 -> 970 -> 940.90, so 5.91% in all
		self.assertEqual(get_discount_percent(quotation), 5.91)
		with self.assertRaises(AdjustmentApprovalRequired):
			quotation.submit()

	def test_above_limit_blocks_submit_until_bdm_approves(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=8)
		self.assertEqual(get_adjustments(quotation.name), [])
		with self.assertRaises(AdjustmentApprovalRequired):
			quotation.submit()

		request = api.request_adjustment(quotation.name, "Discount", 8, "Three-year contract")
		self.assertEqual((request["status"], request["current_level"]), ("Pending", "BDM"))
		self.assertIn(BDM, self.notified_users())
		self.assertFalse(self.notified_users() & {BDE, OTHER_BDE, HEAD})

		frappe.set_user(BDM)
		decision = api.decide_adjustment(request["name"], "approve", remarks="Fine for this account")
		self.assertEqual((decision["status"], decision["approved_percent"]), ("Approved", 8))
		self.assertIn(BDE, self.notified_users())

		frappe.set_user(BDE)
		quotation.reload()
		quotation.submit()
		self.assertEqual(quotation.docstatus, 1)

	def test_bdm_limit_exceeded_escalates_to_head_then_director(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=18)
		name = api.request_adjustment(quotation.name, "Discount", 18, "Strategic logo")["name"]

		frappe.set_user(BDM)
		with self.assertRaises(frappe.PermissionError):
			api.decide_adjustment(name, "approve")
		with self.assertRaises(frappe.PermissionError):
			api.decide_adjustment(name, "reject", remarks="Too much")
		forwarded = api.decide_adjustment(name, "forward", remarks="Above my limit")
		self.assertEqual(
			(forwarded["status"], forwarded["current_level"]), ("Escalated", "Sales & Marketing Head")
		)

		frappe.set_user(HEAD)
		self.assertEqual(api.decide_adjustment(name, "forward")["current_level"], "Director")
		self.assertIn(DIRECTOR, self.notified_users())

		frappe.set_user(DIRECTOR)
		self.assertEqual(api.decide_adjustment(name, "approve")["status"], "Approved")
		frappe.set_user(BDE)
		quotation.reload()
		quotation.submit()

	def test_approver_can_approve_less_than_asked(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=9)
		name = api.request_adjustment(quotation.name, "Discount", 9, "Competitor quote")["name"]
		frappe.set_user(BDM)
		self.assertEqual(api.decide_adjustment(name, "approve", percent=7)["approved_percent"], 7)

		frappe.set_user(BDE)
		quotation.reload()
		with self.assertRaises(AdjustmentApprovalRequired):
			quotation.submit()
		quotation = save_quotation(quotation, additional_discount_percentage=7)
		quotation.submit()

	def test_director_adjusts_directly_without_approval(self):
		frappe.set_user(DIRECTOR)
		quotation = make_quotation(discount=20, commission=8)
		quotation.submit()
		self.assertEqual({row.kind for row in get_adjustments(quotation.name)}, {"Discount", "Commission"})
		self.assertTrue(all(row.within_limit for row in get_adjustments(quotation.name)))
		self.notify.assert_not_called()

	def test_director_takes_over_a_request_still_at_bdm(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=12)
		name = api.request_adjustment(quotation.name, "Discount", 12, "Renewal")["name"]

		frappe.set_user(DIRECTOR)
		self.assertIn(name, [row["name"] for row in api.get_pending_approvals()])
		self.assertEqual(api.decide_adjustment(name, "approve")["status"], "Approved")

	def test_ceo_override_needs_a_reason(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=30)
		name = api.request_adjustment(quotation.name, "Discount", 30, "Board-level deal")["name"]

		frappe.set_user(DIRECTOR)
		with self.assertRaises(frappe.PermissionError):
			api.decide_adjustment(name, "override", remarks="I say so")

		frappe.set_user(CEO)
		with self.assertRaises(frappe.ValidationError):
			api.decide_adjustment(name, "override")
		overridden = api.decide_adjustment(name, "override", remarks="Flagship customer")
		self.assertEqual((overridden["status"], overridden["approved_percent"]), ("Overridden", 30))

		frappe.set_user(BDE)
		quotation.reload()
		quotation.submit()

	def test_ceo_can_override_a_rejection(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=7)
		name = api.request_adjustment(quotation.name, "Discount", 7, "Pilot")["name"]
		frappe.set_user(BDM)
		self.assertEqual(api.decide_adjustment(name, "reject", remarks="Not now")["status"], "Rejected")
		frappe.set_user(CEO)
		self.assertEqual(
			api.decide_adjustment(name, "override", 7, "Pilot is strategic")["status"], "Overridden"
		)

	def test_raising_the_percent_voids_the_approval(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=8)
		name = api.request_adjustment(quotation.name, "Discount", 8, "Volume")["name"]
		frappe.set_user(BDM)
		api.decide_adjustment(name, "approve")

		frappe.set_user(BDE)
		quotation = save_quotation(quotation, additional_discount_percentage=9)
		voided = frappe.get_doc("TBO Quotation Adjustment", name)
		self.assertTrue(voided.voided)
		self.assertEqual(voided.steps[-1].action, "Voided")
		# going back down does not bring a voided approval back
		quotation = save_quotation(quotation, additional_discount_percentage=8)
		with self.assertRaises(AdjustmentApprovalRequired):
			quotation.submit()

	def test_raising_above_an_open_request_voids_it(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=8)
		name = api.request_adjustment(quotation.name, "Discount", 8, "Volume")["name"]
		quotation = save_quotation(quotation, additional_discount_percentage=9)
		self.assertTrue(frappe.db.get_value("TBO Quotation Adjustment", name, "voided"))

		frappe.set_user(CEO)
		self.assertNotIn(name, [row["name"] for row in api.get_pending_approvals()])
		with self.assertRaises(frappe.ValidationError):
			api.decide_adjustment(name, "override", remarks="Too late")

		frappe.set_user(BDE)
		self.assertEqual(api.request_adjustment(quotation.name, "Discount", 9, "Volume")["status"], "Pending")

	def test_changing_the_quotation_value_voids_the_approval(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=8)
		name = api.request_adjustment(quotation.name, "Discount", 8, "Volume")["name"]
		frappe.set_user(BDM)
		api.decide_adjustment(name, "approve")

		frappe.set_user(BDE)
		quotation.reload()
		quotation.items[0].qty = 5
		quotation.save(ignore_permissions=True)
		self.assertTrue(frappe.db.get_value("TBO Quotation Adjustment", name, "voided"))
		with self.assertRaises(AdjustmentApprovalRequired):
			quotation.submit()

	def test_lowering_the_percent_keeps_the_approval(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=8)
		name = api.request_adjustment(quotation.name, "Discount", 8, "Volume")["name"]
		frappe.set_user(BDM)
		api.decide_adjustment(name, "approve")

		frappe.set_user(BDE)
		quotation = save_quotation(quotation, additional_discount_percentage=6)
		self.assertFalse(frappe.db.get_value("TBO Quotation Adjustment", name, "voided"))
		quotation.submit()

	def test_unset_limits_need_approval_for_any_adjustment(self):
		configure_quotation_approval(limits={})
		for user in (BDE, DIRECTOR):
			frappe.set_user(user)
			quotation = make_quotation(discount=1)
			with self.assertRaises(AdjustmentApprovalRequired):
				quotation.submit()
		frappe.set_user(BDE)
		self.assertEqual(
			api.get_adjustment_limits()["limit_labels"], {"Discount": "not set", "Commission": "not set"}
		)

		frappe.set_user(CEO)
		make_quotation(discount=1).submit()

	def test_applies_to_discount_commission_or_both(self):
		frappe.set_user(BDE)
		configure_quotation_approval(applies_to="Discount", limits=LIMITS)
		make_quotation(discount=4, commission=50).submit()

		configure_quotation_approval(applies_to="Commission", limits=LIMITS)
		make_quotation(discount=50, commission=2).submit()
		with self.assertRaises(AdjustmentApprovalRequired):
			make_quotation(commission=3).submit()

		configure_quotation_approval(applies_to="Both", limits=LIMITS)
		with self.assertRaises(AdjustmentApprovalRequired):
			make_quotation(discount=4, commission=3).submit()
		with self.assertRaises(AdjustmentApprovalRequired):
			make_quotation(discount=6, commission=1).submit()

		configure_quotation_approval(enabled=0, limits=LIMITS)
		make_quotation(discount=60, commission=60).submit()
		self.assertFalse(api.get_adjustment_limits()["enabled"])

	def test_commission_request_goes_through_its_own_limit(self):
		frappe.set_user(BDE)
		quotation = make_quotation(commission=3)
		name = api.request_adjustment(quotation.name, "Commission", 3, "Partner-sourced")["name"]
		frappe.set_user(BDM)
		self.assertEqual(api.decide_adjustment(name, "approve")["status"], "Approved")
		frappe.set_user(BDE)
		quotation.reload()
		quotation.submit()

	def test_who_may_not_decide(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=8)
		name = api.request_adjustment(quotation.name, "Discount", 8, "Volume")["name"]
		for user in (BDE, OTHER_BDE, NO_LEVEL):
			frappe.set_user(user)
			with self.assertRaises(frappe.PermissionError):
				api.decide_adjustment(name, "approve")
			self.assertNotIn(name, [row["name"] for row in api.get_pending_approvals()])

		frappe.set_user(DIRECTOR)
		quotation = make_quotation(discount=25)
		own = api.request_adjustment(quotation.name, "Discount", 25, "Group deal")
		self.assertEqual(own["current_level"], "CEO")
		with self.assertRaises(frappe.PermissionError):
			api.decide_adjustment(own["name"], "approve")
		self.assertNotIn(own["name"], [row["name"] for row in api.get_pending_approvals()])

	def test_request_refusals(self):
		frappe.set_user(NO_LEVEL)
		quotation = make_quotation(discount=3)
		with self.assertRaises(frappe.PermissionError):
			api.request_adjustment(quotation.name, "Discount", 3, "Please")

		frappe.set_user(BDE)
		quotation = make_quotation(discount=8)
		with self.assertRaises(frappe.ValidationError):
			api.request_adjustment(quotation.name, "Discount", 4, "Within my own limit")
		with self.assertRaises(frappe.ValidationError):
			api.request_adjustment(quotation.name, "Discount", 8, " ")
		api.request_adjustment(quotation.name, "Discount", 8, "Volume")
		with self.assertRaises(frappe.ValidationError):
			api.request_adjustment(quotation.name, "Discount", 9, "Second ask")

	def test_audit_trail_is_complete_and_append_only(self):
		frappe.set_user(BDE)
		quotation = make_quotation(discount=12)
		name = api.request_adjustment(quotation.name, "Discount", 12, "Renewal")["name"]
		frappe.set_user(BDM)
		api.decide_adjustment(name, "forward", remarks="Over my limit")
		frappe.set_user(HEAD)
		api.decide_adjustment(name, "approve", percent=11, remarks="11 is enough")

		frappe.set_user("Administrator")
		adjustment = frappe.get_doc("TBO Quotation Adjustment", name)
		self.assertEqual(
			[(step.action, step.by_user, step.level, step.percent) for step in adjustment.steps],
			[
				("Requested", BDE, "BDE", 12),
				("Forwarded", BDM, "BDM", 12),
				("Approved", HEAD, "Sales & Marketing Head", 11),
			],
		)
		self.assertEqual((adjustment.previous_percent, adjustment.approved_by), (0, HEAD))
		comments = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Quotation",
				"reference_name": quotation.name,
				"comment_type": "Info",
			},
			pluck="content",
		)
		self.assertEqual(len([text for text in comments if name in text]), 3)

		adjustment.steps[0].percent = 5
		with self.assertRaises(frappe.ValidationError):
			adjustment.save(ignore_permissions=True)
		adjustment.reload()
		adjustment.requested_percent = 5
		with self.assertRaises(frappe.ValidationError):
			adjustment.save(ignore_permissions=True)
		adjustment.reload()
		adjustment.steps.pop()
		with self.assertRaises(frappe.ValidationError):
			adjustment.save(ignore_permissions=True)

	def test_limits_and_status_for_the_session_user(self):
		frappe.set_user(BDE)
		limits = api.get_adjustment_limits()
		self.assertEqual(limits["level"]["level"], "BDE")
		self.assertEqual(limits["limits"], {"Discount": 5, "Commission": 2})
		quotation = make_quotation(discount=8)
		status = {row["kind"]: row for row in api.get_quotation_adjustment_status(quotation.name)["kinds"]}
		self.assertEqual((status["Discount"]["percent"], status["Discount"]["approved"]), (8, False))
		self.assertTrue(status["Commission"]["approved"])

		frappe.set_user(CEO)
		self.assertIsNone(api.get_adjustment_limits()["limits"]["Discount"])

	def test_access_flags_for_the_crm_screens(self):
		expected = {
			BDE: (False, False, False, True),
			BDM: (True, False, False, True),
			DIRECTOR: (True, True, False, True),
			CEO: (True, True, True, False),
			NO_LEVEL: (False, False, False, False),
		}
		for user, flags in expected.items():
			frappe.set_user(user)
			limits = api.get_adjustment_limits()
			self.assertEqual(
				(
					limits["is_approver"],
					limits["can_view_all"],
					limits["can_override"],
					limits["can_forward"],
				),
				flags,
				user,
			)

		frappe.set_user(BDE)
		self.assertEqual(api.get_adjustment_limits()["open_requests"], 0)
		quotation = make_quotation(discount=8)
		api.request_adjustment(quotation.name, "Discount", 8, "Volume")
		self.assertEqual(api.get_adjustment_limits()["open_requests"], 1)

	def test_status_for_several_quotations(self):
		frappe.set_user(BDE)
		pending = make_quotation(discount=8)
		within = make_quotation(discount=4)
		api.request_adjustment(pending.name, "Discount", 8, "Volume")

		statuses = api.get_quotations_adjustment_status(
			frappe.as_json([pending.name, within.name, "QTN-DOES-NOT-EXIST"])
		)
		self.assertEqual(set(statuses), {pending.name, within.name})
		discount = {name: status["kinds"][0] for name, status in statuses.items()}
		self.assertEqual(discount[pending.name]["latest"]["status"], "Pending")
		self.assertFalse(discount[pending.name]["approved"])
		self.assertTrue(discount[within.name]["within_limit"])
		self.assertEqual(statuses[pending.name], api.get_quotation_adjustment_status(pending.name))
		with self.assertRaises(frappe.ValidationError):
			api.get_quotations_adjustment_status("not a list")

		# another BDE may read the quotation, but not the request behind it
		frappe.set_user(OTHER_BDE)
		latest = api.get_quotations_adjustment_status([pending.name])[pending.name]["kinds"][0]["latest"]
		self.assertEqual(
			(latest["status"], latest["current_level"], latest["name"]), ("Pending", "BDM", None)
		)
		self.assertNotIn("reason", latest)
		self.assertNotIn("requested_by", latest)
		frappe.set_user(BDM)
		self.assertEqual(
			api.get_quotations_adjustment_status([pending.name])[pending.name]["kinds"][0]["latest"][
				"reason"
			],
			"Volume",
		)

	def test_my_requests_all_requests_and_the_trail(self):
		frappe.set_user(BDE)
		mine = make_quotation(discount=12)
		name = api.request_adjustment(mine.name, "Discount", 12, "Renewal")["name"]
		make_quotation(discount=4)  # an Applied row, which is no request
		frappe.set_user(OTHER_BDE)
		other = api.request_adjustment(make_quotation(discount=9).name, "Discount", 9, "Pilot")["name"]

		frappe.set_user(BDE)
		requests = api.get_adjustment_requests("mine")
		self.assertIn(name, [row["name"] for row in requests])
		self.assertNotIn(other, [row["name"] for row in requests])
		self.assertTrue(all(not row["within_limit"] for row in requests))
		with self.assertRaises(frappe.PermissionError):
			api.get_adjustment_requests("all")
		with self.assertRaises(frappe.ValidationError):
			api.get_adjustment_requests("everything")

		frappe.set_user(BDM)
		with self.assertRaises(frappe.PermissionError):
			api.get_adjustment_requests("all")
		api.decide_adjustment(name, "forward", remarks="Over my limit")

		frappe.set_user(DIRECTOR)
		every = {row["name"]: row for row in api.get_adjustment_requests("all")}
		self.assertTrue({name, other} <= set(every))
		self.assertEqual(every[name]["currency"], mine.currency)
		self.assertEqual(
			{row["name"]: row["currency"] for row in api.get_pending_approvals()}.get(other), mine.currency
		)
		trail = api.get_adjustment_trail(name)
		self.assertEqual([step["action"] for step in trail["steps"]], ["Requested", "Forwarded"])
		self.assertEqual(trail["steps"][1]["by_user"], BDM)
		self.assertEqual(trail["requested_by_name"], frappe.db.get_value("User", BDE, "full_name"))

		frappe.set_user(BDE)
		self.assertEqual(len(api.get_adjustment_trail(name)["steps"]), 2)
		frappe.set_user(OTHER_BDE)
		with self.assertRaises(frappe.PermissionError):
			api.get_adjustment_trail(name)
