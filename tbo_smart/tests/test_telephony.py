import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, today

from tbo_smart import ai
from tbo_smart.api import hub_leads as hub
from tbo_smart.api import telephony as api
from tbo_smart.api.mobile import crm
from tbo_smart.api.mobile import leads as mobile_leads
from tbo_smart.lead_pipeline.overview import SalesActivity
from tbo_smart.telephony import calls, myoperator, setup
from tbo_smart.test_utils import (
	TEST_MYOPERATOR_KEY,
	TEST_MYOPERATOR_SECRET,
	TEST_OPENAI_KEY,
	ai_http_response,
	capabilities_as,
	clear_dial_locks,
	delete_sales_records,
	delete_telephony_records,
	disable_smart_leads,
	disable_telephony,
	enable_ai_leads,
	enable_telephony,
	make_lead,
	make_lead_source,
	make_user,
	mock_ai,
	myoperator_event,
	myoperator_response,
	openai_answer,
	post_myoperator_webhook,
	transcription_answer,
)

HUB = "tel.hub@tbosmart.test"
BDE = "tel.bde@tbosmart.test"
BDE2 = "tel.bde2@tbosmart.test"
NUMBER = "+919876543210"
OTHER_NUMBER = "+919812345678"
UNKNOWN_NUMBER = "+919800011122"
# real audio isn't text (File.get_content decodes text)
AUDIO = b"ID3\x04\x00\xff\xfb\x90\x00"
ADMIN = "tel.admin@tbosmart.test"
NUMBERS = (NUMBER, OTHER_NUMBER, UNKNOWN_NUMBER)
AGENT = {"uuid": "mo-uuid-bde", "email": None, "extension": "10", "contact": "+919000000001"}
AGENTS = [
	{"user": BDE, "myoperator_user_id": "mo-uuid-bde", "extension": "10", "mobile": "+919000000001"},
	{"user": BDE2, "myoperator_user_id": None, "extension": None, "mobile": "9000000002"},
]


class TelephonyTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		make_user(ADMIN, ["System Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_telephony_records(NUMBERS)
		clear_dial_locks((BDE, BDE2))
		delete_sales_records((BDE, BDE2))
		disable_smart_leads()
		enable_telephony(AGENTS)
		# stored the way people type it: spaces and a trunk 0
		self.lead = make_lead(BDE, "Caller Known", mobile_no="098765 43210")

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_telephony()
		disable_smart_leads()

	def call_of(self, call_id: str):
		return frappe.get_doc("TBO Call", {"call_id": call_id})

	def call_events(self, lead: str) -> list[str]:
		return frappe.get_all(
			"Event Participants",
			filters={"reference_doctype": "Lead", "reference_docname": lead, "parenttype": "Event"},
			pluck="parent",
		)


class TestWebhook(TelephonyTestCase):
	def test_a_wrong_or_missing_secret_is_refused(self):
		for token in ("wrong-secret", "", None):
			self.assertRaises(
				frappe.PermissionError, post_myoperator_webhook, myoperator_event("C-AUTH"), token
			)
		self.assertFalse(frappe.db.exists("TBO Call", {"call_id": "C-AUTH"}))
		# the payload can't vouch for itself either: its company ID decides nothing
		forged = myoperator_event("C-AUTH")
		forged["company_id"] = "C-TEST"
		self.assertRaises(frappe.PermissionError, post_myoperator_webhook, forged, "C-TEST")

	def test_switched_off_refuses_even_the_right_secret(self):
		disable_telephony()
		self.assertRaises(frappe.PermissionError, post_myoperator_webhook, myoperator_event("C-OFF"))

	def test_the_same_call_lands_once(self):
		event = myoperator_event("C-ONCE", agent=AGENT)
		self.assertEqual(post_myoperator_webhook(event), {"ok": True})
		self.assertEqual(post_myoperator_webhook(event), {"ok": True, "duplicate": True})
		# call.end after call.summary changes nothing either
		self.assertTrue(
			post_myoperator_webhook(myoperator_event("C-ONCE", event_type="call.end"))["duplicate"]
		)
		self.assertEqual(frappe.db.count("TBO Call", {"call_id": "C-ONCE"}), 1)
		self.assertEqual(len(self.call_events(self.lead)), 1)

	def test_call_end_then_summary_completes_the_call_without_a_second_event(self):
		post_myoperator_webhook(myoperator_event("C-TWO", event_type="call.end"))
		self.assertIsNone(self.call_of("C-TWO").agent)
		post_myoperator_webhook(myoperator_event("C-TWO", agent=AGENT))
		call = self.call_of("C-TWO")
		self.assertEqual((call.agent, call.last_event), (BDE, "call.summary"))
		self.assertEqual(len(self.call_events(self.lead)), 1)

	def test_the_final_summary_corrects_the_event_and_the_call_back(self):
		# call.end said missed with nobody on it; call.summary says Bina answered
		post_myoperator_webhook(
			myoperator_event("C-FIX", event_type="call.end", status="missed", duration=10)
		)
		call = self.call_of("C-FIX")
		self.assertEqual(frappe.db.get_value("ToDo", call.call_back, "status"), "Open")
		post_myoperator_webhook(myoperator_event("C-FIX", agent=AGENT, duration=95))
		call.reload()
		event = frappe.db.get_value("Event", call.event, ["subject", "owner"], as_dict=True)
		self.assertEqual((event.subject, event.owner), (f"Call from {NUMBER} (1m 35s)", BDE))
		self.assertEqual(self.call_events(self.lead), [call.event])
		# the cancelled task stays linked on the call
		self.assertEqual(frappe.db.get_value("ToDo", call.call_back, "status"), "Cancelled")
		self.assertEqual(
			frappe.get_all("ToDo", filters={"reference_name": self.lead}, pluck="name"), [call.call_back]
		)

	def test_a_call_back_another_missed_call_shares_stays_open(self):
		post_myoperator_webhook(myoperator_event("C-M1", status="missed", duration=10))
		post_myoperator_webhook(myoperator_event("C-M2", event_type="call.end", status="missed", duration=12))
		first, second = self.call_of("C-M1"), self.call_of("C-M2")
		self.assertEqual(first.call_back, second.call_back)
		post_myoperator_webhook(myoperator_event("C-M2", agent=AGENT, duration=60))
		self.assertEqual(frappe.db.get_value("ToDo", first.call_back, "status"), "Open")

	def test_a_summary_that_loses_the_insert_race_still_completes_the_call(self):
		post_myoperator_webhook(myoperator_event("C-RACE", event_type="call.end"))
		real = calls.stored_call
		# as if call.end were stored between this request's read and its insert
		with patch("tbo_smart.telephony.calls.stored_call", side_effect=[None, real("C-RACE")]):
			self.assertEqual(post_myoperator_webhook(myoperator_event("C-RACE", agent=AGENT)), {"ok": True})
		call = self.call_of("C-RACE")
		self.assertEqual((call.last_event, call.agent), ("call.summary", BDE))

	def test_other_events_are_acknowledged_and_ignored(self):
		event = myoperator_event("C-LIVE", event_type="call.answered")
		self.assertEqual(post_myoperator_webhook(event), {"ok": True, "ignored": True})
		self.assertFalse(frappe.db.exists("TBO Call", {"call_id": "C-LIVE"}))

	def test_an_answered_call_lands_on_the_lead_as_the_agent(self):
		post_myoperator_webhook(myoperator_event("C-IN", agent=AGENT, duration=125))
		call = self.call_of("C-IN")
		self.assertEqual(
			(call.lead, call.agent, call.direction, call.status, call.duration, call.customer_number),
			(self.lead, BDE, "Incoming", "Answered", 125, NUMBER),
		)
		event = frappe.get_doc("Event", call.event)
		self.assertEqual((event.event_category, event.owner), ("Call", BDE))
		self.assertEqual(event.subject, f"Call from {NUMBER} (2m 05s)")
		# 05:28 UTC is 10:58 in India
		self.assertEqual(str(call.started_at), "2026-10-19 10:58:04")


class TestMatching(TelephonyTestCase):
	def test_number_formats_match_the_same_lead(self):
		for number in ("+91 98765 43210", "9876543210", "09876543210", "+91-98765-43210"):
			self.assertEqual(calls.find_lead(number), self.lead)
		self.assertIsNone(calls.find_lead("+919876500000"))
		self.assertIsNone(calls.find_lead("12"))

	def test_phone_and_whatsapp_numbers_match_and_an_open_lead_comes_first(self):
		other = make_lead(BDE2, "Other Number", whatsapp_no="+91 98123 45678")
		self.assertEqual(calls.find_lead(OTHER_NUMBER), other)
		frappe.db.set_value("Lead", other, "tbo_stage", "Closed – Lost", update_modified=False)
		newer_open = make_lead(BDE2, "Open One", phone="9812345678")
		frappe.db.set_value("Lead", newer_open, "modified", add_days(today(), -30))
		self.assertEqual(calls.find_lead(OTHER_NUMBER), newer_open)

	def test_agents_map_by_user_id_extension_or_mobile(self):
		self.assertEqual(calls.map_agent({"uuid": "mo-uuid-bde"}), BDE)
		self.assertEqual(calls.map_agent({"uuid": "unknown", "extension": "10"}), BDE)
		self.assertEqual(calls.map_agent({"uuid": "unknown", "contact": "+91 90000 00002"}), BDE2)
		self.assertEqual(calls.map_agent({"uuid": "unknown", "email": BDE2}), BDE2)
		self.assertIsNone(calls.map_agent({"uuid": "unknown", "email": "stranger@example.com"}))

	def test_an_unknown_caller_becomes_a_lead_of_the_agent(self):
		make_lead_source("Phone call")
		post_myoperator_webhook(myoperator_event("C-NEW", number=UNKNOWN_NUMBER, agent=AGENT))
		call = self.call_of("C-NEW")
		lead = frappe.get_doc("Lead", call.lead)
		self.assertEqual(
			(lead.mobile_no, lead.source, lead.lead_owner, lead.first_name),
			(UNKNOWN_NUMBER, "Phone call", BDE, UNKNOWN_NUMBER),
		)
		self.assertEqual(self.call_events(lead.name), [call.event])

	def test_no_lead_for_an_unknown_number_when_switched_off_or_outgoing(self):
		enable_telephony(AGENTS, telephony_create_leads=0)
		post_myoperator_webhook(myoperator_event("C-NOLEAD", number=UNKNOWN_NUMBER, agent=AGENT))
		enable_telephony(AGENTS)
		post_myoperator_webhook(
			myoperator_event("C-OUT", number=UNKNOWN_NUMBER, direction="outgoing", agent=AGENT)
		)
		for call_id in ("C-NOLEAD", "C-OUT"):
			call = self.call_of(call_id)
			self.assertEqual((call.lead, call.event), (None, None))
		self.assertFalse(frappe.db.exists("Lead", {"mobile_no": UNKNOWN_NUMBER}))

	def test_a_missed_call_becomes_one_call_back_task_for_the_owner(self):
		post_myoperator_webhook(myoperator_event("C-MISS-1", status="missed", duration=20))
		post_myoperator_webhook(myoperator_event("C-MISS-2", status="missed", duration=15))
		tasks = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Lead", "reference_name": self.lead, "status": "Open"},
			fields=["allocated_to", "date", "priority", "description"],
		)
		self.assertEqual(len(tasks), 1)
		self.assertEqual(
			(tasks[0].allocated_to, getdate(tasks[0].date), tasks[0].priority), (BDE, getdate(), "High")
		)
		self.assertIn(f"Call back: {NUMBER}", tasks[0].description)
		subjects = {frappe.db.get_value("Event", e, "subject") for e in self.call_events(self.lead)}
		self.assertEqual(subjects, {f"Missed call from {NUMBER}"})


class TestRecording(TelephonyTestCase):
	def receive_recorded_call(self, call_id: str = "C-REC", **event):
		with patch(
			"tbo_smart.telephony.myoperator.requests.get",
			side_effect=[
				myoperator_response(
					200, {"status": "success", "data": {"url": "https://rec.myoperator.test/a.mp3"}}
				),
				myoperator_response(200, {}, content=AUDIO),
			],
		) as get:
			post_myoperator_webhook(
				myoperator_event(call_id, agent=AGENT, recording_filename="abc-v2.mp3", **event)
			)
		return self.call_of(call_id), get

	def test_the_recording_is_downloaded_into_a_private_file(self):
		call, get = self.receive_recorded_call()
		link = get.call_args_list[0]
		self.assertEqual(link.args[0], "https://developers.myoperator.co/search/recordings/link")
		self.assertEqual(link.kwargs["params"]["file"], "abc-v2.mp3")
		self.assertEqual(get.call_args_list[1].args[0], "https://rec.myoperator.test/a.mp3")
		recording = calls.recording_file(call.name)
		self.assertTrue(recording.is_private)
		self.assertEqual(recording.get_content(), AUDIO)

	def test_only_people_who_may_read_the_lead_hear_it(self):
		call, _get = self.receive_recorded_call()
		frappe.set_user(BDE)
		api.call_recording(call.name)
		self.assertEqual(frappe.local.response.filecontent, AUDIO)
		self.assertEqual(frappe.local.response.content_type, "audio/mpeg")
		frappe.set_user(BDE2)
		self.assertRaises(frappe.PermissionError, api.call_recording, call.name)
		frappe.set_user(HUB)
		self.assertRaises(frappe.PermissionError, api.call_recording, call.name, acting_user=BDE2)
		api.call_recording(call.name, acting_user=BDE)
		frappe.set_user("Guest")
		self.assertRaises(frappe.PermissionError, api.call_recording, call.name)

	def test_the_timeline_shows_the_call_without_the_transcript(self):
		call, _get = self.receive_recorded_call()
		frappe.db.set_value("TBO Call", call.name, "transcript", "secret words")
		frappe.set_user(HUB)
		item = next(i for i in hub.get_lead(self.lead, acting_user=BDE)["timeline"] if i["kind"] == "call")
		self.assertEqual(
			(item["call"], item["duration"], item["direction"], item["status"], item["recording"]),
			(call.name, 82, "incoming", "answered", call.name),
		)
		self.assertNotIn("secret words", json.dumps(item, default=str))
		frappe.set_user(BDE)
		activity = next(a for a in crm.get_lead(self.lead)["activities"] if a["kind"] == "call")
		self.assertEqual((activity["recording"], activity["direction"]), (call.name, "incoming"))

	def test_old_recordings_are_purged_and_the_call_stays(self):
		call, _get = self.receive_recorded_call()
		# no start time from MyOperator: the day it was stored counts; telephony off purges too
		frappe.db.set_value("TBO Call", call.name, {"started_at": None, "creation": add_days(today(), -200)})
		disable_telephony()
		calls.purge_old_recordings()
		call.reload()
		self.assertFalse(call.recording)
		self.assertTrue(call.event)
		self.assertFalse(
			frappe.db.exists("File", {"attached_to_doctype": "TBO Call", "attached_to_name": call.name})
		)


class TestClickToCall(TelephonyTestCase):
	def test_the_request_rings_the_agent_then_the_lead(self):
		frappe.set_user(HUB)
		with patch(
			"tbo_smart.telephony.myoperator.requests.post", return_value=myoperator_response()
		) as post:
			result = api.click_to_call(self.lead, acting_user=BDE)
		self.assertEqual(result["message"], "MyOperator is calling your phone…")
		self.assertEqual(result["number"], NUMBER)
		self.assertEqual(post.call_args.args[0], "https://obd-api.myoperator.co/obd-api-v1")
		self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], TEST_MYOPERATOR_KEY)
		body = post.call_args.kwargs["json"]
		self.assertEqual(
			{
				key: body[key]
				for key in ("company_id", "secret_token", "type", "number", "public_ivr_id", "user_id")
			},
			{
				"company_id": "C-TEST",
				"secret_token": TEST_MYOPERATOR_SECRET,
				"type": "1",
				"number": NUMBER,
				"public_ivr_id": "IVR-TEST",
				"user_id": "mo-uuid-bde",
			},
		)
		self.assertEqual(body["reference_id"], result["reference_id"])
		self.assertNotIn("number_2", body)

	def test_the_call_it_starts_lands_on_that_lead_by_its_reference(self):
		frappe.set_user(BDE)
		with patch("tbo_smart.telephony.myoperator.requests.post", return_value=myoperator_response()):
			reference = api.click_to_call(self.lead)["reference_id"]
		frappe.set_user("Administrator")
		# MyOperator reports a number no lead has: the reference still finds the lead and agent
		post_myoperator_webhook(
			myoperator_event("C-C2C", number=UNKNOWN_NUMBER, direction="outgoing", client_ref_id=reference)
		)
		call = self.call_of("C-C2C")
		self.assertEqual((call.lead, call.agent), (self.lead, BDE))

	def test_without_a_number_the_leads_first_number_is_called(self):
		only_whatsapp = make_lead(BDE, "WhatsApp Only", whatsapp_no="+91 98123 45678")
		frappe.set_user(BDE)
		with patch(
			"tbo_smart.telephony.myoperator.requests.post", return_value=myoperator_response()
		) as post:
			api.click_to_call(only_whatsapp)
		self.assertEqual(post.call_args.kwargs["json"]["number"], OTHER_NUMBER)

	def test_a_mobile_only_agent_gets_the_anonymous_dialer(self):
		other = make_lead(BDE2, "Second", mobile_no=OTHER_NUMBER)
		frappe.set_user(BDE2)
		with patch(
			"tbo_smart.telephony.myoperator.requests.post", return_value=myoperator_response()
		) as post:
			api.click_to_call(other)
		body = post.call_args.kwargs["json"]
		self.assertEqual((body["number_2"], body.get("user_id")), ("+919000000002", None))

	def test_the_app_sees_who_may_call_and_calls_the_same_way(self):
		make_user("tel.nobody@tbosmart.test", ["Sales User"])
		frappe.set_user(HUB)
		self.assertTrue(hub.get_options(acting_user=BDE)["can_call"])
		self.assertTrue(hub.get_lead(self.lead, acting_user=BDE)["lead"]["can_call"])
		self.assertTrue(capabilities_as(BDE).click_to_call)
		self.assertFalse(capabilities_as("tel.nobody@tbosmart.test").click_to_call)
		frappe.set_user(BDE)
		with patch(
			"tbo_smart.telephony.myoperator.requests.post", return_value=myoperator_response()
		) as post:
			result = mobile_leads.click_to_call(self.lead)
		self.assertEqual((result["ok"], post.call_args.kwargs["json"]["user_id"]), (True, "mo-uuid-bde"))
		disable_telephony()
		self.assertFalse(capabilities_as(BDE).click_to_call)

	def test_a_mapped_person_without_a_sales_role_cant_call(self):
		staff = make_user("tel.staff@tbosmart.test", ["Employee"])
		enable_telephony([*AGENTS, {"user": staff, "myoperator_user_id": "mo-staff"}])
		self.assertFalse(calls.can_call(staff))
		self.assertTrue(calls.can_call(BDE))

	def test_refusals_are_friendly(self):
		frappe.set_user(BDE)
		self.assertRaisesRegex(
			frappe.ValidationError, "own numbers", api.click_to_call, self.lead, "+919999999999"
		)
		frappe.set_user(BDE2)
		self.assertRaises(frappe.PermissionError, api.click_to_call, self.lead)
		frappe.set_user(BDE)
		refused = myoperator_response(
			403, {"status": "error", "code": "403", "details": "Unauthorised source IP"}
		)
		with patch("tbo_smart.telephony.myoperator.requests.post", return_value=refused):
			self.assertRaisesRegex(
				myoperator.MyOperatorError, "IP isn't allowed", api.click_to_call, self.lead
			)
		frappe.set_user("Administrator")
		clear_dial_locks((BDE,))
		enable_telephony([AGENTS[1]])
		frappe.set_user(BDE)
		self.assertRaisesRegex(frappe.ValidationError, "set up for calling", api.click_to_call, self.lead)

	def test_test_connection_lists_users_without_placing_a_call(self):
		users = {
			"status": "success",
			"data": [{"uuid": "u1", "name": "Anu", "extension": "10", "contact_number": "+91900"}],
		}
		frappe.set_user(ADMIN)
		with (
			patch(
				"tbo_smart.telephony.myoperator.requests.get", return_value=myoperator_response(200, users)
			),
			patch("tbo_smart.telephony.myoperator.requests.post") as post,
		):
			result = api.test_connection()
		post.assert_not_called()
		self.assertTrue(result["ok"])
		self.assertEqual(result["users"][0]["uuid"], "u1")

	def test_settings_never_return_a_secret_but_show_the_webhook(self):
		frappe.set_user(ADMIN)
		settings = api.get_settings()
		text = json.dumps(settings, default=str)
		for value in (TEST_MYOPERATOR_KEY, TEST_MYOPERATOR_SECRET, "tbo-test-webhook-secret"):
			self.assertNotIn(value, text)
		self.assertTrue(settings["secrets"]["telephony_webhook_secret"]["set"])
		self.assertTrue(
			settings["webhook"]["url"].endswith("/api/method/tbo_smart.api.telephony.myoperator_webhook")
		)
		new = api.regenerate_webhook_secret()["secret"]
		frappe.set_user("Administrator")
		self.assertRaises(frappe.PermissionError, post_myoperator_webhook, myoperator_event("C-OLD"))
		self.assertEqual(post_myoperator_webhook(myoperator_event("C-NEWSECRET"), new), {"ok": True})
		frappe.set_user(BDE)
		self.assertRaises(frappe.PermissionError, api.get_settings)


