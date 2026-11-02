"""WhatsApp through TBO Chat (docs/whatsapp-outreach.md). TBO Chat is the hub's: nothing here
calls it; the hub's side of the contract is called directly."""

from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

from tbo_smart.api import hub_leads as leads_api
from tbo_smart.api import hub_outreach as hub
from tbo_smart.api import hub_whatsapp as chat
from tbo_smart.lead_pipeline.statuses import CONTACTED, POSITIVE
from tbo_smart.outreach import guards, replies, whatsapp
from tbo_smart.smart_leads.utils import normalise_phone
from tbo_smart.test_utils import (
	delete_chat_communications,
	enable_lead_pipeline,
	make_outreach_message,
	make_queued_whatsapp,
	receive_chat,
)
from tbo_smart.tests.test_outreach import SELLER, OutreachCase, ai_answer, ai_on

PHONE = "+919847012345"


class WhatsAppCase(OutreachCase):
	def tearDown(self):
		delete_chat_communications()
		super().tearDown()

	def as_integration(self, method, **kwargs):
		frappe.set_user("outreach.integration@tbosmart.test")
		try:
			return method(**kwargs)
		finally:
			frappe.set_user("Administrator")

	def message_value(self, name: str, field: str):
		return frappe.db.get_value("TBO Outreach Message", name, field)


class TestPhones(WhatsAppCase):
	def test_numbers_are_normalised_with_the_default_country_code(self):
		self.assertEqual(normalise_phone("98470 12345"), "919847012345")
		self.assertEqual(normalise_phone("098470 12345"), "919847012345")
		self.assertEqual(normalise_phone("+971 50 123 4567"), "971501234567")
		self.assertEqual(normalise_phone("0501234567", "971"), "971501234567")
		self.assertIsNone(normalise_phone("12345"))

	def test_a_chat_finds_its_lead_whatever_the_number_looks_like(self):
		frappe.db.set_value("Lead", self.lead, "mobile_no", "098470 12345")
		self.assertEqual(whatsapp.find_lead("+919847012345"), self.lead)
		self.assertIsNone(whatsapp.find_lead("+919000000000"))

	def test_a_chat_finds_its_lead_by_conversation_first(self):
		frappe.db.set_value("Lead", self.lead, "tbo_chat_conversation_id", "77")
		self.assertEqual(whatsapp.find_lead("+910000000000", 77), self.lead)


class TestApprovingWhatsApp(WhatsAppCase):
	def test_without_an_inbox_approving_stays_manual_as_before(self):
		name = make_outreach_message(self.lead, "WhatsApp", owner=SELLER, recipient=PHONE)
		result = self.as_hub(hub.approve_outreach, names=[name])[0]
		self.assertEqual(result["status"], "Approved")
		self.assertIn("open WhatsApp", result["message"])
		self.assertEqual(self.message_value(name, "chat_status") or "", "")
		rows = self.as_hub(hub.get_outbox, status="Approved")["rows"]
		row = next(row for row in rows if row["name"] == name)
		self.assertTrue(row["url"].startswith("https://wa.me/"))
		self.assertEqual(self.as_integration(chat.get_whatsapp_queue), [])

	def test_with_an_inbox_it_waits_in_the_chat_queue(self):
		name = make_outreach_message(self.lead, "WhatsApp", owner=SELLER, recipient=PHONE)
		result = self.as_hub(hub.approve_outreach, names=[name], send_whatsapp=1)[0]
		self.assertEqual(result["status"], "Approved")
		self.assertEqual(self.message_value(name, "chat_status"), "Queued")
		row = self.as_hub(hub.get_outbox, status="Approved")["rows"][0]
		self.assertEqual(row["whatsapp"]["chat_status"], "Queued")
		self.assertEqual(row["whatsapp"]["first_name"], "Palm")
		self.assertTrue(row["whatsapp"]["why_call"])

	def test_the_email_sender_ignores_whatsapp(self):
		name = make_queued_whatsapp(self.lead, SELLER)
		from tbo_smart.outreach import sending

		sending.send_approved()
		self.assertEqual(self.status_of(name), "Approved")


