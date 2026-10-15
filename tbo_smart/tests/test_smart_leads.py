import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from tbo_smart.api import smart_leads as api
from tbo_smart.api.mobile import crm as mobile_crm
from tbo_smart.smart_leads import scoring, triage
from tbo_smart.smart_leads.call_today import build_call_today
from tbo_smart.smart_leads.follow_ups import (
	DUE_TODAY,
	NO_FOLLOW_UP,
	OVERDUE,
	RE_ENGAGE,
	FollowUpRun,
	build_digest,
	notify_escalations,
	run_follow_ups,
	send_digests,
)
from tbo_smart.smart_leads.utils import WorkCalendar
from tbo_smart.test_utils import (
	age_lead,
	ai_http_response,
	change_lead_status,
	configure_ai_providers,
	delete_smart_lead_records,
	disable_sales_hierarchy,
	disable_smart_leads,
	enable_sales_hierarchy,
	enable_smart_leads,
	ensure_company_holiday_list,
	get_all_keys,
	get_fixture,
	make_approved_leave,
	make_crm_lead,
	make_lead_note,
	make_lead_task,
	make_leave_type,
	make_migration_note,
	make_role,
	make_sales_hierarchy_node,
	make_staff,
	make_user,
	mock_ai,
	openai_answer,
	set_smart_lead_settings,
	set_teams_dm_url,
	set_user_enabled,
)

BDE = "sl.bde@tbosmart.test"
BDE2 = "sl.bde2@tbosmart.test"
BDM = "sl.bdm@tbosmart.test"
HEAD = "sl.head@tbosmart.test"
MANAGER = "sl.manager@tbosmart.test"
GONE = "sl.gone@tbosmart.test"
OWNERS = (BDE, BDE2, BDM, HEAD, MANAGER, GONE)
MANAGER_ROLE = "TBO Smart Test Lead Manager"


def ids(rows) -> list[str]:
	return [row["id"] for row in rows]


def days(n: int) -> str:
	return add_days(nowdate(), n)


class SmartLeadsTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_role(MANAGER_ROLE)
		for user, roles in (
			(BDE, ["Sales User"]),
			(BDE2, ["Sales User"]),
			(BDM, ["Sales User", "Business Development Manager"]),
			(HEAD, ["Sales User", "Sales & Marketing Head"]),
			(MANAGER, ["Sales Manager", MANAGER_ROLE]),
			(GONE, ["Sales User"]),
		):
			make_user(user, roles)
			set_user_enabled(user, True)
		frappe.db.set_value("User", BDE, "mobile_no", "+91 98470 00011")
		ensure_company_holiday_list()
		enable_sales_hierarchy()
		for user in (BDE, BDM):
			frappe.db.delete("CRM Sales Hierarchy", {"user": user})
		bdm_node = make_sales_hierarchy_node(BDM)
		make_sales_hierarchy_node(BDE, bdm_node)
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_smart_lead_records(OWNERS)
		disable_sales_hierarchy((BDE, BDM))
		disable_smart_leads()
		set_smart_lead_settings(escalation_sales_head=None)
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_smart_lead_records(OWNERS)
		set_user_enabled(GONE, True)
		enable_smart_leads(escalation_sales_head=HEAD, lead_manager_role=MANAGER_ROLE)
		frappe.cache.delete_value(scoring.RATES_CACHE_KEY)

	def tearDown(self):
		frappe.set_user("Administrator")


