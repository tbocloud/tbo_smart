"""Where a lead is and what it looks like: its Google Business Profile, a Maps pin, its
coordinates, a note on how to find it, and photos (docs/hub-leads-api.md §2.11).

Photos are private Files attached to the Lead, marked `tbo_lead_photo` so a business card the
app scanned onto the same lead is never listed as one. The bytes are served only through
`hub_leads.get_lead_photo`, which checks the person's right to read the lead.
"""

import io
import re
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import cint, flt

from tbo_smart.lead_pipeline.proposal_media import file_mime, image_format

DOCTYPE = "Lead"
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTOS = 20
MAX_CAPTION = 140
MAX_NOTE = 500
MAX_URL = 500
THUMB_SIZE = 480
MANAGER_ROLES = ("Sales Manager", "System Manager")
# what a HEIC / HEIF file starts with after its size, told by the bytes
HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heim", b"heis")

GOOGLE_HOST = re.compile(r"^(www\.|maps\.)?google\.(com|co\.[a-z]{2}|[a-z]{2,3})$")
# (hosts, path prefix a link needs, or None) a Google Business Profile may be shared as
PROFILE_LINKS = (
	(("maps.app.goo.gl", "g.page", "business.google.com", "share.google"), None),
	(("goo.gl",), "/maps"),
)
MAPS_LINKS = (
	(("maps.app.goo.gl",), None),
	(("goo.gl",), "/maps"),
)


def clean_url(value, links, label: str) -> str | None:
	"""`value` as an https link to one of Google's own hosts, or None when blank. Anything else
	is refused: the link is opened on a phone and shown to others."""
	value = str(value or "").strip()
	if not value:
		return None
	if len(value) > MAX_URL:
		frappe.throw(_("{0} is too long.").format(label))
	if not re.match(r"^[a-z][a-z0-9+.-]*:", value, re.I):
		value = f"https://{value}"
	parsed = urlparse(value)
	host = (parsed.hostname or "").lower()
	if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
		frappe.throw(_("{0} must be a Google Maps link.").format(label))
	if not accepted(host, parsed.path, links):
		frappe.throw(_("{0} must be a Google Maps link.").format(label))
	return parsed._replace(scheme="https", fragment="").geturl()


def accepted(host: str, path: str, links) -> bool:
	if GOOGLE_HOST.match(host) and path.startswith("/maps"):
		return True
	return any(host in hosts and (not prefix or path.startswith(prefix)) for hosts, prefix in links)


def validate_place(doc, method=None) -> None:
	"""Lead validate: the profile and map links are Google's, the coordinates are a real pair
	(both or neither) and the notes fit."""
	doc.tbo_google_profile_url = clean_url(
		doc.get("tbo_google_profile_url"), PROFILE_LINKS, _("The Google profile link")
	)
	doc.tbo_maps_url = clean_url(doc.get("tbo_maps_url"), MAPS_LINKS, _("The map link"))
	latitude, longitude = flt(doc.get("tbo_latitude")), flt(doc.get("tbo_longitude"))
	if not latitude and not longitude:
		doc.tbo_latitude = doc.tbo_longitude = None
	elif not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
		frappe.throw(_("Latitude is -90 to 90 and longitude -180 to 180."))
	doc.tbo_location_note = (doc.get("tbo_location_note") or "").strip()[:MAX_NOTE] or None


def place_of(lead) -> dict:
	"""The place fields as the hub reads them."""
	has_pin = bool(flt(lead.get("tbo_latitude")) or flt(lead.get("tbo_longitude")))
	return {
		"google_profile_url": lead.get("tbo_google_profile_url") or None,
		"maps_url": lead.get("tbo_maps_url") or None,
		"latitude": flt(lead.tbo_latitude) if has_pin else None,
		"longitude": flt(lead.tbo_longitude) if has_pin else None,
		"location_note": lead.get("tbo_location_note") or None,
	}


# ---------- photos ----------


def photo_rows(lead: str) -> list[dict]:
	file = frappe.qb.DocType("File")
	return (
		frappe.qb.from_(file)
		.select(
			file.name,
			file.file_name,
			file.file_url,
			file.file_size,
			file.tbo_caption.as_("caption"),
			file.owner.as_("uploaded_by"),
			file.creation.as_("uploaded_on"),
		)
		.where(file.attached_to_doctype == DOCTYPE)
		.where(file.attached_to_name == lead)
		.where(file.tbo_lead_photo == 1)
		.orderby(file.creation, order=frappe.qb.desc)
		.run(as_dict=True)
	)


