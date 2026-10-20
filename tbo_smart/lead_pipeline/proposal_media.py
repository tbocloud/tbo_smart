"""A proposal's images and letter head (docs/proposals.md §2.1, §2.2).

Images are Files attached to the proposal (an upload is private; a library image is its own
File of the library's file): an Image block's picture, a cover image, or a section's picture. The client's page and the hub's preview load them through the token-gated
`proposal_page.image`, which serves only a file this proposal shows; the PDF embeds them as
data URIs, so nothing private is ever made public.

The letter head is ERPNext's own Letter Head: the proposal's pick, else TBO Smart Settings →
Proposal letter head, else the Letter Head marked default. Every proposal has one; only a site
with no Letter Head at all falls back to the company's logo (and the hub warns editors). Its HTML
is sanitised before a guest sees it.
"""

import base64
import io
import os
import pathlib

import frappe
from frappe import _
from frappe.utils import get_url
from frappe.utils.html_utils import sanitize_html

from tbo_smart.lead_pipeline import setting

DOCTYPE = "TBO Proposal"
LETTER_HEAD = "Letter Head"
# every section may carry an image but these two, which are the price and the signatures
NO_IMAGE = ("Investment", "Acceptance")
IMAGE_SIZES = ("Full", "Half")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
# the PDF's page: A4 less 18 mm on each side; a letter head image spans the content width, and
# one taller than HEADER_MAX_MM is narrowed instead, so it is never clipped
CONTENT_WIDTH_MM = 174
HEADER_MAX_MM = 30
# a footer picture is a band about 6:1 (29 mm at the content width): allow that much before narrowing
FOOTER_MAX_MM = 30
# Geist (SIL OFL, public/fonts/OFL.txt), cut to Latin with tabular figures as the default, because
# wkhtmltopdf can't switch the tnum feature on and can't load Google Fonts at render time. Two
# weights: its WebKit takes 500 for 400, so a Medium face would replace the Regular one.
FONT_WEIGHTS = {400: "Regular", 600: "SemiBold"}
MAX_ALT = 140
MAX_CAPTION = 280
# what an uploaded image may be, told by its bytes (never by its name); SVG is refused: it can
# carry script
FORMATS = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "WEBP": ("webp", "image/webp")}
# what a letter head's own file may be served as to a guest
SERVABLE = {
	".png": "image/png",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".webp": "image/webp",
	".gif": "image/gif",
}


# ---------- images ----------


def upload_image(doc, filename: str | None, content: bytes) -> dict:
	"""A new private image on a draft proposal: at most 5 MB, PNG, JPEG or WebP. Returns
	{file, url}; the builder puts `file` in a block and saves."""
	from tbo_smart.lead_pipeline.proposals import require_draft

	require_draft(doc)
	if not content:
		frappe.throw(_("Choose an image to upload."))
	if len(content) > MAX_IMAGE_BYTES:
		frappe.throw(_("The image is larger than 5 MB. Make it smaller and try again."))
	extension, _mime = image_format(content)
	stem = os.path.splitext(os.path.basename(filename or ""))[0][:80].strip() or "image"
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{stem}.{extension}",
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": doc.name,
			"is_private": 1,
			"content": content,
		}
	)
	# the person's right to the proposal was checked (write on its lead)
	file.insert(ignore_permissions=True)
	return {"file": file.name, "url": image_url(doc, file.name)}


def decode(content: str | None) -> bytes:
	"""An upload sent as base64."""
	try:
		return base64.b64decode(content or "", validate=True)
	except ValueError:  # binascii.Error is a ValueError
		frappe.throw(_("Send the image as base64."))


def image_format(content: bytes) -> tuple[str, str]:
	"""(extension, mime) of a PNG, JPEG or WebP image; anything else is refused."""
	from PIL import Image, UnidentifiedImageError

	try:
		with Image.open(io.BytesIO(content)) as image:
			kind = image.format
			image.verify()
	except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
		kind = None
	if kind not in FORMATS:
		frappe.throw(_("Upload a PNG, JPEG or WebP image."))
	return FORMATS[kind]


def check_images(doc) -> None:
	"""Any section but the Investment and the Acceptance may carry an image; it is one of this
	proposal's own files, and it has alt text."""
	own = set(attached_images(doc.name)) if doc.name else set()
	for row in doc.sections:
		if row.section_type in NO_IMAGE:
			row.image = row.image_alt = row.image_caption = None
		row.image_alt = clean(row.image_alt, MAX_ALT) or None
		row.image_caption = clean(row.image_caption, MAX_CAPTION) or None
		if row.image_size not in IMAGE_SIZES:
			row.image_size = IMAGE_SIZES[0]
		if not row.image:
			continue
		if row.image not in own:
			frappe.throw(
				_("That image isn't one of this proposal's. Upload it again."), frappe.PermissionError
			)
		if not row.image_alt:
			frappe.throw(_("Describe each image in its alt text, for people who can't see it."))