class TestCallSummary(TelephonyTestCase):
	def test_the_call_is_transcribed_summed_up_and_feeds_the_autopilot(self):
		enable_ai_leads()
		enable_telephony(AGENTS, enable_call_summary=1)
		answer = {
			"summary": "They want billing software for 2 shops and asked about the price.",
			"language": "Mixed",
			"interest": "Billing software",
			"sentiment": "Positive",
			"intent": "High",
			"objections": ["Price for 7 users"],
			"next_step": {"what": "Send the quotation", "channel": "email", "due": str(add_days(today(), 2))},
		}
		with (
			patch(
				"tbo_smart.telephony.myoperator.requests.get",
				side_effect=[
					myoperator_response(200, {"data": {"url": "https://rec.myoperator.test/a.mp3"}}),
					myoperator_response(200, {}, content=AUDIO),
				],
			),
			mock_ai(
				transcription_answer("Hello, we have 2 shops ... billing software"), openai_answer(answer)
			) as ai_post,
		):
			post_myoperator_webhook(
				myoperator_event("C-AI", agent=AGENT, duration=120, recording_filename="ai.mp3")
			)
		speech, chat = ai_post.call_args_list
		self.assertEqual(speech.args[0], "https://api.openai.com/v1/audio/transcriptions")
		self.assertEqual(speech.kwargs["data"]["model"], "gpt-4o-transcribe")
		self.assertNotIn("language", speech.kwargs["data"])
		self.assertEqual(speech.kwargs["headers"]["Authorization"], f"Bearer {TEST_OPENAI_KEY}")
		self.assertEqual(chat.args[0], "https://api.openai.com/v1/chat/completions")
		call = self.call_of("C-AI")
		summary = json.loads(call.summary)
		self.assertEqual((call.ai_status, summary["intent"], summary["by"]), ("Done", "High", "TBO AI"))
		# "7 users" was never said: the guard drops that objection
		self.assertEqual(summary["objections"], [])
		self.assertIn("billing software", frappe.db.get_value("Event", call.event, "description"))
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_ai_next_action"), "Send the quotation")
		task = frappe.get_all(
			"ToDo", filters={"reference_name": self.lead, "status": "Open"}, fields=["date", "description"]
		)
		self.assertEqual(getdate(task[0].date), getdate(add_days(today(), 2)))
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_name": self.lead,
					"comment_by": "TBO AI",
					"content": ("like", "Summed up the call%"),
				},
			)
		)
		logs = frappe.get_all(
			"TBO AI Usage Log",
			filters={"reference_name": self.lead},
			fields=["feature", "estimated_cost_usd"],
		)
		cost = {log.feature: log.estimated_cost_usd for log in logs}
		self.assertAlmostEqual(cost["Call transcription"], 2 * 0.006)
		self.assertIn("Call summary", cost)
		self.assertNotIn("Lead insight", cost)

	def test_a_wrong_speech_model_doesnt_pause_the_chat_features(self):
		enable_ai_leads()
		missing = ai_http_response(
			404, {"error": {"message": "The model does not exist", "code": "model_not_found"}}
		)
		with mock_ai(missing):
			self.assertRaises(
				ai.AIProviderError,
				ai.transcribe,
				AUDIO,
				"a.mp3",
				"no-such-model",
				1,
				0.006,
				"Call transcription",
			)
		self.assertFalse(ai.provider_status()["providers"][ai.OPENAI]["paused"])

	def test_short_calls_and_switched_off_summaries_make_no_ai_call(self):
		enable_ai_leads()
		enable_telephony(AGENTS, enable_call_summary=1, call_summary_min_seconds=60)
		with (
			patch(
				"tbo_smart.telephony.myoperator.requests.get",
				side_effect=[
					myoperator_response(200, {"data": {"url": "https://rec.myoperator.test/a.mp3"}}),
					myoperator_response(200, {}, content=AUDIO),
				],
			),
			mock_ai() as ai_post,
		):
			post_myoperator_webhook(
				myoperator_event("C-SHORT", agent=AGENT, duration=40, recording_filename="s.mp3")
			)
		self.assertFalse([c for c in ai_post.call_args_list if "audio" in c.args[0]])
		self.assertFalse(self.call_of("C-SHORT").ai_status)


