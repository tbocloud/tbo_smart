from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart import ai
from tbo_smart.smart_leads.migration import parse_migration_note
from tbo_smart.smart_leads.notify import teams_url_problem
from tbo_smart.smart_leads.utils import (
	dialling_code,
	is_weekly_off,
	match_digits,
	normalise_phone,
	phone_tail,
	tel_link,
	weekly_off_days,
	whatsapp_link,
)
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
			result = ai.call_ai("Answer in JSON.", "hi", "Test", ("Lead", "LEAD-X"))
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
	def setUp(self):
		# the tests below are for an Indian site; TestPhoneCountry covers the others
		patcher = patch("tbo_smart.smart_leads.utils.system_country", return_value="India")
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_ten_digits_are_indian(self):
		self.assertEqual(normalise_phone("98470 12345"), "919847012345")
		self.assertEqual(normalise_phone("098470-12345"), "919847012345")
		self.assertEqual(normalise_phone("+91 98470 12345"), "919847012345")
		self.assertEqual(normalise_phone("919847012345"), "919847012345")

	def test_a_trunk_zero_is_dropped_before_any_country_code(self):
		self.assertEqual(normalise_phone("0501234567", "971"), "971501234567")
		self.assertEqual(normalise_phone("050 123 4567", "971"), "971501234567")
		self.assertEqual(normalise_phone("971501234567", "971"), "971501234567")
		self.assertEqual(normalise_phone("09847012345"), "919847012345")

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


class TestPhoneCountry(FrappeTestCase):
	"""A number typed without a code gets the site's country's (System Settings → Country)."""

	def country(self, name):
		return patch("tbo_smart.smart_leads.utils.system_country", return_value=name)

	def test_saudi_site(self):
		with self.country("Saudi Arabia"):
			self.assertEqual(normalise_phone("0501234567"), "966501234567")
			self.assertEqual(normalise_phone("050 123 4567"), "966501234567")
			self.assertEqual(normalise_phone("501234567"), "966501234567")
			self.assertEqual(normalise_phone("966501234567"), "966501234567")
			self.assertEqual(normalise_phone("+966 50 123 4567"), "966501234567")
			# a number with its own code is kept
			self.assertEqual(normalise_phone("+91 98470 12345"), "919847012345")
			self.assertEqual(tel_link("0501234567"), "tel:+966501234567")
			self.assertEqual(whatsapp_link("0501234567"), "https://wa.me/966501234567")

	def test_india_when_unset_or_unknown(self):
		for name in (None, "", "Atlantis"):
			with self.country(name):
				self.assertEqual(normalise_phone("98470 12345"), "919847012345")

	def test_a_given_country_wins(self):
		with self.country("Saudi Arabia"):
			self.assertEqual(normalise_phone("0501234567", "971"), "971501234567")

	def test_dialling_codes(self):
		self.assertEqual(dialling_code("Saudi Arabia"), "966")
		self.assertEqual(dialling_code("India"), "91")
		self.assertEqual(dialling_code("United Arab Emirates"), "971")
		self.assertEqual(dialling_code(None), "91")

	def test_numbers_match_on_the_national_digits(self):
		from tbo_smart.sales.duplicates import phone_key
		from tbo_smart.sales.numbers import same_digits
		from tbo_smart.telephony.calls import same_number

		with self.country("India"):
			self.assertEqual(match_digits(), 10)
			self.assertEqual(phone_key("+91 98470 12345"), "9847012345")
			# two Indian numbers apart in their first digit stay apart
			self.assertFalse(same_digits("919847012345", "918847012345"))
		with self.country("Saudi Arabia"):
			self.assertEqual(match_digits(), 9)
			self.assertEqual(phone_tail("966501234567"), "501234567")
			# however the lead's field holds it, the key is the same
			for typed in ("050 123 4567", "0501234567", "+966 50 123 4567", "966501234567"):
				self.assertEqual(phone_key(typed), "501234567")
			self.assertTrue(same_number("050 123 4567", "966501234567"))
			self.assertTrue(same_digits("966501234567", "966501234567"))
			self.assertFalse(same_digits("966501234567", "966551234567"))


class TestWeekend(FrappeTestCase):
	"""The weekend is the site country's: Sunday in India, Friday and Saturday in Saudi Arabia."""

	FRIDAY, SATURDAY, SUNDAY = "2026-11-20", "2026-11-21", "2026-11-22"

	def country(self, name):
		return patch("tbo_smart.smart_leads.utils.system_country", return_value=name)

	def test_weekly_off_days(self):
		with self.country("India"):
			self.assertEqual([is_weekly_off(d) for d in (self.FRIDAY, self.SATURDAY, self.SUNDAY)], [0, 0, 1])
		with self.country("Saudi Arabia"):
			self.assertEqual([is_weekly_off(d) for d in (self.FRIDAY, self.SATURDAY, self.SUNDAY)], [1, 1, 0])
		with self.country(None):
			self.assertEqual(weekly_off_days(), (6,))

	def test_outreach_window_skips_the_weekend(self):
		from tbo_smart.outreach.guards import in_window, next_window

		with patch("tbo_smart.outreach.guards.setting", return_value=None):
			with self.country("Saudi Arabia"):
				self.assertFalse(in_window(f"{self.FRIDAY} 11:00:00"))
				self.assertTrue(in_window(f"{self.SUNDAY} 11:00:00"))
				self.assertEqual(str(next_window(f"{self.FRIDAY} 11:00:00")), f"{self.SUNDAY} 09:30:00")
			with self.country("India"):
				self.assertTrue(in_window(f"{self.FRIDAY} 11:00:00"))
				self.assertFalse(in_window(f"{self.SUNDAY} 11:00:00"))

	def test_auto_assign_skips_the_saudi_weekend(self):
		from tbo_smart.sales.call_batches.auto_assign import skips

		department = frappe._dict(auto_assign_skip_sunday=1, auto_assign_skip_saturdays=None)
		with self.country("Saudi Arabia"):
			self.assertTrue(skips(department, self.FRIDAY))
			self.assertTrue(skips(department, self.SATURDAY))
			self.assertFalse(skips(department, self.SUNDAY))
		with self.country("India"):
			self.assertFalse(skips(department, self.FRIDAY))
			self.assertTrue(skips(department, self.SUNDAY))


class TestEveryoneReadsAllLeads(FrappeTestCase):
	def test_setting_opens_reading_only(self):
		from tbo_smart.sales.permissions import everyone_reads_all

		with patch("frappe.db.get_single_value", return_value=1):
			self.assertTrue(everyone_reads_all("read"))
			self.assertTrue(everyone_reads_all(None))
			self.assertFalse(everyone_reads_all("write"))
		with patch("frappe.db.get_single_value", return_value=0):
			self.assertFalse(everyone_reads_all("read"))


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
