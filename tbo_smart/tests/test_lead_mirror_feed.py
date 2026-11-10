from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from tbo_smart.api import hub_lead_mirror as mirror
from tbo_smart.sales import change_log
from tbo_smart.sales.activities import add_call, add_log
from tbo_smart.test_utils import (
	commit_lead_changes,
	delete_call_batches,
	delete_calls_of,
	delete_lead_departments,
	delete_lead_meetings,
	delete_lead_quotations,
	delete_sales_records,
	disable_sales_hierarchy,
	enable_sales_hierarchy,
	get_lead_change_types,
	lead_visible_in_scope,
	make_call,
	make_call_batch,
	make_lead,
	make_lead_department,
	make_lead_meeting,
	make_lead_note,
	make_lead_quotation,
	make_lead_task,
	make_private_file,
	make_sales_hierarchy_node,
	make_user,
	reset_lead_changes,
	set_user_enabled,
)

HUB = "feed.integration@tbosmart.test"
BD = "feed.bd@tbosmart.test"
BD2 = "feed.bd2@tbosmart.test"
LEADER = "feed.leader@tbosmart.test"
HEAD = "feed.head@tbosmart.test"
MANAGER = "feed.manager@tbosmart.test"
OFF = "feed.off@tbosmart.test"
OWNERS = (BD, BD2, LEADER, HEAD, MANAGER, OFF)
PEOPLE = (BD, BD2, LEADER, HEAD, MANAGER)
GROWTHX = "TBO Test Feed GrowthX"


