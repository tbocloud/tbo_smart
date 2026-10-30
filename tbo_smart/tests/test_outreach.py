import json
from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, getdate, now_datetime, today

from tbo_smart.api import hub_leads as leads_api
from tbo_smart.api import hub_outreach as hub
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_LOST,
	CONTACTED,
	FOLLOW_UP_REQUIRED,
	MEETING_SCHEDULED,
	NOT_INTERESTED,
	POSITIVE,
)
from tbo_smart.outreach import OPT_OUT, drafting, guards, replies, sending
from tbo_smart.prospecting.signals import first_message_context
from tbo_smart.smart_leads import autopilot
from tbo_smart.test_utils import (
	delete_lead_departments,
	delete_leads_matching,
	delete_outreach_messages,
	delete_prospect_runs,
	delete_sales_records,
	disable_lead_pipeline,
	disable_outreach,
	disable_sales_hierarchy,
	enable_lead_pipeline,
	enable_outreach,
	make_incoming_reply,
	make_lead,
	make_lead_department,
	make_outgoing_email_account,
	make_outreach_message,
	make_prospect_lead,
	make_prospect_run,
	make_user,
)

HUB = "outreach.integration@tbosmart.test"
MANAGER = "outreach.manager@tbosmart.test"
HEAD = "outreach.head@tbosmart.test"
SELLER = "outreach.seller@tbosmart.test"
OTHER = "outreach.other@tbosmart.test"
PEOPLE = (MANAGER, HEAD, SELLER, OTHER)
DEPARTMENT = "TBO Test Outreach Dept"
DRAFT_ANSWER = {
	"subject": "Online bookings for Palm Resort",
	"text": "Hi, I saw you take bookings by phone only. We build booking tools. Could we talk? Sam",
}
WHATSAPP_ANSWER = {"message": "Hi, I saw you take bookings by phone only. Could we talk? Sam"}


def ai_answer(response: dict):
	return patch("tbo_smart.ai.call_ai", return_value={"response": response})


def ai_on():
	return patch("tbo_smart.ai.ai_ready", return_value=True)


class OutreachCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(MANAGER, ["Sales Manager"])
		for user in (HEAD, SELLER, OTHER):
			make_user(user, ["Sales User"])

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_sales_hierarchy(PEOPLE)
		make_lead_department(DEPARTMENT, heads=[HEAD], members=[SELLER])
		enable_outreach()
		make_outgoing_email_account()
		# the suite may run on a Sunday: the window is open unless a test closes it
		window = patch("tbo_smart.outreach.sending.in_window", return_value=True)
		window.start()
		self.addCleanup(window.stop)
		self.lead = make_prospect_lead(SELLER, tbo_department=DEPARTMENT)

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_outreach()
		disable_lead_pipeline()
		self.clean()

	def clean(self):
		delete_outreach_messages()
		delete_prospect_runs()
		delete_leads_matching(email_id=("like", "%@prospect.test"))
		delete_sales_records(PEOPLE)
		delete_lead_departments([DEPARTMENT])

	def value(self, field: str, lead: str | None = None):
		return frappe.db.get_value("Lead", lead or self.lead, field)

	def status_of(self, name: str) -> str:
		return frappe.db.get_value("TBO Outreach Message", name, "status")

	def as_hub(self, method, user: str = SELLER, **kwargs):
		frappe.set_user(HUB)
		try:
			return method(acting_user=user, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def drain(self, enqueue):
		"""Run the job a request queued, as the background worker would."""
		keys = ("user", "leads", "channel", "batch", "run")
		drafting.run_batch(**{key: enqueue.call_args.kwargs[key] for key in keys})
		frappe.set_user("Administrator")


class TestFirstMessage(OutreachCase):
	def draft(self, lead: str, channel: str = "email", answer: dict | None = None):
		frappe.set_user(SELLER)
		try:
			with ai_answer(answer or DRAFT_ANSWER) as call:
				return autopilot.draft_message(lead, channel), call
		finally:
			frappe.set_user("Administrator")

	def test_a_prospect_lead_is_drafted_from_its_signals_and_why_call(self):
		draft, call = self.draft(self.lead)
		prompt, payload = call.call_args.args[0], json.loads(call.call_args.args[1])
		self.assertIn("first email", prompt)
		self.assertEqual(payload["prospect"]["why_call"], "Takes bookings by phone only.")
		self.assertIs(payload["prospect"]["signals"]["online_booking"], False)
		self.assertNotIn("email", payload["prospect"]["signals"], "their own address is not a finding")
		self.assertTrue(draft["first_message"])
		self.assertEqual(draft["to"], "palm.resort@prospect.test")
		self.assertIn("bookings by phone only", draft["text"])

	def test_whatsapp_uses_the_same_signals(self):
		draft, call = self.draft(self.lead, "whatsapp", WHATSAPP_ANSWER)
		self.assertIn("first WhatsApp", call.call_args.args[0])
		self.assertTrue(draft["first_message"])
		self.assertTrue(draft["url"].startswith("https://wa.me/919847012345?text="))
		self.assertEqual(draft["to"], "+919847012345")

	def test_an_ordinary_lead_keeps_the_ordinary_draft(self):
		lead = make_lead(SELLER, "Plain Lead", email_id="plain@prospect.test")
		draft, call = self.draft(lead)
		self.assertNotIn("prospect", json.loads(call.call_args.args[1]))
		self.assertFalse(draft["first_message"])

	def test_a_lead_we_already_wrote_to_is_no_longer_a_first_message(self):
		self.assertIsNotNone(first_message_context(self.lead))
		make_outreach_message(self.lead, status="Sent")
		self.assertIsNone(first_message_context(self.lead))

	def test_a_mail_already_sent_ends_the_first_message(self):
		frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"sender": "outbox@tbosmart.test",
				"recipients": "palm.resort@prospect.test",
				"subject": "Earlier",
				"content": "Hello",
				"reference_doctype": "Lead",
				"reference_name": self.lead,
			}
		).insert(ignore_permissions=True)
		self.assertIsNone(first_message_context(self.lead))

	def test_a_lead_with_no_reading_still_gets_a_first_message(self):
		frappe.db.set_value("Lead", self.lead, "tbo_web_profile", None)
		self.assertEqual(first_message_context(self.lead), {"why_call": None, "signals": {}})

	def test_numbers_that_are_not_in_the_signals_or_the_lead_are_refused(self):
		frappe.set_user(SELLER)
		try:
			bad = {**DRAFT_ANSWER, "text": "We saved resorts 40% on bookings. Sam"}
			with ai_answer(bad), self.assertRaises(frappe.ValidationError):
				autopilot.draft_message(self.lead, "email")
			# the year the site was last updated is a fact from the signals
			good = {**DRAFT_ANSWER, "text": "Your site was last updated in 2019. Could we talk? Sam"}
			with ai_answer(good):
				self.assertIn("2019", autopilot.draft_message(self.lead, "email")["text"])
		finally:
			frappe.set_user("Administrator")


