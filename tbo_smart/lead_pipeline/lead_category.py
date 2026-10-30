"""How leads are categorised: Industry, State, District and Organisation size
(docs/hub-leads-api.md §2.11).

Three are ERPNext's own Lead fields, reused as they are: `industry` (an Industry Type),
`state` (Data) and `no_of_employees` (a Select of employee bands, used as the organisation size).
District is TBO's one custom field, `tbo_district` (Data). Nothing here is a hard rule: a state or
a district that is not on the lists is kept, only trimmed and given a tidy case.
"""

import frappe
from frappe import _
from frappe.custom.doctype.property_setter.property_setter import make_property_setter
from frappe.utils import cint

INDUSTRY_TYPE = "Industry Type"
MANAGER_ROLES = ("System Manager", "Sales Manager")
MAX_BULK = 500
MAX_INDUSTRY_LENGTH = 140

INDUSTRIES = (
	"Builders & Developers",
	"Real Estate Companies",
	"Resorts",
	"Hotels & Restaurants",
	"Ayurvedic Hospitals",
	"Hospitals & Clinics",
	"Schools & Colleges",
	"Retail & Supermarkets",
	"Manufacturing",
	"Trading & Distribution",
	"Logistics",
	"IT & Software",
	"Travel & Tourism",
	"Automobile",
	"Jewellery",
	"Textiles & Garments",
	"Construction Materials",
	"Healthcare & Pharma",
	"NGOs & Trusts",
	"Government",
	"Other",
)
INDIAN_STATES = (
	"Andhra Pradesh",
	"Arunachal Pradesh",
	"Assam",
	"Bihar",
	"Chhattisgarh",
	"Goa",
	"Gujarat",
	"Haryana",
	"Himachal Pradesh",
	"Jharkhand",
	"Karnataka",
	"Kerala",
	"Madhya Pradesh",
	"Maharashtra",
	"Manipur",
	"Meghalaya",
	"Mizoram",
	"Nagaland",
	"Odisha",
	"Punjab",
	"Rajasthan",
	"Sikkim",
	"Tamil Nadu",
	"Telangana",
	"Tripura",
	"Uttar Pradesh",
	"Uttarakhand",
	"West Bengal",
	"Andaman and Nicobar Islands",
	"Chandigarh",
	"Dadra and Nagar Haveli and Daman and Diu",
	"Delhi",
	"Jammu and Kashmir",
	"Ladakh",
	"Lakshadweep",
	"Puducherry",
)
# TBO has clients in the Gulf: the UAE by emirate, the other countries whole
GCC_REGIONS = (
	"Abu Dhabi",
	"Dubai",
	"Sharjah",
	"Ajman",
	"Umm Al Quwain",
	"Ras Al Khaimah",
	"Fujairah",
	"Saudi Arabia",
	"Qatar",
	"Oman",
	"Bahrain",
	"Kuwait",
)
STATES = INDIAN_STATES + GCC_REGIONS
KERALA = "Kerala"
KERALA_DISTRICTS = (
	"Thiruvananthapuram",
	"Kollam",
	"Pathanamthitta",
	"Alappuzha",
	"Kottayam",
	"Idukki",
	"Ernakulam",
	"Thrissur",
	"Palakkad",
	"Malappuram",
	"Kozhikode",
	"Wayanad",
	"Kannur",
	"Kasaragod",
)
# old or common spellings, by lower case
STATE_ALIASES = {
	"orissa": "Odisha",
	"pondicherry": "Puducherry",
	"uttaranchal": "Uttarakhand",
	"new delhi": "Delhi",
	"ksa": "Saudi Arabia",
}
ORG_SIZES = ("1-10", "11-50", "51-200", "201-500", "501-1000", "1000+")
FACETS = {
	"industry": "industry",
	"state": "state",
	"district": "tbo_district",
	"org_size": "no_of_employees",
}