class TestLeadMirrorFeed(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		for user in (BD, BD2, LEADER, HEAD, OFF):
			make_user(user, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_sales_hierarchy(OWNERS)
		set_user_enabled(OFF, False)
		make_lead_department(GROWTHX, heads=[HEAD], members=[BD, BD2])
		self.mine = make_lead(BD, "Feed Mine", tbo_department=GROWTHX)
		self.theirs = make_lead(BD2, "Feed Theirs")
		reset_lead_changes()

	def tearDown(self):
		frappe.set_user("Administrator")
		set_user_enabled(OFF, True)
		self.clean()

	def clean(self):
		leads = frappe.get_all("Lead", filters={"lead_owner": ("in", OWNERS)}, pluck="name")
		delete_call_batches(OWNERS)
		delete_calls_of(OWNERS)
		delete_lead_meetings(leads)
		delete_lead_quotations(leads)
		delete_sales_records(OWNERS)
		delete_lead_departments((GROWTHX,))
		reset_lead_changes()

	# ---------- the change rows ----------

	def test_each_kind_of_change_is_logged(self):
		lead = self.mine
		actions = {
			"Lead": lambda: self.rename_company(lead),
			"Note": lambda: make_lead_note(lead, "A note"),
			"Call": lambda: make_call(BD, lead),
			"Event": lambda: add_log("Lead", lead, "meeting", "Met at the office"),
			"Follow-up": lambda: make_lead_task(lead, now_datetime(), BD),
			"File": lambda: make_private_file(
				b"x", "feed.txt", attached_to_doctype="Lead", attached_to_name=lead
			),
			"Batch": lambda: make_call_batch(BD, GROWTHX, [lead]),
			"Quotation": lambda: make_lead_quotation(lead),
			"Meeting": lambda: make_lead_meeting(lead),
		}
		frappe.set_user("Administrator")
		for kind, act in actions.items():
			with self.subTest(kind):
				reset_lead_changes()
				act()
				commit_lead_changes()
				self.assertIn(kind, get_lead_change_types(lead))

	@staticmethod
	def rename_company(lead: str) -> None:
		doc = frappe.get_doc("Lead", lead)
		doc.company_name = "Feed Co"
		doc.save(ignore_permissions=True)

	def test_a_call_logged_on_the_lead_is_a_call_change(self):
		frappe.set_user(BD)
		add_call("Lead", self.mine, "Spoke to the owner", phone="9847012345")
		frappe.set_user("Administrator")
		commit_lead_changes()
		self.assertIn("Call", get_lead_change_types(self.mine))

	def test_an_unrelated_record_logs_nothing(self):
		for values in (
			{"reference_doctype": "User", "reference_name": HUB, "comment_type": "Comment"},
			{"reference_doctype": "Lead", "reference_name": self.mine, "comment_type": "Info"},
		):
			frappe.get_doc({"doctype": "Comment", "content": "x", **values}).insert(ignore_permissions=True)
		commit_lead_changes()
		self.assertEqual(frappe.db.count("TBO Lead Change"), 0)

	def test_a_rolled_back_save_writes_nothing(self):
		commit_lead_changes()
		lead = make_lead(BD, "Feed Rolled Back")
		frappe.db.rollback()
		commit_lead_changes()
		self.assertEqual(get_lead_change_types(lead), [])

	def test_a_failed_write_never_breaks_the_save(self):
		with (
			patch.object(frappe.db, "bulk_insert", side_effect=Exception("full disk")),
			patch.object(frappe, "log_error") as log_error,
		):
			make_lead_note(self.mine, "Still saved")
			commit_lead_changes()
		log_error.assert_called_once()
		self.assertTrue(frappe.db.exists("Comment", {"reference_name": self.mine, "content": "Still saved"}))

	def test_a_deleted_lead_is_logged_as_deleted(self):
		frappe.delete_doc("Lead", self.theirs, force=True, ignore_permissions=True)
		commit_lead_changes()
		self.assertEqual(get_lead_change_types(self.theirs)[0], "Deleted")
		feed = self.feed()
		self.assertTrue(next(row for row in feed["changes"] if row["lead"] == self.theirs)["deleted"])

	def test_a_merge_logs_both_leads(self):
		frappe.rename_doc("Lead", self.theirs, self.mine, force=True, merge=True, show_alert=False)
		commit_lead_changes()
		self.assertIn("Deleted", get_lead_change_types(self.theirs))
		self.assertIn("Lead", get_lead_change_types(self.mine))

	def test_old_rows_are_pruned(self):
		old = frappe.get_doc(
			{
				"doctype": "TBO Lead Change",
				"lead": self.mine,
				"change_type": "Note",
				"at": add_days(now_datetime(), -(change_log.KEEP_DAYS + 1)),
			}
		).insert(ignore_permissions=True)
		make_lead_note(self.mine, "Recent")
		commit_lead_changes()
		change_log.prune()
		self.assertFalse(frappe.db.exists("TBO Lead Change", old.name))
		self.assertEqual(get_lead_change_types(self.mine), ["Note"])

	# ---------- the feed ----------

	def feed(self, **params):
		frappe.set_user(HUB)
		try:
			return mirror.get_lead_changes(**params)
		finally:
			frappe.set_user("Administrator")

	def test_the_feed_gives_each_lead_once_in_the_order_it_changed(self):
		make_lead_note(self.mine, "First")
		commit_lead_changes()
		make_lead_note(self.theirs, "Second")
		commit_lead_changes()
		make_lead_note(self.mine, "Third")
		commit_lead_changes()
		feed = self.feed()
		self.assertEqual([row["lead"] for row in feed["changes"]], [self.theirs, self.mine])
		self.assertFalse(feed["more"])
		self.assertEqual(feed["next_since"], feed["changes"][-1]["at"])

	def test_the_feed_pages_and_continues_from_next_since(self):
		make_lead_note(self.mine, "First")
		commit_lead_changes()
		make_lead_note(self.theirs, "Second")
		commit_lead_changes()
		first = self.feed(limit=1)
		self.assertEqual([row["lead"] for row in first["changes"]], [self.mine])
		self.assertTrue(first["more"])
		second = self.feed(since=first["next_since"], limit=1)
		self.assertEqual([row["lead"] for row in second["changes"]], [self.theirs])
		self.assertFalse(second["more"])
		self.assertEqual(self.feed(since=second["next_since"])["changes"], [])

	def test_only_the_integration_user_may_read_the_mirror_methods(self):
		calls = (
			lambda: mirror.get_lead_changes(),
			lambda: mirror.get_lead_index(),
			lambda: mirror.get_leads_bulk([self.mine]),
			lambda: mirror.get_mirror_scopes(),
		)
		for user in (BD, MANAGER, HEAD):
			frappe.set_user(user)
			for call in calls:
				with self.assertRaises(frappe.PermissionError):
					call()
		frappe.set_user("Administrator")

	# ---------- the index ----------

	def test_the_index_lists_every_lead_by_name_in_pages(self):
		frappe.set_user(HUB)
		first = mirror.get_lead_index(limit=1)
		modified, after = {}, None
		while True:
			page = mirror.get_lead_index(after=after, limit=2)
			modified.update({row["name"]: row["modified"] for row in page["leads"]})
			after = page["next_after"]
			if not page["more"]:
				break
		frappe.set_user("Administrator")
		self.assertEqual(first["total"], frappe.db.count("Lead"))
		self.assertEqual(list(modified), sorted(frappe.get_all("Lead", pluck="name")))
		self.assertEqual(modified[self.mine], frappe.db.get_value("Lead", self.mine, "modified"))

	# ---------- the bulk read ----------

	def test_bulk_gives_the_row_the_payload_and_who_sees_it(self):
		make_lead_task(self.mine, now_datetime(), BD2, status="Closed")
		make_lead_task(self.mine, now_datetime(), LEADER, status="Cancelled")
		frappe.set_user(HUB)
		answer = mirror.get_leads_bulk([self.mine, "no-such-lead", self.mine])
		frappe.set_user("Administrator")
		self.assertEqual(answer["missing"], ["no-such-lead"])
		self.assertEqual(answer["errors"], {})
		(entry,) = answer["leads"]
		self.assertEqual(entry["name"], self.mine)
		self.assertEqual(entry["owner"], BD)
		self.assertEqual(entry["department"], GROWTHX)
		self.assertEqual(entry["assignees"], [BD2])  # a closed assignment counts, a cancelled one not
		self.assertEqual(entry["row"]["lead_name"], "Feed Mine")
		self.assertEqual(entry["payload"]["lead"]["name"], self.mine)
		self.assertIn("timeline", entry["payload"])
		for flag in mirror.PERSON_FLAGS:
			self.assertNotIn(flag, entry["payload"]["lead"])
		self.assertNotIn("allowed_stages", entry["payload"])

	def test_bulk_takes_at_most_a_hundred_names(self):
		frappe.set_user(HUB)
		self.assertEqual(mirror.get_leads_bulk([f"x{i}" for i in range(mirror.MAX_BULK)])["leads"], [])
		with self.assertRaises(frappe.ValidationError):
			mirror.get_leads_bulk([f"x{i}" for i in range(mirror.MAX_BULK + 1)])
		frappe.set_user("Administrator")

	def test_bulk_reads_a_json_list_too(self):
		frappe.set_user(HUB)
		answer = mirror.get_leads_bulk(frappe.as_json([self.mine, self.theirs]))
		frappe.set_user("Administrator")
		self.assertEqual({entry["name"] for entry in answer["leads"]}, {self.mine, self.theirs})

	# ---------- who sees what ----------

	def test_scopes_and_bulk_show_each_person_what_the_erp_shows_them(self):
		enable_sales_hierarchy()
		make_sales_hierarchy_node(BD, make_sales_hierarchy_node(LEADER))
		led = make_lead(LEADER, "Feed Led")
		assigned = make_lead(BD2, "Feed Assigned")
		make_lead_task(assigned, now_datetime(), BD)
		in_department = make_lead(BD2, "Feed Dept", tbo_department=GROWTHX)
		leads = [self.mine, self.theirs, led, assigned, in_department]
		frappe.set_user(HUB)
		scopes = mirror.get_mirror_scopes()["scopes"]
		entries = {entry["name"]: entry for entry in mirror.get_leads_bulk(leads)["leads"]}
		frappe.set_user("Administrator")
		for person in PEOPLE:
			self.assertIn(person, scopes)
		self.assertNotIn(HUB, scopes)
		self.assertNotIn(OFF, scopes)
		self.assertTrue(scopes[MANAGER]["all"])
		self.assertFalse(scopes[BD]["all"])
		self.assertEqual(scopes[HEAD]["departments"], [GROWTHX])
		self.assertEqual(sorted(scopes[LEADER]["team"]), sorted([LEADER, BD]))
		seen = 0
		for person in PEOPLE:
			for lead in leads:
				with self.subTest(person=person, lead=lead):
					erp = bool(frappe.has_permission("Lead", "read", lead, user=person))
					self.assertEqual(lead_visible_in_scope(scopes[person], entries[lead]), erp)
					seen += erp
		# the rule really splits people: someone sees some leads, not all of them
		self.assertTrue(0 < seen < len(PEOPLE) * len(leads))
