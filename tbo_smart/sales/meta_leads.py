"""Meta lead ads (the meta_lead_integration app): one lead per person (docs/lead-departments.md §6).

Before it makes a lead, meta_lead_integration asks the `meta_lead_existing_lead` hook whether the
person already is one. Someone with the email or number of a lead we have is added to that lead
as the website form does it: a note with what they sent, and its owner (or its department's
heads) told. Anyone else becomes a new lead, as before.
"""

from frappe import _

from tbo_smart.api.website_lead import enquired_again
from tbo_smart.sales.duplicates import blocking, find_duplicates

# what the note repeats of the enquiry: the Lead fields meta_lead_integration fills
SENT_FIELDS = ("lead_name", "email_id", "mobile_no", "company_name", "city")


def enquiry_again(values: dict) -> str | None:
	"""Hook `meta_lead_existing_lead`: `values` are those of the Lead about to be made. Returns the
	lead this person already is, the enquiry added to it, or None."""
	phone = values.get("mobile_no") or values.get("phone")
	existing = blocking(find_duplicates(values.get("email_id"), phone, None))
	if not existing:
		return None
	platform = values.get("custom_meta_platform") or "Meta"
	sent = " · ".join(str(values[field]) for field in SENT_FIELDS if values.get(field))
	enquired_again(
		existing,
		_("{0} lead form again (form {1}): {2}").format(
			platform, values.get("custom_meta_form_id") or "-", sent or "-"
		),
		_("{0} enquired again through {1} lead ads.").format(
			existing["lead_name"] or existing["name"], platform
		),
	)
	return existing["name"]
