"""Files on a lead: a requirement sheet, a quotation the client sent, a visiting card scan
(docs/hub-leads-api.md §2.34).

A lead's attachments are every File attached to the Lead that isn't one of its photos
(`lead_place.py` lists those), so a file someone attached on the desk form shows in the hub
too. The hub adds them private; the bytes are served only through
`hub_leads.get_lead_file`, which checks the person's right to read the lead.
"""

import os
import re

import frappe
from frappe import _
from frappe.query_builder.functions import IfNull

from tbo_smart.lead_pipeline.lead_place import MANAGER_ROLES, can_delete

DOCTYPE = "Lead"
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 50
# what may be added from the hub, by extension, and the type each is served as; a file
# attached on the desk with any other extension is served as plain bytes to save
TYPES = {
	".pdf": "application/pdf",
	".doc": "application/msword",
	".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
	".xls": "application/vnd.ms-excel",
	".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
	".ppt": "application/vnd.ms-powerpoint",
	".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
	".odt": "application/vnd.oasis.opendocument.text",
	".ods": "application/vnd.oasis.opendocument.spreadsheet",
	".csv": "text/csv",
	".txt": "text/plain",
	".png": "image/png",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".webp": "image/webp",
	".zip": "application/zip",
}


def file_rows(lead: str, name: str | None = None) -> list[dict]:
	file = frappe.qb.DocType("File")
	query = (
		frappe.qb.from_(file)
		.select(
			file.name,
			file.file_name,
			file.file_size,
			file.owner.as_("uploaded_by"),
			file.creation.as_("uploaded_on"),
		)
		.where(file.attached_to_doctype == DOCTYPE)
		.where(file.attached_to_name == lead)
		.where(IfNull(file.tbo_lead_photo, 0) == 0)
		.where(IfNull(file.is_folder, 0) == 0)
		# a link to another site has no bytes here to serve
		.where(file.file_url.like("/%"))
		.orderby(file.creation, order=frappe.qb.desc)
	)
	if name:
		query = query.where(file.name == name)
	return query.run(as_dict=True)


def payloads(lead: str, rows: list[dict]) -> list[dict]:
	"""The rows as the hub lists them; `can_delete` is whether the person may delete each
	(write on the lead, and the rule `remove_file` applies)."""
	lead_doc = frappe.get_doc(DOCTYPE, lead)
	user = frappe.session.user
	deleters = (lead_doc.lead_owner, lead_doc.owner)
	manager = bool(set(MANAGER_ROLES) & set(frappe.get_roles(user)))
	can_write = bool(lead_doc.has_permission("write"))
	return [
		{
			**row,
			"uploaded_by_name": frappe.utils.get_fullname(row["uploaded_by"]),
			"uploaded_on": str(row["uploaded_on"]),
			"can_delete": can_write and (manager or user in (row["uploaded_by"], *deleters)),
		}
		for row in rows
	]


def get_files(lead: str) -> list[dict]:
	frappe.has_permission(DOCTYPE, "read", lead, throw=True)
	return payloads(lead, file_rows(lead))


def clean_name(filename: str | None) -> tuple[str, str]:
	"""(stem, extension) of an upload's name; an extension outside TYPES is refused."""
	base = (filename or "").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
	stem, extension = os.path.splitext(base)
	extension = extension.lower()
	if extension not in TYPES:
		frappe.throw(
			_("Attach a PDF, Word, Excel, PowerPoint, CSV, text, image or ZIP file."),
		)
	stem = re.sub(r"[^\w .()-]", "", stem)[:100].strip()
	return stem or "file", extension


def add_file(lead: str, filename: str | None, content: bytes) -> dict:
	"""A private file on the lead; the person needs write on the lead."""
	frappe.has_permission(DOCTYPE, "write", lead, throw=True)
	stem, extension = clean_name(filename)
	if not content:
		frappe.throw(_("Choose a file to attach."))
	if len(content) > MAX_FILE_BYTES:
		frappe.throw(_("The file is larger than 10 MB. Make it smaller and try again."))
	if len(file_rows(lead)) >= MAX_FILES:
		frappe.throw(_("A lead can have {0} files. Delete one to add another.").format(MAX_FILES))
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{stem}{extension}",
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": lead,
			"is_private": 1,
			"content": content,
		}
	)
	# the person's right to the lead was checked above
	file.insert(ignore_permissions=True)
	return payloads(lead, file_rows(lead, file.name))[0]


def lead_file(lead: str, file: str):
	"""The File `file` of this lead's attachments, or DoesNotExistError (never another lead's
	file or one of its photos)."""
	if not file or not file_rows(lead, file):
		frappe.throw(_("That file isn't on this lead."), frappe.DoesNotExistError)
	return frappe.get_doc("File", file)


def remove_file(lead: str, file: str) -> None:
	lead_doc = frappe.get_doc(DOCTYPE, lead)
	lead_doc.check_permission("write")
	doc = lead_file(lead, file)
	if not can_delete(doc, lead_doc):
		frappe.throw(
			_("Only who added the file, the lead's owner or a manager can delete it."),
			frappe.PermissionError,
		)
	frappe.delete_doc("File", doc.name, ignore_permissions=True)


def file_bytes(lead: str, file: str) -> tuple[bytes, str, str]:
	"""(content, mime, file name) of a file the person may read."""
	frappe.has_permission(DOCTYPE, "read", lead, throw=True)
	doc = lead_file(lead, file)
	content = doc.get_content()
	content = content.encode() if isinstance(content, str) else content
	mime = TYPES.get(os.path.splitext(doc.file_name or "")[1].lower(), "application/octet-stream")
	return content, mime, doc.file_name