class TestTheQueue(WhatsAppCase):
	def test_the_queue_hands_out_a_message_once_with_what_the_template_needs(self):
		name = make_queued_whatsapp(self.lead, SELLER)
		rows = self.as_integration(chat.get_whatsapp_queue)
		self.assertEqual([row["name"] for row in rows], [name])
		self.assertEqual(rows[0]["to"], PHONE)
		self.assertEqual(rows[0]["first_name"], "Palm")
		self.assertFalse(rows[0]["window_open"], "a first message needs a template")
		self.assertTrue(rows[0]["why_call"])
		self.assertEqual(self.as_integration(chat.get_whatsapp_queue), [])
		self.assertEqual(self.message_value(name, "chat_status"), "Sending")

	def test_the_country_code_comes_from_the_hub(self):
		frappe.db.set_value("Lead", self.lead, "mobile_no", "0501234567")
		make_queued_whatsapp(self.lead, SELLER)
		rows = self.as_integration(chat.get_whatsapp_queue, country_code="971")
		self.assertEqual(rows[0]["to"], "+971501234567")

	def test_inside_24_hours_the_queue_says_free_text_is_allowed(self):
		receive_chat(PHONE, "Hello", conversation_id="5", message_id="m0")
		make_queued_whatsapp(self.lead, SELLER)
		self.assertTrue(self.as_integration(chat.get_whatsapp_queue)[0]["window_open"])

	def test_after_24_hours_a_template_is_needed_again(self):
		frappe.db.set_value("Lead", self.lead, "tbo_whatsapp_last_in", add_to_date(now_datetime(), hours=-25))
		make_queued_whatsapp(self.lead, SELLER)
		self.assertFalse(self.as_integration(chat.get_whatsapp_queue)[0]["window_open"])

	def test_an_unsubscribed_lead_is_never_handed_out(self):
		name = make_queued_whatsapp(self.lead, SELLER)
		frappe.db.set_value("Lead", self.lead, "unsubscribed", 1)
		self.assertEqual(self.as_integration(chat.get_whatsapp_queue), [])
		self.assertEqual(self.status_of(name), "Cancelled")

	def test_the_queue_waits_for_the_send_window(self):
		make_queued_whatsapp(self.lead, SELLER)
		with patch("tbo_smart.outreach.whatsapp.in_window", return_value=False):
			self.assertEqual(self.as_integration(chat.get_whatsapp_queue), [])

	def test_only_the_integration_user_may_use_the_hub_calls(self):
		frappe.set_user(SELLER)
		try:
			with self.assertRaises(frappe.PermissionError):
				chat.get_whatsapp_queue()
			with self.assertRaises(frappe.PermissionError):
				chat.receive_whatsapp(PHONE, "hi")
		finally:
			frappe.set_user("Administrator")


class TestReporting(WhatsAppCase):
	def setUp(self):
		super().setUp()
		self.name = make_queued_whatsapp(self.lead, SELLER)
		self.as_integration(chat.get_whatsapp_queue)

	def report(self, state, **fields):
		return self.as_integration(chat.report_whatsapp, name=self.name, state=state, **fields)

	def test_sent_files_the_message_on_the_lead_and_keeps_the_chat_ids(self):
		result = self.report(
			"sent",
			text="Hi Palm, ...",
			contact_id="11",
			conversation_id="22",
			message_id="wamid-1",
			template="tbo_intro",
		)
		self.assertEqual(result["status"], "Sent")
		self.assertEqual(self.message_value(self.name, "template"), "tbo_intro")
		self.assertTrue(self.message_value(self.name, "communication"))
		self.assertEqual(self.value("tbo_chat_conversation_id"), "22")
		self.assertEqual(self.value("tbo_chat_contact_id"), "11")
		timeline = leads_api.timeline(self.lead, [], [])
		self.assertEqual(
			[(i["kind"], i["direction"]) for i in timeline if i["kind"] == "whatsapp"],
			[("whatsapp", "outgoing")],
		)

	def test_failed_keeps_the_reason_for_a_person(self):
		result = self.report("failed", error="Number is not on WhatsApp")
		self.assertEqual(result["status"], "Failed")
		self.assertEqual(self.message_value(self.name, "error"), "Number is not on WhatsApp")

	def test_manual_falls_back_to_the_wa_me_link(self):
		self.assertEqual(self.report("manual")["status"], "Approved")
		self.assertEqual(self.message_value(self.name, "via_chat"), 0)
		self.assertEqual(self.as_integration(chat.get_whatsapp_queue), [])
		self.assertEqual(self.as_hub(hub.mark_outreach_sent, names=[self.name])[0]["status"], "Sent")

	def test_a_message_is_reported_once(self):
		self.report("sent", text="x", message_id="wamid-2")
		self.assertIn("not waiting", self.report("failed", error="late")["message"])
		self.assertEqual(self.status_of(self.name), "Sent")


class TestDeliveryStatus(WhatsAppCase):
	def setUp(self):
		super().setUp()
		self.name = make_queued_whatsapp(self.lead, SELLER)
		self.as_integration(chat.get_whatsapp_queue)
		self.as_integration(
			chat.report_whatsapp, name=self.name, state="sent", text="x", message_id="wamid-9"
		)

	def status(self, status, **fields):
		return self.as_integration(chat.whatsapp_status, message_id="wamid-9", status=status, **fields)

	def test_delivered_then_read(self):
		self.assertEqual(self.status("delivered")["status"], "Delivered")
		self.assertEqual(self.status("read")["status"], "Read")

	def test_a_late_receipt_never_undoes_a_newer_one(self):
		self.status("read")
		self.assertEqual(self.status("delivered")["status"], "Read")

	def test_failed_marks_the_message_failed(self):
		self.status("failed", error="Not on WhatsApp")
		self.assertEqual(self.status_of(self.name), "Failed")
		self.assertEqual(self.message_value(self.name, "error"), "Not on WhatsApp")

	def test_an_unknown_message_is_not_ours(self):
		result = self.as_integration(chat.whatsapp_status, message_id="nope", status="read")
		self.assertFalse(result["matched"])


