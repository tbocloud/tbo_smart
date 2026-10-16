import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from tbo_smart.api import lead_pipeline as api
from tbo_smart.api.mobile import crm as mobile_crm
from tbo_smart.api.mobile import meeting as mobile_meeting
from tbo_smart.lead_pipeline import meetings, status_migration
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_LOST,
	CLOSED_WON,
	CONTACTED,
	FIRST_MEETING,
	MEETING_SCHEDULED,
	NEGOTIATION,
	NEW_LEAD,
	POSITIVE,
	PROPOSAL_SENT,
	SECOND_MEETING,
	STATUS_NAMES,
	ensure_statuses,
)
from tbo_smart.lead_pipeline.transitions import moves_forward
from tbo_smart.test_utils import (
	change_lead_status,
	configure_ai_providers,
	delete_lead_deals,
	delete_lead_meetings,
	delete_smart_lead_records,
	disable_lead_pipeline,
	disable_sales_hierarchy,
	disable_smart_leads,
	enable_lead_pipeline,
	enable_sales_hierarchy,
	ensure_company_holiday_list,
	get_all_keys,
	get_fixture,
	hours_from_now,
	make_crm_lead,
	make_lead_meeting,
	make_migration_note,
	make_old_lead_status,
	make_sales_hierarchy_node,
	make_user,
	mock_ai,
	move_meeting,
	openai_answer,
	set_lead_values,
	set_smart_lead_settings,
)

BDE = "lp.bde@tbosmart.test"
BDE2 = "lp.bde2@tbosmart.test"
BDM = "lp.bdm@tbosmart.test"
HEAD = "lp.head@tbosmart.test"
OWNERS = (BDE, BDE2, BDM, HEAD)


class LeadPipelineTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		for user, roles in (
			(BDE, ["Sales User"]),
			(BDE2, ["Sales User"]),
			(BDM, ["Sales User", "Business Development Manager"]),
			(HEAD, ["Sales User", "Sales & Marketing Head"]),
		):
			make_user(user, roles)
		frappe.db.set_value("User", BDE, "mobile_no", "+91 98470 00021")
		ensure_company_holiday_list()
		enable_sales_hierarchy()
		for user in (BDE, BDM):
			frappe.db.delete("CRM Sales Hierarchy", {"user": user})
		make_sales_hierarchy_node(BDE, make_sales_hierarchy_node(BDM))
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		cls.clean()
		disable_sales_hierarchy((BDE, BDM))
		disable_lead_pipeline()
		disable_smart_leads()
		# FrappeTestCase rolls back here; the switches must stay off for the next test files
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	@classmethod
	def clean(cls):
		leads = frappe.get_all("CRM Lead", filters={"lead_owner": ("in", OWNERS)}, pluck="name")
		delete_lead_meetings(leads)
		delete_lead_deals(leads)
		delete_smart_lead_records(OWNERS)

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_smart_leads()
		enable_lead_pipeline(escalation_sales_head=HEAD)

	def tearDown(self):
		frappe.set_user("Administrator")


class TestStatuses(LeadPipelineTestCase):
	def test_pipeline_statuses_and_types(self):
		statuses = frappe.get_all("CRM Lead Status", fields=["name", "type"], order_by="position asc")
		names = [row.name for row in statuses]
		self.assertEqual([name for name in names if name in STATUS_NAMES], list(STATUS_NAMES))
		types = {row.name: row.type for row in statuses}
		self.assertEqual((types[NEW_LEAD], types[CLOSED_WON], types[CLOSED_LOST]), ("Open", "Won", "Lost"))
		# a new lead starts on New Lead (the only Open status)
		self.assertEqual(frappe.db.get_value("CRM Lead", make_crm_lead(BDE, "Fresh"), "status"), NEW_LEAD)

	def test_lost_reason_required(self):
		lead = make_crm_lead(BDE, "Lost One")
		self.assertRaises(frappe.ValidationError, change_lead_status, lead, CLOSED_LOST)
		frappe.set_user(BDE)
		self.assertRaises(frappe.ValidationError, mobile_crm.set_lead_status, lead, CLOSED_LOST)
		result = mobile_crm.set_lead_status(lead, CLOSED_LOST, "Budget went elsewhere")
		self.assertEqual(result["lost_reason"], "Budget went elsewhere")
		self.assertEqual(
			frappe.db.get_value("CRM Lead", lead, ["status", "lost_reason"]), (CLOSED_LOST, "Other")
		)

	def test_moves_only_forward(self):
		self.assertTrue(moves_forward(NEW_LEAD, MEETING_SCHEDULED))
		self.assertTrue(moves_forward(POSITIVE, MEETING_SCHEDULED))
		self.assertFalse(moves_forward(FIRST_MEETING, MEETING_SCHEDULED))
		self.assertFalse(moves_forward(NEGOTIATION, SECOND_MEETING))
		self.assertFalse(moves_forward(CLOSED_LOST, MEETING_SCHEDULED))
		self.assertTrue(moves_forward(NEGOTIATION, CLOSED_WON))


