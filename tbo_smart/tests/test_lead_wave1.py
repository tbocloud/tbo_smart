from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from tbo_smart.api import hub_leads as hub
from tbo_smart.api import website_lead as website
from tbo_smart.sales.activities import add_call, add_follow_up_task, add_note
from tbo_smart.sales.stages import set_lead_stage
from tbo_smart.telephony.calls import log_manual_call
from tbo_smart.test_utils import (
	age_lead_activity,
	delete_lead_departments,
	delete_leads_matching,
	delete_sales_records,
	disable_sales_hierarchy,
	make_email_communication,
	make_lead,
	make_lead_department,
	make_log_email_account,
	make_test_mobile,
	make_user,
	set_smart_lead_settings,
)

HUB = "wave1.integration@tbosmart.test"
ONE = "wave1.one@tbosmart.test"
TWO = "wave1.two@tbosmart.test"
THREE = "wave1.three@tbosmart.test"
OUTSIDER = "wave1.outsider@tbosmart.test"
MANAGER = "wave1.manager@tbosmart.test"
PEOPLE = (ONE, TWO, THREE, OUTSIDER, MANAGER)
ROUND = "TBO Test Wave1 Round"
PLAIN = "TBO Test Wave1 Plain"
DOMAIN = "@wave1.test"


def ten_digits() -> str:
	return "9" + make_test_mobile()


