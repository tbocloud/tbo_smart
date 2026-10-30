"""Districts to places, through OpenStreetMap's Nominatim: one polite lookup per district, then
remembered for a month (Nominatim's policy: at most one request a second, a real User-Agent, no
bulk geocoding).
"""

import time

import frappe
import requests
from frappe import _

from tbo_smart.prospecting import USER_AGENT

NOMINATIM = "https://nominatim.openstreetmap.org/search"
CACHE_KEY = "tbo_prospect_geo:"
KEEP_SECONDS = 30 * 24 * 3600
TIMEOUT = (5, 20)
PAUSE = 1.1


def locate(district: str, state: str) -> dict:
	"""{name, relation (OSM relation id or None), south, north, west, east, lat, lng} of a
	district. Throws in words when Nominatim does not know it."""
	key = f"{CACHE_KEY}{state}|{district}".lower()
	cached = frappe.cache.get_value(key)
	if cached:
		return cached
	time.sleep(PAUSE)
	try:
		response = requests.get(
			NOMINATIM,
			params={"q": f"{district}, {state}", "format": "jsonv2", "limit": 1},
			headers={"User-Agent": USER_AGENT},
			timeout=TIMEOUT,
		)
		response.raise_for_status()
		hits = response.json()
	except (requests.RequestException, ValueError):
		frappe.throw(
			_("Couldn't look up {0}, {1} on OpenStreetMap. Try again in a minute.").format(district, state)
		)
	if not hits:
		frappe.throw(_("OpenStreetMap does not know {0}, {1}. Check the spelling.").format(district, state))
	hit = hits[0]
	south, north, west, east = (float(value) for value in hit["boundingbox"])
	place = {
		"name": district,
		"relation": int(hit["osm_id"]) if hit.get("osm_type") == "relation" else None,
		"south": south,
		"north": north,
		"west": west,
		"east": east,
		"lat": float(hit["lat"]),
		"lng": float(hit["lon"]),
	}
	frappe.cache.set_value(key, place, expires_in_sec=KEEP_SECONDS)
	return place


def inside(place: dict, latitude, longitude) -> bool:
	"""Whether a point is in the district's bounding box (a business without coordinates is kept)."""
	if latitude in (None, "") or longitude in (None, ""):
		return True
	return (
		place["south"] <= float(latitude) <= place["north"]
		and place["west"] <= float(longitude) <= place["east"]
	)
