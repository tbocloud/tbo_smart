import json
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline import proposal_profile as web
from tbo_smart.prospecting import importer, mapping, runs, signals
from tbo_smart.prospecting.providers import ProviderError, first_phone, get_provider
from tbo_smart.prospecting.providers.dataforseo import DataForSEO
from tbo_smart.prospecting.providers.osm import OpenStreetMap
from tbo_smart.prospecting.providers.serper import Serper
from tbo_smart.test_utils import (
	delete_lead_departments,
	delete_leads_matching,
	delete_prospect_runs,
	disable_prospecting,
	disable_sales_hierarchy,
	enable_prospecting,
	make_industry,
	make_lead,
	make_lead_department,
	make_prospect_business,
	make_prospect_category,
	make_prospect_place,
	make_prospect_run,
	make_user,
)

HUB = "prospect.integration@tbosmart.test"
MANAGER = "prospect.manager@tbosmart.test"
HEAD = "prospect.head@tbosmart.test"
OTHER_HEAD = "prospect.otherhead@tbosmart.test"
SELLER = "prospect.seller@tbosmart.test"
PEOPLE = (MANAGER, HEAD, OTHER_HEAD, SELLER)
DEPARTMENT = "TBO Test Prospect Dept"
OTHER_DEPARTMENT = "TBO Test Prospect Other"
VALUES = {
	"industry": "Resorts",
	"state": "Kerala",
	"districts": ["Idukki"],
	"provider": "OpenStreetMap",
	"max_results": 10,
	"department": DEPARTMENT,
}
PAGE = """<html><head><title>Palm Resort</title><script>var c = "© 1999"</script></head><body>
© 2019 Palm Resort <a href="https://wa.me/919847000000">Chat</a> <a href="/careers">Careers</a>
<a href="/about-us">About</a> <a href="/contact">Contact</a> <a href="mailto:Info@palm.test">Mail</a>
<a href="https://www.facebook.com/palmresort">Facebook</a>
<script src="/wp-content/themes/x.js"></script> We keep our books in Tally.</body></html>"""


class ProspectCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(MANAGER, ["Sales Manager"])
		for user in (HEAD, OTHER_HEAD, SELLER):
			make_user(user, ["Sales User"])
		make_industry("Resorts")

	def setUp(self):
		frappe.set_user("Administrator")
		self.clean()
		disable_sales_hierarchy(PEOPLE)
		make_lead_department(DEPARTMENT, heads=[HEAD], members=[SELLER], assignment="Round robin")
		make_lead_department(OTHER_DEPARTMENT, heads=[OTHER_HEAD])
		enable_prospecting(prospect_default_department=DEPARTMENT)

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_prospecting()
		self.clean()

	def clean(self):
		delete_prospect_runs()
		delete_leads_matching(tbo_department=("in", [DEPARTMENT, OTHER_DEPARTMENT]))
		delete_leads_matching(company_name=("like", "Prospect Test %"))
		delete_lead_departments([DEPARTMENT, OTHER_DEPARTMENT])
		frappe.db.delete("TBO Prospect Category")
		frappe.db.commit()  # nosemgrep


class TestMapping(ProspectCase):
	def test_an_industry_has_built_in_tags_and_a_settings_row_overrides_them(self):
		self.assertIn("tourism=resort", mapping.category_for("Resorts")["osm"])
		settings = frappe.get_single("TBO Smart Settings")
		settings.append(
			"prospect_categories",
			{"industry": "Resorts", "osm_tags": "tourism=chalet", "search_phrase": "villas"},
		)
		settings.save(ignore_permissions=True)
		spec = mapping.category_for("Resorts")
		self.assertEqual((spec["osm"], spec["phrase"], spec["dfs"]), (["tourism=chalet"], "villas", "resort"))

	def test_an_unknown_industry_falls_back_to_its_name_and_no_tags(self):
		spec = mapping.category_for("Space Elevators")
		self.assertEqual((spec["osm"], spec["phrase"]), ([], "space elevators"))

	def test_tags_that_could_change_the_query_are_refused(self):
		for line in ('shop=jewelry"];out;', "shop=a b", 'amenity=clinic;name~x"];', "amenity=clinic;ref~abc"):
			with self.assertRaises(frappe.ValidationError):
				mapping.parse_tag(line)
		self.assertEqual(
			mapping.parse_tag("amenity=clinic;name~ayur|vaidya"), ("amenity", "clinic", "ayur|vaidya")
		)