def clean(value, limit: int) -> str:
	return str(value or "").strip()[:limit]


def attached_images(name: str) -> list[str]:
	file = frappe.qb.DocType("File")
	return [
		row[0]
		for row in frappe.qb.from_(file)
		.select(file.name)
		.where(file.attached_to_doctype == DOCTYPE)
		.where(file.attached_to_name == name)
		.where(file.is_folder == 0)
		.run()
	]


def used_images(doc) -> set[str]:
	return {row.image for row in doc.sections if row.image}


def copy_images(source, revision) -> None:
	"""A revision shows the same images: each gets its own File on the revision (the same
	file on disk), so a revision never depends on its predecessor's attachments."""
	copies = {}
	for name in used_images(source):
		original = frappe.get_doc("File", name)
		copy = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": original.file_name,
				"file_url": original.file_url,
				"attached_to_doctype": DOCTYPE,
				"attached_to_name": revision.name,
				"is_private": 1,
			}
		)
		copy.insert(ignore_permissions=True)
		copies[name] = copy.name
	for row in revision.sections:
		row.image = copies.get(row.image) if row.image else None


def drop_unused_images(doc) -> None:
	"""When a proposal goes out its content is fixed: uploads it doesn't show are deleted."""
	used = used_images(doc)
	for name in attached_images(doc.name):
		if name not in used:
			frappe.delete_doc("File", name, ignore_permissions=True)


def image_url(doc, file_name: str) -> str:
	return get_url(f"/api/method/tbo_smart.api.proposal_page.image?token={doc.public_token}&file={file_name}")


def image_src(doc, file_name: str | None, pdf: bool = False) -> str | None:
	"""Where the page loads an image from: the token-gated link, or in the PDF the image
	itself as a data URI (wkhtmltopdf fetches nothing)."""
	if not file_name:
		return None
	if not pdf:
		return image_url(doc, file_name)
	file = frappe.get_doc("File", file_name)
	return data_uri(file)


def data_uri(file) -> str | None:
	mime = SERVABLE.get(os.path.splitext(file.file_name or file.file_url or "")[1].lower())
	if not mime:
		return None
	try:
		content = file.get_content()
	except (OSError, frappe.DoesNotExistError):
		return None
	if isinstance(content, str):
		content = content.encode()
	return f"data:{mime};base64,{base64.b64encode(content).decode()}"


def image_payload(doc, row, pdf: bool = False) -> dict | None:
	"""A block's image as the page shows it, or None."""
	if not row.image or row.section_type in NO_IMAGE:
		return None
	src = image_src(doc, row.image, pdf)
	if not src:
		return None
	return {
		"src": src,
		"alt": row.image_alt or "",
		"caption": row.image_caption,
		"size": "half" if row.image_size == "Half" else "full",
	}


def servable_file(doc, file_name: str | None):
	"""The File a guest may load for this proposal: an image one of its blocks shows (attached
	to it), or a private image of its letter head. None for anything else."""
	if not isinstance(file_name, str) or not file_name or not frappe.db.exists("File", file_name):
		return None
	file = frappe.get_doc("File", file_name)
	if file_name in used_images(doc):
		if file.attached_to_doctype == DOCTYPE and file.attached_to_name == doc.name:
			return file
		return None
	head = letter_head_name(doc)
	if head and file.is_private and file.file_url in letter_head_files(head):
		return file
	return None


def file_mime(file) -> str | None:
	return SERVABLE.get(os.path.splitext(file.file_name or file.file_url or "")[1].lower())


# ---------- the letter head ----------


def letter_head_name(doc) -> str | None:
	"""The proposal's letter head: its own pick, else the default."""
	if usable_letter_head(doc.get("letter_head_override")):
		return doc.letter_head_override
	return default_letter_head()


def default_letter_head() -> str | None:
	"""TBO Smart Settings → Proposal letter head, else the Letter Head marked default."""
	chosen = setting("proposal_letter_head")
	if usable_letter_head(chosen):
		return chosen
	return frappe.db.get_value(LETTER_HEAD, {"is_default": 1, "disabled": 0}, "name")


def usable_letter_head(name: str | None) -> bool:
	return bool(name) and bool(frappe.db.exists(LETTER_HEAD, {"name": name, "disabled": 0}))


def letter_heads() -> list[dict]:
	"""The enabled Letter Heads, for a picker."""
	head = frappe.qb.DocType(LETTER_HEAD)
	return (
		frappe.qb.from_(head)
		.select(head.name, head.is_default)
		.where(head.disabled == 0)
		.orderby(head.name)
		.run(as_dict=True)
	)