def seed_industries() -> None:
	"""The starting Industry Types, each added only when it is not there yet; the ones admins
	added from the hub (or any that exist) are left alone."""
	for name in INDUSTRIES:
		if not frappe.db.exists(INDUSTRY_TYPE, name):
			frappe.get_doc({"doctype": INDUSTRY_TYPE, "industry": name}).insert(ignore_permissions=True)


def ensure_blank_org_size() -> None:
	"""ERPNext's employee bands have no empty choice, so a Lead saved without one got the first
	("1-10"). A leading empty option keeps "not known" as it is."""
	make_property_setter("Lead", "no_of_employees", "options", "\n" + "\n".join(ORG_SIZES), "Text")


def clear_default_org_size() -> int:
	"""Clears "1-10" on every lead, but only while no lead holds any other band: then it can only
	be the old forced default (the leads were never asked), not an answer. Returns the leads
	cleared."""
	other = frappe.db.exists(
		"Lead", {"no_of_employees": ("in", [band for band in ORG_SIZES if band != ORG_SIZES[0]])}
	)
	if other:
		return 0
	leads = frappe.get_all("Lead", filters={"no_of_employees": ORG_SIZES[0]}, pluck="name")
	for chunk in range(0, len(leads), 1000):
		frappe.db.set_value(
			"Lead",
			{"name": ("in", leads[chunk : chunk + 1000])},
			"no_of_employees",
			None,
			update_modified=False,
		)
	return len(leads)


def org_sizes() -> list[str]:
	"""ERPNext's employee bands (1-10 … 1000+): the organisation sizes."""
	options = frappe.get_meta("Lead").get_field("no_of_employees").options or ""
	return [band for band in options.split("\n") if band]


def tidy(value) -> str:
	"""Trimmed, inner spaces collapsed, and Title Case when it was typed all lower or all upper."""
	text = " ".join(str(value or "").split())
	return text.title() if text and (text == text.lower() or text == text.upper()) else text


def canonical(value, known: tuple | list, aliases: dict | None = None) -> str:
	"""`value` as the entry of `known` it matches ignoring case, else tidied as typed."""
	text = tidy(value)
	for name in known:
		if name.lower() == text.lower():
			return name
	return (aliases or {}).get(text.lower(), text)


def clean_state(value) -> str:
	return canonical(value, STATES, STATE_ALIASES)


def clean_district(value) -> str:
	return canonical(value, KERALA_DISTRICTS)


def clean_org_size(value) -> str:
	size = " ".join(str(value or "").split())
	if size and size not in org_sizes():
		frappe.throw(_("{0} is not an organisation size.").format(size))
	return size


def clean_industry(value) -> str:
	"""The Industry Type the text names (any case), "" to clear it."""
	name = " ".join(str(value or "").split())
	if not name:
		return ""
	found = frappe.db.exists(INDUSTRY_TYPE, name)
	if not found:
		frappe.throw(_("{0} is not an industry.").format(name))
	return found


def category_values(values: dict) -> dict:
	"""`values` (Lead field names) with the category fields it holds cleaned; an empty value
	clears the field."""
	cleaners = {
		"industry": clean_industry,
		"state": clean_state,
		"tbo_district": clean_district,
		"no_of_employees": clean_org_size,
	}
	return {key: cleaners[key](value) if key in cleaners else value for key, value in values.items()}


def category_fields(values: dict) -> dict:
	"""The category part of an API payload ({industry, state, district, org_size}), under the
	Lead's field names and cleaned."""
	return category_values({FACETS[key]: values[key] for key in FACETS if key in values})


def as_list(value) -> list[str]:
	"""A filter given as one value, a list, or a JSON list, as a list of non-empty strings."""
	if isinstance(value, str) and value.strip().startswith("["):
		value = frappe.parse_json(value)
	if not isinstance(value, list | tuple):
		value = [value]
	return [text for text in (" ".join(str(item or "").split()) for item in value) if text]


