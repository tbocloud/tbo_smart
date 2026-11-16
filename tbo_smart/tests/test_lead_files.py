import base64
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api import hub_leads as hub
from tbo_smart.lead_pipeline import lead_files
from tbo_smart.test_utils import (
	delete_sales_records,
	disable_sales_hierarchy,
	make_image_base64,
	make_lead,
	make_user,
)

HUB = "files.integration@tbosmart.test"
BDE = "files.bde@tbosmart.test"
BDE2 = "files.bde2@tbosmart.test"
MANAGER = "files.manager@tbosmart.test"
PEOPLE = (BDE, BDE2, MANAGER)
PDF = base64.b64encode(b"%PDF-1.4 requirement sheet").decode()


class TestLeadFiles(FrappeTestCase):
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
		self.lead = make_lead(BDE, "Files Lead", company_name="Files Traders")
		frappe.set_user(HUB)

	def tearDown(self):
		frappe.set_user("Administrator")

	def upload(self, user=BDE, **values):
		args = {"filename": "requirements.pdf", "content": PDF, **values}
		return hub.upload_lead_file(self.lead, acting_user=user, **args)

	def listed(self, user=BDE):
		return [row["name"] for row in hub.get_lead_files(self.lead, acting_user=user)]

	def test_a_file_is_added_listed_and_served(self):
		added = self.upload()
		self.assertEqual(added["file_name"], "requirements.pdf")
		self.assertEqual(added["uploaded_by"], BDE)
		self.assertTrue(added["can_delete"])
		self.assertEqual(self.listed(), [added["name"]])
		self.assertTrue(frappe.db.get_value("File", added["name"], "is_private"))
		hub.get_lead_file(self.lead, added["name"], acting_user=BDE)
		self.assertEqual(frappe.local.response.filecontent, b"%PDF-1.4 requirement sheet")
		self.assertEqual(frappe.local.response.content_type, "application/pdf")
		self.assertEqual(frappe.local.response.display_content_as, "attachment")

	def test_photos_are_not_files_but_desk_attachments_are(self):
		hub.upload_lead_photo(self.lead, "front.png", make_image_base64(), acting_user=BDE)
		frappe.set_user("Administrator")
		desk = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "notes.md",
				"attached_to_doctype": "Lead",
				"attached_to_name": self.lead,
				"is_private": 1,
				"content": b"# notes",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(HUB)
		self.assertEqual(self.listed(), [desk.name])
		# a type the hub can't add is still handed over, as bytes to save
		content, mime, _name = lead_files.file_bytes(self.lead, desk.name)
		self.assertEqual(mime, "application/octet-stream")
		self.assertEqual(content, b"# notes")

	def test_only_known_types_are_accepted(self):
		for name in ("page.html", "run.exe", "logo.svg", "noextension"):
			self.assertRaises(frappe.ValidationError, self.upload, filename=name)
		self.assertRaises(frappe.ValidationError, self.upload, content="")
		self.assertRaises(frappe.ValidationError, self.upload, content="not base64!!")
		self.assertEqual(self.listed(), [])

	def test_the_name_is_cleaned(self):
		added = self.upload(filename="../../etc/Quote <v2>.PDF")
		self.assertEqual(added["file_name"], "Quote v2.pdf")

	def test_a_file_is_at_most_10_mb(self):
		with patch.object(lead_files, "MAX_FILE_BYTES", 10):
			self.assertRaises(frappe.ValidationError, self.upload)

	def test_a_lead_holds_at_most_50_files(self):
		with patch.object(lead_files, "MAX_FILES", 2):
			self.upload()
			self.upload(filename="b.pdf")
			self.assertRaises(frappe.ValidationError, self.upload, filename="c.pdf")

	def test_files_follow_the_lead_permission(self):
		added = self.upload()
		self.assertRaises(frappe.PermissionError, self.upload, user=BDE2)
		self.assertRaises(frappe.PermissionError, hub.get_lead_files, self.lead, acting_user=BDE2)
		self.assertRaises(
			frappe.PermissionError, hub.get_lead_file, self.lead, added["name"], acting_user=BDE2
		)
		self.assertRaises(
			frappe.PermissionError, hub.delete_lead_file, self.lead, added["name"], acting_user=BDE2
		)

	def test_another_leads_file_or_a_photo_is_not_served(self):
		other = make_lead(BDE, "Other Files Lead")
		elsewhere = hub.upload_lead_file(other, "a.pdf", PDF, acting_user=BDE)
		photo = hub.upload_lead_photo(self.lead, "front.png", make_image_base64(), acting_user=BDE)
		for name in (elsewhere["name"], photo["name"]):
			self.assertRaises(frappe.DoesNotExistError, hub.get_lead_file, self.lead, name, acting_user=BDE)
			self.assertRaises(
				frappe.DoesNotExistError, hub.delete_lead_file, self.lead, name, acting_user=BDE
			)

	def test_the_uploader_and_a_manager_may_delete(self):
		added = self.upload()
		hub.delete_lead_file(self.lead, added["name"], acting_user=BDE)
		self.assertFalse(frappe.db.exists("File", added["name"]))
		again = self.upload()
		hub.delete_lead_file(self.lead, again["name"], acting_user=MANAGER)
		self.assertEqual(self.listed(), [])

	def test_someone_else_with_write_may_not_delete(self):
		frappe.set_user("Administrator")
		added = lead_files.add_file(self.lead, "a.pdf", base64.b64decode(PDF))
		frappe.db.set_value("Lead", self.lead, {"lead_owner": BDE2, "owner": BDE2})
		frappe.set_user(BDE)
		doc = lead_files.lead_file(self.lead, added["name"])
		self.assertFalse(lead_files.can_delete(doc, frappe.get_doc("Lead", self.lead)))
