"""DataForSEO Business Listings: Google Business Profile data as a database. Paid.

Port of the DataForSEO Business Listings discoverer in Harvey (https://github.com/ethanplusai/harvey,
MIT, Copyright (c) 2026 Ethan Rogers): the request shape, the price model and reading `cost` from
the answer. Adapted to Frappe; a district is searched as a point and radius around its centre and
the answer is cut to the district's bounding box.

Price (Harvey's notes, October 2026; check DataForSEO's current price list): USD 0.012 a request
plus USD 0.00036 a business, so 100 businesses cost about USD 0.048.
"""

import math

import requests

from tbo_smart.prospecting.providers import (
	DATAFORSEO,
	MAPS_CID,
	MAPS_PIN,
	Provider,
	ProviderError,
	first_phone,
)

API = "https://api.dataforseo.com/v3/business_data/business_listings/search/live"
TASK_COST = 0.012
ITEM_COST = 0.00036
MAX_RADIUS_KM = 100
TIMEOUT = (8, 60)


class DataForSEO(Provider):
	key = DATAFORSEO
	label = "DataForSEO Business Listings (paid)"
	paid = True
	enabled_field = "prospect_dataforseo_enabled"
	secret_fields = ("dataforseo_login", "dataforseo_password")
	coverage = "Google Business Profile data: phone, website, rating. About USD 0.05 per 100 businesses."

	def estimate(self, spec, places, limit, require_phone):
		return {
			"cost": round(len(places) * TASK_COST + limit * ITEM_COST, 4),
			"expected": limit,
			"note": "Up to the maximum; the district may hold fewer. You pay for what DataForSEO returns.",
		}

	def search(self, spec, place, limit, require_phone):
		payload = {
			"limit": min(int(limit), 1000),
			"location_coordinate": f"{place['lat']:.6f},{place['lng']:.6f},{radius_km(place)}",
			"order_by": ["rating.votes_count,desc"],
		}
		if spec["dfs"]:
			payload["categories"] = [spec["dfs"]]
		else:
			payload["title"] = spec["phrase"]
		try:
			response = requests.post(
				API,
				json=[payload],
				auth=(self.secret("dataforseo_login"), self.secret("dataforseo_password")),
				timeout=TIMEOUT,
			)
			response.raise_for_status()
			data = response.json()
		except (requests.RequestException, ValueError) as e:
			raise ProviderError("DataForSEO could not be reached or refused the login.") from e
		cost = float(data.get("cost") or 0)
		task = (data.get("tasks") or [{}])[0]
		if data.get("status_code") != 20000 or task.get("status_code") != 20000:
			raise ProviderError(f"DataForSEO: {task.get('status_message') or data.get('status_message')}")
		items = ((task.get("result") or [{}])[0] or {}).get("items") or []
		return [self.business(item) for item in items if item.get("title")], cost

	@staticmethod
	def business(item: dict) -> dict:
		info = item.get("address_info") or {}
		cid, place_id = item.get("cid"), item.get("place_id")
		latitude, longitude = item.get("latitude"), item.get("longitude")
		if cid:
			maps_url = MAPS_CID.format(cid=cid)
		elif latitude is not None and longitude is not None:
			maps_url = MAPS_PIN.format(lat=latitude, lng=longitude)
		else:
			maps_url = ""
		return {
			"name": item["title"].strip(),
			"phone": first_phone(item.get("phone")),
			"website": (item.get("url") or "").strip(),
			"address": item.get("address") or "",
			"city": info.get("city") or "",
			"latitude": latitude,
			"longitude": longitude,
			"ref": f"gplace:{place_id}" if place_id else (f"gcid:{cid}" if cid else ""),
			"maps_url": maps_url,
			"category": item.get("category") or "",
		}


def radius_km(place: dict) -> int:
	"""Half the district box's diagonal, rounded up, at most 100 km."""
	height = (place["north"] - place["south"]) * 111.0
	width = (place["east"] - place["west"]) * 111.0 * math.cos(math.radians(place["lat"]))
	return max(1, min(MAX_RADIUS_KM, math.ceil(math.hypot(height, width) / 2)))