def category_filters(industry=None, state=None, district=None, org_size=None) -> dict:
	"""Lead filters for the categories asked: each takes a value or a list (any of them)."""
	asked = {
		"industry": industry,
		"state": [clean_state(item) for item in as_list(state)],
		"tbo_district": [clean_district(item) for item in as_list(district)],
		"no_of_employees": org_size,
	}
	filters = {}
	for field, value in asked.items():
		names = as_list(value)
		if names:
			filters[field] = ("in", names)
	return filters


def require_manager() -> None:
	if not set(MANAGER_ROLES) & set(frappe.get_roles()):
		frappe.throw(_("Only a Sales Manager can do this."), frappe.PermissionError)


def add_industry(name: str) -> dict:
	"""{name, created}: the Industry Type, added when it is new (the same name in another case is
	the existing one)."""
	require_manager()
	name = " ".join(str(name or "").split())
	if not name:
		frappe.throw(_("Give the industry a name."))
	if len(name) > MAX_INDUSTRY_LENGTH:
		frappe.throw(_("An industry name is at most {0} characters.").format(MAX_INDUSTRY_LENGTH))
	found = frappe.db.exists(INDUSTRY_TYPE, name)
	if found:
		return {"name": found, "created": False}
	doc = frappe.get_doc({"doctype": INDUSTRY_TYPE, "industry": name}).insert(ignore_permissions=True)
	return {"name": doc.name, "created": True}


def bulk_set(leads: list, values: dict) -> dict:
	"""Set the categories in `values` ({industry?, state?, district?, org_size?}; "" clears one)
	on up to MAX_BULK leads: {updated, skipped: [{name, reason}]}. A lead the acting user may not
	write is skipped, not an error."""
	require_manager()
	leads = list(dict.fromkeys(as_list(leads)))
	if not leads:
		frappe.throw(_("Pick the leads first."))
	if len(leads) > MAX_BULK:
		frappe.throw(_("At most {0} leads at a time.").format(MAX_BULK))
	unknown = sorted(set(values) - set(FACETS))
	if unknown:
		frappe.throw(_("Unknown fields: {0}").format(", ".join(unknown)))
	changes = category_fields(values)
	if not changes:
		frappe.throw(_("Choose at least one of industry, state, district and size."))
	updated, skipped = 0, []
	for name in leads:
		if not frappe.db.exists("Lead", name):
			skipped.append({"name": name, "reason": "missing"})
		elif not frappe.has_permission("Lead", "write", name):
			skipped.append({"name": name, "reason": "not allowed"})
		else:
			frappe.db.set_value("Lead", name, changes)
			updated += 1
	return {"updated": updated, "skipped": skipped}


def districts_by_state() -> dict[str, list[str]]:
	"""The districts used on the leads the acting user may see, by state (most used first); leads
	without a state are under "". Someone who may not read leads (TBO Management alone) gets none."""
	if not frappe.has_permission("Lead", "read"):
		return {}
	rows = frappe.get_list(
		"Lead",
		filters={"tbo_district": ("is", "set")},
		fields=["state", "tbo_district", "count(name) as total"],
		group_by="state, tbo_district",
		order_by="total desc",
	)
	grouped: dict[str, list[str]] = {}
	for row in rows:
		grouped.setdefault(row.state or "", []).append(row.tbo_district)
	return grouped


def get_options() -> dict:
	return {
		"industries": frappe.get_all(INDUSTRY_TYPE, pluck="name", order_by="name asc"),
		"org_sizes": org_sizes(),
		"states": list(STATES),
		"kerala_districts": list(KERALA_DISTRICTS),
		"districts_by_state": districts_by_state(),
	}


def get_counts() -> dict:
	"""{industry, state, district, org_size}: each {value: leads} over the leads the acting user
	may see (leads without a value are left out)."""
	counts = {}
	for key, field in FACETS.items():
		rows = frappe.get_list(
			"Lead",
			filters={field: ("is", "set")},
			fields=[field, "count(name) as total"],
			group_by=field,
		)
		counts[key] = {row[field]: cint(row.total) for row in rows}
	return counts
