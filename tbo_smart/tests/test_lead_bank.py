import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_months, get_first_day, getdate, today

from tbo_smart.api import hub_commission, hub_lead_bank, hub_leads
from tbo_smart.lead_bank import REGIONS, cap, detect_region, rate, referral
from tbo_smart.lead_pipeline import commission
from tbo_smart.test_utils import (
	LEAD_BANK_EMAIL_DOMAIN,
	delete_lead_bank_records,
	delete_lead_departments,
	make_collected_lead,
	make_lead,
	make_lead_bank_form,
	make_lead_bank_photos,
	make_lead_department,
	make_lead_receipt,
	make_management_user,
	make_project,
	make_system_user,
	make_user,
	reset_referral_settings,
	set_lead_stage_directly,
	set_referral_settings,
)

HUB = "lb.integration@tbosmart.test"
DESIGNER = "lb.designer@tbosmart.test"
DESIGNER2 = "lb.designer2@tbosmart.test"
SELLER = "lb.seller@tbosmart.test"
MANAGER = "lb.manager@tbosmart.test"
BOSS = "lb.management@tbosmart.test"
DEPARTMENT = "TBO Lead Bank Dept"
WON, NEGOTIATION = "Closed – Won", "Negotiation"


def this_month():
	return getdate(get_first_day(today()))


class TestRegionDetection(FrappeTestCase):
	def test_the_phone_prefix_names_the_region(self):
		for phone, region in (
			("+91 98470 12345", "India"),
			("+971 50 123 4567", "UAE"),
			("00966512345678", "KSA"),
			("971501234567", "UAE"),
			("966512345678", "KSA"),
			("919847012345", "India"),
			("9847012345", "India"),
		):
			self.assertEqual(detect_region([phone]), {"region": region, "by": "phone"}, phone)

	def test_a_local_number_with_a_leading_zero_is_left_to_the_gps(self):
		self.assertEqual(detect_region(["0501234567"])["region"], None)
		self.assertEqual(detect_region(["0501234567"], 25.2, 55.3), {"region": "UAE", "by": "gps"})

	def test_the_phone_wins_over_the_gps(self):
		self.assertEqual(detect_region(["+919847012345"], 25.2, 55.3)["region"], "India")

	def test_gps_alone_names_the_region(self):
		self.assertEqual(detect_region([], 9.5, 76.5)["region"], "India")
		self.assertEqual(detect_region([], 24.7, 46.7)["region"], "KSA")
		self.assertEqual(detect_region([], 25.2, 55.3)["region"], "UAE")
		self.assertEqual(detect_region([], 51.5, -0.1)["region"], None)
		self.assertEqual(detect_region([])["region"], None)


class LeadBankCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		for user in (DESIGNER, DESIGNER2):
			make_system_user(user)
		make_user(SELLER, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])
		make_management_user(BOSS)
		frappe.db.commit()  # nosemgrep

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		delete_lead_bank_records()
		delete_lead_departments([DEPARTMENT])
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		delete_lead_bank_records()
		delete_lead_departments([DEPARTMENT])
		make_lead_department(DEPARTMENT, heads=[MANAGER], members=[SELLER])
		reset_referral_settings(DEPARTMENT)
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def submit(self, user=DESIGNER, photos=1, **fields):
		return hub_lead_bank.submit_lead(
			make_lead_bank_form(**fields), make_lead_bank_photos(photos), acting_user=user
		)