class TestDraftOutreach(OutreachCase):
	def request(self, **kwargs):
		kwargs.setdefault("channel", "Email")
		with ai_on(), patch("frappe.enqueue") as enqueue:
			result = self.as_hub(hub.draft_outreach, **kwargs)
		return result, enqueue

	def test_drafts_are_made_in_the_background_for_the_leads(self):
		result, enqueue = self.request(leads=[self.lead])
		self.assertEqual((result["queued"], result["skipped"]), (1, []))
		self.assertEqual(frappe.db.count("TBO Outreach Message"), 0, "nothing is drafted in the request")
		with ai_answer(DRAFT_ANSWER):
			self.drain(enqueue)
		(message,) = frappe.get_all(
			"TBO Outreach Message",
			fields=[
				"lead",
				"channel",
				"status",
				"drafted_by",
				"subject",
				"body",
				"recipient",
				"batch",
				"owner",
			],
		)
		self.assertEqual(
			(message.lead, message.channel, message.status, message.drafted_by),
			(self.lead, "Email", "Draft", "AI"),
		)
		self.assertEqual(
			(message.recipient, message.owner, message.batch),
			("palm.resort@prospect.test", SELLER, result["batch"]),
		)
		self.assertEqual(message.subject, DRAFT_ANSWER["subject"])

	def test_a_run_drafts_its_leads(self):
		run = make_prospect_run()
		frappe.db.set_value("Lead", self.lead, "tbo_prospect_run", run)
		result, enqueue = self.request(run=run)
		self.assertEqual(result["queued"], 1)
		with ai_answer(DRAFT_ANSWER):
			self.drain(enqueue)
		self.assertEqual(
			frappe.db.get_value("TBO Outreach Message", {"lead": self.lead}, "prospect_run"), run
		)

	def test_whatsapp_drafts_carry_the_number(self):
		_result, enqueue = self.request(leads=[self.lead], channel="whatsapp")
		with ai_answer(WHATSAPP_ANSWER):
			self.drain(enqueue)
		row = frappe.db.get_value(
			"TBO Outreach Message", {"lead": self.lead}, ["channel", "recipient"], as_dict=True
		)
		self.assertEqual((row.channel, row.recipient), ("WhatsApp", "+919847012345"))

	def test_leads_that_must_not_be_written_to_are_skipped_with_a_reason(self):
		unsubscribed = make_prospect_lead(SELLER, "Unsub Resort", unsubscribed=1)
		lost = make_prospect_lead(SELLER, "Lost Resort", tbo_stage=NOT_INTERESTED)
		no_email = make_prospect_lead(SELLER, "Silent Resort")
		frappe.db.set_value("Lead", no_email, "email_id", None)
		foreign = make_prospect_lead(OTHER, "Foreign Resort")
		drafted = make_prospect_lead(SELLER, "Drafted Resort")
		make_outreach_message(drafted)
		result, _enqueue = self.request(
			leads=[self.lead, unsubscribed, lost, no_email, foreign, drafted, "NOPE"]
		)
		self.assertEqual(result["queued"], 1)
		reasons = {row["lead"]: row["reason"] for row in result["skipped"]}
		self.assertIn("unsubscribed", reasons[unsubscribed])
		self.assertIn("Not Interested", reasons[lost])
		self.assertIn("no email", reasons[no_email])
		self.assertIn("cannot edit", reasons[foreign])
		self.assertIn("already in the outbox", reasons[drafted])
		self.assertIn("does not exist", reasons["NOPE"])

	def test_a_cancelled_message_can_be_drafted_again_but_a_live_one_cannot(self):
		name = make_outreach_message(self.lead)
		result, _enqueue = self.request(leads=[self.lead])
		self.assertEqual(result["queued"], 0)
		frappe.db.set_value("TBO Outreach Message", name, "status", "Cancelled")
		result, _enqueue = self.request(leads=[self.lead])
		self.assertEqual(result["queued"], 1)

	def test_a_request_is_capped(self):
		enable_outreach(outreach_max_batch=2)
		others = [make_prospect_lead(SELLER, f"Resort {n}") for n in range(3)]
		result, enqueue = self.request(leads=others)
		self.assertEqual(result["queued"], 2)
		self.assertIn("Over the limit of 2", result["skipped"][0]["reason"])
		self.assertEqual(len(enqueue.call_args.kwargs["leads"]), 2)

	def test_the_ai_must_be_set_up_and_outreach_on(self):
		with patch("tbo_smart.ai.ai_ready", return_value=False), self.assertRaises(frappe.ValidationError):
			self.as_hub(hub.draft_outreach, leads=[self.lead], channel="Email")
		disable_outreach()
		with ai_on(), self.assertRaises(frappe.ValidationError):
			self.as_hub(hub.draft_outreach, leads=[self.lead], channel="Email")

	def test_a_failing_draft_does_not_stop_the_batch(self):
		second = make_prospect_lead(SELLER, "Second Resort")
		_result, enqueue = self.request(leads=[self.lead, second])
		answers = [
			{"response": {"subject": "S", "text": "We saved 99 resorts. Sam"}},
			{"response": DRAFT_ANSWER},
		]
		with patch("tbo_smart.ai.call_ai", side_effect=answers):
			self.drain(enqueue)
		self.assertEqual(frappe.get_all("TBO Outreach Message", pluck="lead"), [second])

	def test_the_progress_shows_in_the_outbox_while_drafting(self):
		result, _enqueue = self.request(leads=[self.lead])
		outbox = self.as_hub(hub.get_outbox)
		self.assertEqual(
			(outbox["drafting"]["batch"], outbox["drafting"]["total"], outbox["drafting"]["done"]),
			(result["batch"], 1, 0),
		)


