from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_system_timezone

from tbo_smart.api import telephony as api
from tbo_smart.telephony import callerdesk, calls
from tbo_smart.test_utils import (
	TEST_CALLERDESK_KEY,
	TEST_CALLERDESK_TOKEN,
	callerdesk_live,
	callerdesk_report,
	clear_dial_locks,
	delete_sales_records,
	delete_telephony_records,
	disable_smart_leads,
	disable_telephony,
	enable_callerdesk,
	enable_telephony,
	make_lead,
	make_user,
	myoperator_response,
	post_callerdesk_webhook,
	set_smart_lead_settings,
)

BDE = "cd.bde@tbosmart.test"
BDE2 = "cd.bde2@tbosmart.test"
ADMIN = "cd.admin@tbosmart.test"
ADMIN_HUB = "callerdesk.adminhub@tbosmart.test"
NUMBER = "+919876543210"
UNKNOWN_NUMBER = "+919800011122"
OTHER_NUMBER = "+919812345678"
NUMBERS = (NUMBER, OTHER_NUMBER, UNKNOWN_NUMBER)
# real audio isn't text (File.get_content decodes text)
AUDIO = b"RIFF\x24\x00\x00\x00WAVEfmt \xff\xfb\x90"
RECORDING_URL = "https://callrecords.callerdesk.test/incoming/01_rec.wav"
CLICK_URL = "https://app.callerdesk.io/api/click_to_call_v2"
AGENTS = [
	{"user": BDE, "callerdesk_number": "9000000001", "mobile": None},
	{"user": BDE2, "callerdesk_number": None, "mobile": "+919000000002"},
]