class TestAddALead(LeadBankCase):
	def test_a_staff_member_with_no_sales_role_adds_a_lead(self):
		form = make_lead_bank_form(state="kerala", latitude=9.5091, longitude=76.5302, note="Owner is keen")
		answer = hub_lead_bank.submit_lead(form, make_lead_bank_photos(2), acting_user=DESIGNER)
		self.assertTrue(answer["created"])
		self.assertEqual(
			(answer["region"], answer["department"], answer["in_pool"]), ("India", DEPARTMENT, False)
		)
		lead = frappe.get_doc("Lead", answer["lead"])
		self.assertEqual(lead.tbo_collected_by, DESIGNER)
		self.assertEqual(lead.tbo_collected_region, "India")
		self.assertEqual((lead.tbo_department, lead.tbo_stage), (DEPARTMENT, "New Lead"))
		self.assertEqual((lead.first_name, lead.last_name), ("Ravi", "Kumar"))
		self.assertEqual((lead.state, lead.country), ("Kerala", "India"))
		self.assertEqual(lead.get("source") or lead.get("utm_source"), "Staff referral")
		self.assertEqual(lead.tbo_business_type, "Hotels & Restaurants")
		self.assertEqual(lead.tbo_address, "MG Road, Kottayam")
		self.assertAlmostEqual(lead.tbo_latitude, 9.5091, places=4)
		self.assertIn("google.com/maps", lead.tbo_maps_url)
		photos = frappe.get_all("File", filters={"attached_to_name": lead.name, "tbo_lead_photo": 1})
		self.assertEqual(len(photos), 2)
		self.assertTrue(
			frappe.db.exists("Comment", {"reference_name": lead.name, "content": ("like", "%Owner is keen%")})
		)

	def test_the_business_type_fills_the_industry_when_it_is_one(self):
		answer = self.submit(business_type="Hotels & Restaurants")
		self.assertEqual(frappe.db.get_value("Lead", answer["lead"], "industry"), "Hotels & Restaurants")
		other = self.submit(business_type="Wedding photographer")
		self.assertIsNone(frappe.db.get_value("Lead", other["lead"], "industry"))
		self.assertEqual(
			frappe.db.get_value("Lead", other["lead"], "tbo_business_type"), "Wedding photographer"
		)

	def test_the_region_comes_from_the_phone_then_the_gps_and_can_be_changed(self):
		uae = self.submit(mobile_no="+971501234567", email_id=f"uae1@{LEAD_BANK_EMAIL_DOMAIN}")
		self.assertEqual(uae["region"], "UAE")
		self.assertEqual(frappe.db.get_value("Lead", uae["lead"], "country"), "United Arab Emirates")
		gps = self.submit(
			mobile_no="0501234568", email_id=f"ksa1@{LEAD_BANK_EMAIL_DOMAIN}", latitude=24.7, longitude=46.7
		)
		self.assertEqual(gps["region"], "KSA")
		chosen = self.submit(region="KSA")
		self.assertEqual(chosen["region"], "KSA")
		nothing = {"mobile_no": "0501234569", "email_id": f"none1@{LEAD_BANK_EMAIL_DOMAIN}"}
		self.assertRaises(
			frappe.ValidationError,
			hub_lead_bank.submit_lead,
			make_lead_bank_form(**nothing),
			make_lead_bank_photos(),
			acting_user=DESIGNER,
		)

	def test_a_region_without_a_department_puts_the_lead_in_the_pool_and_tells_managers(self):
		answer = self.submit(mobile_no="+966512345678", email_id=f"pool1@{LEAD_BANK_EMAIL_DOMAIN}")
		self.assertEqual((answer["region"], answer["department"], answer["in_pool"]), ("KSA", None, True))
		lead = frappe.get_doc("Lead", answer["lead"])
		self.assertFalse(lead.lead_owner)
		self.assertFalse(lead.tbo_department)
		for manager in (MANAGER, BOSS):
			self.assertTrue(
				frappe.db.exists(
					"Notification Log",
					{
						"for_user": manager,
						"document_name": answer["lead"],
						"subject": ("like", "A Lead Bank lead%"),
					},
				),
				manager,
			)

	def test_a_department_set_for_the_region_takes_the_lead(self):
		set_referral_settings(lead_bank_department_uae=DEPARTMENT)
		answer = self.submit(mobile_no="+971501234500", email_id=f"uae2@{LEAD_BANK_EMAIL_DOMAIN}")
		self.assertEqual((answer["department"], answer["in_pool"]), (DEPARTMENT, False))
		self.assertEqual(frappe.db.get_value("Lead", answer["lead"], "tbo_department"), DEPARTMENT)

	def test_a_photo_and_a_way_to_reach_them_are_needed(self):
		form = make_lead_bank_form()
		self.assertRaises(frappe.ValidationError, hub_lead_bank.submit_lead, form, [], acting_user=DESIGNER)
		for field in ("mobile_no", "email_id"):
			form.pop(field)
		self.assertRaises(
			frappe.ValidationError,
			hub_lead_bank.submit_lead,
			form,
			make_lead_bank_photos(),
			acting_user=DESIGNER,
		)
		self.assertRaises(
			frappe.ValidationError,
			hub_lead_bank.submit_lead,
			make_lead_bank_form(company_name="", contact_name=""),
			make_lead_bank_photos(),
			acting_user=DESIGNER,
		)

	def test_unknown_fields_and_a_bad_photo_are_refused(self):
		self.assertRaises(
			frappe.ValidationError,
			hub_lead_bank.submit_lead,
			make_lead_bank_form(lead_owner=SELLER),
			make_lead_bank_photos(),
			acting_user=DESIGNER,
		)
		self.assertRaises(
			frappe.ValidationError,
			hub_lead_bank.submit_lead,
			make_lead_bank_form(),
			[{"filename": "x.jpg", "content": "bm90IGEgcGhvdG8="}],
			acting_user=DESIGNER,
		)
		self.assertRaises(
			frappe.ValidationError,
			hub_lead_bank.submit_lead,
			make_lead_bank_form(),
			make_lead_bank_photos(6),
			acting_user=DESIGNER,
		)

	def test_a_user_who_is_not_on_the_site_cannot_add_a_lead(self):
		for user in ("nobody.lb@tbosmart.test", "Guest"):
			self.assertRaises(
				frappe.PermissionError,
				hub_lead_bank.submit_lead,
				make_lead_bank_form(),
				make_lead_bank_photos(),
				acting_user=user,
			)
		self.assertRaises(
			frappe.PermissionError, hub_lead_bank.submit_lead, make_lead_bank_form(), make_lead_bank_photos()
		)