class TestProviders(ProspectCase):
	def test_the_openstreetmap_query_covers_the_district_and_asks_for_a_phone(self):
		spec = {"osm": ["tourism=resort", "tourism=hotel;name~resort|retreat"]}
		query = OpenStreetMap.build(spec, make_prospect_place(), True, "out count;")
		self.assertIn("area(3601819274)->.a;", query)
		self.assertIn('nwr["tourism"="hotel"]["name"~"resort|retreat",i]', query)
		self.assertIn("contact:phone", query)
		self.assertNotIn("contact:phone", OpenStreetMap.build(spec, make_prospect_place(), False, "out;"))

	def test_a_district_that_is_not_a_relation_is_searched_by_its_box(self):
		place = {**make_prospect_place(), "relation": None}
		self.assertIn(
			"(9.3,76.5,10.3,77.5)", OpenStreetMap.build({"osm": ["shop=jewelry"]}, place, False, "out;")
		)

	def test_an_openstreetmap_element_becomes_a_business(self):
		business = OpenStreetMap.business(
			{
				"type": "way",
				"id": 7,
				"center": {"lat": 9.9, "lon": 77.1},
				"tags": {
					"name": "Palm",
					"contact:phone": "+91 484 123456; 9847012345",
					"website": "palm.test",
				},
			}
		)
		self.assertEqual(business["ref"], "osm:way/7")
		self.assertEqual(business["phone"], "+91 484 123456")
		self.assertIn("9.9,77.1", business["maps_url"])
		self.assertIsNone(OpenStreetMap.business({"type": "node", "id": 1, "tags": {"amenity": "x"}}))

	def test_a_throttled_server_is_skipped_and_all_throttled_raises(self):
		busy, fine = MagicMock(status_code=429), MagicMock(status_code=200)
		fine.json.return_value = {"elements": []}
		with patch("time.sleep"), patch("requests.post", side_effect=[busy, fine]) as post:
			self.assertEqual(OpenStreetMap.query("x"), {"elements": []})
		self.assertEqual(post.call_count, 2)
		with patch("time.sleep"), patch("requests.post", return_value=busy):
			with self.assertRaises(ProviderError):
				OpenStreetMap.query("x")

	def test_openstreetmap_with_no_tags_says_so(self):
		with self.assertRaises(ProviderError):
			OpenStreetMap().estimate(
				{"osm": [], "phrase": "space elevators"}, [make_prospect_place()], 10, True
			)

	def test_dataforseo_prices_before_it_runs_and_reads_the_cost_it_was_charged(self):
		provider = DataForSEO()
		estimate = provider.estimate({}, [make_prospect_place()] * 2, 100, True)
		self.assertAlmostEqual(estimate["cost"], 2 * 0.012 + 100 * 0.00036, places=4)
		answer = MagicMock()
		answer.json.return_value = {
			"status_code": 20000,
			"cost": 0.0291,
			"tasks": [
				{
					"status_code": 20000,
					"result": [
						{
							"items": [
								{
									"title": "Palm Resort",
									"phone": "+91 4865 230000",
									"url": "https://palm.test",
									"place_id": "ChIJabc",
									"cid": "42",
									"latitude": 9.8,
									"longitude": 77.0,
									"address_info": {"city": "Munnar"},
								}
							]
						}
					],
				}
			],
		}
		with (
			patch.object(provider, "secret", return_value="x"),
			patch("requests.post", return_value=answer) as post,
		):
			found, cost = provider.search(mapping_spec(), make_prospect_place(), 5, True)
		self.assertEqual(cost, 0.0291)
		self.assertEqual(
			(found[0]["ref"], found[0]["city"], found[0]["website"]),
			("gplace:ChIJabc", "Munnar", "https://palm.test"),
		)
		payload = post.call_args.kwargs["json"][0]
		self.assertEqual(payload["categories"], ["resort"])
		self.assertTrue(payload["location_coordinate"].startswith("9.800000,77.000000,"))

	def test_dataforseo_reports_a_refusal_in_words(self):
		answer = MagicMock()
		answer.json.return_value = {"status_code": 40101, "status_message": "Authentication failed"}
		with (
			patch.object(DataForSEO, "secret", return_value="x"),
			patch("requests.post", return_value=answer),
		):
			with self.assertRaises(ProviderError):
				DataForSEO().search(mapping_spec(), make_prospect_place(), 5, True)
		with (
			patch.object(DataForSEO, "secret", return_value="x"),
			patch("requests.post", side_effect=requests.ConnectionError),
		):
			with self.assertRaises(ProviderError):
				DataForSEO().search(mapping_spec(), make_prospect_place(), 5, True)

	def test_serper_pages_until_enough_and_charges_per_page(self):
		page = MagicMock()
		page.json.return_value = {
			"places": [
				{"title": f"Place {n}", "phoneNumber": "9847012345", "placeId": f"p{n}"} for n in range(20)
			]
		}
		with (
			patch.object(Serper, "secret", return_value="k"),
			patch("requests.post", return_value=page) as post,
		):
			found, cost = Serper().search(mapping_spec(), make_prospect_place(), 30, True)
		self.assertEqual((len(found), post.call_count), (30, 2))
		self.assertAlmostEqual(cost, 0.006)
		self.assertEqual(post.call_args.kwargs["json"]["q"], "resorts in Idukki")

	def test_a_provider_without_its_key_is_not_available(self):
		self.assertTrue(get_provider("OpenStreetMap").available())
		self.assertFalse(get_provider("DataForSEO").available())
		self.assertEqual(first_phone("call 98470 12345 / 0484 22"), "98470 12345")