class TestCallerDesk(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		make_user(ADMIN, ["System Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_telephony_records(NUMBERS)
		clear_dial_locks((BDE, BDE2))
		delete_sales_records((BDE, BDE2))
		disable_smart_leads()
		enable_callerdesk(AGENTS)
		# stored the way people type it: spaces and a trunk 0
		self.lead = make_lead(BDE, "Caller Known", mobile_no="098765 43210")

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_telephony()
		disable_smart_leads()

	def call_of(self, call_id: str):
		return frappe.get_doc("TBO Call", {"call_id": call_id})

	def place_click_to_call(self, body: dict | None = None):
		frappe.set_user(BDE)
		answer = myoperator_response(
			200, {"status": "success", "campid": "57305000"} if body is None else body
		)
		with patch("tbo_smart.telephony.callerdesk.requests.get", return_value=answer) as get:
			result = api.click_to_call(self.lead)
		return result, get


class TestWebhook(TestCallerDesk):
	def test_an_answered_call_lands_on_the_lead_as_the_agent(self):
		self.assertEqual(post_callerdesk_webhook(callerdesk_report("CD-1")), {"ok": True})
		call = self.call_of("CD-1")
		self.assertEqual(
			(call.lead, call.agent, call.status, call.direction, call.customer_number),
			(self.lead, BDE, "Answered", "Incoming", NUMBER),
		)
		self.assertEqual((call.duration, call.talk_seconds), (18, 74))
		india = datetime(2026, 10, 19, 10, 58, 4, tzinfo=ZoneInfo("Asia/Kolkata"))
		self.assertEqual(
			call.started_at, india.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
		)
		event = frappe.get_doc("Event", call.event)
		self.assertEqual(event.owner, BDE)
		self.assertTrue(event.subject.startswith(f"Call from {NUMBER} ("))

	def test_the_recording_is_downloaded_into_a_private_file(self):
		with patch(
			"tbo_smart.telephony.calls.requests.get", return_value=myoperator_response(200, {}, content=AUDIO)
		) as get:
			post_callerdesk_webhook(callerdesk_report("CD-REC", CallRecordingUrl=RECORDING_URL))
		self.assertEqual(get.call_args.args[0], RECORDING_URL)
		recording = calls.recording_file(self.call_of("CD-REC").name)
		self.assertTrue(recording.is_private)
		self.assertTrue(recording.file_name.endswith(".wav"))
		self.assertEqual(recording.get_content(), AUDIO)

	def test_the_recording_download_is_queued_for_the_background(self):
		with patch("tbo_smart.telephony.calls.frappe.enqueue") as enqueue:
			post_callerdesk_webhook(callerdesk_report("CD-Q", CallRecordingUrl=RECORDING_URL))
			calls.process_call(self.call_of("CD-Q").name)
		queued = [call.kwargs for call in enqueue.call_args_list if "fetch_recording" in call.args[0]]
		self.assertEqual(
			[(job["queue"], job["call"]) for job in queued], [("long", self.call_of("CD-Q").name)]
		)

	def test_a_repeated_call_sid_is_ignored(self):
		post_callerdesk_webhook(callerdesk_report("CD-DUP"))
		self.assertEqual(
			post_callerdesk_webhook(callerdesk_report("CD-DUP")), {"ok": True, "duplicate": True}
		)
		self.assertEqual(frappe.db.count("TBO Call", {"call_id": "CD-DUP"}), 1)
		self.assertEqual(len(calls_events(self.lead)), 1)

	def test_a_wrong_or_missing_token_is_refused(self):
		for token in ("wrong-token", "", None):
			with self.assertRaises(frappe.PermissionError):
				post_callerdesk_webhook(callerdesk_report("CD-BAD"), token=token)
		self.assertFalse(frappe.db.exists("TBO Call", {"call_id": "CD-BAD"}))

	def test_it_is_refused_while_off_or_while_the_other_provider_is_chosen(self):
		enable_telephony(AGENTS)
		set_smart_lead_settings(telephony_provider="MyOperator")
		with self.assertRaises(frappe.PermissionError):
			post_callerdesk_webhook(callerdesk_report("CD-OFF"))
		enable_callerdesk(AGENTS)
		frappe.db.set_single_value("TBO Smart Settings", "enable_telephony", 0)
		frappe.clear_document_cache("TBO Smart Settings", "TBO Smart Settings")
		with self.assertRaises(frappe.PermissionError):
			post_callerdesk_webhook(callerdesk_report("CD-OFF"))

	def test_a_missed_call_is_missed_with_a_call_back_and_no_recording(self):
		post_callerdesk_webhook(
			callerdesk_report("CD-MISS", Status="NOANSWER", CallRecordingUrl=RECORDING_URL, TalkDuration="0")
		)
		call = self.call_of("CD-MISS")
		self.assertEqual((call.status, call.talk_seconds, call.recording_filename), ("Missed", 0, None))
		self.assertTrue(call.call_back)
		self.assertTrue(frappe.db.get_value("Event", call.event, "subject").startswith("Missed call from"))

	def test_an_unknown_caller_becomes_a_lead_of_the_agent_when_switched_on(self):
		post_callerdesk_webhook(callerdesk_report("CD-NEW", SourceNumber="9800011122"))
		lead = self.call_of("CD-NEW").lead
		self.assertEqual(
			frappe.db.get_value("Lead", lead, ["mobile_no", "lead_owner"]), (UNKNOWN_NUMBER, BDE)
		)

	def test_no_lead_for_an_unknown_caller_when_switched_off(self):
		enable_callerdesk(AGENTS, telephony_create_leads=0)
		post_callerdesk_webhook(callerdesk_report("CD-NONE", SourceNumber="9800011122"))
		self.assertIsNone(self.call_of("CD-NONE").lead)


class TestOutgoing(TestCallerDesk):
	def test_an_outgoing_call_from_the_agents_phone_finds_the_customer_by_number(self):
		post_callerdesk_webhook(
			callerdesk_report(
				"CD-OUT", Direction="WEBOBD", SourceNumber="9000000001", DialWhomNumber="9876543210"
			)
		)
		call = self.call_of("CD-OUT")
		self.assertEqual(
			(call.direction, call.customer_number, call.lead, call.agent),
			("Outgoing", NUMBER, self.lead, BDE),
		)
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "mobile_no"), "098765 43210")

	def test_an_outgoing_click_to_call_is_matched_by_its_campid(self):
		result, _get = self.place_click_to_call()
		self.assertEqual(result["reference_id"], "57305000")
		frappe.set_user("Administrator")
		# CallerDesk reports a number no lead has: the campid still finds the lead and the agent
		post_callerdesk_webhook(
			callerdesk_report(
				"CD-C2C",
				Direction="WEBOBD",
				SourceNumber="9000000001",
				DialWhomNumber="9800011122",
				campid="57305000",
			)
		)
		call = self.call_of("CD-C2C")
		self.assertEqual((call.lead, call.agent, call.customer_number), (self.lead, BDE, UNKNOWN_NUMBER))
		self.assertEqual(call.reference_id, "57305000")


class TestClickToCall(TestCallerDesk):
	def test_the_request_rings_the_agent_first_then_the_lead(self):
		result, get = self.place_click_to_call()
		self.assertEqual(result["message"], "CallerDesk is calling your phone…")
		self.assertEqual(result["number"], NUMBER)
		self.assertEqual(get.call_args.args[0], CLICK_URL)
		self.assertEqual(
			get.call_args.kwargs["params"],
			{
				"calling_party_a": "9000000001",
				"calling_party_b": "9876543210",
				"deskphone": "1200000000",
				"call_from_did": "1",
				"authcode": TEST_CALLERDESK_KEY,
			},
		)

	def test_a_call_group_is_sent_when_set_and_a_mobile_rings_when_there_is_no_callerdesk_number(self):
		enable_callerdesk(AGENTS, callerdesk_group="TestGroup")
		lead = make_lead(BDE2, "Second Caller", mobile_no=OTHER_NUMBER)
		frappe.set_user(BDE2)
		with patch(
			"tbo_smart.telephony.callerdesk.requests.get",
			return_value=myoperator_response(200, {"status": "success"}),
		) as get:
			result = api.click_to_call(lead)
		params = get.call_args.kwargs["params"]
		self.assertEqual(
			(params["group_name"], params["calling_party_a"], params["calling_party_b"]),
			("TestGroup", "9000000002", "9812345678"),
		)
		self.assertIsNone(result["reference_id"])

	def test_refusals_are_in_plain_words(self):
		for status, body, expected in (
			(
				200,
				{"status": "error", "message": "Agent number busy"},
				"CallerDesk didn't accept the call: Agent number busy",
			),
			(
				# what CallerDesk really sends: a 200 whose `type` says error
				200,
				{"type": "error", "message": "Invalid Auth Code!"},
				"CallerDesk didn't accept the call: Invalid Auth Code!",
			),
			(
				403,
				{},
				"CallerDesk didn't accept the call: it refused the API key. Ask an admin to check Settings → Telephony.",
			),
			(500, {}, "CallerDesk didn't accept the call (500). Try again in a minute."),
		):
			clear_dial_locks((BDE,))
			with self.assertRaises(frappe.ValidationError) as caught:
				self.refuse(status, body)
			self.assertEqual(str(caught.exception), expected)
			# the hub reads the reason from the response's messages
			shown = " ".join(str(row.get("message")) for row in frappe.get_message_log())
			self.assertIn(expected, shown)
			frappe.clear_messages()

	def refuse(self, status: int, body: dict):
		frappe.set_user(BDE)
		with patch(
			"tbo_smart.telephony.callerdesk.requests.get", return_value=myoperator_response(status, body)
		):
			api.click_to_call(self.lead)

	def test_an_unreachable_callerdesk_never_shows_the_key(self):
		frappe.set_user(BDE)
		with patch(
			"tbo_smart.telephony.callerdesk.requests.get",
			side_effect=requests.ConnectionError(
				f"https://app.callerdesk.io/?authcode={TEST_CALLERDESK_KEY}"
			),
		):
			with self.assertRaises(callerdesk.CallerDeskError) as caught:
				api.click_to_call(self.lead)
		self.assertEqual(str(caught.exception), "Couldn't reach CallerDesk. Try again in a minute.")

	def test_missing_settings_and_unmapped_people_are_told(self):
		enable_callerdesk(AGENTS, callerdesk_deskphone=None)
		frappe.set_user(BDE)
		with self.assertRaises(frappe.ValidationError) as caught:
			api.click_to_call(self.lead)
		self.assertIn("Virtual number (deskphone)", str(caught.exception))
		self.assertTrue(calls.can_call(BDE))
		frappe.set_user("Administrator")
		self.assertFalse(calls.can_call(ADMIN))


class TestLiveCall(TestCallerDesk):
	def status_of(self, user: str = BDE, reference_id: str = "57305000") -> dict:
		frappe.set_user(user)
		try:
			return calls.call_status(reference_id)
		finally:
			frappe.set_user("Administrator")

	def test_a_placed_call_is_calling_until_something_is_heard(self):
		self.place_click_to_call()
		frappe.set_user("Administrator")
		self.assertEqual(self.status_of(), {"state": "calling", "lead": self.lead})

	def test_live_events_move_the_call_from_ringing_to_on_call(self):
		self.place_click_to_call()
		frappe.set_user("Administrator")
		post_callerdesk_webhook(callerdesk_live("CD-L1", Status="RINGING"))
		self.assertEqual(self.status_of()["state"], "ringing")
		# the telecaller's own phone is picked up first: the lead's phone rings now
		post_callerdesk_webhook(callerdesk_live("CD-L1", DialWhomNumber="9000000001"))
		self.assertEqual(self.status_of()["state"], "ringing_lead")
		self.assertTrue(self.call_of("CD-L1").leg_a_picked_at)
		self.assertEqual(frappe.db.count("Event", {"subject": ("like", "%Caller Known%")}), 0)
		self.assertEqual(post_callerdesk_webhook(callerdesk_live("CD-L1")), {"ok": True})
		status = self.status_of()
		self.assertEqual((status["state"], status["lead"]), ("on_call", self.lead))
		self.assertEqual(self.call_of("CD-L1").agent, BDE)

	def test_a_repeated_or_older_live_event_changes_nothing(self):
		self.place_click_to_call()
		frappe.set_user("Administrator")
		post_callerdesk_webhook(callerdesk_live("CD-L2"))
		self.assertEqual(post_callerdesk_webhook(callerdesk_live("CD-L2")), {"ok": True, "duplicate": True})
		self.assertEqual(
			post_callerdesk_webhook(callerdesk_live("CD-L2", Status="RINGING")),
			{"ok": True, "duplicate": True},
		)
		self.assertEqual(self.status_of()["state"], "on_call")
		self.assertEqual(frappe.db.count("TBO Call", {"call_id": "CD-L2"}), 1)

	def test_an_event_without_a_call_sid_is_ignored(self):
		event = callerdesk_live("")
		self.assertEqual(post_callerdesk_webhook(event), {"ok": True, "ignored": True})
		self.assertEqual(frappe.db.count("TBO Call"), 0)

	def test_the_call_report_closes_the_call(self):
		self.place_click_to_call()
		frappe.set_user("Administrator")
		post_callerdesk_webhook(callerdesk_live("CD-L3"))
		post_callerdesk_webhook(
			callerdesk_report(
				"CD-L3",
				Direction="WEBOBD",
				SourceNumber="9000000001",
				DialWhomNumber="9876543210",
				campid="57305000",
				LegB_Picked_time="2026-10-19 10:58:30",
			)
		)
		status = self.status_of()
		self.assertEqual(
			(status["state"], status["answered"], status["duration"], status["talk_seconds"]),
			("ended", True, 18, 74),
		)
		call = self.call_of("CD-L3")
		self.assertEqual((call.live_status, call.lead, call.agent), ("Ended", self.lead, BDE))
		self.assertTrue(call.leg_b_picked_at)
		self.assertEqual(len(calls_events(self.lead)), 1)
		# a late Live Call event can't reopen it
		self.assertEqual(
			post_callerdesk_webhook(callerdesk_live("CD-L3", Status="RINGING")),
			{"ok": True, "duplicate": True},
		)
		self.assertEqual(self.status_of()["state"], "ended")

	def test_a_missed_call_ends_as_not_answered(self):
		self.place_click_to_call()
		frappe.set_user("Administrator")
		post_callerdesk_webhook(
			callerdesk_report(
				"CD-L4",
				Status="NOANSWER",
				Direction="WEBOBD",
				SourceNumber="9000000001",
				DialWhomNumber="9876543210",
				campid="57305000",
			)
		)
		self.assertEqual(self.status_of()["answered"], False)

	def test_only_the_person_who_placed_the_call_can_ask(self):
		self.place_click_to_call()
		frappe.set_user("Administrator")
		post_callerdesk_webhook(callerdesk_live("CD-L5"))
		with self.assertRaises(frappe.PermissionError):
			self.status_of(BDE2)
		with self.assertRaises(frappe.PermissionError):
			self.status_of(BDE, "no-such-call")

	def test_the_hub_asks_for_the_status_as_the_person(self):
		self.place_click_to_call()
		frappe.set_user("Administrator")
		with self.assertRaises(frappe.PermissionError):
			api.get_call_status("57305000", acting_user=BDE2)

	def test_an_incoming_call_shows_to_the_telecaller_it_rings(self):
		post_callerdesk_webhook(
			callerdesk_live(
				"CD-IN",
				Direction="IVR",
				SourceNumber="9876543210",
				DialWhomNumber="9000000001",
				campid="",
				Status="RINGING",
			)
		)
		frappe.set_user(BDE)
		self.assertTrue(api.get_incoming_call()["enabled"])
		incoming = calls.incoming_call()
		self.assertEqual(
			(incoming["number"], incoming["lead"], incoming["state"]), (NUMBER, self.lead, "ringing")
		)
		frappe.set_user(BDE2)
		self.assertIsNone(calls.incoming_call())
		frappe.set_user("Administrator")
		post_callerdesk_webhook(callerdesk_report("CD-IN"))
		frappe.set_user(BDE)
		self.assertIsNone(calls.incoming_call())


class TestSettings(TestCallerDesk):
	def test_choosing_callerdesk_makes_the_webhook_token_and_the_address_carries_it(self):
		frappe.db.delete("__Auth", {"doctype": "TBO Smart Settings", "fieldname": "callerdesk_webhook_token"})
		doc = frappe.get_single("TBO Smart Settings")
		doc.telephony_provider = "CallerDesk"
		doc.save(ignore_permissions=True)
		token = frappe.get_single("TBO Smart Settings").get_password("callerdesk_webhook_token")
		self.assertEqual(len(token), 40)
		frappe.set_user(ADMIN)
		url = api.get_callerdesk_webhook_url()["url"]
		self.assertTrue(url.endswith(f"/api/method/tbo_smart.api.telephony.callerdesk_webhook?token={token}"))
		self.assertNotEqual(api.regenerate_callerdesk_token()["url"], url)

	def test_the_settings_never_return_the_key_or_the_token_and_only_managers_see_the_address(self):
		frappe.set_user(ADMIN)
		shown = frappe.as_json(api.get_settings())
		self.assertNotIn(TEST_CALLERDESK_KEY, shown)
		self.assertNotIn(TEST_CALLERDESK_TOKEN, shown)
		self.assertTrue(api.test_connection()["ok"])
		frappe.set_user(BDE)
		with self.assertRaises(frappe.PermissionError):
			api.get_callerdesk_webhook_url()

	def test_the_desk_form_works_for_a_manager_who_also_holds_the_hub_role(self):
		# Administrator holds every role, the hub integration role too: the desk form names no hub
		# user and must still get the address
		make_user(ADMIN_HUB, ["System Manager", "Hub Integration"])
		frappe.set_user(ADMIN_HUB)
		self.assertIn("token=", api.get_callerdesk_webhook_url()["url"])
		self.assertTrue(api.test_connection()["ok"])

	def test_a_person_needs_a_number_to_ring(self):
		doc = frappe.get_single("TBO Smart Settings")
		doc.set("telephony_agents", [])
		doc.append("telephony_agents", {"user": BDE})
		with self.assertRaises(frappe.ValidationError):
			doc.validate_telephony()


def calls_events(lead: str) -> list[str]:
	return frappe.get_all(
		"Event Participants",
		filters={"reference_doctype": "Lead", "reference_docname": lead, "parenttype": "Event"},
		pluck="parent",
	)
