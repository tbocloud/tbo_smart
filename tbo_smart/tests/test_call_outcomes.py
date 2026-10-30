import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline.call_outcomes import apply_call_outcome, result_of, target_stage
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_LOST,
	CLOSED_WON,
	CONTACTED,
	FOLLOW_UP_REQUIRED,
	LOST_STATUSES,
	MEETING_SCHEDULED,
	NEW_LEAD,
	NOT_INTERESTED,
	NOT_RESPONDING,
	OPEN_STATUSES,
	PROGRESS,
	STATUS_NAMES,
	WRONG_NUMBER,
	needs_lost_reason,
	status_type,
)
from tbo_smart.test_utils import (
	delete_sales_records,
	disable_lead_pipeline,
	disable_sales_hierarchy,
	enable_lead_pipeline,
	make_lead,
	make_user,
)

HUB = "outcomes.integration@tbosmart.test"
BD = "outcomes.bd@tbosmart.test"
OWNERS = (BD,)


class TestCallOutcomeStages(FrappeTestCase):
	def test_the_new_stages_have_their_types(self):
		self.assertEqual(status_type(NOT_RESPONDING), "Ongoing")
		self.assertEqual((status_type(NOT_INTERESTED), status_type(WRONG_NUMBER)), ("Lost", "Lost"))
		self.assertIn(NOT_RESPONDING, OPEN_STATUSES)
		self.assertTrue({CLOSED_LOST, NOT_INTERESTED, WRONG_NUMBER} <= set(LOST_STATUSES))
		self.assertFalse({NOT_INTERESTED, WRONG_NUMBER} & set(OPEN_STATUSES))
		self.assertEqual(PROGRESS[NOT_RESPONDING], PROGRESS[CONTACTED])
		self.assertEqual(STATUS_NAMES.index(NOT_RESPONDING), STATUS_NAMES.index(CONTACTED) + 1)
		self.assertEqual(
			(
				needs_lost_reason(CLOSED_LOST),
				needs_lost_reason(NOT_INTERESTED),
				needs_lost_reason(WRONG_NUMBER),
			),
			(True, False, False),
		)

	def test_outcomes_read_from_every_way_of_logging_a_call(self):
		self.assertEqual(
			[
				result_of(name)
				for name in ("No answer", "Voicemail", "Missed", "Answered", "Busy", "Call back")
			],
			["Not answered", "Not answered", "Not answered", "Connected", "Busy", "Call back"],
		)
		self.assertIsNone(result_of("Talked"))

	def test_the_stage_a_call_moves_a_lead_to(self):
		for current, result, expected in (
			(NEW_LEAD, "Not answered", NOT_RESPONDING),
			(CONTACTED, "Busy", NOT_RESPONDING),
			(NOT_RESPONDING, "Not answered", None),
			(MEETING_SCHEDULED, "Not answered", None),
			(NOT_RESPONDING, "Connected", CONTACTED),
			(NEW_LEAD, "Connected", CONTACTED),
			(FOLLOW_UP_REQUIRED, "Connected", None),
			(CONTACTED, "Not interested", NOT_INTERESTED),
			(NOT_RESPONDING, "Wrong number", WRONG_NUMBER),
			(MEETING_SCHEDULED, "Not interested", None),
			(NEW_LEAD, "Call back", FOLLOW_UP_REQUIRED),
			(CLOSED_WON, "Connected", None),
			(CLOSED_LOST, "Connected", None),
			(NOT_INTERESTED, "Connected", None),
		):
			self.assertEqual(target_stage(current, result), expected, (current, result))


