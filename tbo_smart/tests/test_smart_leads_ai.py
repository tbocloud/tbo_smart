import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart import ai
from tbo_smart.smart_leads.migration import parse_migration_note
from tbo_smart.smart_leads.notify import teams_url_problem
from tbo_smart.smart_leads.utils import normalise_phone, tel_link, whatsapp_link
from tbo_smart.test_utils import (
	TEST_OPENAI_KEY,
	ai_http_response,
	anthropic_answer,
	configure_ai_providers,
	disable_smart_leads,
	mock_ai,
	openai_answer,
	set_smart_lead_settings,
)


class TestAIProviders(FrappeTestCase):
	def setUp(self):
		disable_smart_leads()

	def tearDown(self):
		disable_smart_leads()

	def test_no_provider_is_a_clear_error(self):
		self.assertFalse(ai.ai_ready())
		with self.assertRaises(ai.AINotConfigured):
			ai.call_ai("Answer in JSON.", "hi", "Test")

	def test_openai_answer_is_parsed_and_logged_with_cost(self):
		configure_ai_providers(openai=True)
		with mock_ai(openai_answer({"ok": True}, prompt_tokens=1_000_000, completion_tokens=500_000)) as post:
			result = ai.call_ai("Answer in JSON.", "hi", "Test", ("CRM Lead", "LEAD-X"))
		self.assertEqual(result["response"], {"ok": True})
		self.assertEqual(result["provider"], ai.OPENAI)
		url = post.call_args.args[0]
		body = post.call_args.kwargs["json"]
		self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
		self.assertEqual(body["model"], "gpt-5.6-luna")
		self.assertEqual(body["response_format"], {"type": "json_object"})
		self.assertNotIn("temperature", body)
		log = frappe.get_last_doc("TBO AI Usage Log", filters={"reference_name": "LEAD-X"})
		# $2 per 1M input + $8 per 1M output
		self.assertAlmostEqual(log.estimated_cost_usd, 2 + 4)
		self.assertEqual((log.provider, log.feature, log.ok), (ai.OPENAI, "Test", 1))

	def test_billing_failure_fails_over_and_pauses_the_provider(self):
		configure_ai_providers(openai=True, anthropic=True)
		billing = ai_http_response(
			429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}
		)
		with mock_ai(billing, anthropic_answer({"via": "kimi"})) as post:
			result = ai.call_ai("Answer in JSON.", "hi", "Test")
		self.assertEqual(result["response"], {"via": "kimi"})
		self.assertEqual(post.call_args.args[0], "https://api.moonshot.ai/anthropic/v1/messages")
		self.assertEqual(post.call_args.kwargs["headers"]["x-api-key"], "sk-test-kimi-key-987654321")
		status = ai.provider_status()
		self.assertTrue(status["providers"][ai.OPENAI]["paused"])
		self.assertEqual(status["in_use"], ai.ANTHROPIC)
		# paused: the next call goes straight to the fallback
		with mock_ai(anthropic_answer({"again": 1})) as post:
			ai.call_ai("Answer in JSON.", "hi", "Test")
		self.assertEqual(post.call_count, 1)

	def test_auth_and_server_errors_fail_over(self):
		for status in (401, 500):
			configure_ai_providers(openai=True, anthropic=True)
			with mock_ai(
				ai_http_response(status, {"error": {"message": "nope"}}), anthropic_answer({"ok": status})
			):
				self.assertEqual(ai.call_ai("JSON", "hi", "Test")["response"], {"ok": status})

	def test_rejected_request_does_not_fail_over(self):
		configure_ai_providers(openai=True, anthropic=True)
		with mock_ai(ai_http_response(400, {"error": {"message": "bad request"}})) as post:
			with self.assertRaises(ai.AIProviderError) as raised:
				ai.call_ai("JSON", "hi", "Test")
		self.assertEqual(raised.exception.kind, "invalid")
		self.assertEqual(post.call_count, 1)

	def test_every_provider_failing_raises_without_the_key(self):
		configure_ai_providers(openai=True)
		echo = ai_http_response(401, {"error": {"message": f"Incorrect API key provided: {TEST_OPENAI_KEY}"}})
		with mock_ai(echo):
			with self.assertRaises(ai.AIProviderError) as raised:
				ai.call_ai("JSON", "hi", "Test")
		self.assertNotIn(TEST_OPENAI_KEY, str(raised.exception))
		self.assertNotIn("sk-test", str(raised.exception))

	def test_thinking_model_gets_a_bigger_budget_once(self):
		configure_ai_providers(openai=True)
		empty = ai_http_response(
			200, {"choices": [{"message": {"content": ""}, "finish_reason": "length"}], "usage": {}}
		)
		with mock_ai(empty, openai_answer({"ok": 1})) as post:
			ai.call_ai("JSON", "hi", "Test")
		budgets = [call.kwargs["json"]["max_completion_tokens"] for call in post.call_args_list]
		self.assertEqual(budgets, list(ai.TOKEN_BUDGETS))

	def test_test_connection(self):
		self.assertFalse(ai.test_provider(ai.OPENAI)["ok"])
		configure_ai_providers(openai=True)
		with mock_ai(openai_answer({"reply": "OK"})):
			result = ai.test_provider(ai.OPENAI)
		self.assertTrue(result["ok"])
		self.assertEqual(result["details"][0]["value"], "gpt-5.6-luna")
		with mock_ai(ai_http_response(404, {"error": {"message": "The model does not exist"}})):
			self.assertIn("wasn't found", ai.test_provider(ai.OPENAI)["message"])


