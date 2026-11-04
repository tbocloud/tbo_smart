"""The Lead Bank (docs/lead-bank.md): any staff member adds a lead from a photo, and earns a
referral on the money collected when it is won.

This file is the one home of the regions: how a phone number or a GPS fix names one, and what
each earns and which department takes its leads.
"""

import re

import frappe
from frappe.utils import flt

from tbo_smart.lead_pipeline import setting
from tbo_smart.sales.departments import DEPARTMENT

INDIA, UAE, KSA = "India", "UAE", "KSA"
REGIONS = (INDIA, UAE, KSA)
# prefix: the country calling code; rate / department: the TBO Smart Settings fields
REGION_INFO = {
	INDIA: {
		"prefix": "91",
		"country": "India",
		"rate": "referral_rate_india",
		"department": "lead_bank_department_india",
		"default_rate": 2.0,
		"default_department": "TBO",
	},
	UAE: {
		"prefix": "971",
		"country": "United Arab Emirates",
		"rate": "referral_rate_uae",
		"department": "lead_bank_department_uae",
		"default_rate": 3.0,
		"default_department": None,
	},
	KSA: {
		"prefix": "966",
		"country": "Saudi Arabia",
		"rate": "referral_rate_ksa",
		"department": "lead_bank_department_ksa",
		"default_rate": 3.0,
		"default_department": None,
	},
}
DEFAULT_CAP = 25000.0
# (south, north, west, east); the UAE is looked at before the KSA, whose box overlaps it
GPS_BOXES = (
	(UAE, (22.5, 26.2, 51.0, 56.5)),
	(INDIA, (6.0, 37.5, 68.0, 97.5)),
	(KSA, (16.0, 32.5, 34.5, 55.8)),
)
# a number without a plus that is long enough to carry its country code
FULL_LENGTHS = {"971": (11, 12), "966": (11, 12), "91": (12,)}


def region_from_phone(phone: str | None) -> str | None:
	"""The region a phone number names: +91 / +971 / +966 (or 00 for the plus), a number with the
	code and the right length, or a 10-digit Indian mobile starting 6 to 9. A local number with a
	leading 0 could be any of them: None."""
	text = (phone or "").strip()
	digits = re.sub(r"\D", "", text)
	if not digits:
		return None
	if text.startswith("+") or digits.startswith("00"):
		digits = digits[2:] if digits.startswith("00") else digits
		return next((r for r in REGIONS if digits.startswith(REGION_INFO[r]["prefix"])), None)
	for region in REGIONS:
		prefix = REGION_INFO[region]["prefix"]
		if digits.startswith(prefix) and len(digits) in FULL_LENGTHS[prefix]:
			return region
	return INDIA if len(digits) == 10 and digits[0] in "6789" else None


def region_from_gps(latitude, longitude) -> str | None:
	if not (flt(latitude) or flt(longitude)):
		return None
	for region, (south, north, west, east) in GPS_BOXES:
		if south <= flt(latitude) <= north and west <= flt(longitude) <= east:
			return region
	return None


def detect_region(phones=(), latitude=None, longitude=None) -> dict:
	"""{region, by}: the region the phone numbers name (the first that names one), else the one
	the GPS fix is in; region None while neither says. The collector can always change it."""
	if isinstance(phones, str):
		phones = [phones]
	for phone in phones:
		if region := region_from_phone(phone):
			return {"region": region, "by": "phone"}
	if region := region_from_gps(latitude, longitude):
		return {"region": region, "by": "gps"}
	return {"region": None, "by": None}


def check_region(region: str | None) -> str:
	if region not in REGIONS:
		frappe.throw(frappe._("Pick the region: India, UAE or KSA."))
	return region


# ---------- what each region earns and where its leads go ----------


def rate(region: str) -> float:
	"""The referral percentage of the region (TBO Smart Settings)."""
	info = REGION_INFO[region]
	value = setting(info["rate"])
	return info["default_rate"] if value is None else flt(value)


def cap() -> float:
	"""The most one lead can earn in referral, in the company currency; 0 is no cap."""
	value = setting("referral_cap")
	return DEFAULT_CAP if value is None else flt(value)


def department_for(region: str) -> str | None:
	"""The active lead department that takes the region's leads, or None: the lead then waits in
	the unassigned pool (India falls back to the department called TBO)."""
	info = REGION_INFO[region]
	name = setting(info["department"]) or info["default_department"]
	if name and frappe.db.get_value(DEPARTMENT, name, "is_active"):
		return name
	return None