class TestStatusMigration(LeadPipelineTestCase):
	OLD = (
		("New", "Open"),
		("Nurture", "Ongoing"),
		("Qualified", "Won"),
		("Unqualified", "Lost"),
		("Junk", "Lost"),
		("Converted", "Won"),
	)

	def setUp(self):
		super().setUp()
		self.retired = frappe.db.get_single_value("TBO Smart Settings", status_migration.RETIRED_FIELD)
		for name, kind in self.OLD:
			make_old_lead_status(name, kind)

	def tearDown(self):
		self.clean()
		for name, _kind in self.OLD:
			frappe.delete_doc_if_exists("CRM Lead Status", name, force=True)
		frappe.db.set_single_value("TBO Smart Settings", status_migration.RETIRED_FIELD, self.retired)
		frappe.db.commit()  # nosemgrep
		super().tearDown()

	def old_lead(self, name: str, status: str, original: str | None = None, **values) -> str:
		lead = make_crm_lead(BDE, name)
		set_lead_values(lead, status=status, **values)
		if original:
			make_migration_note(lead, status=original)
		return lead

	def test_mapping_table(self):
		cases = {
			("New", None, False): NEW_LEAD,
			("Contacted", "Open", False): CONTACTED,
			("Contacted", "Meeting Fixed", False): FIRST_MEETING,
			("Contacted", "Demo Given", False): FIRST_MEETING,
			("Nurture", None, False): POSITIVE,
			("Qualified", "Quotation", False): PROPOSAL_SENT,
			("Qualified", "Interested", False): POSITIVE,
			("Unqualified", None, False): CLOSED_LOST,
			("Junk", None, False): CLOSED_LOST,
			("Converted", None, False): CLOSED_WON,
			("Qualified", "Quotation", True): CLOSED_WON,
			(NEGOTIATION, None, False): None,
		}
		for (status, original, converted), expected in cases.items():
			self.assertEqual(status_migration.new_status(status, original, converted), expected, status)

	def test_migrates_once_keeps_the_old_status_and_reverts(self):
		leads = {
			"new": self.old_lead("Old New", "New"),
			"demo": self.old_lead("Old Demo", "Contacted", "Demo"),
			"quote": self.old_lead("Old Quote", "Qualified", "Quotation"),
			"junk": self.old_lead("Old Junk", "Junk"),
			"lost": self.old_lead(
				"Old Unqualified", "Unqualified", lost_reason="Other", lost_notes="No budget"
			),
			"won": self.old_lead("Old Converted", "Converted"),
		}
		counts = status_migration.execute()
		self.assertEqual(counts[("Contacted", FIRST_MEETING)], 1)
		self.assertEqual(counts[("Qualified", PROPOSAL_SENT)], 1)
		got = {
			key: frappe.db.get_value(
				"CRM Lead",
				lead,
				["status", "tbo_pre_pipeline_status", "lost_reason", "lost_notes"],
				as_dict=True,
			)
			for key, lead in leads.items()
		}
		self.assertEqual(got["new"].status, NEW_LEAD)
		self.assertEqual(
			(got["demo"].status, got["demo"].tbo_pre_pipeline_status), (FIRST_MEETING, "Contacted")
		)
		self.assertEqual(got["quote"].status, PROPOSAL_SENT)
		self.assertEqual((got["junk"].status, got["junk"].lost_reason), (CLOSED_LOST, "Other"))
		self.assertEqual(got["lost"].lost_notes, "No budget")
		self.assertEqual(got["won"].status, CLOSED_WON)
		# no deal for the historical won leads
		self.assertFalse(frappe.db.exists("CRM Deal", {"lead": leads["won"]}))
		# the old statuses are retired, their definitions kept
		self.assertFalse(frappe.db.exists("CRM Lead Status", "Junk"))
		kept = json.loads(frappe.db.get_single_value("TBO Smart Settings", status_migration.RETIRED_FIELD))
		self.assertIn("Junk", {row["name"] for row in kept})

		# idempotent: a second run moves nothing, even a mapped lead someone set back by hand
		set_lead_values(leads["demo"], status=CONTACTED)
		self.assertEqual(sum(status_migration.execute().values()), 0)
		self.assertEqual(frappe.db.get_value("CRM Lead", leads["demo"], "status"), CONTACTED)

		# revert puts back every lead still on the status it was given (the site's other leads
		# too, so they are mapped again after); the one set back by hand is kept for a person
		reverted = status_migration.revert()
		self.assertIn(leads["demo"], reverted["kept"])
		self.assertGreaterEqual(reverted["restored"], len(leads) - 1)
		self.assertEqual(frappe.db.get_value("CRM Lead", leads["junk"], "status"), "Junk")
		self.assertTrue(frappe.db.exists("CRM Lead Status", "Junk"))
		self.assertTrue(frappe.db.exists("CRM Lead Status", CONTACTED))
		ensure_statuses()
		status_migration.execute()
		self.assertEqual(frappe.db.get_value("CRM Lead", leads["junk"], "status"), CLOSED_LOST)