def mapping_spec() -> dict:
	return {"osm": ["tourism=resort"], "phrase": "resorts", "dfs": "resort"}


class TestImport(ProspectCase):
	def context(self, **fields):
		return frappe._dict(
			name=make_prospect_run(department=DEPARTMENT),
			industry="Resorts",
			state="Kerala",
			department=DEPARTMENT,
			provider="OpenStreetMap",
			require_phone=1,
			district="Idukki",
			**fields,
		)

	def test_a_business_becomes_an_unassigned_lead_even_in_a_round_robin_department(self):
		context = self.context()
		self.assertEqual(
			importer.import_business(
				make_prospect_business("Prospect Test A", website="https://www.palm.test/home"), context
			),
			"created",
		)
		lead = frappe.get_doc("Lead", {"company_name": "Prospect Test A"})
		self.assertFalse(lead.lead_owner)
		self.assertEqual(
			(lead.tbo_department, lead.industry, lead.mobile_no, lead.city, lead.website),
			(DEPARTMENT, "Resorts", "+91 98470 12345", "Munnar", "https://www.palm.test/home"),
		)
		self.assertEqual(
			(lead.tbo_source_ref, lead.tbo_prospect_run), ("gplace:Prospect-Test-A", context.name)
		)
		self.assertEqual(lead.get("utm_source") or lead.get("source"), "Prospecting – OpenStreetMap")
		self.assertEqual((lead.tbo_latitude, lead.tbo_location_note), (9.8, "Munnar Road"))
		if frappe.get_meta("Lead").has_field("tbo_district"):
			self.assertEqual((lead.state, lead.tbo_district), ("Kerala", "Idukki"))

	def test_the_same_place_phone_company_or_website_is_a_duplicate(self):
		context = self.context()
		first = make_prospect_business("Prospect Test B", website="https://palm-b.test")
		self.assertEqual(importer.import_business(first, context), "created")
		cases = {
			"same place": make_prospect_business("Prospect Test B2", ref=first["ref"], phone="9000000001"),
			"same phone": make_prospect_business("Prospect Test B3", phone="98470 12345"),
			"same company": make_prospect_business("prospect test b ", phone="9000000002"),
			"same website": make_prospect_business(
				"Prospect Test B4", phone="9000000003", website="http://www.palm-b.test/"
			),
		}
		for label, business in cases.items():
			self.assertEqual(importer.import_business(business, context), "duplicate", label)
		self.assertEqual(frappe.db.count("Lead", {"tbo_prospect_run": context.name}), 1)

	def test_a_lead_someone_else_already_has_is_a_duplicate(self):
		make_lead(SELLER, "Prospect Test Owner", mobile_no="9847012345")
		self.assertEqual(
			importer.import_business(make_prospect_business("Prospect Test C"), self.context()), "duplicate"
		)

	def test_no_phone_is_skipped_when_a_phone_is_required_and_kept_when_not(self):
		business = make_prospect_business("Prospect Test D", phone="")
		self.assertEqual(importer.import_business(business, self.context()), "skipped")
		self.assertEqual(importer.import_business(business, self.context(require_phone=0)), "created")

	def test_shared_hosts_are_not_a_websites_identity(self):
		self.assertIsNone(importer.domain_of("https://www.facebook.com/palm"))
		self.assertEqual(importer.domain_of("https://WWW.Palm.test/a"), "palm.test")
		context = self.context()
		for n in (1, 2):
			business = make_prospect_business(
				f"Prospect Test F{n}", phone=f"900000010{n}", website="https://facebook.com/palm"
			)
			self.assertEqual(importer.import_business(business, context), "created")