class TestOutbox(OutreachCase):
	def test_who_sees_which_drafts(self):
		mine = make_outreach_message(self.lead, owner=SELLER)
		other_lead = make_prospect_lead(OTHER, "Other Resort", tbo_department=None)
		theirs = make_outreach_message(other_lead, owner=OTHER)
		names = lambda user: {row["name"] for row in self.as_hub(hub.get_outbox, user)["rows"]}  # noqa: E731
		self.assertEqual(names(SELLER), {mine})
		self.assertEqual(names(OTHER), {theirs})
		self.assertEqual(names(HEAD), {mine}, "a head sees their department's drafts")
		self.assertEqual(names(MANAGER), {mine, theirs})

	def test_counts_status_filter_and_the_cap_left(self):
		make_outreach_message(self.lead, owner=SELLER)
		sent_lead = make_prospect_lead(SELLER, "Sent Resort")
		make_outreach_message(
			sent_lead, owner=SELLER, status="Sent", approved_by=SELLER, sent_on=now_datetime()
		)
		outbox = self.as_hub(hub.get_outbox)
		self.assertEqual((outbox["counts"]["Draft"], outbox["counts"]["Sent"]), (1, 1))
		self.assertEqual(outbox["cap"], {"limit": 30, "used": 1, "left": 29, "queued": 0})
		self.assertTrue(outbox["email_ready"] and outbox["enabled"])
		self.assertEqual(outbox["opt_out"], OPT_OUT)
		drafts = self.as_hub(hub.get_outbox, status='["Draft"]')
		self.assertEqual([row["status"] for row in drafts["rows"]], ["Draft"])
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(hub.get_outbox, status="Bogus")

	def test_a_whatsapp_row_carries_its_link(self):
		make_outreach_message(self.lead, "WhatsApp", owner=SELLER, recipient="+919847012345", body="Hi there")
		(row,) = self.as_hub(hub.get_outbox)["rows"]
		self.assertEqual(row["url"], "https://wa.me/919847012345?text=Hi%20there")

	def test_someone_without_a_sales_role_is_refused(self):
		make_user("outreach.nobody@tbosmart.test", [])
		with self.assertRaises(frappe.PermissionError):
			self.as_hub(hub.get_outbox, "outreach.nobody@tbosmart.test")