class TestMeetings(LeadPipelineTestCase):
	def test_stage_numbering_and_lead_moves(self):
		lead = make_crm_lead(BDE, "Meet Me")
		first = make_lead_meeting(lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), MEETING_SCHEDULED)
		cancelled = make_lead_meeting(lead)
		frappe.set_user(BDE)
		api.set_meeting_status(cancelled, meetings.CANCELLED)
		frappe.set_user("Administrator")
		second = make_lead_meeting(lead)
		third = make_lead_meeting(lead)
		fourth = make_lead_meeting(lead)
		labels = [
			frappe.db.get_value("TBO Lead Meeting", name, "stage_label")
			for name in (first, second, third, fourth)
		]
		self.assertEqual(labels, ["1st meeting", "2nd meeting", "3rd meeting", "Additional meeting 1"])
		self.assertEqual(frappe.db.get_value("TBO Lead Meeting", first, "bde"), BDE)

	def test_manual_status_is_not_pulled_back(self):
		lead = make_crm_lead(BDE, "Negotiating")
		change_lead_status(lead, NEGOTIATION)
		make_lead_meeting(lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), NEGOTIATION)

	def test_switch_off_moves_nothing(self):
		disable_lead_pipeline()
		lead = make_crm_lead(BDE, "Quiet")
		make_lead_meeting(lead)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), NEW_LEAD)
		self.assertRaises(frappe.ValidationError, api.get_lead_pipeline, lead)

	def test_clock_in_progress_mom_pending_and_reminder(self):
		lead = make_crm_lead(BDE, "Clockwork")
		soon = make_lead_meeting(lead, hours_from_now(0.5))
		now_on = make_lead_meeting(lead, hours_from_now(2))
		move_meeting(now_on, hours_from_now(-0.25))
		past = make_lead_meeting(lead, hours_from_now(3))
		move_meeting(past, hours_from_now(-3))
		with (
			patch("tbo_smart.lead_pipeline.meetings.deliver", return_value="teams") as deliver,
			patch("tbo_smart.lead_pipeline.meetings.notify") as notify,
		):
			result = meetings.run_meeting_clock()
			self.assertEqual(frappe.db.get_value("TBO Lead Meeting", now_on, "status"), meetings.IN_PROGRESS)
			self.assertEqual(frappe.db.get_value("TBO Lead Meeting", past, "status"), meetings.MOM_PENDING)
			self.assertTrue(frappe.db.get_value("TBO Lead Meeting", past, "mom_pending_since"))
			self.assertTrue(frappe.db.get_value("TBO Lead Meeting", soon, "reminded_at"))
			self.assertEqual(deliver.call_count, 1)
			self.assertIn([BDE], [call.args[0] for call in notify.call_args_list])
			self.assertGreaterEqual(result["mom_pending"], 1)
			meetings.run_meeting_clock()
			self.assertEqual(deliver.call_count, 1)

	def test_submit_mom_moves_lead_creates_follow_up_and_next_meeting(self):
		lead = make_crm_lead(BDE, "Minutes")
		meeting = make_lead_meeting(lead)
		move_meeting(meeting, hours_from_now(-2))
		follow_up = add_days(nowdate(), 3)
		frappe.set_user(BDE)
		self.assertRaises(frappe.ValidationError, api.save_mom, meeting, {"agenda": "Demo"}, 1)
		self.assertRaises(frappe.ValidationError, api.save_mom, meeting, {"action_items": ["Send prices"]})
		result = api.save_mom(
			meeting,
			json.dumps(
				{
					"discussion_points": "Walked through billing and stock.",
					"client_interest": "Positive",
					"next_follow_up_on": follow_up,
					"action_items": [
						{"item": "Send the price list", "responsible": "TBO", "due_date": follow_up}
					],
				}
			),
			1,
			next_meeting=json.dumps(
				{
					"scheduled_at": str(hours_from_now(48)),
					"meeting_type": "Physical visit",
					"location": "Kochi office",
				}
			),
		)
		self.assertEqual(result["status"], meetings.MOM_SUBMITTED)
		self.assertEqual(result["actions_total"], 1)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), FIRST_MEETING)
		task = frappe.db.get_value(
			"CRM Task", result["follow_up_task"], ["tbo_task_type", "assigned_to", "due_date"], as_dict=True
		)
		self.assertEqual(
			(task.tbo_task_type, task.assigned_to, getdate(task.due_date)),
			("Sales follow-up", BDE, getdate(follow_up)),
		)
		next_meeting = frappe.db.get_value("TBO Lead Meeting", meeting, "next_meeting")
		self.assertEqual(frappe.db.get_value("TBO Lead Meeting", next_meeting, "stage_label"), "2nd meeting")
		# a submitted MOM is final
		self.assertRaises(frappe.ValidationError, api.save_mom, meeting, {"agenda": "x"})
		# the summary
		summary = api.get_lead_pipeline(lead)["summary"]
		self.assertEqual(summary["meetings_completed"], 1)
		self.assertEqual(summary["next_activity"]["kind"], "meeting")
		self.assertFalse(summary["needs_follow_up"]["flag"])

	def test_mom_can_close_the_lead_with_a_reason(self):
		lead = make_crm_lead(BDE, "Not now")
		meeting = make_lead_meeting(lead)
		move_meeting(meeting, hours_from_now(-2))
		frappe.set_user(BDE)
		self.assertRaises(
			frappe.ValidationError,
			api.save_mom,
			meeting,
			{"discussion_points": "Went with another vendor"},
			1,
			CLOSED_LOST,
		)
		api.save_mom(meeting, {"discussion_points": "Went with another vendor"}, 1, CLOSED_LOST, "Price")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "status"), CLOSED_LOST)

	def test_needs_follow_up_flag(self):
		lead = make_crm_lead(BDE, "Waiting")
		meeting = make_lead_meeting(lead)
		move_meeting(meeting, hours_from_now(-3))
		with patch("tbo_smart.lead_pipeline.meetings.notify"):
			meetings.run_meeting_clock()
		flag = api.get_lead_pipeline(lead)["summary"]["needs_follow_up"]
		self.assertTrue(flag["flag"])
		self.assertIn("MOM pending for the 1st meeting", flag["reasons"])

	def test_mom_pending_escalates_bde_bdm_head_once_per_level(self):
		lead = make_crm_lead(BDE, "Late Minutes")
		meeting = make_lead_meeting(lead)
		move_meeting(meeting, hours_from_now(-3))
		with patch("tbo_smart.lead_pipeline.meetings.notify"):
			meetings.run_meeting_clock()
		from tbo_smart.smart_leads.follow_ups import FollowUpRun

		def escalate(days_later: int):
			run = FollowUpRun(add_days(nowdate(), days_later))
			with patch("tbo_smart.lead_pipeline.meetings.notify") as notify:
				meetings.escalate_mom_pending(run)
			return [set(call.args[0]) for call in notify.call_args_list if call.args[3] == lead]

		self.assertEqual(escalate(0), [])
		# the ladder's working days (2 / 4); look far enough ahead to clear weekends and holidays
		calendar = FollowUpRun().calendar
		day = getdate()
		at_bdm = next(n for n in range(1, 30) if calendar.working_days_between(day, add_days(day, n)) >= 2)
		self.assertEqual(escalate(at_bdm), [{BDE, BDM}])
		self.assertEqual(escalate(at_bdm), [])
		at_head = next(n for n in range(1, 40) if calendar.working_days_between(day, add_days(day, n)) >= 4)
		self.assertEqual(escalate(at_head), [{BDE, BDM, HEAD}])

	def test_permissions(self):
		lead = make_crm_lead(BDE, "Mine")
		meeting = make_lead_meeting(lead)
		frappe.set_user(BDE2)
		self.assertRaises(frappe.PermissionError, api.get_lead_pipeline, lead)
		self.assertRaises(frappe.PermissionError, api.get_meeting, meeting)
		self.assertNotIn(meeting, frappe.get_list("TBO Lead Meeting", pluck="name"))
		frappe.set_user(BDE)
		self.assertIn(meeting, frappe.get_list("TBO Lead Meeting", pluck="name"))
		self.assertRaises(frappe.PermissionError, frappe.delete_doc, "TBO Lead Meeting", meeting)

	def test_link_must_be_https(self):
		lead = make_crm_lead(BDE, "Linky")
		self.assertRaises(frappe.ValidationError, make_lead_meeting, lead, meeting_link="meet.google.com/x")

	def test_ai_draft_proposes_and_guards_numbers(self):
		configure_ai_providers()
		set_smart_lead_settings(enable_lead_notes=1)
		lead = make_crm_lead(BDE, "Drafty")
		meeting = make_lead_meeting(lead)
		move_meeting(meeting, hours_from_now(-2))
		follow_up = add_days(nowdate(), 4)
		frappe.set_user(BDE)
		with mock_ai(
			openai_answer(
				{
					"discussion_points": "Client wants billing and GST reports.",
					"client_requirements": "Needs 25 user licences.",
					"client_interest": "Positive",
					"next_follow_up_on": follow_up,
					"action_items": [
						{"item": "Send the demo video", "responsible": "TBO", "due_date": follow_up}
					],
				}
			)
		):
			draft = api.draft_mom(meeting, "billing um GST reports um venam, demo video ayakkanam")
		self.assertEqual(draft["fields"]["discussion_points"], "Client wants billing and GST reports.")
		self.assertNotIn("client_requirements", draft["fields"])
		self.assertTrue(draft["warnings"])
		self.assertEqual(draft["fields"]["next_follow_up_on"], follow_up)
		self.assertEqual(draft["action_items"][0]["item"], "Send the demo video")
		self.assertEqual(frappe.db.get_value("TBO Lead Meeting", meeting, "status"), meetings.SCHEDULED)
		self.assertFalse(frappe.db.get_value("TBO Lead Meeting", meeting, "discussion_points"))

	def test_mobile_shapes(self):
		lead = make_crm_lead(BDE, "On the phone")
		frappe.set_user(BDE)
		scheduled = mobile_meeting.schedule(
			lead, str(hours_from_now(5)), "Online", "https://teams.microsoft.com/l/meetup-join/x"
		)
		self.assertEqual(scheduled["stage_label"], "1st meeting")
		result = mobile_meeting.get_meetings(lead)
		self.assertEqual(get_all_keys(result), get_all_keys(get_fixture("meeting.get_meetings")["message"]))
		self.assertRaises(
			frappe.ValidationError, mobile_meeting.set_status, scheduled["name"], meetings.MOM_SUBMITTED
		)