class TestDuplicates(LeadBankCase):
	def test_the_first_collector_keeps_the_credit_and_a_repeat_is_a_comment(self):
		form = make_lead_bank_form()
		first = hub_lead_bank.submit_lead(form, make_lead_bank_photos(), acting_user=DESIGNER)
		again = hub_lead_bank.submit_lead(
			{**form, "note": "Saw their new board"}, make_lead_bank_photos(), acting_user=DESIGNER2
		)
		self.assertFalse(again["created"])
		self.assertTrue(again["duplicate"])
		self.assertIn("already in our leads", again["message"])
		self.assertIsNone(again["lead"])
		self.assertEqual(frappe.db.get_value("Lead", first["lead"], "tbo_collected_by"), DESIGNER)
		self.assertEqual(frappe.db.count("Lead", {"email_id": form["email_id"]}), 1)
		comments = frappe.get_all(
			"Comment", filters={"reference_name": first["lead"], "comment_type": "Comment"}, pluck="content"
		)
		self.assertTrue(any("also found this business" in text and "new board" in text for text in comments))
		self.assertEqual(frappe.db.count("File", {"attached_to_name": first["lead"]}), 1)

	def test_the_same_collector_is_told_they_added_it(self):
		form = make_lead_bank_form()
		hub_lead_bank.submit_lead(form, make_lead_bank_photos(), acting_user=DESIGNER)
		again = hub_lead_bank.submit_lead(form, make_lead_bank_photos(), acting_user=DESIGNER)
		self.assertTrue(again["mine"])
		self.assertIn("You already added", again["message"])

	def test_a_lead_the_team_already_has_is_a_duplicate_by_phone_email_or_company(self):
		lead = make_lead(
			SELLER,
			"Existing",
			mobile_no="+919847000111",
			company_name="Old Shop Ltd",
			email_id=f"old@{LEAD_BANK_EMAIL_DOMAIN}",
		)
		answer = hub_lead_bank.check_duplicate(phone="098470 00111", acting_user=DESIGNER)
		self.assertEqual(set(answer), {"duplicate", "mine", "message"})
		self.assertTrue(answer["duplicate"])
		self.assertTrue(
			hub_lead_bank.check_duplicate(company="old shop ltd", acting_user=DESIGNER)["duplicate"]
		)
		self.assertTrue(
			hub_lead_bank.check_duplicate(email=f"OLD@{LEAD_BANK_EMAIL_DOMAIN}", acting_user=DESIGNER)[
				"duplicate"
			]
		)
		self.assertFalse(
			hub_lead_bank.check_duplicate(email="fresh@x.test", acting_user=DESIGNER)["duplicate"]
		)
		again = self.submit(mobile_no="+919847000111", email_id="")
		self.assertTrue(again["duplicate"])
		self.assertIsNone(frappe.db.get_value("Lead", lead, "tbo_collected_by"))


