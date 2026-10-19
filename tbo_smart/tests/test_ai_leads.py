import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline.statuses import CLOSED_WON, NEW_LEAD, POSITIVE
from tbo_smart.smart_leads import autopilot
from tbo_smart.smart_leads.follow_ups import FollowUpRun, build_digest
from tbo_smart.test_utils import (
	age_lead,
	ai_lead_insight,
	delete_lead_pipeline_records,
	disable_sales_hierarchy,
	disable_smart_leads,
	enable_ai_leads,
	make_lead,
	make_lead_email,
	make_lead_note,
	make_lead_task,
	make_user,
	mock_ai,
	openai_answer,
	set_lead_values,
	set_smart_lead_settings,
)

HUB = "ai.integration@tbosmart.test"
BDE = "ai.bde@tbosmart.test"
BDE2 = "ai.bde2@tbosmart.test"
OWNERS = (BDE, BDE2)


def ai_todos(lead: str) -> list:
	return frappe.get_all(
		"ToDo",
		filters={"reference_type": "Lead", "reference_name": lead, "status": "Open"},
		fields=["allocated_to", "date", "description"],
	)


class TestAILeads(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		disable_sales_hierarchy(OWNERS)

	def setUp(self):
		frappe.set_user("Administrator")
		disable_smart_leads()
		delete_lead_pipeline_records(OWNERS)
		frappe.cache.delete_keys("tbo_smart:ai_leads")
		self.lead = make_lead(BDE, "Anu Textiles", company_name="Anu Textiles", mobile_no="9847012345")

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_smart_leads()

	def read_after_note(self, answer: dict, text: str = "Asked for a demo of billing") -> None:
		with mock_ai(openai_answer(answer)) as post:
			make_lead_note(self.lead, text)
		self.assertEqual(post.call_count, 1)

	def test_nothing_happens_while_switched_off(self):
		with mock_ai() as post:
			make_lead_note(self.lead, "Asked for a demo")
		post.assert_not_called()
		self.assertIsNone(frappe.db.get_value("Lead", self.lead, "tbo_ai_insight"))

	def test_a_note_makes_the_ai_read_the_lead_and_plan_the_next_step(self):
		enable_ai_leads()
		self.read_after_note(ai_lead_insight())
		row = frappe.db.get_value(
			"Lead",
			self.lead,
			["tbo_ai_sentiment", "tbo_ai_intent", "tbo_ai_next_action", "tbo_ai_stage", "tbo_stage"],
			as_dict=True,
		)
		self.assertEqual((row.tbo_ai_sentiment, row.tbo_ai_intent), ("Positive", "High"))
		self.assertEqual(row.tbo_ai_next_action, "Call to fix the demo")
		# the stage is only suggested: a person applies it
		self.assertEqual((row.tbo_ai_stage, row.tbo_stage), (POSITIVE, NEW_LEAD))
		[todo] = ai_todos(self.lead)
		self.assertEqual((todo.allocated_to, str(todo.date)), (BDE, add_days(nowdate(), 2)))
		self.assertIn("AI: Call to fix the demo", todo.description)
		# logged on the timeline, by the AI
		frappe.set_user(HUB)
		timeline = hub.get_lead(self.lead, acting_user=BDE)["timeline"]
		[note] = [item for item in timeline if item["kind"] == "ai"]
		self.assertEqual((note["who"], note["who_name"]), (None, "TBO AI"))
		self.assertIn("Suggests moving it to Positive / Interested", note["text"])
		# what the AI did never counts as a note
		self.assertEqual(next(item for item in timeline if item["kind"] == "note")["who"], "Administrator")

	def test_a_persons_own_plan_wins_and_an_unchanged_lead_isnt_read_again(self):
		enable_ai_leads()
		make_lead_task(self.lead, add_days(nowdate(), 5), BDE)
		self.read_after_note(ai_lead_insight())
		self.assertEqual(len(ai_todos(self.lead)), 1)
		self.assertNotIn("AI:", ai_todos(self.lead)[0].description)
		with mock_ai() as post:
			self.assertIsNone(autopilot.refresh_insight(self.lead))
		post.assert_not_called()

	def test_won_lost_and_invented_numbers_are_never_taken(self):
		enable_ai_leads()
		self.read_after_note(
			ai_lead_insight(
				suggested_stage=CLOSED_WON,
				summary="Will buy 40 licences",
				message={"channel": "whatsapp", "text": "We can give you 25% off"},
			)
		)
		insight = autopilot.stored_insight(self.lead)
		self.assertIsNone(insight["suggested_stage"])
		self.assertIsNone(insight["summary"])
		self.assertIsNone(insight["message"])
		self.assertEqual(insight["next_action"]["what"], "Call to fix the demo")

	def test_emails_are_read_too(self):
		enable_ai_leads()
		with mock_ai(openai_answer(ai_lead_insight(sentiment="Negative"))) as post:
			make_lead_email(self.lead, "Unhappy", "Nobody called us back after the demo")
		self.assertIn("Nobody called us back", post.call_args.kwargs["json"]["messages"][1]["content"])
		self.assertEqual(frappe.db.get_value("Lead", self.lead, "tbo_ai_sentiment"), "Negative")

	def test_apply_or_dismiss_the_suggested_stage(self):
		enable_ai_leads()
		self.read_after_note(ai_lead_insight())
		frappe.set_user(HUB)
		suggestion = hub.get_lead_ai(self.lead, acting_user=BDE)
		self.assertEqual(suggestion["insight"]["suggested_stage"], POSITIVE)
		self.assertEqual(suggestion["health"]["status"], "healthy")
		payload = hub.apply_ai_stage(self.lead, acting_user=BDE)
		self.assertEqual(payload["lead"]["tbo_stage"], POSITIVE)
		self.assertEqual(
			frappe.db.get_value("TBO Stage Change", {"parent": self.lead, "stage": POSITIVE}, "changed_by"),
			BDE,
		)
		self.assertIsNone(hub.get_lead_ai(self.lead, acting_user=BDE)["insight"]["suggested_stage"])
		self.assertRaises(frappe.ValidationError, hub.apply_ai_stage, self.lead, acting_user=BDE)
		# someone who can't see the lead can't apply anything
		self.assertRaises(frappe.PermissionError, hub.get_lead_ai, self.lead, acting_user=BDE2)

	def test_health_and_duplicates(self):
		old = make_lead(BDE, "Anu Textiles again", mobile_no="+91 98470 12345", email_id="anu@textiles.test")
		age_lead(old, 40)
		same_company = make_lead(BDE2, "Someone else", company_name="Anu Textiles")
		frappe.set_user(HUB)
		result = hub.get_lead_ai(old, acting_user=BDE)
		self.assertFalse(result["enabled"])
		self.assertEqual(result["health"]["status"], "at_risk")
		self.assertEqual({s["key"] for s in result["health"]["signals"]}, {"touch", "follow_up"})
		[dupe] = result["duplicates"]
		self.assertEqual((dupe["name"], dupe["match"]), (self.lead, ["phone"]))
		# BDE2's lead isn't visible to BDE, so it isn't offered
		self.assertNotIn(
			same_company, [d["name"] for d in hub.get_lead_ai(self.lead, acting_user=BDE)["duplicates"]]
		)

	def test_drafts_are_for_a_person_to_send(self):
		enable_ai_leads()
		set_lead_values(self.lead, email_id="anu@textiles.test")
		frappe.set_user(HUB)
		with (
			mock_ai(openai_answer({"subject": "Your demo", "text": "Hello, shall we fix the demo? Anu"})),
			patch("frappe.sendmail") as sendmail,
		):
			draft = hub.draft_lead_message(self.lead, "email", acting_user=BDE)
		self.assertEqual((draft["subject"], draft["to"]), ("Your demo", "anu@textiles.test"))
		with mock_ai(openai_answer({"message": "Hello, shall we fix the demo? Anu"})):
			whatsapp = hub.draft_lead_message(self.lead, "whatsapp", acting_user=BDE)
		self.assertTrue(whatsapp["url"].startswith("https://wa.me/919847012345"))
		sendmail.assert_not_called()
		self.assertFalse(frappe.db.exists("Communication", {"reference_name": self.lead}))
		# once sent from their own WhatsApp, the person logs it
		entry = hub.log_message_sent(self.lead, "whatsapp", "Hello, shall we fix the demo?", acting_user=BDE)
		self.assertEqual((entry["who"], entry["kind"]), (BDE, "activity"))

	def test_the_overview_has_the_days_plan(self):
		enable_ai_leads()
		self.read_after_note(ai_lead_insight())
		make_lead_task(self.lead, add_days(nowdate(), -1), BDE)
		hot = make_lead(BDE, "Keen Traders")
		set_lead_values(hot, tbo_temperature="Hot", tbo_score=80)
		frappe.set_user(HUB)
		result = hub.get_overview(acting_user=BDE)["ai"]
		self.assertEqual((result["high_intent"], result["stage_suggestions"]), (1, 1))
		plan = {row["name"]: row for row in result["plan"]["rows"]}
		self.assertEqual(set(plan), {self.lead, hot})
		self.assertEqual(plan[self.lead]["next_action"], "Call to fix the demo")
		self.assertEqual(plan[hot]["why"], "Hot lead")
		set_smart_lead_settings(enable_ai_leads=0)
		self.assertEqual(hub.get_overview(acting_user=BDE)["ai"], {"enabled": False})

	def test_leads_gone_quiet_get_a_draft_and_the_owner_is_told_once(self):
		enable_ai_leads()
		age_lead(self.lead, 40)
		with (
			mock_ai(openai_answer(ai_lead_insight())),
			patch("tbo_smart.smart_leads.notify.notify") as notify,
		):
			result = autopilot.nudge_stale_leads()
		self.assertEqual(result, {"nudged": 1, "owners": 1})
		self.assertEqual(notify.call_args.args[0], [BDE])
		self.assertEqual(str(frappe.db.get_value("Lead", self.lead, "tbo_ai_nudged_on")), nowdate())
		self.assertTrue(autopilot.stored_insight(self.lead)["message"])
		with mock_ai() as post:
			self.assertEqual(autopilot.nudge_stale_leads()["nudged"], 0)
		post.assert_not_called()

	def test_the_morning_digest_names_the_next_action(self):
		enable_ai_leads(enable_lead_follow_ups=1)
		self.read_after_note(ai_lead_insight())
		frappe.db.set_value("ToDo", {"reference_name": self.lead}, "date", getdate())
		run = FollowUpRun()
		run.evaluate()
		self.assertIn("AI suggests: Call to fix the demo", build_digest(run, BDE)["text"])

	def test_the_days_limit_caps_cost(self):
		enable_ai_leads(ai_daily_lead_limit=1)
		self.read_after_note(ai_lead_insight())
		other = make_lead(BDE, "Second lead")
		with mock_ai(openai_answer(ai_lead_insight())) as post:
			make_lead_note(other, "Called, no answer")
		post.assert_not_called()
		self.assertEqual(json.loads(frappe.db.get_value("Lead", other, "tbo_ai_insight") or "null"), None)