class TestNumberGuard(FrappeTestCase):
	def test_numbers_must_come_from_the_facts(self):
		known = ai.fact_numbers({"days_since_last_touch": 6, "created_on": "2026-03-07"})
		self.assertIsNotNone(ai.guarded("Last spoke 6 days ago, lead from March 2026", known))
		self.assertIsNotNone(ai.guarded("Came in on 07 March", known))
		self.assertIsNone(ai.guarded("Called 3 times, wants 50 licences", known))
		self.assertIsNone(ai.guarded("", known))

	def test_parse_json_answer_reads_fences_and_prose(self):
		self.assertEqual(ai.parse_json_answer('Sure:\n```json\n{"a": 1}\n```'), {"a": 1})
		self.assertEqual(ai.parse_json_answer('Here {"a": 2} done'), {"a": 2})
		self.assertEqual(ai.parse_json_answer("no json"), {})


class TestPhone(FrappeTestCase):
	def test_ten_digits_are_indian(self):
		self.assertEqual(normalise_phone("98470 12345"), "919847012345")
		self.assertEqual(normalise_phone("098470-12345"), "919847012345")
		self.assertEqual(normalise_phone("+91 98470 12345"), "919847012345")
		self.assertEqual(normalise_phone("919847012345"), "919847012345")

	def test_international_numbers_are_kept(self):
		self.assertEqual(normalise_phone("+971 50 418 2276"), "971504182276")
		self.assertEqual(normalise_phone("00971504182276"), "971504182276")

	def test_unusable_numbers(self):
		for value in (None, "", "12345", "call me"):
			self.assertIsNone(normalise_phone(value))
		self.assertIsNone(whatsapp_link("abc"))

	def test_links(self):
		self.assertEqual(tel_link("9847012345"), "tel:+919847012345")
		self.assertEqual(
			whatsapp_link("9847012345", "Hello Ajith, can we talk?"),
			"https://wa.me/919847012345?text=Hello%20Ajith%2C%20can%20we%20talk%3F",
		)


