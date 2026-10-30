"""Our Industry Types as each provider names them (docs/prospecting.md §3).

The built-in table below is the starting point. A row for the same industry in TBO Smart Settings
→ Prospecting → "Industry to provider categories" replaces it, field by field (a blank field keeps
the built-in value), so admins tune it without a deploy.
"""

import re

import frappe
from frappe import _

# OpenStreetMap tags, one per entry: "key=value", optionally ";name~word|word" to also require the
# name to match (OSM tags clinics and hospitals alike, so Ayurvedic ones are told by name).
BUILTIN = {
	"Builders & Developers": {
		"osm": ("office=construction_company", "craft=builder"),
		"phrase": "builders and developers",
		"dfs": "building_construction_contractor",
	},
	"Real Estate Companies": {
		"osm": ("office=estate_agent",),
		"phrase": "real estate agents",
		"dfs": "real_estate_agency",
	},
	"Resorts": {
		"osm": (
			"tourism=resort",
			"tourism=chalet",
			"tourism=hotel;name~resort|retreat|villa|cottage|backwater",
			"tourism=guest_house;name~resort|retreat|villa|cottage|homestay",
		),
		"phrase": "resorts",
		"dfs": "resort",
	},
	"Hotels & Restaurants": {
		"osm": ("tourism=hotel", "amenity=restaurant"),
		"phrase": "hotels and restaurants",
		"dfs": "hotel",
	},
	"Ayurvedic Hospitals": {
		"osm": (
			"amenity=hospital;name~ayur|ayush|vaidya|panchakarma|vaidyasala",
			"amenity=clinic;name~ayur|ayush|vaidya|panchakarma|vaidyasala",
			"healthcare=alternative",
		),
		"phrase": "ayurvedic hospitals",
		"dfs": "ayurvedic_hospital",
	},
	"Hospitals & Clinics": {
		"osm": ("amenity=hospital", "amenity=clinic"),
		"phrase": "hospitals and clinics",
		"dfs": "hospital",
	},
	"Schools & Colleges": {
		"osm": ("amenity=school", "amenity=college", "amenity=university"),
		"phrase": "schools and colleges",
		"dfs": "school",
	},
	"Retail & Supermarkets": {
		"osm": ("shop=supermarket", "shop=department_store", "shop=mall"),
		"phrase": "supermarkets",
		"dfs": "supermarket",
	},
	"Manufacturing": {
		"osm": ("man_made=works", "industrial=factory"),
		"phrase": "manufacturers",
		"dfs": "manufacturer",
	},
	"Trading & Distribution": {
		"osm": ("shop=wholesale",),
		"phrase": "wholesale distributors",
		"dfs": "wholesaler",
	},
	"Logistics": {
		"osm": ("office=logistics", "amenity=courier"),
		"phrase": "logistics companies",
		"dfs": "logistics_service",
	},
	"IT & Software": {
		"osm": ("office=it",),
		"phrase": "software companies",
		"dfs": "software_company",
	},
	"Travel & Tourism": {
		"osm": ("shop=travel_agency", "office=travel_agent"),
		"phrase": "travel agencies",
		"dfs": "travel_agency",
	},
	"Automobile": {
		"osm": ("shop=car", "shop=car_repair"),
		"phrase": "car dealers",
		"dfs": "car_dealer",
	},
	"Jewellery": {"osm": ("shop=jewelry",), "phrase": "jewellery shops", "dfs": "jewelry_store"},
	"Textiles & Garments": {
		"osm": ("shop=fabric", "shop=clothes;name~textile|silks|sarees|garments"),
		"phrase": "textile shops",
		"dfs": "textile_store",
	},
	"Construction Materials": {
		"osm": ("shop=trade;name~building|cement|tiles|hardware", "shop=doityourself", "shop=hardware"),
		"phrase": "building material suppliers",
		"dfs": "building_materials_store",
	},
	"Healthcare & Pharma": {
		"osm": ("amenity=pharmacy",),
		"phrase": "pharmacies",
		"dfs": "pharmacy",
	},
	"NGOs & Trusts": {
		"osm": ("office=ngo", "office=foundation"),
		"phrase": "trusts and NGOs",
		"dfs": "non_profit_organization",
	},
	"Government": {
		"osm": ("office=government",),
		"phrase": "government offices",
		"dfs": "government_office",
	},
}

KEY = re.compile(r"^[a-z][a-z0-9_:]{0,40}$")
VALUE = re.compile(r"^[A-Za-z0-9_:-]{1,60}$")
# Overpass regex: words, spaces, | and a few separators; nothing that can close the quoted string.
NAME_REGEX = re.compile(r"^[\w |.-]{1,120}$")


def parse_tag(line: str) -> tuple[str, str, str | None]:
	"""(key, value, name regex or None) from "key=value" or "key=value;name~a|b". Anything that
	could alter the Overpass query is refused."""
	main, _sep, rest = line.strip().partition(";")
	key, _eq, value = main.partition("=")
	key, value = key.strip(), value.strip()
	if not (KEY.match(key) and VALUE.match(value)):
		frappe.throw(_("{0} is not an OpenStreetMap tag like shop=jewelry.").format(line))
	regex = None
	if rest.strip():
		field, _tilde, regex = rest.strip().partition("~")
		regex = regex.strip()
		if field.strip() != "name" or not NAME_REGEX.match(regex):
			frappe.throw(_("{0}: after the tag use only ;name~word|word.").format(line))
	return key, value, regex


def category_for(industry: str) -> dict:
	"""{osm: [tag lines], phrase, dfs} for an Industry Type: the settings row over the built-in
	one; empty osm and phrase when neither knows the industry."""
	spec = dict(BUILTIN.get(industry) or {"osm": (), "phrase": "", "dfs": ""})
	rows = frappe.get_all(
		"TBO Prospect Category",
		filters={"parent": "TBO Smart Settings", "industry": industry},
		fields=["osm_tags", "dataforseo_category", "search_phrase"],
		order_by="idx desc",
		limit=1,
	)
	if rows:
		row = rows[0]
		lines = [line.strip() for line in (row.osm_tags or "").splitlines() if line.strip()]
		spec["osm"] = tuple(lines) or spec["osm"]
		spec["dfs"] = (row.dataforseo_category or "").strip() or spec["dfs"]
		spec["phrase"] = (row.search_phrase or "").strip() or spec["phrase"]
	if not spec["phrase"]:
		spec["phrase"] = industry.lower()
	return {"osm": list(spec["osm"]), "phrase": spec["phrase"], "dfs": spec["dfs"]}