def photo_count(lead: str) -> int:
	return frappe.db.count(
		"File", {"attached_to_doctype": DOCTYPE, "attached_to_name": lead, "tbo_lead_photo": 1}
	)


def photo_payload(row: dict) -> dict:
	return {
		**row,
		"uploaded_by_name": frappe.utils.get_fullname(row["uploaded_by"]),
		"uploaded_on": str(row["uploaded_on"]),
	}


def get_photos(lead: str) -> list[dict]:
	frappe.has_permission(DOCTYPE, "read", lead, throw=True)
	return [photo_payload(row) for row in photo_rows(lead)]


def check_photo_bytes(content: bytes) -> str:
	"""The extension of a PNG, JPEG or WebP photo of at most 10 MB; anything else is refused."""
	if not content:
		frappe.throw(_("Choose a photo to upload."))
	if len(content) > MAX_PHOTO_BYTES:
		frappe.throw(_("The photo is larger than 10 MB. Make it smaller and try again."))
	if content[4:8] == b"ftyp" and content[8:12] in HEIF_BRANDS:
		frappe.throw(_("HEIC photos can't be stored. Take the photo as JPEG, or upload it from the hub."))
	return image_format(content)[0]


def add_photo(lead: str, filename: str | None, content: bytes, caption: str | None = None) -> dict:
	"""A private photo on the lead; the person needs write on the lead."""
	frappe.has_permission(DOCTYPE, "write", lead, throw=True)
	extension = check_photo_bytes(content)
	if photo_count(lead) >= MAX_PHOTOS:
		frappe.throw(_("A lead can have {0} photos. Delete one to add another.").format(MAX_PHOTOS))
	stem = re.sub(r"[^\w .-]", "", (filename or "").rsplit(".", 1)[0].rsplit("/", 1)[-1])[:80].strip()
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{stem or 'photo'}.{extension}",
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": lead,
			"is_private": 1,
			"content": content,
			"tbo_lead_photo": 1,
			"tbo_caption": (caption or "").strip()[:MAX_CAPTION] or None,
		}
	)
	# the person's right to the lead was checked above
	file.insert(ignore_permissions=True)
	return photo_payload(next(row for row in photo_rows(lead) if row["name"] == file.name))


def photo_file(lead: str, file: str):
	"""The File of this lead's photo `file`, or DoesNotExistError (never another lead's or
	another kind of attachment)."""
	found = frappe.db.get_value(
		"File",
		{"name": file or "", "attached_to_doctype": DOCTYPE, "attached_to_name": lead, "tbo_lead_photo": 1},
	)
	if not found:
		frappe.throw(_("That photo isn't on this lead."), frappe.DoesNotExistError)
	return frappe.get_doc("File", found)


def can_delete(doc, lead_doc) -> bool:
	"""Who uploaded it, the lead's owner, or a manager."""
	user = frappe.session.user
	return user in (doc.owner, lead_doc.lead_owner, lead_doc.owner) or bool(
		set(MANAGER_ROLES) & set(frappe.get_roles(user))
	)


def remove_photo(lead: str, file: str) -> None:
	lead_doc = frappe.get_doc(DOCTYPE, lead)
	lead_doc.check_permission("write")
	doc = photo_file(lead, file)
	if not can_delete(doc, lead_doc):
		frappe.throw(
			_("Only who added the photo, the lead's owner or a manager can delete it."),
			frappe.PermissionError,
		)
	frappe.delete_doc("File", doc.name, ignore_permissions=True)


def photo_bytes(lead: str, file: str, thumb: int | str = 0) -> tuple[bytes, str, str]:
	"""(content, mime, file name) of a photo the person may read; `thumb` a JPEG of at most
	480 px, turned the right way up."""
	from PIL import Image, ImageOps

	frappe.has_permission(DOCTYPE, "read", lead, throw=True)
	doc = photo_file(lead, file)
	content = doc.get_content()
	content = content.encode() if isinstance(content, str) else content
	if not cint(thumb):
		return content, file_mime(doc) or "application/octet-stream", doc.file_name
	with Image.open(io.BytesIO(content)) as image:
		small = ImageOps.exif_transpose(image).convert("RGB")
	small.thumbnail((THUMB_SIZE, THUMB_SIZE))
	out = io.BytesIO()
	small.save(out, format="JPEG", quality=80)
	return out.getvalue(), "image/jpeg", f"thumb-{doc.name}.jpg"