class TestMyLeads(LeadBankCase):
	def test_a_collector_sees_only_their_own_leads_and_no_crm(self):
		mine = self.submit(DESIGNER)["lead"]
		theirs = self.submit(DESIGNER2)["lead"]
		rows = hub_lead_bank.get_my_leads(acting_user=DESIGNER)
		self.assertEqual([row["id"] for row in rows["rows"]], [mine])
		self.assertNotIn(theirs, str(rows))
		self.assertEqual(rows["summary"]["leads"], 1)
		self.assertEqual(
			set(rows["rows"][0]),
			{"id", "title", "collected_on", "region", "status", "earned", "pending"},
		)
		self.assertEqual(rows["rows"][0]["status"], "New")
		self.assertRaises(frappe.PermissionError, hub_leads.list_leads, acting_user=DESIGNER)
		self.assertRaises(frappe.PermissionError, hub_leads.get_lead, mine, acting_user=DESIGNER)

	def test_the_simple_status_has_five_words(self):
		lead = self.submit(DESIGNER)["lead"]
		seen = {}
		for stage in (
			"New Lead",
			"Contacted",
			"Not Responding",
			"Meeting Scheduled",
			"Proposal Sent",
			WON,
			"Closed – Lost",
			"Not Interested",
			"Wrong Number",
		):
			set_lead_stage_directly(lead, stage)
			seen[stage] = hub_lead_bank.get_my_leads(acting_user=DESIGNER)["rows"][0]["status"]
		self.assertEqual(
			seen,
			{
				"New Lead": "New",
				"Contacted": "Being called",
				"Not Responding": "Being called",
				"Meeting Scheduled": "Meeting",
				"Proposal Sent": "Meeting",
				WON: "Won",
				"Not Interested": "Not interested",
				"Wrong Number": "Not interested",
			},
		)

	def test_the_dashboard_and_credit_are_for_managers_only(self):
		self.assertRaises(frappe.PermissionError, hub_lead_bank.get_dashboard, acting_user=DESIGNER)
		lead = self.submit(DESIGNER)["lead"]
		self.assertRaises(
			frappe.PermissionError,
			hub_lead_bank.reassign_credit,
			lead,
			DESIGNER2,
			"Because",
			acting_user=DESIGNER,
		)
		self.assertRaises(frappe.PermissionError, hub_commission.get_referral_settings, acting_user=DESIGNER)
		self.assertTrue(hub_lead_bank.get_options(acting_user=MANAGER)["is_manager"])
		self.assertFalse(hub_lead_bank.get_options(acting_user=DESIGNER)["is_manager"])

	def test_options_list_the_regions_with_their_rates(self):
		options = hub_lead_bank.get_options(acting_user=DESIGNER)
		self.assertEqual([r["region"] for r in options["regions"]], list(REGIONS))
		self.assertEqual([r["rate"] for r in options["regions"]], [2, 3, 3])
		self.assertEqual([r["has_department"] for r in options["regions"]], [True, False, False])
		self.assertEqual(options["cap"], 25000)


