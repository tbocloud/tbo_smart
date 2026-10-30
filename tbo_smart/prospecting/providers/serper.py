"""Serper's Google Maps search: the places Google Maps shows for "<what> in <district>". Paid.

Result shape and the one-key set-up follow the Serper discoverer in Harvey
(https://github.com/ethanplusai/harvey, MIT, Copyright (c) 2026 Ethan Rogers); the Maps endpoint
is Serper's own. Each page holds up to 20 places.
"""

import math

import requests

from tbo_smart.prospecting.providers import MAPS_CID, MAPS_PIN, SERPER, Provider, ProviderError, first_phone

API = "https://google.serper.dev/maps"
PAGE_SIZE = 20
# Serper bills Maps searches in credits; USD 0.003 a page is the dearer end of its price list
PAGE_COST = 0.003
MAX_PAGES = 10
TIMEOUT = (8, 30)


class Serper(Provider):
	key = SERPER
	label = "Serper Google Maps (paid)"
	paid = True
	enabled_field = "prospect_serper_enabled"
	secret_fields = ("serper_api_key",)
	coverage = "Places from Google Maps with phone and website. About USD 0.003 per 20 places."

	def estimate(self, spec, places, limit, require_phone):
		pages = max(len(places), math.ceil(limit / PAGE_SIZE))
		return {
			"cost": round(pages * PAGE_COST, 4),
			"expected": limit,
			"note": "Up to the maximum; Serper's price per search may differ from this estimate.",
		}

	def search(self, spec, place, limit, require_phone):
		found, cost = [], 0.0
		for page in range(1, min(MAX_PAGES, math.ceil(limit / PAGE_SIZE)) + 1):
			try:
				response = requests.post(
					API,
					json={"q": f"{spec['phrase']} in {place['name']}", "gl": "in", "page": page},
					headers={"X-API-KEY": self.secret("serper_api_key")},
					timeout=TIMEOUT,
				)
				response.raise_for_status()
				places = response.json().get("places") or []
			except (requests.RequestException, ValueError) as e:
				if page == 1:
					raise ProviderError("Serper could not be reached or refused the key.") from e
				break
			cost += PAGE_COST
			found += [self.business(item) for item in places if item.get("title")]
			if len(places) < PAGE_SIZE or len(found) >= limit:
				break
		return found[:limit], cost

	@staticmethod
	def business(item: dict) -> dict:
		cid, place_id = item.get("cid"), item.get("placeId")
		latitude, longitude = item.get("latitude"), item.get("longitude")
		if cid:
			maps_url = MAPS_CID.format(cid=cid)
		elif latitude is not None and longitude is not None:
			maps_url = MAPS_PIN.format(lat=latitude, lng=longitude)
		else:
			maps_url = ""
		return {
			"name": item["title"].strip(),
			"phone": first_phone(item.get("phoneNumber")),
			"website": (item.get("website") or "").strip(),
			"address": item.get("address") or "",
			"city": "",
			"latitude": latitude,
			"longitude": longitude,
			"ref": f"gplace:{place_id}" if place_id else (f"gcid:{cid}" if cid else ""),
			"maps_url": maps_url,
			"category": item.get("category") or "",
		}