class TestEditAndCancel(OutreachCase):
	def test_a_draft_can_be_edited(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		row = self.as_hub(hub.update_outreach, name=name, subject="New subject", body="New body")
		self.assertEqual(row["status"], "Draft")
		self.assertEqual(
			frappe.db.get_value("TBO Outreach Message", name, ["subject", "body"]),
			("New subject", "New body"),
		)

	def test_an_edit_needs_text_a_subject_and_an_open_message(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		for values in ({"body": " "}, {"body": "x", "subject": ""}):
			with self.assertRaises(frappe.ValidationError):
				self.as_hub(hub.update_outreach, name=name, **values)
		frappe.db.set_value("TBO Outreach Message", name, "status", "Sent")
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(hub.update_outreach, name=name, body="x", subject="y")

	def test_someone_who_cannot_edit_the_lead_cannot_edit_its_message(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		with self.assertRaises(frappe.PermissionError):
			self.as_hub(hub.update_outreach, OTHER, name=name, body="x", subject="y")

	def test_a_failed_message_becomes_a_draft_again_when_edited(self):
		name = make_outreach_message(self.lead, owner=SELLER, status="Failed", error="Boom")
		self.as_hub(hub.update_outreach, name=name, subject="Again", body="Again")
		self.assertEqual(
			frappe.db.get_value("TBO Outreach Message", name, ["status", "error"]), ("Draft", None)
		)

	def test_regenerate_replaces_the_text(self):
		name = make_outreach_message(self.lead, owner=SELLER, body="Old")
		with ai_answer(DRAFT_ANSWER):
			self.as_hub(hub.regenerate_outreach, name=name)
		self.assertIn("bookings by phone only", frappe.db.get_value("TBO Outreach Message", name, "body"))

	def test_cancel_withdraws_open_messages_but_not_sent_ones(self):
		draft = make_outreach_message(self.lead, owner=SELLER)
		sent_lead = make_prospect_lead(SELLER, "Sent Resort")
		sent = make_outreach_message(sent_lead, owner=SELLER, status="Sent")
		results = self.as_hub(hub.cancel_outreach, names=[draft, sent, "OUT-NOPE"])
		self.assertEqual([row["status"] for row in results], ["Cancelled", "Sent", None])
		self.assertEqual(self.status_of(draft), "Cancelled")
		self.assertEqual(self.status_of(sent), "Sent")


class TestApproveAndSend(OutreachCase):
	def approve(self, *names, user: str = SELLER):
		with patch("frappe.sendmail") as sendmail:
			results = self.as_hub(hub.approve_outreach, user, names=list(names))
		return results, sendmail

	def test_nothing_is_sent_until_a_person_approves(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		with patch("frappe.sendmail") as sendmail:
			sending.send_approved()
		sendmail.assert_not_called()
		self.assertEqual(self.status_of(name), "Draft")

	def test_approving_an_email_sends_it_and_logs_it_on_the_lead(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		results, sendmail = self.approve(name)
		self.assertEqual(results[0]["status"], "Sent")
		sendmail.assert_called_once()
		sent = sendmail.call_args.kwargs
		self.assertEqual(sent["recipients"], ["palm.resort@prospect.test"])
		self.assertEqual((sent["reference_doctype"], sent["reference_name"]), ("Lead", self.lead))
		self.assertIn(OPT_OUT, sent["message"], "every email carries the opt-out line")
		row = frappe.db.get_value(
			"TBO Outreach Message",
			name,
			["status", "approved_by", "approved_on", "sent_on", "communication"],
			as_dict=True,
		)
		self.assertEqual((row.status, row.approved_by), ("Sent", SELLER))
		self.assertTrue(row.approved_on and row.sent_on)
		communication = frappe.get_doc("Communication", row.communication)
		self.assertEqual(
			(communication.reference_doctype, communication.reference_name, communication.sent_or_received),
			("Lead", self.lead, "Sent"),
		)
		self.assertEqual(sent["communication"], communication.name)

	def test_without_an_outgoing_account_approval_fails_clearly(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		with patch("tbo_smart.outreach.sending.outgoing_account", return_value=None):
			with self.assertRaises(frappe.ValidationError) as caught:
				self.as_hub(hub.approve_outreach, names=[name])
		self.assertIn("Set up an outgoing email account first", str(caught.exception))
		self.assertEqual(self.status_of(name), "Draft")

	def test_outside_the_send_window_it_waits_and_the_sender_delivers_it_later(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		with patch("tbo_smart.outreach.sending.in_window", return_value=False):
			results, sendmail = self.approve(name)
			with patch("frappe.sendmail") as later:
				sending.send_approved()
		self.assertEqual(results[0]["status"], "Approved")
		self.assertIn("will go out from", results[0]["message"])
		sendmail.assert_not_called()
		later.assert_not_called()
		self.assertTrue(self.as_hub(hub.get_outbox)["rows"][0]["queued"])
		with patch("frappe.sendmail") as sendmail:
			sending.send_approved()
		sendmail.assert_called_once()
		self.assertEqual(self.status_of(name), "Sent")

	def test_the_daily_cap_holds_the_rest_until_tomorrow(self):
		enable_outreach(outreach_daily_cap=1)
		second_lead = make_prospect_lead(SELLER, "Second Resort")
		first = make_outreach_message(self.lead, owner=SELLER)
		second = make_outreach_message(second_lead, owner=SELLER)
		results, sendmail = self.approve(first, second)
		self.assertEqual([row["status"] for row in results], ["Sent", "Approved"])
		self.assertEqual(sendmail.call_count, 1)
		self.assertIn("limit", results[1]["message"])
		with patch("frappe.sendmail") as again:
			sending.send_approved()
		again.assert_not_called()
		self.assertEqual(guards.cap_left(SELLER), 0)
		enable_outreach(outreach_daily_cap=2)
		with patch("frappe.sendmail") as again:
			sending.send_approved()
		again.assert_called_once()
		self.assertEqual(self.status_of(second), "Sent")

	def test_a_cap_of_zero_stops_email(self):
		enable_outreach(outreach_daily_cap=0)
		name = make_outreach_message(self.lead, owner=SELLER)
		results, sendmail = self.approve(name)
		self.assertEqual(results[0]["status"], "Approved")
		sendmail.assert_not_called()

	def test_an_unsubscribed_or_lost_lead_is_never_written_to(self):
		for fields in ({"unsubscribed": 1}, {"tbo_stage": CLOSED_LOST, "tbo_lost_reason": "Other"}):
			lead = make_prospect_lead(SELLER, "Blocked Resort")
			frappe.db.set_value("Lead", lead, fields)
			name = make_outreach_message(lead, owner=SELLER)
			results, sendmail = self.approve(name)
			self.assertEqual(results[0]["status"], "Cancelled", fields)
			sendmail.assert_not_called()
			self.assertTrue(frappe.db.get_value("TBO Outreach Message", name, "error"))
			frappe.db.delete("TBO Outreach Message", {"lead": lead})
			delete_leads_matching(name=lead)

	def test_a_lead_that_unsubscribes_before_delivery_is_not_sent_to(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		with patch("tbo_smart.outreach.sending.in_window", return_value=False):
			self.approve(name)
		frappe.db.set_value("Lead", self.lead, "unsubscribed", 1)
		with patch("frappe.sendmail") as sendmail:
			sending.send_approved()
		sendmail.assert_not_called()
		self.assertEqual(self.status_of(name), "Cancelled")

	def test_a_sending_error_is_kept_on_the_message(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		with patch("frappe.sendmail", side_effect=frappe.OutgoingEmailError("SMTP refused")):
			results = self.as_hub(hub.approve_outreach, names=[name])
		self.assertEqual(results[0]["status"], "Failed")
		row = frappe.db.get_value("TBO Outreach Message", name, ["status", "error"], as_dict=True)
		self.assertEqual(row.status, "Failed")
		self.assertIn("SMTP refused", row.error)
		self.assertEqual(self.as_hub(hub.get_outbox)["rows"][0]["error"], row.error)

	def test_only_a_draft_can_be_approved_and_only_by_someone_who_can_edit_the_lead(self):
		sent = make_outreach_message(self.lead, owner=SELLER, status="Sent")
		results, _sendmail = self.approve(sent)
		self.assertEqual(results[0]["status"], "Sent")
		self.assertIn("cannot be approved", results[0]["message"])
		draft_lead = make_prospect_lead(SELLER, "Mine Resort")
		draft = make_outreach_message(draft_lead, owner=SELLER)
		results, sendmail = self.approve(draft, user=OTHER)
		self.assertIsNone(results[0]["status"])
		sendmail.assert_not_called()
		self.assertEqual(self.status_of(draft), "Draft")

	def test_an_empty_message_is_not_approved(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		frappe.db.set_value("TBO Outreach Message", name, "subject", "")
		results, sendmail = self.approve(name)
		self.assertEqual(results[0]["status"], "Draft")
		sendmail.assert_not_called()

	def test_approving_needs_outreach_switched_on(self):
		name = make_outreach_message(self.lead, owner=SELLER)
		disable_outreach()
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(hub.approve_outreach, names=[name])

	def test_the_html_escapes_the_text_and_ends_with_the_opt_out(self):
		html = sending.message_html("Hi <b>there</b>\nsecond line\n\nNew paragraph")
		self.assertIn("&lt;b&gt;there", html)
		self.assertIn("second line", html)
		self.assertEqual(html.count("<p"), 3)
		self.assertTrue(html.rstrip().endswith("</p>") and OPT_OUT in html)


class TestWhatsAppByHand(OutreachCase):
	def test_approving_marks_it_ready_and_the_bd_confirms_it_sent(self):
		name = make_outreach_message(self.lead, "WhatsApp", owner=SELLER, recipient="+919847012345")
		with patch("frappe.sendmail") as sendmail:
			results = self.as_hub(hub.approve_outreach, names=[name])
		sendmail.assert_not_called()
		self.assertEqual(results[0]["status"], "Approved")
		self.assertIn("open WhatsApp", results[0]["message"])
		before = frappe.db.count("Event", {"owner": SELLER})
		sent = self.as_hub(hub.mark_outreach_sent, names=[name])
		self.assertEqual(sent[0]["status"], "Sent")
		self.assertTrue(frappe.db.get_value("TBO Outreach Message", name, "sent_on"))
		self.assertEqual(frappe.db.count("Event", {"owner": SELLER}), before + 1, "logged on the lead")

	def test_a_whatsapp_message_does_not_need_an_email_account_or_use_the_cap(self):
		name = make_outreach_message(self.lead, "WhatsApp", owner=SELLER, recipient="+919847012345")
		with patch("tbo_smart.outreach.sending.outgoing_account", return_value=None):
			results = self.as_hub(hub.approve_outreach, names=[name])
		self.assertEqual(results[0]["status"], "Approved")
		self.as_hub(hub.mark_outreach_sent, names=[name])
		self.assertEqual(guards.sent_today(SELLER), 0)

	def test_only_an_approved_whatsapp_message_can_be_marked_sent(self):
		draft = make_outreach_message(self.lead, "WhatsApp", owner=SELLER)
		self.assertIn("Only an approved", self.as_hub(hub.mark_outreach_sent, names=[draft])[0]["message"])
		self.assertEqual(self.status_of(draft), "Draft")

	def test_a_later_whatsapp_sender_can_be_plugged_in(self):
		name = make_outreach_message(self.lead, "WhatsApp", owner=SELLER, recipient="+919847012345")
		delivered = []

		def fake_cloud_api(doc):
			delivered.append(doc.name)
			doc.status = "Sent"
			doc.save(ignore_permissions=True)

		with patch.dict(sending.DELIVERERS, {"WhatsApp": fake_cloud_api}):
			results = self.as_hub(hub.approve_outreach, names=[name])
		self.assertEqual((delivered, results[0]["status"]), ([name], "Sent"))


class TestSendWindow(FrappeTestCase):
	def setUp(self):
		enable_outreach(outreach_window_start="09:30:00", outreach_window_end="18:00:00")

	def tearDown(self):
		disable_outreach()

	def test_working_hours_are_monday_to_saturday_inside_the_window(self):
		monday = datetime(2026, 11, 2)
		self.assertTrue(guards.in_window(monday.replace(hour=10)))
		self.assertTrue(guards.in_window(monday.replace(hour=9, minute=30)))
		self.assertFalse(guards.in_window(monday.replace(hour=9, minute=29)))
		self.assertFalse(guards.in_window(monday.replace(hour=18)))
		self.assertTrue(guards.in_window(datetime(2026, 11, 7, 10)), "Saturday")
		self.assertFalse(guards.in_window(datetime(2026, 11, 8, 10)), "Sunday")

	def test_the_next_window(self):
		self.assertEqual(guards.next_window(datetime(2026, 11, 2, 8)), datetime(2026, 11, 2, 9, 30))
		self.assertEqual(guards.next_window(datetime(2026, 11, 2, 19)), datetime(2026, 11, 3, 9, 30))
		self.assertEqual(guards.next_window(datetime(2026, 11, 7, 19)), datetime(2026, 11, 9, 9, 30))
		self.assertEqual(guards.next_window(datetime(2026, 11, 8, 12)), datetime(2026, 11, 9, 9, 30))
		self.assertEqual(guards.next_window(datetime(2026, 11, 2, 10)), datetime(2026, 11, 2, 10))


class TestReplies(OutreachCase):
	def setUp(self):
		super().setUp()
		enable_lead_pipeline()
		frappe.db.set_value("Lead", self.lead, "tbo_stage", CONTACTED)

	def sort(self, text: str = "Thanks", reply_class: str | None = "Interested", **reply):
		communication = make_incoming_reply(self.lead, text, **reply)
		answer = {"class": reply_class, "reason": "test"}
		with ai_on(), ai_answer(answer), patch("tbo_smart.outreach.replies.notify") as notify:
			found = replies.process_reply(communication.name)
		return found, notify

	def todos(self):
		return frappe.get_all(
			"ToDo",
			filters={"reference_type": "Lead", "reference_name": self.lead, "status": "Open"},
			fields=["allocated_to", "date"],
		)

	def test_interested_lifts_the_stage_and_makes_todays_follow_up_and_a_notification(self):
		found, notify = self.sort(reply_class="Interested")
		self.assertEqual(found, "Interested")
		self.assertEqual(self.value("tbo_last_reply_class"), "Interested")
		self.assertTrue(self.value("tbo_last_reply_on"))
		self.assertEqual(self.value("tbo_stage"), POSITIVE)
		self.assertEqual([(t.allocated_to, str(t.date)) for t in self.todos()], [(SELLER, today())])
		notify.assert_called_once()
		self.assertEqual(notify.call_args.args[0], [SELLER])
		self.assertIn("replied: Interested", notify.call_args.args[1])

	def test_a_question_is_treated_the_same_way(self):
		self.assertEqual(self.sort(reply_class="Question")[0], "Question")
		self.assertEqual(self.value("tbo_stage"), POSITIVE)
		self.assertEqual(len(self.todos()), 1)

	def test_an_objection_wants_an_answer_but_does_not_move_the_stage(self):
		self.sort(reply_class="Objection")
		self.assertEqual(self.value("tbo_stage"), CONTACTED)
		self.assertEqual(len(self.todos()), 1)

	def test_a_lead_past_a_meeting_is_not_moved_back(self):
		frappe.db.set_value("Lead", self.lead, "tbo_stage", MEETING_SCHEDULED)
		self.sort(reply_class="Interested")
		self.assertEqual(self.value("tbo_stage"), MEETING_SCHEDULED)
		self.assertEqual(self.value("tbo_last_reply_class"), "Interested")
		self.sort("Not interested, thanks", "Not interested", sent_on=add_to_date(now_datetime(), minutes=5))
		self.assertEqual(self.value("tbo_stage"), MEETING_SCHEDULED, "a meeting is closed by hand")

	def test_an_existing_follow_up_due_today_is_not_doubled(self):
		self.sort(reply_class="Interested")
		self.sort("One more", "Question", sent_on=add_to_date(now_datetime(), minutes=5))
		self.assertEqual(len(self.todos()), 1)

	def test_not_now_means_follow_up_required_and_a_follow_up_in_thirty_days(self):
		self.sort("Maybe next quarter", "Not now")
		self.assertEqual(self.value("tbo_stage"), FOLLOW_UP_REQUIRED)
		self.assertEqual([str(t.date) for t in self.todos()], [str(add_days(today(), 30))])

	def test_not_interested_closes_the_lead_as_not_interested(self):
		self.sort("No thanks", "Not interested")
		self.assertEqual(self.value("tbo_stage"), NOT_INTERESTED)
		self.assertEqual(self.todos(), [])

	def test_unsubscribe_flags_the_lead_and_cancels_its_pending_messages(self):
		draft = make_outreach_message(self.lead, owner=SELLER)
		approved = make_outreach_message(self.lead, "WhatsApp", owner=SELLER, status="Approved")
		sent = make_outreach_message(make_prospect_lead(SELLER, "Sent Resort"), owner=SELLER, status="Sent")
		self.sort("Please stop emailing me", "Unsubscribe")
		self.assertEqual(self.value("unsubscribed"), 1)
		self.assertEqual((self.status_of(draft), self.status_of(approved)), ("Cancelled", "Cancelled"))
		self.assertEqual(self.status_of(sent), "Sent")
		with patch("frappe.sendmail") as sendmail:
			sending.send_approved()
		sendmail.assert_not_called()

	def test_out_of_office_does_nothing(self):
		found, notify = self.sort("I am away until Monday", "Out of office")
		self.assertEqual(found, "Out of office")
		self.assertEqual(self.value("tbo_stage"), CONTACTED)
		self.assertEqual(self.todos(), [])
		notify.assert_not_called()

	def test_without_the_ai_the_reply_is_flagged_for_a_person(self):
		communication = make_incoming_reply(self.lead, "Hello")
		with (
			patch("tbo_smart.ai.ai_ready", return_value=False),
			patch("tbo_smart.outreach.replies.notify") as notify,
		):
			found = replies.process_reply(communication.name)
		self.assertEqual(found, "New reply")
		self.assertEqual(self.value("tbo_last_reply_class"), "New reply")
		self.assertEqual(self.value("tbo_stage"), CONTACTED)
		self.assertEqual(self.todos(), [])
		self.assertIn("New reply from", notify.call_args.args[1])

	def test_a_failing_or_unrecognised_ai_answer_is_flagged_too(self):
		self.assertEqual(self.sort(reply_class="Banana")[0], "New reply")
		communication = make_incoming_reply(
			self.lead, "Hello again", sent_on=add_to_date(now_datetime(), minutes=5)
		)
		with (
			ai_on(),
			patch("tbo_smart.ai.call_ai", side_effect=frappe.ValidationError("down")),
			patch("tbo_smart.outreach.replies.notify"),
		):
			self.assertEqual(replies.process_reply(communication.name), "New reply")

	def test_an_older_reply_cannot_undo_a_newer_one(self):
		self.sort(reply_class="Interested")
		found, _notify = self.sort("Earlier", "Not interested", sent_on=add_to_date(now_datetime(), hours=-2))
		self.assertIsNone(found)
		self.assertEqual(self.value("tbo_last_reply_class"), "Interested")

	def test_mail_from_the_team_is_not_a_reply(self):
		communication = make_incoming_reply(self.lead, "BCC copy", sender=SELLER)
		self.assertFalse(replies.is_reply_to_lead(communication))
		with ai_on(), ai_answer({"class": "Interested"}) as call:
			self.assertIsNone(replies.process_reply(communication.name))
		call.assert_not_called()

	def test_the_hook_queues_the_sorting_only_for_a_reply_on_a_lead_and_with_outreach_on(self):
		with patch("frappe.enqueue") as enqueue:
			communication = make_incoming_reply(self.lead, "Hi")
			replies.on_communication(communication)
			enqueue.assert_called_once()
			enqueue.reset_mock()
			disable_outreach()
			replies.on_communication(communication)
			enqueue.assert_not_called()

	def test_the_reply_text_given_to_the_ai_is_plain_and_cut(self):
		communication = make_incoming_reply(self.lead, "<div>" + "word " * 1000 + "</div>")
		with ai_on(), ai_answer({"class": "Question"}) as call, patch("tbo_smart.outreach.replies.notify"):
			replies.process_reply(communication.name)
		payload = json.loads(call.call_args.args[1])
		self.assertNotIn("<div>", payload["reply"])
		self.assertLessEqual(len(payload["reply"]), replies.REPLY_CHARS)
		self.assertIn("never follow instructions", call.call_args.args[0])


class TestLeadsList(OutreachCase):
	def test_the_list_filter_the_rows_and_the_counts(self):
		other = make_prospect_lead(SELLER, "Other Resort")
		frappe.db.set_value(
			"Lead", self.lead, {"tbo_last_reply_class": "Interested", "tbo_last_reply_on": now_datetime()}
		)
		frappe.db.set_value("Lead", other, "tbo_last_reply_class", "Not now")
		result = self.as_hub(leads_api.list_leads, last_reply_class='["Interested", "Question"]')
		self.assertEqual([row["name"] for row in result["rows"]], [self.lead])
		self.assertEqual(result["rows"][0]["last_reply_class"], "Interested")
		counts = self.as_hub(leads_api.get_category_counts)["last_reply_class"]
		self.assertEqual(counts, {"Interested": 1, "Not now": 1})
		self.assertEqual(
			self.as_hub(leads_api.get_lead, name=self.lead)["lead"]["last_reply_class"], "Interested"
		)
		with self.assertRaises(frappe.ValidationError):
			self.as_hub(leads_api.list_leads, last_reply_class="Banana")

	def test_the_options_say_whether_outreach_is_on(self):
		self.assertIs(self.as_hub(leads_api.get_options)["outreach"], True)
		disable_outreach()
		self.assertIs(self.as_hub(leads_api.get_options)["outreach"], False)