class TestReplies(WhatsAppCase):
	def setUp(self):
		super().setUp()
		enable_lead_pipeline()
		frappe.db.set_value("Lead", self.lead, "tbo_stage", CONTACTED)

	def sort(self, text="Yes, tell me more", reply_class="Interested"):
		found = receive_chat(PHONE, text, conversation_id="31", contact_id="9", message_id=f"in-{text}")
		with ai_on(), ai_answer({"class": reply_class}), patch("tbo_smart.outreach.replies.notify"):
			return found, replies.process_reply(found["communication"])

	def test_a_reply_is_filed_on_the_lead_and_opens_the_window(self):
		found = receive_chat(PHONE, "Hello", conversation_id="31", contact_id="9", message_id="m1")
		self.assertEqual(found["lead"], self.lead)
		self.assertEqual(self.value("tbo_chat_conversation_id"), "31")
		self.assertTrue(whatsapp.lead_state(self.lead)["window_open"])
		items = [i for i in leads_api.timeline(self.lead, [], []) if i["kind"] == "whatsapp"]
		self.assertEqual((items[0]["direction"], items[0]["text"]), ("incoming", "Hello"))

	def test_the_same_chat_message_is_filed_once(self):
		receive_chat(PHONE, "Hello", message_id="m1")
		receive_chat(PHONE, "Hello", message_id="m1")
		self.assertEqual(frappe.db.count("Communication", {"communication_medium": "Chat"}), 1)

	def test_a_chat_from_nobody_we_know_is_not_matched(self):
		self.assertFalse(receive_chat("+919000000001", "Hi support")["matched"])

	def test_an_interested_reply_is_classified_and_moves_the_stage(self):
		_found, reply_class = self.sort()
		self.assertEqual(reply_class, "Interested")
		self.assertEqual(self.value("tbo_stage"), POSITIVE)
		self.assertEqual(self.value("tbo_last_reply_class"), "Interested")

	def test_the_reply_hook_queues_the_sorter_for_whatsapp(self):
		with patch("frappe.enqueue") as enqueue:
			whatsapp.receive(PHONE, "Hello", message_id="m5")
		self.assertEqual(enqueue.call_args.args[0], "tbo_smart.outreach.replies.process_reply")

	def test_stop_unsubscribes_without_asking_the_ai_and_stops_sending(self):
		draft = make_outreach_message(self.lead, "WhatsApp", owner=SELLER, recipient=PHONE)
		queued = make_queued_whatsapp(self.lead, SELLER)
		found = receive_chat(PHONE, "STOP", message_id="m7")
		with patch("tbo_smart.ai.call_ai") as call_ai, patch("tbo_smart.outreach.replies.notify"):
			self.assertEqual(replies.process_reply(found["communication"]), "Unsubscribe")
		call_ai.assert_not_called()
		self.assertEqual(self.value("unsubscribed"), 1)
		self.assertEqual((self.status_of(draft), self.status_of(queued)), ("Cancelled", "Cancelled"))
		self.assertEqual(self.as_integration(chat.get_whatsapp_queue), [])
		self.assertTrue(guards.never_contact(self.lead))
		self.assertTrue(whatsapp.lead_state(self.lead)["blocked"])

	def test_without_the_ai_a_reply_is_flagged_for_a_person(self):
		found = receive_chat(PHONE, "Maybe", message_id="m8")
		with patch("tbo_smart.ai.ai_ready", return_value=False), patch("tbo_smart.outreach.replies.notify"):
			self.assertEqual(replies.process_reply(found["communication"]), "New reply")


class TestReplyBox(WhatsAppCase):
	def test_free_text_is_logged_on_the_lead_inside_the_window(self):
		receive_chat(PHONE, "Hello", conversation_id="5", message_id="m1")
		state = self.as_hub(chat.get_lead_whatsapp, lead=self.lead)
		self.assertTrue(state["window_open"])
		self.as_hub(
			chat.log_lead_whatsapp, lead=self.lead, text="Thanks!", message_id="out-1", conversation_id="5"
		)
		items = [i for i in leads_api.timeline(self.lead, [], []) if i["kind"] == "whatsapp"]
		self.assertEqual({i["direction"] for i in items}, {"incoming", "outgoing"})

	def test_without_a_reply_the_window_is_closed(self):
		state = self.as_hub(chat.get_lead_whatsapp, lead=self.lead)
		self.assertFalse(state["window_open"])
		self.assertEqual(state["to"], PHONE)
		self.assertTrue(state["can_write"])

	def test_an_unsubscribed_lead_cannot_be_written_to(self):
		frappe.db.set_value("Lead", self.lead, "unsubscribed", 1)
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(chat.log_lead_whatsapp, lead=self.lead, text="Hi")

	def test_the_lead_page_carries_the_whatsapp_state(self):
		payload = self.as_hub(leads_api.get_lead, name=self.lead)
		self.assertIn("window_open", payload["lead"]["whatsapp"])