class TestScoring(SmartLeadsTestCase):
	def score(self, lead: str) -> frappe._dict:
		scoring.score_leads([lead])
		return frappe.db.get_value(
			"CRM Lead", lead, ["lead_score", "lead_temperature", "tbo_score_reason"], as_dict=True
		)

	def test_status_history_and_migration_raise_the_score(self):
		hot = make_crm_lead(BDE, "Hot Lead", mobile_no="9847000001")
		change_lead_status(hot, "Contacted")
		change_lead_status(hot, "Qualified")
		make_migration_note(hot, status="Interested", priority="High")
		cold = make_crm_lead(BDE, "Cold Lead", mobile_no="9847000002")
		make_migration_note(cold, status="Do Not Contact", created="2025-01-02")
		age_lead(cold, 400)

		hot_score, cold_score = self.score(hot), self.score(cold)
		self.assertEqual(hot_score.lead_temperature, "Hot")
		self.assertGreaterEqual(hot_score.lead_score, scoring.HOT_FROM)
		self.assertTrue(hot_score.tbo_score_reason.startswith("Qualified"))
		self.assertIn("was Interested before the migration", hot_score.tbo_score_reason)
		self.assertEqual((cold_score.lead_score, cold_score.lead_temperature), (0, "Cold"))

	def test_closed_lead_scores_zero(self):
		lead = make_crm_lead(BDE, "Junk Lead")
		change_lead_status(lead, "Junk", lost_reason="Other", lost_notes="Spam")
		result = self.score(lead)
		self.assertEqual((result.lead_score, result.lead_temperature), (0, "Cold"))
		self.assertEqual(result.tbo_score_reason, "Closed (Junk)")

	def test_source_conversion_rate_counts(self):
		for source in ("SL Referral", "SL Cold Call"):
			if not frappe.db.exists("CRM Lead Source", source):
				frappe.get_doc({"doctype": "CRM Lead Source", "source_name": source}).insert()
		for index in range(10):
			make_crm_lead(
				BDE2, f"Ref {index}", source="SL Referral", status="Converted" if index < 5 else "New"
			)
			make_crm_lead(BDE2, f"Cold {index}", source="SL Cold Call")
		referral = make_crm_lead(BDE, "Referred", source="SL Referral", mobile_no="9847000003")
		cold_call = make_crm_lead(BDE, "Cold called", source="SL Cold Call", mobile_no="9847000004")
		# the inserts above re-scored as they came in and cached the rates of that moment
		frappe.cache.delete_value(scoring.RATES_CACHE_KEY)
		referral_score, cold_score = self.score(referral), self.score(cold_call)
		# 5 converted of the 11 SL Referral leads (the one scored included)
		self.assertIn("SL Referral leads convert 46%", referral_score.tbo_score_reason)
		self.assertGreater(referral_score.lead_score, cold_score.lead_score)

	def test_ai_adjusts_within_bounds_and_reads_each_change_once(self):
		configure_ai_providers(openai=True)
		lead = make_crm_lead(BDE, "Quote Asker", mobile_no="9847000005")
		make_lead_note(lead, "Asked for a quote for the billing module")
		base = self.score(lead).lead_score

		def answer(url, body):
			leads = json.loads(body["messages"][1]["content"])["leads"]
			return openai_answer(
				{"leads": [{"id": row["lead"], "adjust": 40, "reason": "Asked for a quote"} for row in leads]}
			)

		with mock_ai(answer):
			scoring.score_leads([lead], use_ai=True, ai_limit=10)
		row = frappe.db.get_value(
			"CRM Lead", lead, ["lead_score", "tbo_ai_adjust", "tbo_score_reason"], as_dict=True
		)
		self.assertEqual(row.tbo_ai_adjust, scoring.AI_LIMIT)
		self.assertEqual(row.lead_score, min(base + scoring.AI_LIMIT, 100))
		self.assertTrue(row.tbo_score_reason.startswith("Asked for a quote"))
		# nothing new on the lead: the AI isn't asked again
		with mock_ai() as post:
			scoring.score_leads([lead], use_ai=True, ai_limit=10)
		self.assertEqual(post.call_count, 0)

	def test_ai_reason_with_an_invented_number_is_dropped(self):
		configure_ai_providers(openai=True)
		lead = make_crm_lead(BDE, "Licence Lead", mobile_no="9847000006")
		make_lead_note(lead, "Interested in licences")
		with mock_ai(openai_answer({"leads": [{"id": lead, "adjust": 10, "reason": "Wants 50 licences"}]})):
			result = scoring.score_leads([lead], use_ai=True, ai_limit=10)
		self.assertEqual(result["ai"], 0)
		self.assertFalse(frappe.db.get_value("CRM Lead", lead, "tbo_ai_reason"))

	def test_a_new_note_rescores_the_lead(self):
		lead = make_crm_lead(BDE, "Fresh Note", mobile_no="9847000007")
		frappe.db.set_value("CRM Lead", lead, "tbo_scored_at", None)
		make_lead_note(lead, "Called, call back next week")
		self.assertTrue(frappe.db.get_value("CRM Lead", lead, "tbo_scored_at"))

	def test_dashboard_cards(self):
		from tbo_smart.smart_leads.dashboard import get_cards

		make_crm_lead(BDE, "Card Lead", mobile_no="9847000008")
		frappe.set_user(BDE)
		cards = {card["name"]: card["data"] for card in get_cards(nowdate(), nowdate(), BDE)}
		self.assertEqual(cards["tbo_call_today"]["value"], 1)
		self.assertEqual(cards["tbo_lead_follow_ups_overdue"]["value"], 0)
		frappe.set_user("Administrator")
		disable_smart_leads()
		self.assertEqual(get_cards(nowdate(), nowdate(), BDE), [])


