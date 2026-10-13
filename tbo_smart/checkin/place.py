"""A short place name for a check-in ("Kanjirappally, Kottayam, Kerala").

The app names the place with the phone's own geocoder and sends it with the punch. When it
doesn't (an older app, the HRMS PWA, a desk entry with coordinates), a background job asks a
Nominatim server, following the OpenStreetMap usage policy
(https://operations.osmfoundation.org/policies/nominatim/): an identifying User-Agent and
contact email, at most one request a second across all workers, and results cached by
coordinates rounded to 4 decimals (about 11 m). A failure leaves the name blank and is logged.
"""

import time

import frappe
import requests
from frappe.utils import flt, get_url

SETTINGS = "TBO Smart Settings"
DEFAULT_REVERSE_GEOCODE_URL = "https://nominatim.openstreetmap.org/reverse"
CACHE_PREFIX = "tbo_smart:place:"
CACHE_SECONDS = 90 * 24 * 60 * 60
SLOT_KEY = "tbo_smart:nominatim_slot"
SLOT_MILLISECONDS = 1000
SLOT_WAIT_SECONDS = 15
TIMEOUT_SECONDS = 10
MAX_LENGTH = 140
LOCALITY_KEYS = ("village", "town", "city", "suburb", "hamlet", "municipality", "neighbourhood")
DISTRICT_KEYS = ("state_district", "county")
# A burst of punches can outwait the 1-per-second slot; the job then queues itself again.
MAX_SLOT_RETRIES = 3


class SlotTimeout(Exception):
	"""No Nominatim slot came free in time: not a lookup failure, so the job retries."""


def queue_place_name(checkin: str, attempt: int = 0) -> None:
	if not frappe.db.get_single_value(SETTINGS, "reverse_geocode_url"):
		return
	frappe.enqueue(
		"tbo_smart.checkin.place.fill_place_name",
		queue="short",
		enqueue_after_commit=True,
		# A retry gets its own id: the running job still holds the first one.
		job_id=f"tbo_smart_place_{checkin}_{attempt}",
		deduplicate=True,
		checkin=checkin,
		attempt=attempt,
	)


def fill_place_name(checkin: str, attempt: int = 0) -> None:
	"""Background job: store the place name on the check-in unless one is already there."""
	row = frappe.db.get_value(
		"Employee Checkin", checkin, ["latitude", "longitude", "tbo_location_name"], as_dict=True
	)
	if not row or row.tbo_location_name or not (row.latitude or row.longitude):
		return
	try:
		name = reverse_geocode(row.latitude, row.longitude)
	except SlotTimeout:
		if attempt < MAX_SLOT_RETRIES:
			queue_place_name(checkin, attempt + 1)
		else:
			frappe.log_error(title="TBO Smart: reverse geocoding failed", message=f"{checkin}: no free slot")
		return
	if name:
		frappe.db.set_value("Employee Checkin", checkin, "tbo_location_name", name, update_modified=False)


def reverse_geocode(latitude: float, longitude: float) -> str | None:
	"""The short place name for a point, from the cache or one Nominatim request."""
	key = f"{CACHE_PREFIX}{flt(latitude):.4f},{flt(longitude):.4f}"
	cached = frappe.cache.get_value(key, expires=True)
	if cached is not None:
		return cached or None
	try:
		name = short_place_name(request_address(latitude, longitude))
	except SlotTimeout:
		raise
	except Exception:
		frappe.log_error(title="TBO Smart: reverse geocoding failed")
		return None
	# Points with no address (open water) are cached too, as "", so they aren't asked again.
	frappe.cache.set_value(key, name or "", expires_in_sec=CACHE_SECONDS)
	return name


def request_address(latitude: float, longitude: float) -> dict:
	url = frappe.db.get_single_value(SETTINGS, "reverse_geocode_url")
	email = frappe.db.get_single_value(SETTINGS, "reverse_geocode_email")
	params = {
		"format": "jsonv2",
		"lat": f"{flt(latitude):.6f}",
		"lon": f"{flt(longitude):.6f}",
		"zoom": 18,
		"addressdetails": 1,
		"accept-language": "en",
	}
	if email:
		params["email"] = email
	wait_for_slot()
	response = requests.get(
		url or DEFAULT_REVERSE_GEOCODE_URL,
		params=params,
		headers={"User-Agent": user_agent(email)},
		timeout=TIMEOUT_SECONDS,
	)
	response.raise_for_status()
	return response.json()


def user_agent(email: str | None) -> str:
	contact = "; ".join(part for part in (get_url(), email) if part)
	return f"TBO Smart check-in/1.0 ({contact})"


def wait_for_slot() -> None:
	"""At most one request per second from every worker of this site: a Redis key set with
	NX that expires after a second is the slot."""
	key = frappe.cache.make_key(SLOT_KEY)
	deadline = time.monotonic() + SLOT_WAIT_SECONDS
	# make_key above prefixes the site, so this raw SET NX stays per site.
	while not frappe.cache.set(key, 1, px=SLOT_MILLISECONDS, nx=True):  # site-prefixed key - nosemgrep
		if time.monotonic() > deadline:
			raise SlotTimeout("No reverse-geocoding slot within 15 s")
		time.sleep(0.25)


def short_place_name(payload: dict) -> str | None:
	"""Locality, district and state, e.g. "Kanjirappally, Kottayam, Kerala"."""
	address = payload.get("address") or {}
	parts = [
		first_of(address, LOCALITY_KEYS),
		first_of(address, DISTRICT_KEYS),
		address.get("state"),
	]
	names = []
	for part in parts:
		part = clean_part(part)
		if part and part not in names:
			names.append(part)
	if not names and payload.get("display_name"):
		names = [p.strip() for p in payload["display_name"].split(",")[:3] if p.strip()]
	return ", ".join(names)[:MAX_LENGTH] or None


def first_of(address: dict, keys: tuple) -> str | None:
	return next((address[key] for key in keys if address.get(key)), None)


def clean_part(part: str | None) -> str | None:
	if not part:
		return None
	for suffix in (" District", " district", " Taluk", " taluk"):
		if part.endswith(suffix):
			part = part[: -len(suffix)]
	return part.strip() or None


def clean_device_place(value: str | None) -> str | None:
	"""A place name the device sent: one line, trimmed, at most 140 characters."""
	value = " ".join((value or "").split())
	return value[:MAX_LENGTH] or None
