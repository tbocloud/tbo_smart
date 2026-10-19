"""The editors' side of the proposal libraries (docs/proposals.md §3.2–§3.4): the module
catalogue, the section libraries and the image library, read by everyone who builds proposals
and changed only by the roles that edit presets and prices."""

import os

import frappe
from frappe import _
from frappe.utils import cint, flt

from tbo_smart.lead_pipeline import document_types
from tbo_smart.lead_pipeline import proposal_builder as builder
from tbo_smart.lead_pipeline import proposal_media as media
from tbo_smart.lead_pipeline.presets import can_edit_presets, require_preset_editor

KINDS = {"module": builder.MODULE, "item": builder.LIBRARY, "image": builder.IMAGE}
FIELDS = {
	"module": (
		"module_name",
		"solution_types",
		"description",
		"features",
		"effort_days",
		"default_price",
		"item",
		"fit",
		"sort_order",
		"enabled",
		"scope",
		"deliverables",
		"assumptions",
		"out_of_scope",
		"workflow",
		"image_tag",
	),
	"item": (
		"library",
		"title",
		"body",
		"group",
		"condition",
		"weeks",
		"amount",
		"solution_types",
		"sort_order",
		"enabled",
	),
	"image": ("title", "alt_text", "sections", "solution_types", "modules", "enabled"),
}
NUMBERS = {"effort_days", "default_price", "weeks", "amount"}
INTEGERS = {"sort_order", "enabled"}


def library_payload() -> dict:
	"""Everything in the libraries (disabled ones too, for the editors), and the pick lists."""
	item_meta = frappe.get_meta(builder.LIBRARY)
	return {
		"can_edit": can_edit_presets(),
		"modules": frappe.get_all(
			builder.MODULE, fields=["name", *FIELDS["module"]], order_by="sort_order asc, module_name asc"
		),
		"items": frappe.get_all(
			builder.LIBRARY,
			fields=["name", *FIELDS["item"]],
			order_by="library asc, sort_order asc, creation asc",
		),
		"images": [
			{**row, "url": frappe.utils.get_url(row.image) if row.image else None}
			for row in frappe.get_all(
				builder.IMAGE, fields=["name", "image", *FIELDS["image"]], order_by="title asc"
			)
		],
		"libraries": (item_meta.get_field("library").options or "").split("\n"),
		"conditions": [c for c in (item_meta.get_field("condition").options or "").split("\n") if c],
		"fits": list(builder.FITS),
		"scope_groups": list(builder.SCOPE_GROUPS),
		# an Agreement Clause's group: the agreement section it shows in (docs/client-documents.md §4)
		"clause_groups": list(dict.fromkeys(document_types.CLAUSE_GROUPS.values())),
		"hosting": list(builder.HOSTING),
	}


def save_record(kind: str, data: dict) -> str:
	"""Create or update a module, a library item or a library image's tags (editors only)."""
	require_preset_editor()
	doctype = doctype_of(kind)
	name = (data.get("name") or "").strip()
	doc = (
		frappe.get_doc(doctype, name) if name and frappe.db.exists(doctype, name) else frappe.new_doc(doctype)
	)
	if kind == "image" and doc.is_new():
		frappe.throw(_("Upload a new library image first."))
	values = {}
	for field in FIELDS[kind]:
		if field not in data:
			continue
		value = data[field]
		if field in NUMBERS:
			value = max(flt(value), 0)
		elif field in INTEGERS:
			value = cint(value)
		elif isinstance(value, list):
			value = "\n".join(str(entry).strip() for entry in value if str(entry).strip())
		values[field] = value if value not in ("", None) else (0 if field in NUMBERS | INTEGERS else None)
	doc.update(values)
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	return doc.name


def delete_record(kind: str, name: str) -> None:
	"""Delete a module, a library item or a library image (editors only). A proposal keeps what
	it already copied."""
	require_preset_editor()
	doctype = doctype_of(kind)
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} not found.").format(name), frappe.DoesNotExistError)
	frappe.delete_doc(doctype, name, ignore_permissions=True)


def upload_library_image(filename: str | None, content: bytes, values: dict) -> str:
	"""A new library image (editors only): PNG, JPEG or WebP up to 5 MB, a public file, as
	library images are meant for clients' eyes (no confidential pictures here)."""
	require_preset_editor()
	if not content:
		frappe.throw(_("Choose an image to upload."))
	if len(content) > media.MAX_IMAGE_BYTES:
		frappe.throw(_("The image is larger than 5 MB. Make it smaller and try again."))
	extension, _mime = media.image_format(content)
	title = media.clean(values.get("title"), 140)
	alt = media.clean(values.get("alt_text"), media.MAX_ALT)
	if not (title and alt):
		frappe.throw(_("Give the image a title and its alt text."))
	stem = os.path.splitext(os.path.basename(filename or ""))[0][:80].strip() or "image"
	doc = frappe.new_doc(builder.IMAGE)
	doc.update({"title": title, "alt_text": alt, "enabled": 1})
	for field in ("sections", "solution_types", "modules"):
		entries = values.get(field) or []
		doc.set(field, "\n".join(entries) if isinstance(entries, list) else entries)
	file = frappe.get_doc(
		{"doctype": "File", "file_name": f"{stem}.{extension}", "is_private": 0, "content": content}
	).insert(ignore_permissions=True)
	doc.image = file.file_url
	doc.insert(ignore_permissions=True)
	file.db_set({"attached_to_doctype": builder.IMAGE, "attached_to_name": doc.name})
	return doc.name


def doctype_of(kind: str) -> str:
	if kind not in KINDS:
		frappe.throw(_("Kind is one of: {0}.").format(", ".join(KINDS)))
	return KINDS[kind]


def draft_module(name: str) -> dict:
	"""The AI's draft of a catalogue module's text (editors; library upkeep, not per proposal).
	Nothing is saved: the editor reviews it and saves."""
	from tbo_smart.lead_pipeline.proposals import ai_enabled

	require_preset_editor()
	if not ai_enabled():
		frappe.throw(_("AI proposal drafts are not switched on."))
	if not frappe.db.exists(builder.MODULE, name):
		frappe.throw(_("{0} not found.").format(name), frappe.DoesNotExistError)
	return builder.draft_module(name)
