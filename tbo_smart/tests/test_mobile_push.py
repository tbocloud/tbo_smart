from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart import push
from tbo_smart.api.mobile import device
from tbo_smart.test_utils import (
	disable_mobile_push,
	enable_mobile_push,
	make_mobile_device,
	make_user,
)

PHONE_OWNER = "push.owner@tbosmart.test"
SECOND_USER = "push.second@tbosmart.test"
TOKEN = "ExponentPushToken[tbo-smart-test-1]"


class TestMobilePush(FrappeTestCase):
	def setUp(self):
		make_user(PHONE_OWNER, ["Employee"])
		make_user(SECOND_USER, ["Employee"])
		frappe.db.delete(
			"TBO Mobile Device", {"expo_push_token": ("like", "ExponentPushToken[tbo-smart-test%")}
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_mobile_push()

	def test_register_is_idempotent_and_follows_the_signed_in_user(self):
		frappe.set_user(PHONE_OWNER)
		first = device.register_device(TOKEN, "android", "1.0.0")
		again = device.register_device(TOKEN, "android", "1.0.1")
		self.assertEqual(first["name"], again["name"])

		frappe.set_user(SECOND_USER)
		device.register_device(TOKEN, "ios", "1.0.1")
		self.assertEqual(
			frappe.db.get_value("TBO Mobile Device", {"expo_push_token": TOKEN}, "user"), SECOND_USER
		)

	def test_register_rejects_bad_tokens(self):
		frappe.set_user(PHONE_OWNER)
		with self.assertRaises(frappe.ValidationError):
			device.register_device("fcm-token", "android")

	def test_unregister_only_removes_my_own_token(self):
		make_mobile_device(PHONE_OWNER, TOKEN)
		frappe.set_user(SECOND_USER)
		self.assertEqual(device.unregister_device(TOKEN), {"removed": False})
		frappe.set_user(PHONE_OWNER)
		self.assertEqual(device.unregister_device(TOKEN), {"removed": True})

	def test_no_push_while_the_setting_is_off(self):
		make_mobile_device(PHONE_OWNER, TOKEN)
		disable_mobile_push()
		with patch("frappe.enqueue") as enqueue:
			push.enqueue_push(PHONE_OWNER, "Hi", "", "CRM Deal", "CRM-DEAL-1")
		enqueue.assert_not_called()

	def test_notification_log_enqueues_a_push_when_on(self):
		make_mobile_device(PHONE_OWNER, TOKEN)
		enable_mobile_push()
		with patch("frappe.enqueue") as enqueue:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": PHONE_OWNER,
					"type": "Alert",
					"subject": "<b>New deal</b> assigned",
					"document_type": "CRM Deal",
					"document_name": "CRM-DEAL-1",
				}
			).insert(ignore_permissions=True)
		kwargs = next(
			call.kwargs for call in enqueue.call_args_list if call.args[:1] == ("tbo_smart.push.send_push",)
		)
		self.assertEqual((kwargs["user"], kwargs["title"]), (PHONE_OWNER, "New deal assigned"))

	def test_send_push_payload_and_dead_token_cleanup(self):
		make_mobile_device(PHONE_OWNER, TOKEN)
		response = MagicMock()
		response.json.return_value = {
			"data": [{"status": "error", "details": {"error": "DeviceNotRegistered"}}]
		}
		with patch("tbo_smart.push.requests.post", return_value=response) as post:
			push.send_push(PHONE_OWNER, "Hi", "Body", "CRM Deal", "CRM-DEAL-1")
		message = post.call_args.kwargs["json"][0]
		self.assertEqual(
			message["data"],
			{"site": "ess", "doctype": "CRM Deal", "name": "CRM-DEAL-1", "route": "/crm/deal/CRM-DEAL-1"},
		)
		self.assertFalse(frappe.db.exists("TBO Mobile Device", {"expo_push_token": TOKEN}))

	def test_send_push_never_raises(self):
		make_mobile_device(PHONE_OWNER, TOKEN)
		with patch("tbo_smart.push.requests.post", side_effect=OSError("network down")):
			push.send_push(PHONE_OWNER, "Hi", "Body", None, None)

	def test_leave_routes_differ_for_employee_and_approver(self):
		with patch("frappe.db.get_value", side_effect=["HR-EMP-1", "push.staff@tbosmart.test"]):
			self.assertEqual(
				push.route_for("push.staff@tbosmart.test", "Leave Application", "LA-1"), "/hr/leave"
			)
		with patch("frappe.db.get_value", side_effect=["HR-EMP-1", "push.staff@tbosmart.test"]):
			self.assertEqual(
				push.route_for(PHONE_OWNER, "Leave Application", "LA-1"), "/team/approvals?kind=leave"
			)