class TestRuns(ProspectCase):
	def estimate(self, user=MANAGER, **values):
		frappe.set_user(HUB)
		with (
			patch("tbo_smart.prospecting.geo.locate", return_value=make_prospect_place()),
			patch.object(OpenStreetMap, "count", return_value=37),
		):
			return hub.estimate_prospect_run({**VALUES, **values}, acting_user=user)

	def test_an_estimate_saves_a_run_with_the_expected_count_and_the_coverage_warning(self):
		run = self.estimate()
		self.assertEqual((run["status"], run["estimated_cost"], run["expected_count"]), ("Estimated", 0, 10))
		self.assertIn("37 on the map with a phone number", run["estimate_note"])
		self.assertIn("one business in ten", run["estimate_note"])
		self.assertEqual(run["districts"], ["Idukki"])

	def test_who_may_find_prospects(self):
		self.assertEqual(self.estimate(user=HEAD)["department"], DEPARTMENT)
		for user in (SELLER, OTHER_HEAD):
			with self.assertRaises(frappe.PermissionError):
				self.estimate(user=user)

	def test_it_must_be_switched_on_and_the_values_sane(self):
		for bad in (
			{"max_results": 101},
			{"districts": []},
			{"industry": "Nothing"},
			{"provider": "DataForSEO"},
			{"department": "Nope"},
		):
			with self.assertRaises(frappe.ValidationError, msg=str(bad)):
				self.estimate(**bad)
		disable_prospecting()
		with self.assertRaises(frappe.ValidationError):
			self.estimate()

	def test_a_paid_run_is_refused_over_the_monthly_cap(self):
		enable_prospecting(
			prospect_dataforseo_enabled=1,
			dataforseo_login="login",
			prospect_monthly_cap=0.01,
			prospect_default_department=DEPARTMENT,
		)
		with patch.object(DataForSEO, "configured", return_value=True):
			with self.assertRaises(frappe.ValidationError) as raised:
				self.estimate(provider="DataForSEO", max_results=10)
			self.assertIn("cap", str(raised.exception))
			enable_prospecting(
				prospect_dataforseo_enabled=1,
				dataforseo_login="login",
				prospect_monthly_cap=5,
				prospect_default_department=DEPARTMENT,
			)
			run = self.estimate(provider="DataForSEO", max_results=10)
			self.assertAlmostEqual(run["estimated_cost"], 0.012 + 10 * 0.00036, places=4)

	def test_start_needs_an_estimate_and_queues_the_job_once(self):
		draft = make_prospect_run(department=DEPARTMENT, status="Draft")
		frappe.set_user(HUB)
		with self.assertRaises(frappe.ValidationError):
			hub.start_prospect_run(draft, acting_user=MANAGER)
		run = make_prospect_run(department=DEPARTMENT)
		with patch("frappe.enqueue") as enqueue:
			started = hub.start_prospect_run(run, acting_user=HEAD)
			self.assertEqual(started["status"], "Running")
			enqueue.assert_called_once()
			with self.assertRaises(frappe.ValidationError):
				hub.start_prospect_run(run, acting_user=HEAD)
		with self.assertRaises(frappe.PermissionError):
			hub.start_prospect_run(run, acting_user=SELLER)

	def test_the_job_imports_counts_and_queues_the_website_reading(self):
		make_lead(SELLER, "Prospect Test Existing", mobile_no="9000000009")
		run = make_prospect_run(department=DEPARTMENT, status="Running", max_results=5)
		found = [
			make_prospect_business("Prospect Test G1", phone="9000000001"),
			make_prospect_business("Prospect Test G2", phone="9000000002"),
			make_prospect_business("Prospect Test G3", phone="9000000009"),
			make_prospect_business("Prospect Test G4", phone=""),
			make_prospect_business("Prospect Test G5", phone="9000000005", latitude=40.0, longitude=10.0),
		]
		with (
			patch("tbo_smart.prospecting.geo.locate", return_value=make_prospect_place()),
			patch.object(OpenStreetMap, "search", return_value=(found, 0.0)),
			patch("frappe.enqueue") as enqueue,
		):
			runs.run_job(run)
		doc = frappe.get_doc("TBO Prospect Run", run)
		self.assertEqual(doc.status, "Done")
		self.assertEqual(
			(doc.count_found, doc.count_created, doc.count_duplicates, doc.count_skipped), (4, 2, 1, 1)
		)
		self.assertIn("Idukki: 5 from OpenStreetMap, 4 inside the district.", doc.log)
		self.assertTrue(doc.finished_at)
		self.assertEqual(enqueue.call_args.args[0], "tbo_smart.prospecting.runs.read_websites")
		self.assertEqual(frappe.db.count("Lead", {"tbo_prospect_run": run, "lead_owner": ("is", "set")}), 0)

	def test_a_provider_failure_marks_the_run_failed_and_keeps_the_cost_so_far(self):
		run = make_prospect_run(department=DEPARTMENT, status="Running", districts="Idukki\nKottayam")
		answers = [([make_prospect_business("Prospect Test H1")], 0.5), ProviderError("Overpass is busy")]
		with (
			patch("tbo_smart.prospecting.geo.locate", return_value=make_prospect_place()),
			patch.object(OpenStreetMap, "search", side_effect=answers),
			patch("frappe.enqueue") as enqueue,
		):
			runs.run_job(run)
		doc = frappe.get_doc("TBO Prospect Run", run)
		self.assertEqual((doc.status, doc.count_created, doc.actual_cost), ("Failed", 1, 0.5))
		self.assertIn("Overpass is busy", doc.log)
		enqueue.assert_not_called()

	def test_runs_are_listed_and_opened_with_their_leads(self):
		own = make_prospect_run(department=DEPARTMENT)
		other = make_prospect_run(department=OTHER_DEPARTMENT)
		importer.import_business(
			make_prospect_business("Prospect Test J"),
			frappe._dict(
				name=own,
				industry="Resorts",
				state="Kerala",
				department=DEPARTMENT,
				provider="OpenStreetMap",
				require_phone=1,
				district="Idukki",
			),
		)
		frappe.set_user(HUB)
		self.assertEqual({row["name"] for row in hub.get_prospect_runs(acting_user=MANAGER)}, {own, other})
		self.assertEqual([row["name"] for row in hub.get_prospect_runs(acting_user=HEAD)], [own])
		opened = hub.get_prospect_run(own, acting_user=HEAD)
		self.assertEqual([lead["company_name"] for lead in opened["leads"]], ["Prospect Test J"])
		with self.assertRaises(frappe.PermissionError):
			hub.get_prospect_run(other, acting_user=HEAD)

	def test_the_options_flag_follows_the_role_and_the_switch(self):
		frappe.set_user(HUB)
		manager = hub.get_options(acting_user=MANAGER)
		self.assertTrue(manager["can_find_prospects"])
		self.assertEqual(manager["prospecting"]["default_department"], DEPARTMENT)
		self.assertEqual(
			{p["key"] for p in manager["prospecting"]["providers"]}, {"OpenStreetMap", "DataForSEO", "Serper"}
		)
		self.assertTrue(hub.get_options(acting_user=HEAD)["can_find_prospects"])
		self.assertFalse(hub.get_options(acting_user=SELLER)["can_find_prospects"])
		disable_prospecting()
		self.assertFalse(hub.get_options(acting_user=MANAGER)["can_find_prospects"])