class TestCallToday(SmartLeadsTestCase):
	def test_order_overdue_due_today_then_best_score(self):
		overdue = make_crm_lead(BDE, "Overdue", mobile_no="9847000101")
		make_lead_task(overdue, days(-3), BDE)
		due = make_crm_lead(BDE, "Due Today", mobile_no="9847000102")
		make_lead_task(due, nowdate(), BDE)
		interested = make_crm_lead(BDE, "Interested Before", mobile_no="9847000103")
		make_migration_note(interested, status="Interested")
		plain = make_crm_lead(BDE, "Plain", mobile_no="9847000104")
		later = make_crm_lead(BDE, "Planned Later", mobile_no="9847000105")
		make_lead_task(later, days(3), BDE)
		touched = make_crm_lead(BDE, "Touched Today", mobile_no="9847000106")
		make_lead_note(touched, "Spoke this morning")
		junk = make_crm_lead(BDE, "Junk", mobile_no="9847000107")
		change_lead_status(junk, "Junk", lost_reason="Other", lost_notes="Spam")
		other = make_crm_lead(BDE2, "Not Mine", mobile_no="9847000108")

		frappe.set_user(BDE)
		result = build_call_today()
		self.assertEqual(ids(result["data"]), [overdue, due, interested, plain])
		self.assertNotIn(other, ids(result["data"]))
		first = result["data"][0]
		self.assertEqual(first["why"], "overdue")
		self.assertTrue(first["follow_up"]["overdue"])
		self.assertEqual(first["tel"], "tel:+919847000101")
		self.assertEqual(first["whatsapp"], "https://wa.me/919847000101")

		short = build_call_today(size=2)
		self.assertEqual((len(short["data"]), short["more"]), (2, 2))

	def test_only_my_team_may_open_my_list(self):
		make_crm_lead(BDE, "Team Lead", mobile_no="9847000109")
		frappe.set_user(BDE2)
		self.assertRaises(frappe.PermissionError, build_call_today, BDE)
		frappe.set_user(BDM)
		self.assertEqual(len(build_call_today(BDE)["data"]), 1)
		frappe.set_user(MANAGER)
		self.assertEqual(build_call_today(BDE)["user"], BDE)

	def test_off_switch_refuses(self):
		disable_smart_leads()
		frappe.set_user(BDE)
		self.assertRaises(frappe.ValidationError, api.get_call_today)
		self.assertRaises(frappe.ValidationError, mobile_crm.get_call_today)

	def test_mobile_shape_matches_the_contract_fixture(self):
		lead = make_crm_lead(BDE, "Shape", mobile_no="9847000110", email="shape@example.com")
		make_lead_task(lead, days(-1), BDE)
		make_lead_note(lead, "Asked for a demo", days_ago=2)
		frappe.set_user(BDE)
		response = mobile_crm.get_call_today()
		fixture = get_fixture("crm.get_call_today")["message"]
		self.assertEqual(set(response), set(fixture))
		row, fixture_row = response["data"][0], fixture["data"][0]
		self.assertEqual(set(row), set(fixture_row))
		self.assertEqual(set(row["last_touch"]), set(fixture_row["last_touch"]))
		self.assertEqual(set(row["follow_up"]), set(fixture_row["follow_up"]))
		self.assertEqual(set(get_all_keys(response)), set(get_all_keys(fixture)))