class TestMigrationNote(FrappeTestCase):
	def test_label_lines_with_multi_line_remarks(self):
		parsed = parse_migration_note(
			"<p>Status: Interested</p><p>Source: Cold Calling</p><p>Lead Owner: anjana@tbo.test</p>"
			"<p>Created On: 2025-03-07</p><p>Priority: High</p><p>Project Type: ERP</p>"
			"<p>Remarks: Wants a demo</p><p>call after Onam</p>"
		)
		self.assertEqual(parsed["status"], "Interested")
		self.assertEqual(parsed["source"], "Cold Calling")
		self.assertEqual(parsed["owner"], "anjana@tbo.test")
		self.assertEqual(parsed["created"], "2025-03-07")
		self.assertEqual(parsed["priority"], "High")
		self.assertEqual(parsed["project_type"], "ERP")
		self.assertEqual(parsed["remarks"], "Wants a demo\ncall after Onam")

	def test_table_rows_and_unknown_labels(self):
		parsed = parse_migration_note(
			"<table><tr><td>Lead Status</td><td>Lead</td></tr><tr><td>Territory</td><td>Kerala</td></tr>"
			"<tr><td>Type of project</td><td>Website</td></tr></table>"
		)
		self.assertEqual(parsed, {"status": "Lead", "project_type": "Website"})

	def test_live_migration_note_layout(self):
		# the exact layout of the notes written by the 2026-10-14 migration
		parsed = parse_migration_note(
			"<p><b>Original status:</b> Interested</p><p><b>Original source:</b> Cold Calling</p>"
			"<p><b>Original owner:</b> anjana@tbo.test</p><p><b>Created on old site:</b> 2024-11-23 10:43</p>"
			"<p><b>WhatsApp:</b> 88864212</p><p><b>City/State/Country:</b> India</p>"
			"<p><b>Priority:</b> Medium</p><p><b>Project type:</b> External</p>"
			"<p><b>Remarks:</b> call after demo<br>next week</p><p><b>Comment:</b> asked for price</p>"
		)
		self.assertEqual(parsed["status"], "Interested")
		self.assertEqual(parsed["source"], "Cold Calling")
		self.assertEqual(parsed["owner"], "anjana@tbo.test")
		self.assertEqual(parsed["created"], "2024-11-23 10:43")
		self.assertEqual(parsed["priority"], "Medium")
		self.assertEqual(parsed["project_type"], "External")
		self.assertEqual(parsed["remarks"], "call after demo\nnext week")

	def test_empty_note(self):
		self.assertEqual(parse_migration_note(None), {})


class TestSmartLeadSettings(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()
		disable_smart_leads()

	def test_teams_url_checks(self):
		self.assertIn("Teams message", teams_url_problem("https://teams.microsoft.com/l/message/123"))
		self.assertIn(
			"cut off", teams_url_problem("https://prod-01.westeurope.logic.azure.com/workflows/abc")
		)
		self.assertIsNone(
			teams_url_problem("https://x.westeurope.logic.azure.com/workflows/a?sp=1&sv=1.0&sig=abc")
		)

	def test_settings_refuse_bad_values(self):
		doc = frappe.get_single("TBO Smart Settings")
		doc.teams_dm_workflow_url = "https://teams.microsoft.com/l/message/123"
		self.assertRaises(frappe.ValidationError, doc.save)
		doc = frappe.get_single("TBO Smart Settings")
		doc.update({"lead_ladder_l1_days": 5, "lead_ladder_l2_days": 3, "lead_ladder_l3_days": 7})
		self.assertRaises(frappe.ValidationError, doc.save)
		doc = frappe.get_single("TBO Smart Settings")
		doc.openai_base_url = "http://api.openai.com/v1"
		self.assertRaises(frappe.ValidationError, doc.save)
		doc = frappe.get_single("TBO Smart Settings")
		doc.call_today_size = 50
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_settings_api_never_returns_a_key(self):
		from tbo_smart.api import smart_leads_settings

		configure_ai_providers(openai=True)
		result = smart_leads_settings.get_settings()
		self.assertNotIn(TEST_OPENAI_KEY, frappe.as_json(result))
		self.assertTrue(result["secrets"]["openai_api_key"]["set"])
		smart_leads_settings.save_settings({"openai_model": "gpt-5.6-luna", "enable_lead_scoring": 1})
		self.assertEqual(ai.password("openai_api_key"), TEST_OPENAI_KEY)
		smart_leads_settings.save_settings({"openai_api_key": ""})
		self.assertIsNone(ai.password("openai_api_key"))
		set_smart_lead_settings(enable_lead_scoring=0)

	def test_settings_api_is_for_system_managers(self):
		from tbo_smart.api import smart_leads_settings
		from tbo_smart.test_utils import make_user

		frappe.set_user(make_user("sl.bde@tbosmart.test", ["Sales User"]))
		try:
			self.assertRaises(frappe.PermissionError, smart_leads_settings.get_settings)
		finally:
			frappe.set_user("Administrator")