class TestDashboard(LeadBankCase):
	def test_the_month_by_region_and_collector_with_conversion(self):
		a = self.submit(DESIGNER)["lead"]
		self.submit(DESIGNER)
		self.submit(DESIGNER2, mobile_no="+971501230000", email_id=f"d1@{LEAD_BANK_EMAIL_DOMAIN}")
		self.submit(DESIGNER, mobile_no="+966501230000", email_id=f"d2@{LEAD_BANK_EMAIL_DOMAIN}")
		set_lead_stage_directly(a, WON)
		board = hub_lead_bank.get_dashboard(str(this_month()), acting_user=BOSS)
		self.assertEqual((board["collected"], board["won"], board["conversion"]), (4, 1, 25.0))
		regions = {row["region"]: row for row in board["by_region"]}
		self.assertEqual((regions["India"]["collected"], regions["India"]["won"]), (2, 1))
		self.assertEqual(regions["UAE"]["collected"], 1)
		self.assertEqual(regions["KSA"]["collected"], 1)
		self.assertEqual(regions["India"]["department"], DEPARTMENT)
		top = board["collectors"]
		self.assertEqual(
			[(row["user"], row["collected"], row["won"]) for row in top],
			[(DESIGNER, 3, 1), (DESIGNER2, 1, 0)],
		)
		self.assertEqual(board["pool"]["count"], 2)

	def test_a_manager_moves_the_credit_with_a_reason(self):
		lead = self.submit(DESIGNER)["lead"]
		self.assertRaises(
			frappe.ValidationError, hub_lead_bank.reassign_credit, lead, DESIGNER2, " ", acting_user=MANAGER
		)
		hub_lead_bank.reassign_credit(lead, DESIGNER2, "Designer 2 found it first", acting_user=MANAGER)
		self.assertEqual(frappe.db.get_value("Lead", lead, "tbo_collected_by"), DESIGNER2)
		self.assertTrue(
			frappe.db.exists("Comment", {"reference_name": lead, "content": ("like", "%found it first%")})
		)
		self.assertEqual(hub_lead_bank.get_my_leads(acting_user=DESIGNER)["total"], 0)
		self.assertEqual(hub_lead_bank.get_my_leads(acting_user=DESIGNER2)["total"], 1)
		self.assertRaises(
			frappe.ValidationError,
			hub_lead_bank.reassign_credit,
			lead,
			DESIGNER2,
			"Same",
			acting_user=MANAGER,
		)


class TestReferralSettings(LeadBankCase):
	def test_managers_set_the_rates_the_cap_and_the_departments(self):
		saved = hub_commission.save_referral_settings(
			{
				"regions": [
					{"region": "India", "rate": 2.5, "department": DEPARTMENT},
					{"region": "UAE", "rate": 4, "department": DEPARTMENT},
				],
				"cap": 10000,
			},
			acting_user=BOSS,
		)
		regions = {row["region"]: row for row in saved["regions"]}
		self.assertEqual(
			(regions["India"]["rate"], regions["UAE"]["rate"], regions["KSA"]["rate"]), (2.5, 4, 3)
		)
		self.assertEqual(regions["UAE"]["effective_department"], DEPARTMENT)
		self.assertEqual(regions["KSA"]["effective_department"], None)
		self.assertEqual((saved["cap"], rate("India"), cap()), (10000, 2.5, 10000))

	def test_bad_settings_are_refused(self):
		for values in (
			{"regions": [{"region": "India", "rate": 101}]},
			{"regions": [{"region": "Mars", "rate": 1}]},
			{"regions": [{"region": "India", "department": "No such department"}]},
			{"cap": -1},
		):
			self.assertRaises(
				frappe.ValidationError, hub_commission.save_referral_settings, values, acting_user=BOSS
			)

	def test_the_defaults_are_india_2_uae_and_ksa_3_and_25000(self):
		reset_referral_settings()
		self.assertEqual([rate(region) for region in REGIONS], [2, 3, 3])
		self.assertEqual(cap(), 25000)


