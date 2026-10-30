"""Businesses found by a run become leads in the department's unassigned pool (docs/prospecting.md §4).

A business is a duplicate when the same place was imported before (its `tbo_source_ref`), a lead
already has its phone or company name (the lead departments' duplicate check) or its website's
domain. A duplicate is counted and left alone. A new lead has no owner: the call batches and the
round robin hand it out; nothing here assigns anyone.
"""

import re
from urllib.parse import urlparse

import frappe
from frappe.query_builder.functions import Lower

from tbo_smart import lead_source
from tbo_smart.sales import duplicates
from tbo_smart.sales.stages import system_save

# a listing's website that is a profile on someone else's site says nothing about the business
SHARED_HOSTS = (
	"facebook.com",
	"instagram.com",
	"google.com",
	"goo.gl",
	"business.site",
	"wixsite.com",
	"blogspot.com",
	"linktr.ee",
	"youtube.com",
	"justdial.com",
)
SOURCE_PREFIX = "Prospecting – "
NAME_LENGTH = 140


def source_name(provider: str) -> str:
	return f"{SOURCE_PREFIX}{provider}"


def domain_of(url: str | None) -> str | None:
	"""example.com from https://www.example.com/page, None for a shared host or a non-site."""
	url = (url or "").strip()
	if not url:
		return None
	host = (urlparse(url if "//" in url else f"//{url}").hostname or "").lower()
	host = re.sub(r"^www\.", "", host)
	if "." not in host or any(host == shared or host.endswith(f".{shared}") for shared in SHARED_HOSTS):
		return None
	return host


def existing_lead(business: dict, domain: str | None) -> tuple[str, str] | None:
	"""(lead, why) of the lead this business already is, or None."""
	if business.get("ref"):
		lead = frappe.db.get_value("Lead", {"tbo_source_ref": business["ref"]}, "name")
		if lead:
			return lead, "same place"
	matches = duplicates.find_duplicates(None, business.get("phone"), business["name"])
	if matches:
		return matches[0]["name"], "/".join(matches[0]["matched_on"])
	if domain:
		Lead = frappe.qb.DocType("Lead")
		rows = (
			frappe.qb.from_(Lead)
			.select(Lead.name)
			.where(Lower(Lead.website).like(f"%{domain}%"))
			.limit(1)
			.run()
		)
		if rows:
			return rows[0][0], "website"
	return None


def lead_values(business: dict, run) -> dict:
	meta = frappe.get_meta("Lead")
	name = business["name"][:NAME_LENGTH]
	values = {
		"first_name": name,
		"company_name": name,
		"mobile_no": business.get("phone") or None,
		"website": business.get("website") or None,
		"city": business.get("city") or run.district or None,
		"industry": run.industry,
		"tbo_department": run.department,
		lead_source.field(): source_name(run.provider),
		"tbo_maps_url": business.get("maps_url") or None,
		"tbo_latitude": business.get("latitude"),
		"tbo_longitude": business.get("longitude"),
		"tbo_location_note": business.get("address") or None,
		"tbo_source_ref": business.get("ref") or None,
		"tbo_prospect_run": run.name,
		"state": run.state,
		"tbo_district": run.district,
	}
	return {key: value for key, value in values.items() if value is not None and meta.has_field(key)}


def import_business(business: dict, run) -> str:
	"""Create the lead for one business. `run` carries name, industry, state, district (the one
	being imported), department, provider and require_phone. Returns "created", "duplicate" or
	"skipped"."""
	if not (business.get("name") or "").strip():
		return "skipped"
	if run.require_phone and not business.get("phone"):
		return "skipped"
	domain = domain_of(business.get("website"))
	if existing_lead(business, domain):
		return "duplicate"
	lead = frappe.new_doc("Lead")
	lead.update(lead_values(business, run))
	lead.flags.unassigned_pool = True
	system_save(lead, ignore_permissions=True)
	return "created"