class TestSetup(TelephonyTestCase):
	def test_migrate_gives_an_existing_site_the_defaults_once(self):
		frappe.db.delete("Singles", {"doctype": "TBO Smart Settings", "field": ("in", setup.DEFAULT_FIELDS)})
		setup.setup_telephony()
		self.assertEqual(frappe.db.get_single_value("TBO Smart Settings", "telephony_create_leads"), 1)
		self.assertEqual(frappe.db.get_single_value("TBO Smart Settings", "telephony_recording_days"), 180)
		self.assertTrue(frappe.db.exists("Lead Source", "Phone call"))
		# a value someone saved is never overwritten
		frappe.db.set_single_value("TBO Smart Settings", "telephony_create_leads", 0)
		setup.setup_telephony()
		self.assertEqual(frappe.db.get_single_value("TBO Smart Settings", "telephony_create_leads"), 0)


class TestSalesActivity(TelephonyTestCase):
	def test_answered_calls_and_talk_time_count_for_the_agent(self):
		post_myoperator_webhook(myoperator_event("C-S1", agent=AGENT, duration=100))
		post_myoperator_webhook(myoperator_event("C-S2", agent=AGENT, duration=50))
		post_myoperator_webhook(myoperator_event("C-S3", agent=AGENT, status="missed", duration=10))
		rows = SalesActivity("2026-10-19", "2026-10-19", [BDE, BDE2]).build_activity()["people"]
		bde = next(row for row in rows if row["user"] == BDE)
		self.assertEqual((bde["calls"], bde["talk_seconds"]), (2, 150))
