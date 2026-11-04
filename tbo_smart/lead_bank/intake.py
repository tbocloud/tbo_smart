"""Adding a lead from a photo (docs/lead-bank.md §2-§4).

The hub reads the photos with its AI and shows the collector a short form; this is what the form
submits. The lead is made here, not as the collector (a designer has no CRM permissions): with
who found it, where, the photos, and the source "Staff referral". A business we already have is
not made twice: the first collector keeps the credit and the repeat becomes a comment.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, get_fullname, getdate, now_datetime

from tbo_smart import lead_source
from tbo_smart.lead_bank import REGION_INFO, check_region, department_for, detect_region
from tbo_smart.lead_pipeline import lead_category, lead_place
from tbo_smart.lead_pipeline.proposal_media import decode
from tbo_smart.lead_pipeline.setup import MANAGEMENT_ROLE, STAFF_REFERRAL
from tbo_smart.sales import duplicates
from tbo_smart.sales.assignment import is_round_robin

FIELDS = {
	"company_name",
	"contact_name",
	"mobile_no",
	"phone",
	"email_id",
	"website",
	"business_type",
	"address",
	"region",
	"state",
	"latitude",
	"longitude",
	"note",
}
MAX_TEXT = 500
MAX_PHOTOS = 5
MANAGER_ROLES = ("Sales Manager", MANAGEMENT_ROLE)


def clean_data(data) -> frappe._dict:
	"""The form's fields, trimmed; unknown ones are refused."""
	data = frappe.parse_json(data) if isinstance(data, str) else data
	if not isinstance(data, dict):
		frappe.throw(_("data must be an object."))
	if unknown := sorted(set(data) - FIELDS):
		frappe.throw(_("Unknown fields: {0}").format(", ".join(unknown)))
	clean = frappe._dict({key: " ".join(str(data.get(key) or "").split())[:MAX_TEXT] for key in FIELDS})
	# a note keeps its line breaks
	clean.note = str(data.get("note") or "").strip()[:2000]
	clean.latitude, clean.longitude = data.get("latitude"), data.get("longitude")
	return clean


def check_photos(photos) -> list[tuple[str, bytes]]:
	"""[(file name, bytes)] of the photos sent as [{filename, content (base64)}]: PNG, JPEG or WebP,
	up to 5, each under 10 MB. At least one: the photo is what the team works from."""
	photos = frappe.parse_json(photos) if isinstance(photos, str) else (photos or [])
	if not photos:
		frappe.throw(_("Add a photo of the board, card or ad."))
	if len(photos) > MAX_PHOTOS:
		frappe.throw(_("Add up to {0} photos.").format(MAX_PHOTOS))
	checked = []
	for photo in photos:
		content = decode(photo.get("content"))
		lead_place.check_photo_bytes(content)
		checked.append((photo.get("filename"), content))
	return checked


def existing_lead(data) -> dict | None:
	"""The lead that already has this email, phone or company, if any."""
	phones = [data.mobile_no, data.phone]
	matches = duplicates.find_duplicates(data.email_id, phones[0] or phones[1], data.company_name)
	if not matches and phones[0] and phones[1]:
		matches = duplicates.find_duplicates(None, phones[1], None)
	return matches[0] if matches else None


def duplicate_answer(match: dict | None, user: str) -> dict:
	"""What the collector is told about a business we may already have; nothing of the lead itself."""
	if not match:
		return {"duplicate": False, "mine": False, "message": None}
	mine = frappe.db.get_value("Lead", match["name"], "tbo_collected_by") == user
	message = _("You already added this business.") if mine else _("This business is already in our leads.")
	return {"duplicate": True, "mine": mine, "message": message}


def check_duplicate(user: str, email=None, phone=None, company=None) -> dict:
	data = frappe._dict(email_id=email, mobile_no=phone, phone=None, company_name=company)
	return duplicate_answer(existing_lead(data), user)


def add_comment(lead: str, user: str, text: str) -> None:
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Lead",
			"reference_name": lead,
			"content": escape_html(text),
			"comment_email": user,
			"comment_by": get_fullname(user),
		}
	).insert(ignore_permissions=True)