class Wave1Case(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		for user in (ONE, TWO, THREE, OUTSIDER):
			make_user(user, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_sales_hierarchy(PEOPLE)
		make_lead_department(ROUND, members=[ONE, TWO, THREE], assignment="Round robin")
		make_lead_department(PLAIN, members=[ONE])

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_value("User", TWO, "enabled", 1)
		set_smart_lead_settings(lead_log_email_account=None, website_lead_department=None)
		self.clean()

	def clean(self):
		delete_leads_matching(email_id=("like", f"%{DOMAIN}"))
		delete_leads_matching(tbo_department=("in", [ROUND, PLAIN]))
		delete_leads_matching(company_name=("like", "Wave1 %"))
		delete_sales_records(PEOPLE)
		frappe.db.delete("Communication", {"sender": ("like", f"%{DOMAIN}")})
		delete_lead_departments((ROUND, PLAIN))

	def owner_of(self, lead: str) -> str | None:
		return frappe.db.get_value("Lead", lead, "lead_owner")


class TestRoundRobin(Wave1Case):
	def new_lead(self, name: str, **fields) -> str:
		return make_lead(None, name, tbo_department=ROUND, **fields)

	def test_leads_go_round_the_members_in_order(self):
		owners = [self.owner_of(self.new_lead(f"Wave1 Turn {index}")) for index in range(4)]
		self.assertEqual(owners, [ONE, TWO, THREE, ONE])
		self.assertEqual(frappe.db.get_value("TBO Lead Department", ROUND, "last_assigned_user"), ONE)

	def test_the_member_is_assigned_the_lead(self):
		lead = self.new_lead("Wave1 Assigned")
		todos = frappe.get_all(
			"ToDo", filters={"reference_type": "Lead", "reference_name": lead}, pluck="allocated_to"
		)
		self.assertEqual(todos, [ONE])
		self.assertTrue(frappe.has_permission("Lead", "read", lead, user=ONE))

	def test_a_disabled_member_is_skipped(self):
		frappe.db.set_value("User", TWO, "enabled", 0)
		owners = [self.owner_of(self.new_lead(f"Wave1 Skip {index}")) for index in range(3)]
		self.assertEqual(owners, [ONE, THREE, ONE])

	def test_a_lead_with_an_owner_keeps_it_and_the_turn(self):
		self.assertEqual(self.owner_of(make_lead(THREE, "Wave1 Owned", tbo_department=ROUND)), THREE)
		self.assertIsNone(frappe.db.get_value("TBO Lead Department", ROUND, "last_assigned_user"))

	def test_a_manual_department_leaves_the_owner_empty(self):
		lead = make_lead(None, "Wave1 Manual", tbo_department=PLAIN)
		self.assertIsNone(self.owner_of(lead))
		self.assertFalse(frappe.db.exists("ToDo", {"reference_type": "Lead", "reference_name": lead}))

	def test_the_hub_create_with_no_owner_takes_the_next_member(self):
		frappe.set_user(HUB)
		lead = hub.create_lead({"first_name": "Wave1 Hub", "department": ROUND}, acting_user=MANAGER)["lead"]
		self.assertEqual(lead["lead_owner"], ONE)
		manual = hub.create_lead({"first_name": "Wave1 Hub Manual", "department": PLAIN}, acting_user=ONE)
		self.assertEqual(manual["lead"]["lead_owner"], ONE)
		named = hub.create_lead(
			{"first_name": "Wave1 Hub Named", "department": ROUND, "lead_owner": TWO}, acting_user=MANAGER
		)
		self.assertEqual(named["lead"]["lead_owner"], TWO)

	def test_the_departments_carry_their_assignment(self):
		frappe.set_user(HUB)
		rows = {row["name"]: row for row in hub.get_lead_departments(acting_user=MANAGER)}
		self.assertEqual((rows[ROUND]["assignment"], rows[PLAIN]["assignment"]), ("Round robin", "Manual"))
		options = {row["name"]: row for row in hub.get_options(acting_user=MANAGER)["departments"]}
		self.assertEqual(options[ROUND]["assignment"], "Round robin")
		self.assertEqual(options[ROUND]["stale_after_days"], 0)
		self.assertEqual(rows[ROUND]["stale_after_days"], 0)


class TestDuplicates(Wave1Case):
	def test_matches_by_email_phone_and_company(self):
		phone, email = ten_digits(), f"Dup.A{DOMAIN}"
		lead = make_lead(
			ONE, "Wave1 Dup", email_id=email, mobile_no=f"+91 {phone}", company_name="Wave1 Acme Ltd"
		)
		frappe.set_user(HUB)
		by_email = hub.find_duplicate_leads(email=email.upper(), acting_user=MANAGER)
		self.assertEqual([(row["name"], row["matched_on"]) for row in by_email], [(lead, ["email"])])
		by_phone = hub.find_duplicate_leads(phone=f"0{phone[:5]}-{phone[5:]}", acting_user=MANAGER)
		self.assertEqual(by_phone[0]["matched_on"], ["phone"])
		by_company = hub.find_duplicate_leads(company="  wave1 ACME ltd ", acting_user=MANAGER)
		self.assertEqual(by_company[0]["matched_on"], ["company"])
		both = hub.find_duplicate_leads(email=email, company="Wave1 Acme Ltd", acting_user=MANAGER)
		self.assertEqual(both[0]["matched_on"], ["email", "company"])
		self.assertEqual(hub.find_duplicate_leads(email="nobody" + DOMAIN, acting_user=MANAGER), [])
		self.assertEqual(hub.find_duplicate_leads(acting_user=MANAGER), [])
		self.assertEqual(hub.find_duplicate_leads(email=email, exclude=lead, acting_user=MANAGER), [])

	def test_it_shows_leads_the_acting_user_cannot_open(self):
		email = f"Dup.Hidden{DOMAIN}"
		lead = make_lead(ONE, "Wave1 Hidden", email_id=email)
		self.assertFalse(frappe.has_permission("Lead", "read", lead, user=OUTSIDER))
		frappe.set_user(HUB)
		[match] = hub.find_duplicate_leads(email=email, acting_user=OUTSIDER)
		self.assertEqual(match["name"], lead)
		self.assertEqual(match["lead_owner"], ONE)
		self.assertEqual(match["owner_name"], frappe.utils.get_fullname(ONE))
		self.assertEqual(
			set(match),
			{
				"name",
				"lead_name",
				"company_name",
				"email_id",
				"mobile_no",
				"lead_owner",
				"owner_name",
				"tbo_stage",
				"matched_on",
			},
		)

	def test_at_most_five_come_back(self):
		for index in range(6):
			make_lead(ONE, f"Wave1 Many {index}", company_name="Wave1 Crowd")
		frappe.set_user(HUB)
		self.assertEqual(len(hub.find_duplicate_leads(company="Wave1 Crowd", acting_user=MANAGER)), 5)

	def test_create_lead_refuses_an_email_or_phone_match(self):
		phone = ten_digits()
		lead = make_lead(ONE, "Wave1 Taken", email_id=f"taken{DOMAIN}", mobile_no=phone)
		frappe.set_user(HUB)
		for data in ({"email_id": f"TAKEN{DOMAIN}"}, {"mobile_no": f"+91{phone}"}):
			with self.assertRaises(frappe.DuplicateEntryError) as raised:
				hub.create_lead({"first_name": "Wave1 Again", **data}, acting_user=MANAGER)
			self.assertIn(
				f"Possible duplicate of {lead} ({frappe.utils.get_fullname(ONE)})", str(raised.exception)
			)
		created = hub.create_lead(
			{"first_name": "Wave1 Again", "mobile_no": phone}, allow_duplicate=1, acting_user=MANAGER
		)
		self.assertNotEqual(created["lead"]["name"], lead)

	def test_a_company_match_alone_does_not_block(self):
		make_lead(ONE, "Wave1 Company Owner", company_name="Wave1 Shared Co")
		frappe.set_user(HUB)
		created = hub.create_lead(
			{"first_name": "Wave1 Colleague", "company_name": "Wave1 Shared Co"}, acting_user=MANAGER
		)
		self.assertTrue(created["lead"]["name"])

	def test_data_import_skips_a_duplicate(self):
		phone = ten_digits()
		make_lead(ONE, "Wave1 Imported Before", mobile_no=phone)
		values = {"doctype": "Lead", "first_name": "Wave1 Imported", "mobile_no": phone}
		frappe.flags.in_import = True
		try:
			self.assertRaises(
				frappe.DuplicateEntryError, frappe.get_doc(values).insert, ignore_permissions=True
			)
			frappe.flags.tbo_cutover = True
			frappe.get_doc(values).insert(ignore_permissions=True)
		finally:
			frappe.flags.in_import = False
			frappe.flags.tbo_cutover = False


class TestStaleLeads(Wave1Case):
	def stale(self, user: str = MANAGER, days: int = 3) -> dict:
		frappe.set_user(HUB)
		try:
			return {row["name"]: row for row in hub.get_stale_leads(days, acting_user=user)}
		finally:
			frappe.set_user("Administrator")

	def test_a_quiet_lead_is_listed_with_its_idle_days(self):
		quiet = make_lead(ONE, "Wave1 Quiet", company_name="Wave1 Quiet Co")
		fresh = make_lead(ONE, "Wave1 Fresh")
		age_lead_activity(quiet, 5)
		rows = self.stale()
		self.assertNotIn(fresh, rows)
		self.assertEqual(rows[quiet]["idle_days"], 5)
		self.assertEqual(rows[quiet]["lead_owner"], ONE)
		self.assertEqual(rows[quiet]["company_name"], "Wave1 Quiet Co")
		self.assertTrue(rows[quiet]["last_activity_on"])
		self.assertIn(quiet, self.stale(days=5))
		self.assertNotIn(quiet, self.stale(days=6))

	def test_every_kind_of_activity_ends_the_silence(self):
		activities = {
			"note": lambda lead: add_note("Lead", lead, "Spoke to them"),
			"call event": lambda lead: add_call("Lead", lead, "Rang", "9999999999"),
			"todo": lambda lead: add_follow_up_task("Lead", lead, add_days(now_datetime(), 2)),
			"tbo call": lambda lead: log_manual_call(lead, "No answer"),
			"stage change": lambda lead: set_lead_stage(lead, "Contacted"),
			"email": lambda lead: make_email_communication(f"client{DOMAIN}", ONE).db_set(
				{"reference_doctype": "Lead", "reference_name": lead}
			),
		}
		for label, act in activities.items():
			with self.subTest(label):
				lead = make_lead(ONE, f"Wave1 Activity {label}")
				age_lead_activity(lead, 6)
				self.assertIn(lead, self.stale())
				act(lead)
				self.assertNotIn(lead, self.stale())

	def test_closed_and_on_hold_leads_are_not_stale(self):
		for stage in ("On Hold", "Closed – Won", "Closed – Lost"):
			with self.subTest(stage):
				lead = make_lead(ONE, f"Wave1 {stage}")
				frappe.db.set_value("Lead", lead, "tbo_stage", stage)
				age_lead_activity(lead, 9)
				self.assertNotIn(lead, self.stale())

	def test_a_departments_own_days_replace_the_default(self):
		frappe.db.set_value("TBO Lead Department", ROUND, "stale_after_days", 7)
		inside = make_lead(ONE, "Wave1 Dept Quiet", tbo_department=ROUND)
		outside = make_lead(ONE, "Wave1 Plain Quiet")
		age_lead_activity(inside, 5)
		age_lead_activity(outside, 5)
		rows = self.stale()
		self.assertNotIn(inside, rows)
		self.assertIn(outside, rows)
		age_lead_activity(inside, 8)
		self.assertIn(inside, self.stale())

	def test_it_follows_the_acting_users_visibility(self):
		lead = make_lead(ONE, "Wave1 Visible")
		age_lead_activity(lead, 5)
		self.assertIn(lead, self.stale(ONE))
		self.assertIn(lead, self.stale(MANAGER))
		self.assertNotIn(lead, self.stale(OUTSIDER))

	def test_the_longest_quiet_comes_first(self):
		newer, older = make_lead(ONE, "Wave1 Newer"), make_lead(ONE, "Wave1 Older")
		age_lead_activity(newer, 4)
		age_lead_activity(older, 9)
		frappe.set_user(HUB)
		names = [row["name"] for row in hub.get_stale_leads(3, acting_user=ONE)]
		self.assertLess(names.index(older), names.index(newer))


class TestEmailLogging(Wave1Case):
	def setUp(self):
		super().setUp()
		self.account = make_log_email_account()
		set_smart_lead_settings(lead_log_email_account=self.account)

	def linked(self, communication) -> tuple:
		return frappe.db.get_value(
			"Communication", communication.name, ["reference_doctype", "reference_name"]
		)

	def test_a_mail_from_the_client_lands_on_their_lead(self):
		lead = make_lead(ONE, "Wave1 Mail", email_id=f"client{DOMAIN}")
		mail = make_email_communication(f"Client <client{DOMAIN}>", self.account, self.account)
		self.assertEqual(self.linked(mail), ("Lead", lead))

	def test_a_bcc_of_our_own_mail_lands_on_the_lead_of_the_recipient(self):
		lead = make_lead(ONE, "Wave1 Sent", email_id=f"buyer{DOMAIN}")
		mail = make_email_communication(ONE, f"buyer{DOMAIN}, {self.account}", self.account)
		self.assertEqual(self.linked(mail), ("Lead", lead))

	def test_the_newest_open_lead_wins_and_closed_ones_are_ignored(self):
		older = make_lead(ONE, "Wave1 Older Mail", email_id=f"older{DOMAIN}")
		newer = make_lead(ONE, "Wave1 Newer Mail", email_id=f"newer{DOMAIN}")
		closed = make_lead(ONE, "Wave1 Closed Mail", email_id=f"closed{DOMAIN}")
		frappe.db.set_value("Lead", older, "modified", add_days(now_datetime(), -3), update_modified=False)
		frappe.db.set_value("Lead", closed, "tbo_stage", "Closed – Lost")
		mail = make_email_communication(
			ONE, f"older{DOMAIN}", self.account, cc=f"closed{DOMAIN}, newer{DOMAIN}"
		)
		self.assertEqual(self.linked(mail), ("Lead", newer))
		only_closed = make_email_communication(ONE, f"closed{DOMAIN}", self.account)
		self.assertEqual(self.linked(only_closed), (None, None))

	def test_no_match_other_mailboxes_and_the_setting_off_leave_it_alone(self):
		make_lead(ONE, "Wave1 Quiet Mail", email_id=f"quiet{DOMAIN}")
		unmatched = make_email_communication(f"stranger{DOMAIN}", self.account, self.account)
		self.assertEqual(self.linked(unmatched), (None, None))
		other = make_email_communication(
			f"quiet{DOMAIN}", self.account, make_log_email_account("wave1.other@tbosmart.test")
		)
		self.assertEqual(self.linked(other), (None, None))
		set_smart_lead_settings(lead_log_email_account=None)
		off = make_email_communication(f"quiet{DOMAIN}", self.account, self.account)
		self.assertEqual(self.linked(off), (None, None))


class TestWebsiteLeadForm(Wave1Case):
	def setUp(self):
		super().setUp()
		set_smart_lead_settings(website_lead_department=ROUND)

	def submit(self, **values) -> dict:
		frappe.set_user("Guest")
		try:
			return website.submit_website_lead(**values)
		finally:
			frappe.set_user("Administrator")

	def lead_by_email(self, email: str) -> str | None:
		return frappe.db.get_value("Lead", {"email_id": email}, "name")

	def test_the_endpoint_is_public_post_only_and_rate_limited(self):
		function = website.submit_website_lead
		self.assertIn(function, frappe.guest_methods)
		self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func[function], ["POST"])
		self.assertTrue(hasattr(function, "__wrapped__"))

	def test_an_enquiry_becomes_an_assigned_lead_and_leaks_nothing(self):
		email = f"visitor{DOMAIN}"
		result = self.submit(
			name="Asha Visitor",
			email=email,
			phone="+91 98470 12345",
			company="Wave1 Visitor Co",
			message="Need an ERP",
		)
		self.assertEqual(result, {"ok": True})
		lead = frappe.get_doc("Lead", self.lead_by_email(email))
		self.assertEqual((lead.first_name, lead.last_name), ("Asha", "Visitor"))
		self.assertEqual(lead.source, "Website")
		self.assertEqual(lead.tbo_department, ROUND)
		self.assertEqual(lead.lead_owner, ONE)
		self.assertTrue(frappe.db.exists("ToDo", {"reference_name": lead.name, "allocated_to": ONE}))
		notes = frappe.get_all(
			"Comment", filters={"reference_name": lead.name, "comment_type": "Comment"}, pluck="content"
		)
		self.assertTrue(any("Need an ERP" in note for note in notes))

	def test_the_honeypot_drops_the_enquiry_silently(self):
		email = f"bot{DOMAIN}"
		self.assertEqual(self.submit(name="Bot", email=email, hp="http://spam"), {"ok": True})
		self.assertIsNone(self.lead_by_email(email))

	def test_a_name_and_an_email_or_phone_are_required_and_the_email_checked(self):
		for values in (
			{"email": f"x{DOMAIN}"},
			{"name": "No Contact"},
			{"name": "Bad Email", "email": "not-an-email"},
			{"name": "Short Phone", "phone": "123"},
		):
			with self.subTest(values):
				self.assertRaises(frappe.ValidationError, self.submit, **values)

	def test_the_department_asked_for_must_be_active_else_the_default(self):
		first, second = f"dept.one{DOMAIN}", f"dept.two{DOMAIN}"
		self.submit(name="Plain Asked", email=first, department=PLAIN)
		self.assertEqual(frappe.db.get_value("Lead", {"email_id": first}, "tbo_department"), PLAIN)
		frappe.db.set_value("TBO Lead Department", PLAIN, "is_active", 0)
		self.submit(name="Off Asked", email=second, department=PLAIN)
		self.assertEqual(frappe.db.get_value("Lead", {"email_id": second}, "tbo_department"), ROUND)

	def test_the_same_person_again_adds_a_note_and_tells_the_owner(self):
		email = f"again{DOMAIN}"
		lead = make_lead(TWO, "Wave1 Again Lead", email_id=email)
		with patch("tbo_smart.api.website_lead.notify") as notify:
			result = self.submit(name="Again Person", email=email, message="Still interested")
		self.assertEqual(result, {"ok": True})
		self.assertEqual(frappe.db.count("Lead", {"email_id": email}), 1)
		notes = frappe.get_all(
			"Comment", filters={"reference_name": lead, "comment_type": "Comment"}, pluck="content"
		)
		self.assertTrue(any("Website enquiry again: Still interested" in note for note in notes))
		self.assertEqual(notify.call_args.args[0], [TWO])
		self.assertEqual(notify.call_args.args[2:], ("Lead", lead))

	def test_the_form_field_names_of_the_hub_work_too(self):
		email = f"alias{DOMAIN}"
		result = self.submit(
			lead_name="Alias Person",
			email_id=email,
			mobile_no="+91 98470 54321",
			company_name="Wave1 Alias Co",
		)
		self.assertEqual(result, {"ok": True})
		lead = frappe.get_doc("Lead", self.lead_by_email(email))
		self.assertEqual((lead.first_name, lead.company_name), ("Alias", "Wave1 Alias Co"))
		self.assertEqual(lead.mobile_no, "+91 98470 54321")

	def test_a_known_phone_is_a_duplicate_too(self):
		phone = ten_digits()
		lead = make_lead(TWO, "Wave1 Phone Lead", mobile_no=phone)
		with patch("tbo_smart.api.website_lead.notify"):
			self.submit(name="Phone Person", phone=f"+91 {phone}")
		self.assertEqual(frappe.db.count("Lead", {"mobile_no": ("like", f"%{phone[-9:]}")}), 1)
		self.assertTrue(
			frappe.db.exists("Comment", {"reference_name": lead, "content": ("like", "%enquiry again%")})
		)