class TestApplyCallOutcome(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BD, ["Sales User"])

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		disable_lead_pipeline()
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		disable_sales_hierarchy(OWNERS)
		enable_lead_pipeline()
		self.lead = make_lead(BD, "Called lead", mobile_no="9847012399")

	def value(self, field: str, lead: str | None = None):
		return frappe.db.get_value("Lead", lead or self.lead, field)

	def test_not_answered_then_connected_then_declined(self):
		self.assertEqual(apply_call_outcome(self.lead, "Not answered"), NOT_RESPONDING)
		self.assertEqual(
			(self.value("tbo_stage"), self.value("tbo_last_call_result")), (NOT_RESPONDING, "Not answered")
		)
		self.assertTrue(self.value("tbo_last_call_on"))
		self.assertEqual(apply_call_outcome(self.lead, "Connected"), CONTACTED)
		self.assertEqual(apply_call_outcome(self.lead, "Not interested"), NOT_INTERESTED)
		self.assertEqual(
			self.value("tbo_lost_reason"), "Not interested", "the reason is filled without a prompt"
		)
		# a lost lead stays where it is, but its last call is kept
		self.assertIsNone(apply_call_outcome(self.lead, "Connected"))
		self.assertEqual(
			(self.value("tbo_stage"), self.value("tbo_last_call_result")), (NOT_INTERESTED, "Connected")
		)

	def test_wrong_number_flags_the_lead_and_gives_its_reason(self):
		self.assertEqual(apply_call_outcome(self.lead, "Wrong number"), WRONG_NUMBER)
		self.assertEqual((self.value("tbo_wrong_number"), self.value("tbo_lost_reason")), (1, "Wrong number"))

	def test_a_lead_further_along_keeps_its_stage(self):
		frappe.db.set_value("Lead", self.lead, "tbo_stage", MEETING_SCHEDULED)
		self.assertIsNone(apply_call_outcome(self.lead, "Not answered"))
		self.assertEqual(
			(self.value("tbo_stage"), self.value("tbo_last_call_result")), (MEETING_SCHEDULED, "Not answered")
		)

	def test_an_older_call_cannot_undo_a_newer_one(self):
		apply_call_outcome(self.lead, "Connected", now_datetime())
		self.assertIsNone(
			apply_call_outcome(self.lead, "Not answered", add_to_date(now_datetime(), hours=-1))
		)
		self.assertEqual(
			(self.value("tbo_stage"), self.value("tbo_last_call_result")), (CONTACTED, "Connected")
		)

	def test_call_back_makes_a_follow_up(self):
		at = add_to_date(now_datetime(), days=1)
		self.assertEqual(apply_call_outcome(self.lead, "Call back", callback_at=at), FOLLOW_UP_REQUIRED)
		self.assertTrue(self.value("tbo_callback_at"))
		self.assertTrue(
			frappe.db.exists(
				"ToDo", {"reference_type": "Lead", "reference_name": self.lead, "status": "Open"}
			)
		)

	def test_the_stage_stays_when_the_pipeline_is_off(self):
		disable_lead_pipeline()
		self.assertIsNone(apply_call_outcome(self.lead, "Not answered"))
		self.assertEqual(
			(self.value("tbo_stage"), self.value("tbo_last_call_result")), (NEW_LEAD, "Not answered")
		)

	def test_a_stage_set_by_hand_fills_the_reason_too(self):
		frappe.set_user(HUB)
		hub.set_stage(self.lead, NOT_INTERESTED, acting_user=BD)
		self.assertEqual(self.value("tbo_lost_reason"), "Not interested")
		hub.set_stage(self.lead, WRONG_NUMBER, acting_user=BD)
		self.assertEqual((self.value("tbo_lost_reason"), self.value("tbo_wrong_number")), ("Wrong number", 1))
		self.assertRaises(frappe.ValidationError, hub.set_stage, self.lead, CLOSED_LOST, acting_user=BD)


class TestHubLogCallOutcomes(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BD, ["Sales User"])

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		disable_lead_pipeline()
		frappe.db.commit()  # nosemgrep
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records(OWNERS)
		disable_sales_hierarchy(OWNERS)
		enable_lead_pipeline()
		self.lead = make_lead(BD, "Logged lead", mobile_no="9847012388", company_name="Logged Co")
		frappe.set_user(HUB)

	def test_log_call_moves_the_stage_and_reports_it(self):
		made = hub.log_call(self.lead, "Not answered", acting_user=BD)
		self.assertEqual(made["stage"], NOT_RESPONDING)
		self.assertEqual(frappe.db.get_value("TBO Call", made["name"], "status"), "Missed")
		made = hub.log_call(self.lead, "Not interested", "Has a vendor", acting_user=BD)
		self.assertEqual(
			(made["stage"], frappe.db.get_value("TBO Call", made["name"], "status")),
			(NOT_INTERESTED, "Answered"),
		)
		self.assertRaises(frappe.ValidationError, hub.log_call, self.lead, "Call back", acting_user=BD)

	def test_the_list_and_the_lead_carry_the_last_call(self):
		other = make_lead(BD, "Never called", company_name="Quiet Co")
		hub.log_call(self.lead, "Busy", acting_user=BD)
		rows = {row["name"]: row for row in hub.list_leads(acting_user=BD)["rows"]}
		self.assertEqual(rows[self.lead]["last_call_result"], "Busy")
		self.assertTrue(rows[self.lead]["last_call_on"])
		self.assertIsNone(rows[other]["last_call_result"])
		lead = hub.get_lead(self.lead, acting_user=BD)["lead"]
		self.assertEqual(lead["last_call_result"], "Busy")
		self.assertIn(
			NOT_RESPONDING, hub.get_lead(self.lead, acting_user=BD)["allowed_stages"] + [lead["tbo_stage"]]
		)

	def test_the_list_filters_by_last_call_and_counts_them(self):
		other = make_lead(BD, "Declined", company_name="No Co")
		hub.log_call(self.lead, "Busy", acting_user=BD)
		hub.log_call(other, "Not interested", acting_user=BD)
		both = hub.list_leads(last_call_result='["Busy", "Not interested"]', acting_user=BD)
		self.assertEqual({row["name"] for row in both["rows"]}, {self.lead, other})
		one = hub.list_leads(last_call_result="Busy", acting_user=BD)
		self.assertEqual([row["name"] for row in one["rows"]], [self.lead])
		self.assertRaises(frappe.ValidationError, hub.list_leads, last_call_result="Talked", acting_user=BD)
		counts = hub.get_category_counts(acting_user=BD)["last_call_result"]
		self.assertEqual((counts["Busy"], counts["Not interested"]), (1, 1))