class TestSignals(ProspectCase):
	def pages(self):
		return {
			"https://palm.test": PAGE,
			"https://palm.test/careers": "<html>Jobs</html>",
			"https://palm.test/about-us": "<html>About</html>",
			"https://palm.test/contact": "<html>Contact</html>",
		}

	def fetch(self, pages):
		def fetch(url, user_agent=None):
			if url.rstrip("/") not in pages:
				web.unreachable("gone")
			return url, pages[url.rstrip("/")]

		return fetch

	def test_the_signals_are_read_from_the_page_and_the_code_is_ignored(self):
		found = signals.analyse([("https://palm.test/", PAGE)], has_careers_link=True)
		self.assertTrue(found["whatsapp"] and found["hiring"] and found["https"])
		self.assertFalse(found["online_booking"])
		self.assertEqual(
			(found["email"], found["social"], found["tech"], found["software"]),
			("info@palm.test", ["facebook"], ["WordPress"], ["Tally"]),
		)
		self.assertEqual(found["last_updated_year"], 2019)

	def test_booking_tools_are_named(self):
		found = signals.analyse(
			[("https://x.test/", '<script src="https://app.cloudbeds.com/x.js"></script>')]
		)
		self.assertEqual((found["online_booking"], found["booking_tool"]), (True, "Cloudbeds"))

	def test_why_call_is_built_from_the_findings(self):
		found = signals.analyse([("https://palm.test/", PAGE)], has_careers_link=True)
		line = signals.why_call(found, "Resorts")
		self.assertIn("no online booking", line)
		self.assertIn("hiring", line)
		self.assertLessEqual(len(line), 140)
		self.assertIn("No website", signals.why_call({"has_website": False}))

	def test_a_site_is_read_with_at_most_three_requests(self):
		fetch = MagicMock(side_effect=self.fetch(self.pages()))
		with patch.object(web, "fetch", fetch):
			result = signals.read_site("palm.test", "Resorts")
		self.assertLessEqual(fetch.call_count, 3)
		self.assertTrue(result["signals"]["hiring"])
		self.assertEqual(fetch.call_args_list[0].args[0], "https://palm.test")

	def test_an_unreachable_or_blocked_site_gives_nothing(self):
		with patch.object(web, "fetch", side_effect=self.fetch({})):
			self.assertIsNone(signals.read_site("gone.test"))
		blocked = "<html><head><title>Just a moment...</title></head></html>"
		with patch.object(web, "fetch", return_value=("https://b.test/", blocked)):
			self.assertIsNone(signals.read_site("b.test"))

	def test_private_addresses_are_never_fetched(self):
		with patch("requests.get") as get:
			for url in (
				"http://127.0.0.1/",
				"http://169.254.169.254/latest",
				"http://10.0.0.5/",
				"ftp://example.com/",
				"http://user:pw@example.com/",
			):
				self.assertIsNone(signals.read_site(url), url)
		get.assert_not_called()

	def test_the_findings_sit_beside_the_proposal_summary_and_survive_its_refresh(self):
		lead = make_lead(
			SELLER, "Prospect Test K", company_name="Prospect Test K", website="palm.test", industry="Resorts"
		)
		frappe.db.set_value(
			"Lead", lead, "tbo_web_profile", json.dumps({"about": "A resort", "website": "https://palm.test"})
		)
		with patch.object(web, "fetch", side_effect=self.fetch(self.pages())):
			signals.read_lead(lead)
		raw = frappe.db.get_value("Lead", lead, "tbo_web_profile")
		self.assertEqual(json.loads(raw)["about"], "A resort")
		self.assertTrue(signals.prospect_of(raw)["why_call"])
		self.assertEqual(web.cached_web_profile(raw), {"about": "A resort", "website": "https://palm.test"})
		with (
			patch("tbo_smart.lead_pipeline.proposal_builder.require_ai_budget"),
			patch.object(web, "site_text", return_value="text"),
			patch("tbo_smart.ai.call_ai", return_value={"response": {"about": "New"}}),
		):
			web.read_website(lead, refresh=True)
		self.assertTrue(signals.prospect_of(frappe.db.get_value("Lead", lead, "tbo_web_profile")))

	def test_a_lead_without_a_website_gets_the_no_website_finding(self):
		lead = make_lead(SELLER, "Prospect Test L", company_name="Prospect Test L")
		block = signals.read_lead(lead)
		self.assertFalse(block["signals"]["has_website"])
		self.assertIn("No website", block["why_call"])

	def test_the_reading_goes_on_past_a_failing_lead_and_the_hub_gets_it_on_the_lead(self):
		bad = make_lead(SELLER, "Prospect Test M", company_name="Prospect Test M", website="bad.test")
		good = make_lead(SELLER, "Prospect Test N", company_name="Prospect Test N", website="palm.test")
		original = signals.read_lead

		def read(lead):
			if lead == bad:
				raise RuntimeError("boom")
			return original(lead)

		with (
			patch.object(signals, "read_lead", side_effect=read),
			patch.object(web, "fetch", side_effect=self.fetch(self.pages())),
			patch("time.sleep"),
		):
			self.assertEqual(signals.read_leads([bad, good]), 1)
		frappe.set_user(HUB)
		lead = hub.get_lead(good, acting_user=MANAGER)["lead"]
		self.assertTrue(lead["prospect"]["signals"]["whatsapp"])
		self.assertNotIn("tbo_web_profile", lead)