def add_repeat(match: dict, user: str, data) -> dict:
	"""The credit stays where it is; the repeat is a comment on the lead."""
	text = _("{0} also found this business through the Lead Bank on {1}.").format(
		get_fullname(user), getdate()
	)
	if data.note:
		text = f"{text}\n{data.note}"
	add_comment(match["name"], user, text)
	answer = duplicate_answer(match, user)
	return {
		"created": False,
		**answer,
		"lead": None,
		"region": None,
		"in_pool": False,
		"message": f"{answer['message']} {_('Thank you for looking!')}",
	}


def lead_values(user: str, data, region: str, department: str | None) -> dict:
	"""The new lead's fields from the form."""
	first, _space, last = (data.contact_name or "").partition(" ")
	values = {
		"first_name": first or None,
		"last_name": last or None,
		"company_name": data.company_name or None,
		"mobile_no": data.mobile_no or None,
		"phone": data.phone or None,
		"email_id": data.email_id or None,
		"website": data.website or None,
		"tbo_business_type": data.business_type or None,
		"tbo_address": data.address or None,
		"state": data.state or None,
		"tbo_department": department,
		"tbo_collected_by": user,
		"tbo_collected_on": now_datetime(),
		"tbo_collected_region": region,
		lead_source.field(): lead_source.ensure(STAFF_REFERRAL),
	}
	if data.business_type:
		industry = frappe.db.exists("Industry Type", data.business_type)
		values["industry"] = industry or None
	country = REGION_INFO[region]["country"]
	if frappe.db.exists("Country", country):
		values["country"] = country
	if data.latitude not in (None, "") and data.longitude not in (None, ""):
		values["tbo_latitude"], values["tbo_longitude"] = data.latitude, data.longitude
		values["tbo_maps_url"] = f"https://www.google.com/maps?q={data.latitude},{data.longitude}"
	if values.get("state"):
		values["state"] = lead_category.clean_state(values["state"])
	return values


def notify_managers(lead) -> None:
	"""A lead with no department waits in the pool: tell the managers, who place it."""
	roles = frappe.get_all(
		"Has Role", filters={"role": ("in", MANAGER_ROLES), "parenttype": "User"}, pluck="parent"
	)
	users = frappe.get_all("User", filters={"name": ("in", roles), "enabled": 1}, pluck="name")
	for manager in sorted(set(users) - {"Administrator"}):
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": manager,
				"type": "Alert",
				"document_type": "Lead",
				"document_name": lead.name,
				"subject": _("A Lead Bank lead from {0} has no department yet: {1}").format(
					lead.tbo_collected_region, lead.company_name or lead.lead_name
				),
			}
		).insert(ignore_permissions=True)


def submit(user: str, data, photos) -> dict:
	"""Make the lead for `user`, or, when we already have the business, add their note to it.
	{created, duplicate, mine, lead, region, department, in_pool, message}."""
	data = clean_data(data)
	files = check_photos(photos)
	if not (data.company_name or data.contact_name):
		frappe.throw(_("Add the business name or the contact person."))
	if not (data.mobile_no or data.phone or data.email_id):
		frappe.throw(_("Add a phone number or an email, so our team can reach them."))
	region = (
		data.region or detect_region([data.mobile_no, data.phone], data.latitude, data.longitude)["region"]
	)
	region = check_region(region)
	if match := existing_lead(data):
		return add_repeat(match, user, data)
	department = department_for(region)
	lead = frappe.new_doc("Lead")
	lead.update(lead_values(user, data, region, department))
	# the lead belongs to the department's next member, or waits in the pool for its heads and
	# the managers; it is never owned by whoever happens to insert it
	lead.flags.round_robin = is_round_robin(department)
	lead.flags.unassigned_pool = not lead.flags.round_robin
	lead.flags.ownerless = True
	lead.insert(ignore_permissions=True)
	for filename, content in files:
		lead_place.add_photo(lead.name, filename, content)
	if data.note:
		add_comment(lead.name, user, data.note)
	in_pool = not (lead.lead_owner or department)
	if in_pool:
		notify_managers(lead)
	return {
		"created": True,
		"duplicate": False,
		"mine": False,
		"lead": lead.name,
		"region": region,
		"department": department,
		"in_pool": in_pool,
		"message": _("Thank you! We have your lead."),
	}
