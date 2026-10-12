from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart import push
from tbo_smart.api import quotation_adjustment as adjustments
from tbo_smart.api.mobile import quotation
from tbo_smart.test_utils import (
	capabilities_as,
	configure_quotation_approval,
	make_quotation,
	make_user,
)

BDE = "mq.bde@tbosmart.test"
BDM = "mq.bdm@tbosmart.test"
HEAD = "mq.head@tbosmart.test"
DIRECTOR = "mq.director@tbosmart.test"
CEO = "mq.ceo@tbosmart.test"
NO_LEVEL = "mq.nolevel@tbosmart.test"

# (max discount %, max commission %) per level; the CEO level has no limit
LIMITS = {
	"BDE": (5, 2),
	"BDM": (10, 4),
	"Sales & Marketing Head": (15, 6),
	"Director": (20, 8),
}


class TestMobileQuotation(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		configure_quotation_approval(limits=LIMITS)
		for user, role in (
			(BDE, "Business Development Executive"),
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
		notifier.start()
		self.addCleanup(notifier.stop)

	def tearDown(self):
		frappe.set_user("Administrator")

	def request(self, percent: float) -> str:
		"""A BDE's discount request for `percent` on a new draft quotation."""
		frappe.set_user(BDE)
		draft = make_quotation(discount=percent)
		return adjustments.request_adjustment(draft.name, "Discount", percent, "Three-year contract")["name"]

	def test_capability_is_for_levels_that_decide(self):
		for user in (BDM, HEAD, DIRECTOR, CEO):
			self.assertTrue(capabilities_as(user).approve_quotations, user)
		for user in (BDE, NO_LEVEL):
			self.assertFalse(capabilities_as(user).approve_quotations, user)

	def test_waiting_is_for_approvers_and_mine_for_the_requester(self):
		name = self.request(8)
		with self.assertRaises(frappe.PermissionError):
			quotation.get_approvals("waiting")
		self.assertIn(name, [row["name"] for row in quotation.get_approvals("mine")])

		frappe.set_user(BDM)
		[waiting] = [row for row in quotation.get_approvals("waiting") if row["name"] == name]
		self.assertTrue(waiting["can_approve_in_full"])
		self.assertEqual(waiting["requested_by_name"], frappe.db.get_value("User", BDE, "full_name"))
		with self.assertRaises(frappe.ValidationError):
			quotation.get_approvals("everything")

	def test_actions_follow_the_level_and_its_limit(self):
		within = self.request(8)
		above = self.request(18)

		frappe.set_user(BDM)
		self.assertEqual(
			quotation.get_trail(within)["actions"],
			{
				"approve": True,
				"approve_max": 8,
				"reject": True,
				"forward": True,
				"forward_to": "Sales & Marketing Head",
				"override": False,
			},
		)
		actions = quotation.get_trail(above)["actions"]
		self.assertEqual((actions["approve_max"], actions["reject"], actions["forward"]), (10, False, True))

		frappe.set_user(CEO)
		actions = quotation.get_trail(above)["actions"]
		self.assertEqual((actions["approve_max"], actions["reject"], actions["override"]), (18, True, True))
		self.assertFalse(actions["forward"])

		frappe.set_user(BDE)
		trail = quotation.get_trail(within)
		self.assertFalse(any(trail["actions"][key] for key in ("approve", "reject", "forward", "override")))
		self.assertEqual([step["action"] for step in trail["steps"]], ["Requested"])

	def test_decide_returns_the_trail_and_closes_the_request(self):
		name = self.request(9)
		frappe.set_user(BDE)
		with self.assertRaises(frappe.PermissionError):
			quotation.decide(name, "approve")

		frappe.set_user(BDM)
		trail = quotation.decide(name, "approve", percent=7, remarks="Seven is enough")
		self.assertEqual((trail["status"], trail["approved_percent"]), ("Approved", 7))
		self.assertEqual([step["action"] for step in trail["steps"]], ["Requested", "Approved"])
		self.assertEqual(trail["steps"][-1]["remarks"], "Seven is enough")
		self.assertFalse(trail["actions"]["approve"])
		self.assertNotIn(name, [row["name"] for row in quotation.get_approvals("waiting")])

		frappe.set_user(CEO)
		self.assertTrue(quotation.get_trail(name)["actions"]["override"])
		with self.assertRaises(frappe.ValidationError):
			quotation.decide(name, "override", percent=9)
		trail = quotation.decide(name, "override", percent=9, remarks="Strategic account")
		self.assertEqual((trail["status"], trail["approved_percent"]), ("Overridden", 9))

	def test_push_opens_the_request(self):
		self.assertEqual(
			push.route_for(BDE, "TBO Quotation Adjustment", "QADJ-2026-00001"), "/quotations/QADJ-2026-00001"
		)