class TestReferralCommission(LeadBankCase):
	def setUp(self):
		super().setUp()
		self.project = make_project(f"TBO Referral {frappe.generate_hash(length=6)}")

	def pay(self, lead, amount):
		frappe.set_user("Administrator")
		entry = make_lead_receipt(lead, amount, self.project)
		frappe.set_user(HUB)
		return entry

	def row(self, user, as_user=BOSS):
		month = hub_commission.get_commission(str(this_month()), acting_user=as_user)
		return next((r for r in month["rows"] if r["user"] == user), None)

	def referral(self, user):
		"""What the user earns in referral this month: 0 when they have no row."""
		row = self.row(user)
		return row["referral"] if row else 0

	def won(self, collector=DESIGNER, region="India", owner=None):
		lead = make_collected_lead(collector, region, owner=owner)
		set_lead_stage_directly(lead, WON)
		return lead

	def test_the_collector_earns_the_regions_percent_of_the_money_collected(self):
		india, uae, ksa = self.won(), self.won(region="UAE"), self.won(region="KSA")
		for lead in (india, uae, ksa):
			self.pay(lead, 10000)
		row = self.row(DESIGNER)
		self.assertEqual(row["commission"], 200 + 300 + 300)
		self.assertEqual(row["referral"], 800)
		self.assertEqual(row["collected"], 0)
		self.assertIn("Referral (Lead Bank)", row["plans"])
		lines = {line["lead"]: line for line in row["projects"]}
		self.assertEqual({line["kind"] for line in lines.values()}, {"Referral"})
		self.assertEqual((lines[india]["collected"], lines[india]["commission"]), (10000, 200))
		self.assertEqual(lines[uae]["plan"], "UAE 3%")

	def test_the_cap_limits_one_lead_and_holds_across_receipts(self):
		lead = self.won()
		self.pay(lead, 1000000)
		self.assertEqual(self.row(DESIGNER)["commission"], 20000)
		self.pay(lead, 1000000)
		self.assertEqual(self.row(DESIGNER)["commission"], 25000)
		set_referral_settings(referral_cap=0)
		self.assertEqual(self.row(DESIGNER)["commission"], 40000)

	def test_nothing_is_earned_until_the_lead_is_won_and_the_client_pays(self):
		lead = make_collected_lead(DESIGNER)
		set_lead_stage_directly(lead, NEGOTIATION)
		self.pay(lead, 10000)
		self.assertIsNone(self.row(DESIGNER))
		set_lead_stage_directly(lead, WON)
		self.assertEqual(self.row(DESIGNER)["commission"], 200)
		self.won(DESIGNER2)
		self.assertIsNone(self.row(DESIGNER2))

	def test_a_cancelled_receipt_takes_the_money_back(self):
		lead = self.won()
		entry = self.pay(lead, 10000)
		frappe.set_user("Administrator")
		entry.cancel()
		frappe.set_user(HUB)
		self.assertIsNone(self.row(DESIGNER))

	def test_a_sales_person_who_owns_the_lead_they_collected_earns_no_referral(self):
		lead = self.won(SELLER, owner=SELLER)
		self.pay(lead, 10000)
		self.assertEqual(self.referral(SELLER), 0)
		assigned = self.won(SELLER)
		frappe.set_user("Administrator")
		frappe.get_doc(
			{
				"doctype": "ToDo",
				"reference_type": "Lead",
				"reference_name": assigned,
				"allocated_to": SELLER,
				"description": "Work it",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(HUB)
		self.pay(assigned, 10000)
		self.assertEqual(self.referral(SELLER), 0)

	def test_a_collector_earns_when_a_sales_person_works_the_lead(self):
		lead = self.won(DESIGNER, owner=SELLER)
		self.pay(lead, 10000)
		self.assertEqual(self.row(DESIGNER)["commission"], 200)
		self.assertEqual(self.referral(SELLER), 0)

	def test_a_statement_carries_the_referral_and_goes_draft_approved_paid(self):
		lead = self.won()
		self.pay(lead, 10000)
		[made] = [
			r
			for r in hub_commission.generate_statements(str(this_month()), acting_user=BOSS)
			if r["user"] == DESIGNER
		]
		self.assertEqual((made["status"], made["commission"]), ("Draft", 200))
		doc = frappe.get_doc("TBO Commission Statement", made["name"])
		self.assertEqual((doc.referral_commission, doc.collected), (200, 0))
		[line] = doc.lines
		self.assertEqual(
			(line.kind, line.lead, line.commission, line.collected), ("Referral", lead, 200, 10000)
		)
		hub_commission.approve_statement(made["name"], acting_user=BOSS)
		hub_commission.mark_paid(made["name"], acting_user=BOSS)
		self.assertEqual(self.row(DESIGNER)["status"], "Paid")
		self.assertEqual(hub_lead_bank.get_my_leads(acting_user=DESIGNER)["rows"][0]["earned"], 200)

	def test_an_approved_month_is_never_rewritten(self):
		lead = self.won()
		self.pay(lead, 10000)
		[made] = [
			r
			for r in hub_commission.generate_statements(str(this_month()), acting_user=BOSS)
			if r["user"] == DESIGNER
		]
		hub_commission.approve_statement(made["name"], acting_user=BOSS)
		set_referral_settings(referral_rate_india=10)
		self.pay(lead, 10000)
		again = [
			r
			for r in hub_commission.generate_statements(str(this_month()), acting_user=BOSS)
			if r["user"] == DESIGNER
		]
		self.assertEqual(again[0]["skipped"], 1)
		row = self.row(DESIGNER)
		self.assertEqual((row["commission"], row["frozen"], row["referral"]), (200, 1, 200))
		doc = frappe.get_doc("TBO Commission Statement", made["name"])
		self.assertEqual(len(doc.lines), 1)
		doc.append("lines", {"kind": "Referral", "lead": lead, "commission": 1})
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_the_next_month_pays_only_the_rest_up_to_the_cap(self):
		lead = self.won()
		self.pay(lead, 10000)
		[made] = [
			r
			for r in hub_commission.generate_statements(str(this_month()), acting_user=BOSS)
			if r["user"] == DESIGNER
		]
		hub_commission.approve_statement(made["name"], acting_user=BOSS)
		self.pay(lead, 2000000)
		following = add_months(this_month(), 1)
		history = commission.ledger_lines(referral.EPOCH, add_months(following, 1))
		[row] = referral.month_rows(following, history)
		# 2% of 2,010,000 is over the cap: 25,000, less the 200 the first month paid
		self.assertEqual((row["lead"], row["commission"]), (lead, 24800))
		self.assertEqual(
			referral.month_rows(this_month(), commission.ledger_lines(referral.EPOCH, following))[0][
				"commission"
			],
			25000,
		)

	def test_credit_moved_to_someone_else_does_not_reset_the_cap(self):
		lead = self.won()
		self.pay(lead, 10000)
		[made] = [
			r
			for r in hub_commission.generate_statements(str(this_month()), acting_user=BOSS)
			if r["user"] == DESIGNER
		]
		hub_commission.approve_statement(made["name"], acting_user=BOSS)
		hub_lead_bank.reassign_credit(lead, DESIGNER2, "Second designer took the photo", acting_user=MANAGER)
		following = add_months(this_month(), 1)
		history = commission.ledger_lines(referral.EPOCH, add_months(following, 1))
		self.assertEqual(referral.month_rows(following, history), [])
		self.pay(lead, 10000)
		history = commission.ledger_lines(referral.EPOCH, add_months(following, 1))
		[row] = referral.month_rows(following, history)
		self.assertEqual((row["user"], row["commission"]), (DESIGNER2, 200))
		self.assertEqual(frappe.get_doc("TBO Commission Statement", made["name"]).commission, 200)

	def test_my_leads_shows_what_is_earned_and_what_is_pending(self):
		lead = self.won()
		self.pay(lead, 10000)
		[row] = hub_lead_bank.get_my_leads(acting_user=DESIGNER)["rows"]
		self.assertEqual((row["status"], row["earned"], row["pending"]), ("Won", 0, 200))
		[made] = [
			r
			for r in hub_commission.generate_statements(str(this_month()), acting_user=BOSS)
			if r["user"] == DESIGNER
		]
		hub_commission.approve_statement(made["name"], acting_user=BOSS)
		summary = hub_lead_bank.get_my_leads(acting_user=DESIGNER)["summary"]
		self.assertEqual((summary["earned"], summary["pending"], summary["won"]), (200, 0, 1))

	def test_the_dashboard_shows_the_referral_due(self):
		lead = self.won()
		self.pay(lead, 10000)
		board = hub_lead_bank.get_dashboard(str(this_month()), acting_user=BOSS)
		self.assertEqual((board["referral"]["earned"], board["referral"]["due"]), (200, 200))
		self.assertEqual(board["referral"]["by_collector"][0]["user"], DESIGNER)
