import base64
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline import lead_place
from tbo_smart.test_utils import (
	delete_sales_records,
	disable_sales_hierarchy,
	make_image_base64,
	make_lead,
	make_user,
)

HUB = "place.integration@tbosmart.test"
BDE = "place.bde@tbosmart.test"
BDE2 = "place.bde2@tbosmart.test"
MANAGER = "place.manager@tbosmart.test"
PEOPLE = (BDE, BDE2, MANAGER)
PROFILE = "https://maps.app.goo.gl/AbC123"
MAPS = "https://www.google.com/maps/place/Shop/@10.0,76.3,17z"


class TestLeadPlace(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		make_user(HUB, ["System Manager", "Hub Integration"])
		make_user(BDE, ["Sales User"])
		make_user(BDE2, ["Sales User"])
		make_user(MANAGER, ["Sales Manager"])

	def setUp(self):
		frappe.set_user("Administrator")
		delete_sales_records(PEOPLE)
		disable_sales_hierarchy(PEOPLE)
		self.lead = make_lead(BDE, "Place Lead", company_name="Place Traders")
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def update(self, **data):
		return hub.update_lead(self.lead, data, acting_user=BDE)

	def upload(self, user=BDE, **values):
		args = {"filename": "front.png", "content": make_image_base64(), **values}
		return hub.upload_lead_photo(self.lead, acting_user=user, **args)

	# ---------- the place ----------

	def test_a_lead_has_no_place_until_one_is_saved(self):
		lead = hub.get_lead(self.lead, acting_user=BDE)["lead"]
		self.assertEqual(
			lead.place,
			{
				"google_profile_url": None,
				"maps_url": None,
				"latitude": None,
				"longitude": None,
				"location_note": None,
			},
		)
		self.assertEqual(lead.photo_count, 0)

	def test_the_place_is_saved_and_read_back(self):
		lead = self.update(
			google_profile_url=PROFILE,
			maps_url=MAPS,
			latitude=10.0151,
			longitude="76.3412",
			location_note="  2nd floor, above SBI  ",
		)["lead"]
		self.assertEqual(lead.place["google_profile_url"], PROFILE)
		self.assertEqual(lead.place["maps_url"], MAPS)
		self.assertEqual((lead.place["latitude"], lead.place["longitude"]), (10.0151, 76.3412))
		self.assertEqual(lead.place["location_note"], "2nd floor, above SBI")

	def test_clearing_the_place(self):
		self.update(maps_url=MAPS, latitude=10.0, longitude=76.0, location_note="x")
		lead = self.update(maps_url="", latitude=None, longitude=None, location_note="")["lead"]
		self.assertTrue(all(value is None for value in lead.place.values()))

	def test_only_google_links_are_accepted(self):
		for link in (
			"https://maps.app.goo.gl/x1",
			"https://g.page/some-shop",
			"https://business.google.com/n/123",
			"https://www.google.com/maps/place/Shop",
			"https://goo.gl/maps/abc",
			"www.google.co.in/maps?q=10,76",
		):
			self.assertTrue(self.update(google_profile_url=link)["lead"].place["google_profile_url"])
		for link in (
			"https://evil.example.com/maps",
			"https://google.com.evil.example/maps",
			"https://www.google.com/search?q=x",
			"https://goo.gl/other",
			"javascript:alert(1)",
			"https://user:pass@www.google.com/maps",
			"ftp://maps.app.goo.gl/x",
		):
			with self.assertRaises(frappe.ValidationError, msg=link):
				self.update(google_profile_url=link)

	def test_a_maps_link_is_not_a_profile_page(self):
		self.assertRaises(frappe.ValidationError, self.update, maps_url="https://g.page/some-shop")

	def test_coordinates_are_a_real_pair(self):
		for bad in ({"latitude": 91, "longitude": 10}, {"latitude": 10, "longitude": 181}):
			self.assertRaises(frappe.ValidationError, self.update, **bad)

	def test_writing_the_place_needs_write_on_the_lead(self):
		self.assertRaises(
			frappe.PermissionError, hub.update_lead, self.lead, {"maps_url": MAPS}, acting_user=BDE2
		)

	# ---------- photos ----------

	def test_a_photo_is_added_listed_and_served(self):
		photo = self.upload(caption="Shop front")
		self.assertEqual(photo["caption"], "Shop front")
		self.assertEqual(photo["uploaded_by"], BDE)
		self.assertTrue(photo["file_name"].endswith(".png"))
		self.assertEqual(
			[row["name"] for row in hub.get_lead_photos(self.lead, acting_user=BDE)], [photo["name"]]
		)
		self.assertEqual(hub.get_lead(self.lead, acting_user=BDE)["lead"].photo_count, 1)
		content, mime, _name = lead_place.photo_bytes(self.lead, photo["name"])
		self.assertEqual(mime, "image/png")
		self.assertEqual(content[:4], b"\x89PNG")

	def test_a_photo_is_private(self):
		photo = self.upload()
		self.assertTrue(frappe.db.get_value("File", photo["name"], "is_private"))
		self.assertTrue(photo["file_url"].startswith("/private/files/"))

	def test_get_lead_photo_streams_the_bytes_or_a_thumbnail(self):
		photo = self.upload()
		hub.get_lead_photo(self.lead, photo["name"], acting_user=BDE)
		self.assertEqual(frappe.local.response.content_type, "image/png")
		hub.get_lead_photo(self.lead, photo["name"], thumb=1, acting_user=BDE)
		self.assertEqual(frappe.local.response.content_type, "image/jpeg")
		self.assertEqual(frappe.local.response.filecontent[:2], b"\xff\xd8")

	def test_a_thumbnail_is_small(self):
		photo = self.upload(content=make_image_base64("JPEG", (2000, 1000)), filename="big.jpg")
		content, mime, _name = lead_place.photo_bytes(self.lead, photo["name"], 1)
		from io import BytesIO

		from PIL import Image

		with Image.open(BytesIO(content)) as image:
			self.assertEqual(image.size, (480, 240))
		self.assertEqual(mime, "image/jpeg")

	def test_only_images_are_accepted(self):
		for content in (
			base64.b64encode(b"%PDF-1.4 not an image").decode(),
			make_image_base64("GIF"),
			"",
			base64.b64encode(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heic").decode(),
		):
			with self.assertRaises(frappe.ValidationError):
				self.upload(content=content)
		self.assertRaises(frappe.ValidationError, self.upload, content="not base64!!")
		self.assertEqual(hub.get_lead_photos(self.lead, acting_user=BDE), [])

	def test_a_photo_is_at_most_10_mb(self):
		with patch.object(lead_place, "MAX_PHOTO_BYTES", 10):
			self.assertRaises(frappe.ValidationError, self.upload)

	def test_a_lead_holds_at_most_20_photos(self):
		with patch.object(lead_place, "MAX_PHOTOS", 2):
			self.upload()
			self.upload()
			self.assertRaises(frappe.ValidationError, self.upload)

	def test_photos_follow_the_lead_permission(self):
		photo = self.upload()
		self.assertRaises(frappe.PermissionError, self.upload, user=BDE2)
		self.assertRaises(frappe.PermissionError, hub.get_lead_photos, self.lead, acting_user=BDE2)
		self.assertRaises(
			frappe.PermissionError, hub.get_lead_photo, self.lead, photo["name"], acting_user=BDE2
		)
		self.assertRaises(
			frappe.PermissionError, hub.delete_lead_photo, self.lead, photo["name"], acting_user=BDE2
		)

	def test_a_file_that_is_not_a_lead_photo_is_not_served(self):
		other = make_lead(BDE, "Other Lead")
		elsewhere = hub.upload_lead_photo(other, "a.png", make_image_base64(), acting_user=BDE)
		card = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "card.png",
				"attached_to_doctype": "Lead",
				"attached_to_name": self.lead,
				"is_private": 1,
				"content": base64.b64decode(make_image_base64()),
			}
		).insert(ignore_permissions=True)
		self.assertEqual(hub.get_lead_photos(self.lead, acting_user=BDE), [])
		for name in (card.name, elsewhere["name"]):
			self.assertRaises(frappe.DoesNotExistError, hub.get_lead_photo, self.lead, name, acting_user=BDE)
			self.assertRaises(
				frappe.DoesNotExistError, hub.delete_lead_photo, self.lead, name, acting_user=BDE
			)

	def test_the_uploader_and_a_manager_may_delete(self):
		photo = self.upload()
		hub.delete_lead_photo(self.lead, photo["name"], acting_user=BDE)
		self.assertFalse(frappe.db.exists("File", photo["name"]))
		again = self.upload()
		hub.delete_lead_photo(self.lead, again["name"], acting_user=MANAGER)
		self.assertEqual(hub.get_lead_photos(self.lead, acting_user=BDE), [])

	def test_someone_else_with_write_may_not_delete(self):
		frappe.set_user("Administrator")
		photo = lead_place.add_photo(self.lead, "a.png", base64.b64decode(make_image_base64()))
		with patch.object(lead_place, "MANAGER_ROLES", ()):
			frappe.set_user(BDE)
			doc = lead_place.photo_file(self.lead, photo["name"])
			lead = frappe.get_doc("Lead", self.lead)
			# BDE owns the lead, so the lead's owner may; a different owner may not
			self.assertTrue(lead_place.can_delete(doc, lead))
			lead.lead_owner = BDE2
			lead.owner = BDE2
			self.assertFalse(lead_place.can_delete(doc, lead))