class TestFollowUps(SmartLeadsTestCase):
	def items(self, run: FollowUpRun) -> dict:
		return {item.lead: item for item in run.items}

	def test_rules_and_the_ladder(self):
		fresh = make_crm_lead(BDE, "No Plan")
		stale = make_crm_lead(BDE, "Stale")
		age_lead(stale, 30)
		due = make_crm_lead(BDE, "Due")
		make_lead_task(due, nowdate(), BDE)
		late = {}
		for overdue_days in (1, 2, 4, 7):
			lead = make_crm_lead(BDE, f"Late {overdue_days}")
			make_lead_task(lead, days(-overdue_days), BDE)
			late[overdue_days] = lead

		run = FollowUpRun()
		run.evaluate()
		items = self.items(run)
		self.assertEqual(items[fresh].rule, NO_FOLLOW_UP)
		self.assertEqual(items[stale].rule, RE_ENGAGE)
		self.assertEqual((items[due].rule, items[due].recipients), (DUE_TODAY, {BDE}))
		self.assertEqual([items[late[n]].level for n in (1, 2, 4, 7)], [0, 1, 2, 3])
		self.assertEqual(items[late[1]].recipients, {BDE})
		self.assertEqual(items[late[2]].recipients, {BDE, BDM})
		self.assertEqual(items[late[4]].recipients, {BDE, BDM, HEAD})
		self.assertEqual(items[late[7]].recipients, {BDE, BDM, HEAD, MANAGER})
		self.assertTrue(all(items[lead].rule == OVERDUE for lead in late.values()))

	def test_people_on_leave_are_skipped_and_covered(self):
		employee = make_staff(BDE)
		leave_type = make_leave_type()
		lead = make_crm_lead(BDE, "While Away")
		make_lead_task(lead, days(-1), BDE)
		late = make_crm_lead(BDE, "Escalated While Away")
		make_lead_task(late, days(-4), BDE)
		application = make_approved_leave(employee, leave_type, nowdate(), days(2))
		try:
			run = FollowUpRun()
			run.evaluate()
			items = self.items(run)
			self.assertEqual(items[lead].recipients, {BDM})
			self.assertIn("is on leave until", items[lead].cover_note)
			self.assertEqual(items[late].recipients, {BDM, HEAD})
			self.assertNotIn(BDE, send_digests(run))
		finally:
			frappe.get_doc("Leave Application", application).cancel()

	def test_a_half_day_still_counts_as_working(self):
		employee = make_staff(BDE)
		lead = make_crm_lead(BDE, "Half Day")
		make_lead_task(lead, days(-1), BDE)
		application = make_approved_leave(employee, make_leave_type(), nowdate(), half_day=1)
		try:
			run = FollowUpRun()
			run.evaluate()
			self.assertEqual(self.items(run)[lead].recipients, {BDE})
		finally:
			frappe.get_doc("Leave Application", application).cancel()

	def test_disabled_owner_goes_to_the_fallback_or_needs_owner(self):
		lead = make_crm_lead(GONE, "Orphan")
		set_user_enabled(GONE, False)
		run = FollowUpRun()
		run.evaluate()
		self.assertIn(lead, run.needs_owner)
		self.assertEqual(self.items(run)[lead].recipients, set())
		self.assertIn(lead, [row["id"] for row in api.get_needs_owner()])

		set_smart_lead_settings(lead_fallback_owner=BDE2)
		run = FollowUpRun()
		run.evaluate()
		self.assertEqual(self.items(run)[lead].recipients, {BDE2})
		self.assertNotIn(lead, run.needs_owner)

		api.reassign_leads(json.dumps([lead]), BDE2)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "lead_owner"), BDE2)

	def test_only_sales_heads_see_needs_owner(self):
		frappe.set_user(BDE)
		self.assertRaises(frappe.PermissionError, api.get_needs_owner)
		self.assertRaises(frappe.PermissionError, api.reassign_leads, "[]", BDE)

	def test_escalation_notices_go_out_once_per_level(self):
		lead = make_crm_lead(BDE, "Climbing")
		task = make_lead_task(lead, days(-2), BDE)
		run = FollowUpRun()
		run.evaluate()
		with patch("tbo_smart.smart_leads.follow_ups.notify") as notify:
			notify_escalations(run)
			self.assertEqual(frappe.db.get_value("CRM Task", task, "tbo_escalation_level"), 1)
			calls = [call for call in notify.call_args_list if call.args[2:] == ("CRM Lead", lead)]
			self.assertEqual(len(calls), 1)
			self.assertEqual(calls[0].args[0], {BDE, BDM})
			notify.reset_mock()
			again = FollowUpRun()
			again.evaluate()
			notify_escalations(again)
			self.assertFalse([call for call in notify.call_args_list if call.args[2:] == ("CRM Lead", lead)])

	def test_digest_goes_to_teams_else_email(self):
		lead = make_crm_lead(BDE, "Digest Lead", organization="Galom")
		make_lead_task(lead, days(-1), BDE)
		run = FollowUpRun()
		run.evaluate()
		digest = build_digest(run, BDE)
		self.assertIn("1 overdue", digest["subject"])
		self.assertIn("Digest Lead (Galom): 1 working day overdue", digest["text"])
		self.assertIn("/crm/call-today", digest["html"])

		set_teams_dm_url()
		with patch("tbo_smart.smart_leads.notify.requests.post", return_value=ai_http_response(202)) as post:
			self.assertEqual(send_digests(run, only=BDE), {BDE: "teams"})
		self.assertEqual(post.call_args.kwargs["json"]["recipient"], BDE)

		set_teams_dm_url(None)
		with patch("tbo_smart.smart_leads.notify.frappe.sendmail") as sendmail:
			self.assertEqual(send_digests(run, only=BDE), {BDE: "email"})
		self.assertEqual(sendmail.call_args.kwargs["recipients"], [BDE])

	def test_daily_job_runs_once_and_only_when_on(self):
		lead = make_crm_lead(BDE, "Job Lead")
		make_lead_task(lead, days(-1), BDE)
		with (
			patch("tbo_smart.smart_leads.follow_ups.frappe.db.commit"),
			patch("tbo_smart.smart_leads.notify.frappe.sendmail"),
		):
			self.assertIn(BDE, run_follow_ups()["sent"])
			self.assertIsNone(run_follow_ups())
			set_smart_lead_settings(lead_digest_sent_on=None, enable_lead_follow_ups=0)
			self.assertIsNone(run_follow_ups())

	def test_summary_counts_my_leads(self):
		make_crm_lead(BDE, "Summary No Plan")
		due = make_crm_lead(BDE, "Summary Due")
		make_lead_task(due, nowdate(), BDE)
		frappe.set_user(BDE)
		counts = api.get_follow_up_summary()
		self.assertEqual((counts["due_today"], counts["no_follow_up"], counts["overdue"]), (1, 1, 0))
		self.assertRaises(frappe.PermissionError, api.get_follow_up_summary, BDE2)