def letter_head_files(name: str) -> set[str]:
	values = frappe.db.get_value(LETTER_HEAD, name, ["image", "footer_image"], as_dict=True) or {}
	return {url for url in values.values() if url}


def letter_head_context(doc, pdf: bool = False) -> dict | None:
	"""The letter head's header and footer as the page shows them, or None (the page then shows
	the company's logo). Each part is {image: src, align} or {html: sanitised HTML}."""
	name = letter_head_name(doc)
	if not name:
		return None
	head = frappe.get_doc(LETTER_HEAD, name)
	header = letter_head_part(doc, head.source, head.image, head.content, head.align, pdf, HEADER_MAX_MM)
	# an HTML footer around a picture (Frappe writes one from an attached footer image) is that
	# picture: shown at the content's width, without the text beside it
	footer_image = head.footer_image if head.footer_source == "Image" else html_image(head.footer)
	if footer_image:
		footer = letter_head_part(doc, "Image", footer_image, None, head.footer_align, pdf, FOOTER_MAX_MM)
	else:
		footer = letter_head_part(doc, head.footer_source, None, head.footer, head.footer_align, pdf)
	if not header and not footer:
		return None
	return {"name": name, "header": header, "footer": footer}


def letter_head_part(
	doc, source, image, html, align, pdf: bool, max_mm: float = HEADER_MAX_MM
) -> dict | None:
	align = (align or "Left").lower()
	if source == "Image":
		src = letter_head_image_src(doc, image, pdf)
		if not src:
			return None
		return {"image": src, "align": align, "height": image_height(src, max_mm) if pdf else None}
	html = sanitize_html(html or "", always_sanitize=True).strip()
	return {"html": html} if html else None


def letter_head_image_src(doc, file_url: str | None, pdf: bool) -> str | None:
	"""A public letter head image by its URL; a private one through the proposal's token. In
	the PDF, either as a data URI."""
	if not file_url or not file_url.startswith(("/files/", "/private/files/")):
		return None
	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if pdf:
		return data_uri(frappe.get_doc("File", file_name)) if file_name else None
	if file_url.startswith("/files/"):
		return get_url(file_url)
	return image_url(doc, file_name) if file_name else None


def html_image(html: str | None) -> str | None:
	"""The site file shown by the first picture in a letter head's HTML, or None."""
	from urllib.parse import urlparse

	from bs4 import BeautifulSoup

	img = BeautifulSoup(html or "", "html.parser").find("img")
	path = urlparse(img.get("src") or "").path if img else ""
	return path if path.startswith(("/files/", "/private/files/")) else None


def image_height(uri: str, max_mm: float = HEADER_MAX_MM) -> float | None:
	"""A data-URI letter head image's height in mm at the content width, at most `max_mm` (a
	taller one is narrowed instead); None when the file isn't an image Pillow reads."""
	from PIL import Image

	try:
		with Image.open(io.BytesIO(base64.b64decode(uri.partition(",")[2]))) as image:
			width, height = image.size
	except OSError:
		return None
	return round(min(CONTENT_WIDTH_MM * height / width, max_mm), 1)


def font_data(weight: int = 400) -> bytes:
	"""A bundled Geist face (a fixed file of the app, never a name from a request)."""
	return pathlib.Path(
		frappe.get_app_path("tbo_smart", "public", "fonts", f"Geist-{FONT_WEIGHTS[weight]}.ttf")
	).read_bytes()


def font_faces(pdf: bool) -> list[dict]:
	"""[{weight, src}] for the page's @font-face rules: the bundled files on the web page, data
	URIs in the PDF (wkhtmltopdf runs with local file access off)."""
	if not pdf:
		return [
			{"weight": weight, "src": f"/assets/tbo_smart/fonts/Geist-{name}.ttf"}
			for weight, name in FONT_WEIGHTS.items()
		]
	return [
		{"weight": weight, "src": f"data:font/ttf;base64,{base64.b64encode(font_data(weight)).decode()}"}
		for weight in FONT_WEIGHTS
	]


def set_default_letter_head(name: str | None) -> None:
	"""TBO Smart Settings → Proposal letter head (blank: the Letter Head marked default)."""
	if name and not usable_letter_head(name):
		frappe.throw(_("Letter head {0} not found.").format(name), frappe.DoesNotExistError)
	frappe.db.set_single_value("TBO Smart Settings", "proposal_letter_head", name or None)
	# a cached settings document saved later must not write the old value back
	frappe.clear_document_cache("TBO Smart Settings", "TBO Smart Settings")


def check_letter_head(doc) -> None:
	"""A proposal's own letter head is an enabled one."""
	if doc.get("letter_head_override") and not usable_letter_head(doc.letter_head_override):
		frappe.throw(
			_("Letter head {0} not found.").format(doc.letter_head_override), frappe.DoesNotExistError
		)
