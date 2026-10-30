"""OpenStreetMap through Overpass: free, no account, thin coverage.

Port of the OpenStreetMap discoverer in Harvey (https://github.com/ethanplusai/harvey, MIT,
Copyright (c) 2026 Ethan Rogers): the tag-based query, the mirror list and the rule that a
throttled run must fail rather than look like an empty market. Adapted to Frappe, districts by
area instead of radius, and tags from `mapping`.

Coverage is the catch: OSM maps places, not phone numbers. In a real check (Idukki, October 2026)
tourism=resort had 3 named places, and of 345 named resorts, hotels, guest houses and chalets only
39 had a phone number. Use it to try the flow and to cross-check; use a paid provider to fill a pool.
"""

import time

import requests

from tbo_smart.prospecting import USER_AGENT
from tbo_smart.prospecting.mapping import parse_tag
from tbo_smart.prospecting.providers import MAPS_PIN, OSM, Provider, ProviderError, first_phone

ENDPOINTS = (
	"https://overpass-api.de/api/interpreter",
	"https://overpass.kumi.systems/api/interpreter",
	"https://overpass.private.coffee/api/interpreter",
)
TIMEOUT = (8, 70)
BACKOFF = 5.0
MIN_INTERVAL = 2.0
PHONE_KEYS = '[~"^(phone|contact:phone|contact:mobile|mobile)$"~"."]'


class OpenStreetMap(Provider):
	key = OSM
	label = "OpenStreetMap (free)"
	enabled_field = "prospect_osm_enabled"
	coverage = (
		"Free, but OSM maps places, not phone numbers: expect few resorts and about one business in ten "
		"with a phone. Good for trying the flow, not for filling a pool."
	)

	def estimate(self, spec, places, limit, require_phone):
		if not spec["osm"]:
			raise ProviderError(f"OpenStreetMap has no tags for {spec['phrase']}. Use another provider.")
		total = 0
		for place in places:
			total += self.count(spec, place, require_phone)
		return {
			"cost": 0.0,
			"expected": min(total, limit),
			"note": f"{total} on the map" + (" with a phone number." if require_phone else "."),
		}

	def count(self, spec: dict, place: dict, require_phone: bool) -> int:
		data = self.query(self.build(spec, place, require_phone, "out count;"))
		for element in data.get("elements") or []:
			if element.get("type") == "count":
				return int((element.get("tags") or {}).get("total") or 0)
		return 0

	def search(self, spec, place, limit, require_phone):
		if not spec["osm"]:
			raise ProviderError(f"OpenStreetMap has no tags for {spec['phrase']}. Use another provider.")
		data = self.query(self.build(spec, place, require_phone, f"out tags center {int(limit)};"))
		found = [business for element in data.get("elements") or [] if (business := self.business(element))]
		return found, 0.0

	@staticmethod
	def build(spec: dict, place: dict, require_phone: bool, output: str) -> str:
		if place.get("relation"):
			where, scope = f"area({3600000000 + int(place['relation'])})->.a;", "(area.a)"
		else:
			where, scope = "", f"({place['south']},{place['west']},{place['north']},{place['east']})"
		clauses = ""
		for line in spec["osm"]:
			key, value, regex = parse_tag(line)
			name_filter = f'["name"~"{regex}",i]' if regex else '["name"]'
			clauses += f'nwr["{key}"="{value}"]{name_filter}{PHONE_KEYS if require_phone else ""}{scope};'
		return f"[out:json][timeout:60];{where}({clauses});{output}"

	@staticmethod
	def query(body: str) -> dict:
		"""The Overpass answer. Mirrors are tried in turn; when all throttle it raises, because
		returning nothing would look like a district with no businesses."""
		for index, endpoint in enumerate(ENDPOINTS):
			if index:
				time.sleep(BACKOFF)
			time.sleep(MIN_INTERVAL)
			try:
				response = requests.post(
					endpoint, data={"data": body}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
				)
			except requests.RequestException:
				continue
			if response.status_code in (429, 502, 503, 504):
				continue
			try:
				response.raise_for_status()
				return response.json()
			except (requests.RequestException, ValueError) as e:
				raise ProviderError("OpenStreetMap answered something unreadable.") from e
		raise ProviderError(
			"Every public Overpass server is busy or unreachable. Wait a few minutes and run again."
		)

	@staticmethod
	def business(element: dict) -> dict | None:
		tags = element.get("tags") or {}
		name = (tags.get("name") or "").strip()
		if not name:
			return None
		centre = element.get("center") or element
		latitude, longitude = centre.get("lat"), centre.get("lon")
		address = ", ".join(
			part
			for part in (
				" ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x),
				tags.get("addr:suburb"),
				tags.get("addr:city"),
				tags.get("addr:postcode"),
			)
			if part
		)
		kind, osm_id = element.get("type"), element.get("id")
		return {
			"name": name,
			"phone": first_phone(
				tags.get("phone")
				or tags.get("contact:phone")
				or tags.get("contact:mobile")
				or tags.get("mobile")
			),
			"website": (tags.get("website") or tags.get("contact:website") or "").strip(),
			"address": address,
			"city": tags.get("addr:city") or "",
			"latitude": latitude,
			"longitude": longitude,
			"ref": f"osm:{kind}/{osm_id}",
			"maps_url": MAPS_PIN.format(lat=latitude, lng=longitude) if latitude is not None else "",
			"category": tags.get("tourism")
			or tags.get("amenity")
			or tags.get("shop")
			or tags.get("office")
			or "",
		}