class TestWorkCalendar(FrappeTestCase):
	def test_working_days_skip_holidays(self):
		calendar = WorkCalendar()
		monday = getdate("2026-10-12")
		calendar.holidays = {getdate("2026-10-13"), getdate("2026-10-18")}
		self.assertEqual(calendar.working_days_between(monday, "2026-10-15"), 2)
		self.assertEqual(calendar.next_working_day(monday), getdate("2026-10-14"))
		self.assertEqual(calendar.next_working_day("2026-10-17"), getdate("2026-10-19"))


def triage_answer(close: set, invent: set = frozenset()):
	"""An AI answer for every lead in the request: close the ones in `close`."""

	def answer(url, body):
		leads = json.loads(body["messages"][1]["content"])["leads"]
		return openai_answer(
			{
				"leads": [
					{
						"id": row["lead"],
						"bucket": "suggest_close" if row["lead"] in close else "call_again",
						"reason": "Said 3 times they are not interested"
						if row["lead"] in invent
						else ("No interest in the notes" if row["lead"] in close else "Never called back"),
					}
					for row in leads
				]
			}
		)

	return answer


class TestTriage(SmartLeadsTestCase):
	def setUp(self):
		super().setUp()
		configure_ai_providers(openai=True)
		frappe.db.delete("TBO Lead Triage")

	def mine(self, run: dict) -> dict:
		return {row["lead"]: row for row in run["rows"] if row["owner"] in OWNERS}

	def test_preview_buckets_and_apply_never_closes(self):
		call_again = make_crm_lead(BDE, "Call Again")
		closing = make_crm_lead(BDE, "Not Interested")
		make_lead_note(closing, "Not interested, bought elsewhere", days_ago=40)
		planned = make_crm_lead(BDE, "Planned")
		make_lead_task(planned, days(2), BDE)
		active = make_crm_lead(BDE, "Active")
		change_lead_status(active, "Contacted")

		with mock_ai(*[triage_answer({closing})] * 100):
			run = api.start_triage()
		self.assertEqual(run["status"], "Ready")
		rows = self.mine(run)
		self.assertEqual(set(rows), {call_again, closing})
		self.assertEqual(rows[call_again]["bucket"], "Call again")
		self.assertEqual(rows[closing]["bucket"], "Suggest close")
		self.assertEqual(
			getdate(rows[call_again]["follow_up_on"]), WorkCalendar().next_working_day(nowdate())
		)
		self.assertFalse(frappe.db.exists("CRM Task", {"reference_docname": call_again}))

		applied = api.apply_triage(run["name"])
		self.assertEqual(applied["run"]["status"], "Applied")
		task = frappe.db.get_value(
			"CRM Task", {"reference_docname": call_again}, ["tbo_task_type", "assigned_to"], as_dict=True
		)
		self.assertEqual((task.tbo_task_type, task.assigned_to), ("Sales follow-up", BDE))
		self.assertEqual(frappe.db.get_value("CRM Lead", closing, "status"), "New")
		self.assertFalse(frappe.db.exists("CRM Task", {"reference_docname": closing}))
		self.assertRaises(frappe.ValidationError, api.apply_triage, run["name"])

		closed = api.close_triage_leads(run["name"], json.dumps([closing]), "Junk")
		self.assertEqual(closed["closed"], [closing])
		self.assertEqual(
			frappe.db.get_value("CRM Lead", closing, ["status", "lost_reason"]), ("Junk", "Other")
		)

	def test_dates_follow_capacity(self):
		set_smart_lead_settings(triage_daily_capacity=1)
		first = make_crm_lead(BDE, "First")
		second = make_crm_lead(BDE, "Second")
		with mock_ai(*[triage_answer(set())] * 100):
			rows = self.mine(api.start_triage())
		days_planned = sorted(getdate(rows[lead]["follow_up_on"]) for lead in (first, second))
		calendar = WorkCalendar()
		self.assertEqual(days_planned[1], calendar.next_working_day(days_planned[0]))

	def test_invented_reason_is_not_used(self):
		lead = make_crm_lead(BDE, "Invented")
		with mock_ai(*[triage_answer({lead}, invent={lead})] * 100):
			run = api.start_triage()
		self.assertNotIn(lead, self.mine(run))
		self.assertGreaterEqual(run["skipped"], 1)

	def test_triage_is_for_managers(self):
		frappe.set_user(BDE)
		self.assertRaises(frappe.PermissionError, api.start_triage)
		self.assertFalse(api.get_access()["triage"])