class TestDealAtWon(LeadPipelineTestCase):
	def test_won_creates_the_deal_once(self):
		lead = make_crm_lead(BDE, "Winner", organization="Winner Traders", mobile_no="9847000301")
		frappe.set_user(BDE)
		self.assertRaises(frappe.ValidationError, mobile_crm.convert_lead, lead)
		mobile_crm.set_lead_status(lead, CLOSED_WON)
		frappe.set_user("Administrator")
		deal = frappe.db.get_value("CRM Deal", {"lead": lead}, ["name", "status", "deal_owner"], as_dict=True)
		self.assertTrue(deal)
		self.assertEqual(frappe.db.get_value("CRM Deal Status", deal.status, "type"), "Won")
		self.assertEqual(deal.deal_owner, BDE)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, ["status", "converted"]), (CLOSED_WON, 1))
		self.assertEqual(api.get_lead_pipeline(lead)["deal"], deal.name)
		# saving it again makes no second deal
		change_lead_status(lead, CLOSED_WON, lead_name="Winner Again")
		self.assertEqual(frappe.db.count("CRM Deal", {"lead": lead}), 1)

	def test_switch_off_keeps_crm_conversion(self):
		disable_lead_pipeline()
		lead = make_crm_lead(BDE, "Plain Won", organization="Plain Org")
		change_lead_status(lead, CLOSED_WON)
		self.assertFalse(frappe.db.exists("CRM Deal", {"lead": lead}))


class TestWithoutCRM(LeadPipelineTestCase):
	def test_no_crm_guard(self):
		with (
			patch("tbo_smart.lead_pipeline.crm_installed", return_value=False),
			patch("tbo_smart.lead_pipeline.setup.crm_installed", return_value=False),
			patch("tbo_smart.lead_pipeline.setup.create_custom_fields") as create,
		):
			from tbo_smart.lead_pipeline import pipeline_enabled
			from tbo_smart.lead_pipeline.setup import setup_lead_pipeline

			self.assertFalse(pipeline_enabled())
			setup_lead_pipeline()
			create.assert_not_called()
			self.assertIsNone(meetings.run_meeting_clock())
			self.assertIsNone(meetings.run_meeting_morning())
			self.assertFalse(api.get_access()["enabled"])
