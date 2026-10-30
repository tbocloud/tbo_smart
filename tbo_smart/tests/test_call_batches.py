from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline.statuses import CLOSED_LOST, ON_HOLD
from tbo_smart.sales.call_batches import clock
from tbo_smart.sales.call_batches.fill import build_batch, campaign_of
from tbo_smart.sales.call_batches.outcomes import sync_calls
from tbo_smart.sales.call_batches.reads import get_call_batches_report, get_my_calls
from tbo_smart.telephony.calls import log_manual_call
from tbo_smart.test_utils import (
	age_lead_activity,
	delete_call_batches,
	delete_lead_departments,
	delete_sales_records,
	disable_sales_hierarchy,
	get_batch_items,
	make_batch_department,
	make_call_batch,
	make_callable_lead,
	make_lead_task,
	make_user,
)

HUB = "calls.integration@tbosmart.test"
HEAD = "calls.head@tbosmart.test"
BD = "calls.bd@tbosmart.test"
BD2 = "calls.bd2@tbosmart.test"
MANAGER = "calls.manager@tbosmart.test"
OUTSIDER = "calls.outsider@tbosmart.test"
OWNERS = (HEAD, BD, BD2, MANAGER, OUTSIDER)
DEPARTMENT = "TBO Test Calls"


class TestCallBatches(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		for user in (HEAD, BD, BD2, OUTSIDER):
			make_user(user, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_sales_hierarchy(OWNERS)
		# Sundays are off when a user has no employee record: the tests run on any day
		for target in (
			"tbo_smart.sales.call_batches.next_batch.is_off_today",
			"tbo_smart.sales.call_batches.clock.is_off_today",
		):
			patcher = patch(target, return_value=None)
			patcher.start()
			self.addCleanup(patcher.stop)
		self.department = make_batch_department(DEPARTMENT, members=[BD, BD2], heads=[HEAD])

	def tearDown(self):
		frappe.set_user("Administrator")
		self.clean()

	def clean(self):
		delete_call_batches(OWNERS)
		delete_sales_records(OWNERS)
		frappe.db.delete("TBO Call", {"agent": ("in", [BD, BD2])})
		for lead in frappe.get_all("Lead", filters={"tbo_department": DEPARTMENT}, pluck="name"):
			frappe.delete_doc("Lead", lead, force=True, ignore_permissions=True)
		delete_lead_departments((DEPARTMENT,))

	def lead(self, name: str, owner: str | None = BD, **fields) -> str:
		return make_callable_lead(owner, name, DEPARTMENT, **fields)

	def build(self, user: str = BD, size: int = 50, **request):
		department = frappe.get_doc("TBO Lead Department", DEPARTMENT)
		now = now_datetime()
		return build_batch(
			department,
			user,
			"Day",
			add_to_date(now, hours=-1),
			add_to_date(now, hours=3),
			size,
			campaign_of(department, request),
		)

	def reasons(self, batch) -> dict:
		return {row.lead: row.priority_reason for row in batch.items}

	# ---------- filling ----------

	def test_a_batch_is_filled_in_priority_order_up_to_its_size(self):
		due = self.lead("Calls Due")
		retry = self.lead("Calls Retry")
		hot = self.lead("Calls Hot", tbo_temperature="Hot")
		cold = self.lead("Calls Cold")
		quiet = self.lead("Calls Quiet")
		make_lead_task(due, add_days(getdate(), -1), BD)
		make_call_batch(
			BD, DEPARTMENT, [retry], days_ago=1, status="Closed", outcomes={retry: "Not answered"}
		)
		frappe.set_user(BD)
		log_manual_call(quiet, "Connected")
		frappe.set_user("Administrator")
		age_lead_activity(quiet, 12)
		batch = self.build(size=4)
		self.assertEqual([row.lead for row in batch.items], [due, retry, hot, cold])
		self.assertEqual(
			[row.priority_reason for row in batch.items],
			["Follow-up due", "Retry from yesterday", "Never called", "Never called"],
		)

	def test_a_quiet_lead_that_was_called_before_comes_after_the_never_called(self):
		fresh = self.lead("Calls Fresh")
		quiet = self.lead("Calls Quiet")
		frappe.set_user(BD)
		log_manual_call(quiet, "Connected")
		frappe.set_user("Administrator")
		age_lead_activity(quiet, 9)
		batch = self.build()
		self.assertEqual([row.lead for row in batch.items], [fresh, quiet])
		self.assertEqual(self.reasons(batch)[quiet], "Gone quiet")

	def test_closed_on_hold_phoneless_and_other_peoples_leads_stay_out(self):
		ok = self.lead("Calls Ok")
		lost = self.lead("Calls Lost")
		hold = self.lead("Calls Hold")
		frappe.db.set_value("Lead", lost, "tbo_stage", CLOSED_LOST)
		frappe.db.set_value("Lead", hold, "tbo_stage", ON_HOLD)
		nophone = self.lead("Calls No Phone")
		frappe.db.set_value("Lead", nophone, {"mobile_no": None, "phone": None})
		theirs = self.lead("Calls Theirs", owner=BD2)
		batch = self.build()
		listed = {row.lead for row in batch.items}
		self.assertIn(ok, listed)
		self.assertTrue({lost, hold, nophone, theirs}.isdisjoint(listed))

	def test_a_lead_is_never_in_two_batches(self):
		self.lead("Calls Once")
		first = self.build()
		second = self.build()
		self.assertEqual(len(first.items), 1)
		self.assertEqual(len(second.items), 0)

	def test_the_daily_cap_and_a_members_own_cap_limit_the_batch(self):
		for number in range(5):
			self.lead(f"Calls Cap {number}")
		frappe.db.set_value("TBO Lead Department", DEPARTMENT, "daily_call_cap", 3)
		self.assertEqual(len(self.build().items), 3)
		self.assertIsNone(self.build())
		frappe.db.set_value(
			"TBO Lead Department User", {"parent": DEPARTMENT, "user": BD}, "daily_call_cap", 4
		)
		self.assertEqual(len(self.build().items), 1)

	def test_unassigned_leads_are_pulled_and_given_to_the_person(self):
		free = self.lead("Calls Free", owner=None)
		batch = self.build()
		self.assertEqual([row.lead for row in batch.items], [free])
		self.assertEqual(batch.items[0].priority_reason, "New lead for you")
		self.assertEqual(frappe.db.get_value("Lead", free, "lead_owner"), BD)
		self.assertTrue(
			frappe.db.exists("ToDo", {"reference_type": "Lead", "reference_name": free, "allocated_to": BD})
		)

	def test_two_people_never_pull_the_same_lead(self):
		self.lead("Calls Only One", owner=None)
		self.assertEqual(len(self.build(BD).items), 1)
		self.assertEqual(len(self.build(BD2).items), 0)

	def test_pulling_can_be_switched_off(self):
		free = self.lead("Calls Free", owner=None)
		frappe.db.set_value("TBO Lead Department", DEPARTMENT, "pull_unassigned", 0)
		self.assertEqual(len(self.build().items), 0)
		self.assertFalse(frappe.db.get_value("Lead", free, "lead_owner"))

	def test_the_campaign_filter_narrows_new_leads_not_follow_ups(self):
		kerala = self.lead("Calls Kerala", industry=None)
		due = self.lead("Calls Due")
		make_lead_task(due, getdate(), BD)
		frappe.db.set_value("TBO Lead Department", DEPARTMENT, "campaign_state", "Nowhere")
		batch = self.build()
		self.assertEqual([row.lead for row in batch.items], [due])
		self.assertNotIn(kerala, [row.lead for row in batch.items])

	# ---------- outcomes ----------

	def batch_of(self, count: int = 3, user: str = BD, **kwargs):
		leads = [self.lead(f"Calls Row {number}", owner=user) for number in range(count)]
		batch = make_call_batch(user, DEPARTMENT, leads, **kwargs)
		return leads, batch, get_batch_items(batch)

	def record(self, item, outcome, user: str = BD, **kwargs):
		frappe.set_user(user)
		try:
			return hub.record_call_outcome(item.name, outcome, acting_user=None, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def test_connected_logs_the_call_and_leaves_the_queue(self):
		leads, batch, items = self.batch_of()
		due_task = make_lead_task(leads[0], getdate(), BD)
		self.record(items[0], "Connected", note="Wants a demo")
		row = get_batch_items(batch)[0]
		self.assertEqual(row.outcome, "Connected")
		self.assertTrue(row.called_at)
		self.assertTrue(frappe.db.exists("TBO Call", {"lead": leads[0], "agent": BD}))
		self.assertEqual(frappe.db.get_value("ToDo", due_task, "status"), "Closed")
		frappe.set_user(BD)
		calls = get_my_calls()
		self.assertEqual(calls["counts"]["pending"], 2)
		self.assertEqual(calls["counts"]["called"], 1)
		self.assertEqual(calls["counts"]["size"], 3)

	def test_an_outcome_is_recorded_once_and_only_by_the_owner(self):
		_leads, _batch, items = self.batch_of()
		self.assertRaises(frappe.PermissionError, self.record, items[0], "Connected", BD2)
		self.assertRaises(frappe.ValidationError, self.record, items[0], "Maybe")
		self.record(items[0], "Connected")
		self.assertRaises(frappe.ValidationError, self.record, items[0], "Busy")

	def test_not_answered_comes_back_once_two_hours_later(self):
		leads, batch, items = self.batch_of(1)
		result = self.record(items[0], "Not answered")
		self.assertTrue(result["retry_at"])
		rows = get_batch_items(batch)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[1].lead, leads[0])
		self.assertEqual(rows[1].is_repeat, 1)
		hours = (get_datetime(rows[1].retry_at) - now_datetime()).total_seconds() / 3600
		self.assertAlmostEqual(hours, 2, delta=0.1)
		# not due yet: it waits, and does not block
		frappe.set_user(BD)
		calls = get_my_calls()
		self.assertEqual((calls["counts"]["pending"], calls["counts"]["scheduled"]), (0, 1))
		# the retry gets no answer either: the lead waits for tomorrow
		frappe.db.set_value(
			"TBO Call Batch Item", rows[1].name, "retry_at", add_to_date(now_datetime(), minutes=-1)
		)
		frappe.set_user("Administrator")
		self.assertIsNone(self.record(get_batch_items(batch)[1], "Busy")["retry_at"])
		self.assertEqual(len(get_batch_items(batch)), 2)

	def test_no_retry_is_made_when_the_slot_ends_first(self):
		_leads, batch, items = self.batch_of(1, ends_in_hours=1)
		self.assertIsNone(self.record(items[0], "Busy")["retry_at"])
		self.assertEqual(len(get_batch_items(batch)), 1)

	def test_yesterdays_not_answered_is_retried_tomorrow_as_priority_two(self):
		lead = self.lead("Calls Missed")
		make_call_batch(BD, DEPARTMENT, [lead], days_ago=1, status="Closed", outcomes={lead: "Busy"})
		batch = self.build()
		self.assertEqual(batch.items[0].priority, 2)

	def test_a_call_back_returns_to_the_queue_at_its_time(self):
		leads, batch, items = self.batch_of(1)
		self.assertRaises(frappe.ValidationError, self.record, items[0], "Call back")
		at = add_to_date(now_datetime(), hours=1)
		self.record(items[0], "Call back", callback_at=str(at))
		self.assertTrue(frappe.db.get_value("Lead", leads[0], "tbo_callback_at"))
		self.assertTrue(
			frappe.db.exists("ToDo", {"reference_type": "Lead", "reference_name": leads[0], "status": "Open"})
		)
		rows = get_batch_items(batch)
		self.assertEqual((rows[1].is_repeat, rows[1].priority_reason), (1, "Call back"))
		self.assertGreater(get_datetime(rows[1].retry_at), now_datetime())

	def test_wrong_number_flags_the_lead(self):
		leads, _batch, items = self.batch_of(1)
		self.record(items[0], "Wrong number")
		self.assertEqual(frappe.db.get_value("Lead", leads[0], "tbo_wrong_number"), 1)

	def test_a_call_logged_another_way_counts_for_the_item(self):
		leads, batch, _items = self.batch_of(2)
		frappe.set_user(BD)
		hub.log_call(leads[1], "Connected")
		self.assertEqual(sync_calls(BD), 1)
		frappe.set_user("Administrator")
		rows = get_batch_items(batch)
		self.assertFalse(rows[0].outcome)
		self.assertEqual(rows[1].outcome, "Connected")
		frappe.set_user(BD)
		self.assertEqual(get_my_calls()["counts"]["pending"], 1)

	# ---------- the queue and the next batch ----------

	def test_uncalled_items_of_an_ended_slot_are_overdue_and_still_callable(self):
		_leads, _batch, items = self.batch_of(2, status="Closed", ends_in_hours=-1)
		frappe.set_user(BD)
		calls = get_my_calls()
		self.assertEqual(calls["counts"]["overdue"], 2)
		self.assertEqual(len(calls["overdue"]), 2)
		self.assertEqual(calls["pending"], [])
		frappe.set_user("Administrator")
		self.record(items[0], "Connected")
		frappe.set_user(BD)
		self.assertEqual(get_my_calls()["counts"]["overdue"], 1)

	def test_the_next_batch_waits_for_pending_calls(self):
		self.batch_of(2)
		self.lead("Calls Later")
		frappe.set_user(BD)
		calls = get_my_calls()
		self.assertFalse(calls["next_batch"]["allowed"])
		self.assertIn("2 calls", calls["next_batch"]["reason"])
		result = hub.get_next_batch()
		self.assertFalse(result["created"])

	def test_the_next_batch_is_handed_out_when_the_person_is_up_to_date(self):
		_leads, _batch, items = self.batch_of(1)
		fresh = self.lead("Calls Fresh")
		self.record(items[0], "Connected")
		frappe.set_user(BD)
		self.assertTrue(get_my_calls()["next_batch"]["allowed"])
		result = hub.get_next_batch()
		self.assertTrue(result["created"])
		self.assertEqual(result["slot"], "Day +2")
		self.assertEqual(frappe.get_doc("TBO Call Batch", result["batch"]).items[0].lead, fresh)

	def test_too_many_overdue_calls_block_the_next_batch(self):
		frappe.db.set_value("TBO Lead Department", DEPARTMENT, "overdue_block_limit", 1)
		self.batch_of(2, status="Closed", ends_in_hours=-1)
		self.lead("Calls Later")
		frappe.set_user(BD)
		calls = get_my_calls()
		self.assertFalse(calls["next_batch"]["allowed"])
		self.assertIn("overdue", calls["next_batch"]["reason"])

	def test_the_daily_cap_blocks_the_next_batch(self):
		frappe.db.set_value("TBO Lead Department", DEPARTMENT, "daily_call_cap", 1)
		_leads, _batch, items = self.batch_of(1)
		self.lead("Calls Later")
		self.record(items[0], "Connected")
		frappe.set_user(BD)
		self.assertIn("limit", get_my_calls()["next_batch"]["reason"])

	def test_a_department_without_batches_gets_an_empty_queue(self):
		frappe.set_user(OUTSIDER)
		calls = get_my_calls()
		self.assertFalse(calls["enabled"])
		self.assertEqual(calls["pending"], [])

	def test_the_hub_reads_it_as_the_acting_user(self):
		leads, _batch, _items = self.batch_of(2)
		frappe.db.set_value("Lead", leads[0], "tbo_ai_next_action", "Send the quote")
		frappe.set_user(HUB)
		calls = hub.get_my_calls(acting_user=BD)
		self.assertEqual(calls["counts"]["pending"], 2)
		summary = next(row["summary"] for row in calls["pending"] if row["lead"] == leads[0])
		self.assertEqual(summary["next_step"], "Send the quote")
		self.assertEqual(summary["phone"], "9847012345")
		self.assertEqual(frappe.session.user, HUB)

	# ---------- the clock ----------

	def test_the_clock_opens_the_running_slot_once_for_every_member(self):
		clock.run_call_batch_clock()
		clock.run_call_batch_clock()
		batches = frappe.get_all("TBO Call Batch", filters={"department": DEPARTMENT}, pluck="user")
		self.assertCountEqual(batches, [BD, BD2])

	def test_the_clock_skips_a_person_who_is_off(self):
		with patch(
			"tbo_smart.sales.call_batches.clock.is_off_today",
			side_effect=lambda user, day: "on leave" if user == BD2 else None,
		):
			clock.run_call_batch_clock()
		batches = frappe.get_all("TBO Call Batch", filters={"department": DEPARTMENT}, pluck="user")
		self.assertEqual(batches, [BD])

	def test_the_clock_closes_an_ended_slot_and_tells_the_heads_when_far_behind(self):
		frappe.db.set_value("TBO Lead Department", DEPARTMENT, "overdue_block_limit", 1)
		leads, batch, _items = self.batch_of(3, ends_in_hours=-0.5)
		with patch("tbo_smart.sales.call_batches.clock.enqueue_create_notification") as notify:
			clock.close_ended_batches(now_datetime())
		self.assertEqual(frappe.db.get_value("TBO Call Batch", batch, "status"), "Closed")
		self.assertEqual(notify.call_args.args[0], [HEAD])
		self.assertIn("3 overdue", notify.call_args.args[1]["subject"])
		self.assertEqual(len(get_batch_items(batch)), len(leads))

	def test_the_heads_are_quiet_when_the_overdue_count_is_within_the_limit(self):
		self.batch_of(2, ends_in_hours=-0.5)
		with patch("tbo_smart.sales.call_batches.clock.enqueue_create_notification") as notify:
			clock.close_ended_batches(now_datetime())
		notify.assert_not_called()

	# ---------- the report and the settings ----------

	def test_the_report_counts_per_person_and_is_for_managers_and_heads(self):
		_leads, _batch, items = self.batch_of(3)
		self.record(items[0], "Connected")
		self.record(items[1], "Not answered")
		make_call_batch(
			BD2, DEPARTMENT, [self.lead("Calls Two", owner=BD2)], status="Closed", ends_in_hours=-1
		)
		frappe.set_user(MANAGER)
		report = get_call_batches_report()
		by_user = {row["user"]: row for row in report["people"]}
		self.assertEqual((by_user[BD]["size"], by_user[BD]["called"], by_user[BD]["connected"]), (3, 2, 1))
		self.assertEqual(by_user[BD2]["overdue"], 1)
		self.assertEqual(report["totals"]["size"], 4)
		frappe.set_user(HEAD)
		self.assertEqual(len(get_call_batches_report()["people"]), 2)
		frappe.set_user(BD)
		self.assertRaises(frappe.PermissionError, get_call_batches_report)

	def test_the_department_settings_round_trip_through_the_hub(self):
		frappe.set_user(HUB)
		saved = hub.save_lead_department(
			{
				"call_slots": [
					{"label": "Early", "start_time": "09:00", "end_time": "11:00", "size": 10},
					{"label": "Late", "start_time": "15:00", "end_time": "18:30", "size": 20},
				],
				"daily_call_cap": 30,
				"pull_unassigned": 0,
				"overdue_block_limit": 5,
				"member_caps": {BD: 12},
			},
			name=DEPARTMENT,
			acting_user=MANAGER,
		)
		self.assertEqual([slot["label"] for slot in saved["call_slots"]], ["Early", "Late"])
		self.assertEqual(saved["call_slots"][1]["end_time"], "18:30")
		self.assertEqual(
			(saved["daily_call_cap"], saved["pull_unassigned"], saved["overdue_block_limit"]), (30, 0, 5)
		)
		self.assertEqual(saved["member_caps"], {BD: 12})
		# saving the people again keeps their caps
		again = hub.save_lead_department({"members": [BD, BD2]}, name=DEPARTMENT, acting_user=MANAGER)
		self.assertEqual(again["member_caps"], {BD: 12})

	def test_slots_must_not_overlap_or_run_backwards(self):
		frappe.set_user(HUB)
		overlap = [
			{"label": "A", "start_time": "09:00", "end_time": "12:00", "size": 5},
			{"label": "B", "start_time": "11:00", "end_time": "14:00", "size": 5},
		]
		self.assertRaises(
			frappe.ValidationError, hub.save_lead_department, {"call_slots": overlap}, DEPARTMENT, MANAGER
		)
		backwards = [{"label": "A", "start_time": "12:00", "end_time": "09:00", "size": 5}]
		self.assertRaises(
			frappe.ValidationError, hub.save_lead_department, {"call_slots": backwards}, DEPARTMENT, MANAGER
		)

	def test_the_default_slots_apply_when_a_department_has_none(self):
		frappe.db.delete("TBO Call Slot", {"parent": DEPARTMENT})
		frappe.set_user(HUB)
		slots = hub.get_lead_departments(acting_user=MANAGER)
		slots = next(row for row in slots if row["name"] == DEPARTMENT)["call_slots"]
		self.assertEqual(
			[(slot["label"], slot["start_time"], slot["end_time"], slot["size"]) for slot in slots],
			[("Morning", "10:00", "13:00", 50), ("Afternoon", "14:00", "18:00", 50)],
		)