class TestNotes(SmartLeadsTestCase):
	def setUp(self):
		super().setUp()
		configure_ai_providers(openai=True)
		self.lead = make_crm_lead(
			BDE, "Ajith Kumar", organization="Thrissur Gold House", mobile_no="9847012345"
		)

	def test_draft_note_proposes_without_writing(self):
		follow_up = days(5)
		frappe.set_user(BDE)
		with mock_ai(
			openai_answer(
				{
					"note": "Called Ajith. He wants a demo of the jewellery ERP.",
					"kind": "call",
					"next_status": "Contacted",
					"follow_up_on": follow_up,
					"follow_up_reason": "Show the demo",
				}
			)
		):
			draft = api.draft_note(self.lead, "ajith ne vilichu, demo venam ennu paranju")
		self.assertEqual(draft["kind"], "call")
		self.assertEqual(draft["next_status"], "Contacted")
		self.assertEqual(draft["follow_up_on"], follow_up)
		self.assertEqual(draft["warnings"], [])
		self.assertFalse(frappe.db.exists("FCRM Note", {"reference_docname": self.lead}))

	def test_draft_note_guard_and_bad_dates(self):
		frappe.set_user(BDE)
		with mock_ai(
			openai_answer(
				{
					"note": "Wants 50 licences",
					"kind": "note",
					"next_status": "Won?",
					"follow_up_on": days(200),
				}
			)
		):
			draft = api.draft_note(self.lead, "wants licences for the team")
		self.assertEqual(draft["note"], "wants licences for the team")
		self.assertTrue(draft["warnings"])
		self.assertIsNone(draft["follow_up_on"])
		self.assertEqual(draft["next_status"], "New")

	def test_save_confirmed_call_with_status_and_follow_up(self):
		frappe.set_user(BDE)
		result = api.save_note(self.lead, "Called Ajith, wants a demo", "call", "Contacted", days(3), "Demo")
		self.assertTrue(frappe.db.exists("CRM Call Log", result["call"]))
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "status"), "Contacted")
		task = frappe.db.get_value(
			"CRM Task", result["task"], ["tbo_task_type", "assigned_to", "title"], as_dict=True
		)
		self.assertEqual((task.tbo_task_type, task.assigned_to, task.title), ("Sales follow-up", BDE, "Demo"))

	def test_a_call_without_my_number_is_kept_as_a_note(self):
		frappe.db.set_value("User", BDE, "mobile_no", None)
		try:
			frappe.set_user(BDE)
			result = api.save_note(self.lead, "Rang, no answer", "call")
			self.assertEqual(result["kind"], "note")
			self.assertTrue(frappe.db.get_value("FCRM Note", result["note"], "content").startswith("Call: "))
		finally:
			frappe.set_user("Administrator")
			frappe.db.set_value("User", BDE, "mobile_no", "+91 98470 00011")

	def test_someone_else_cannot_write_on_my_lead(self):
		frappe.set_user(BDE2)
		self.assertRaises(frappe.PermissionError, api.save_note, self.lead, "Not mine", "note")
		self.assertRaises(frappe.PermissionError, api.draft_note, self.lead, "Not mine")
		self.assertRaises(frappe.PermissionError, api.get_lead_insight, self.lead)

	def test_brief_is_cached_until_the_lead_changes(self):
		make_migration_note(self.lead, status="Interested", remarks="Jewellery shop, wants billing")
		frappe.set_user(BDE)
		self.assertTrue(api.get_lead_insight(self.lead)["brief"]["stale"])
		with mock_ai(
			openai_answer(
				{
					"lines": [
						"Ajith runs Thrissur Gold House and wants billing.",
						"He was Interested before the migration; 12 calls so far.",
						"Call him and offer a demo.",
					]
				}
			)
		):
			brief = api.refresh_brief(self.lead)
		self.assertEqual(len(brief["lines"]), 2)
		self.assertFalse(brief["stale"])
		make_lead_note(self.lead, "Asked for the price list")
		self.assertTrue(api.get_lead_insight(self.lead)["brief"]["stale"])

	def test_whatsapp_draft_opens_wa_me_with_plus_91(self):
		frappe.set_user(BDE)
		with mock_ai(
			openai_answer({"message": "Hello Ajith, shall I show you the demo this week? Regards, sl.bde"})
		):
			draft = api.draft_whatsapp(self.lead, "en")
		self.assertEqual(draft["phone"], "+919847012345")
		self.assertTrue(draft["url"].startswith("https://wa.me/919847012345?text=Hello%20Ajith"))
		frappe.set_user("Administrator")
		no_phone = make_crm_lead(BDE, "No Phone")
		frappe.set_user(BDE)
		self.assertRaises(frappe.ValidationError, api.draft_whatsapp, no_phone, "en")
		self.assertRaises(frappe.ValidationError, api.draft_whatsapp, self.lead, "fr")

	def test_ai_off_means_no_drafts_but_the_box_still_saves(self):
		set_smart_lead_settings(enable_lead_notes=0)
		frappe.set_user(BDE)
		self.assertRaises(frappe.ValidationError, api.draft_note, self.lead, "hello")
		self.assertTrue(api.save_note(self.lead, "Plain note", "note")["note"])


class TestWithoutCRM(FrappeTestCase):
	"""tbo_smart also runs on tbo-smart.tbocloud.in, which has no CRM app."""

	def test_everything_is_off_and_nothing_is_set_up(self):
		from tbo_smart.smart_leads import any_enabled, setup

		enable_smart_leads()
		try:
			with (
				patch("tbo_smart.smart_leads.crm_installed", return_value=False),
				patch("tbo_smart.smart_leads.setup.crm_installed", return_value=False),
				patch("tbo_smart.smart_leads.setup.create_custom_fields") as create,
				patch("tbo_smart.api.smart_leads.crm_installed", return_value=False),
			):
				self.assertFalse(any_enabled())
				setup.after_migrate()
				create.assert_not_called()
				self.assertEqual(api.get_access(), {"enabled": False})
				self.assertIsNone(run_follow_ups())
				scoring.score_all()
		finally:
			disable_smart_leads()
