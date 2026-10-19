"""The quotation catalogue (docs/lead-pipeline.md §9.2): ERPNext's own masters wherever they fit,
plus TBO Quotation Package for bundles.

| Need | Master |
| --- | --- |
| Services and products, their category | Item (sales items) and Item Group |
| Rates | Item Price on Selling Settings' selling price list, or a segment's own (rate_card.py) |
| Packages: default lines, description, qty, rate, project duration | TBO Quotation Package (new; ERPNext's Product Bundle is a stock bundle with no rates or text) |
| GST | Sales Taxes and Charges Template |
| Payment terms, terms and conditions | Payment Terms Template, Terms and Conditions |
| Validity, defaults | TBO Smart Settings |

Only the roles in TBO Smart Settings → "Roles that edit presets and prices" change packages
and rates here.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from tbo_smart.lead_pipeline import setting

PACKAGE = "TBO Quotation Package"
MAX_SERVICES = 500
MAX_LINES = 50
DEFAULT_PRESET_ROLES = ("Sales Manager", "System Manager")


def preset_roles() -> set[str]:
	lines = [line.strip() for line in (setting("quotation_preset_roles") or "").splitlines() if line.strip()]
	return set(lines or DEFAULT_PRESET_ROLES)


def can_edit_presets(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(frappe.get_roles(user)) & preset_roles())


def require_preset_editor() -> None:
	if not can_edit_presets():
		frappe.throw(
			_("Only {0} may change quotation presets and prices.").format(", ".join(sorted(preset_roles()))),
			frappe.PermissionError,
		)


def price_list() -> str | None:
	return frappe.db.get_single_value("Selling Settings", "selling_price_list")


def get_catalogue(price_list_name: str | None = None) -> dict:
	"""{packages, services, item_groups, price_list, can_edit}: what the quotation builder offers,
	priced from `price_list_name` (a segment's list, rate_card.py) where it has a rate, else from
	the selling price list."""
	packages = frappe.get_all(
		PACKAGE,
		# editors see the disabled ones too, to switch them back on; the builder offers enabled ones
		filters=None if can_edit_presets() else {"enabled": 1},
		fields=[
			"name",
			"package_name",
			"item_group",
			"description",
			"project_duration",
			"validity_days",
			"enabled",
		],
		order_by="package_name asc",
		limit_page_length=0,
	)
	lines = frappe.get_all(
		"TBO Quotation Package Item",
		filters={"parenttype": PACKAGE, "parent": ("in", [row.name for row in packages] or [""])},
		fields=["parent", "item_code", "description", "qty", "rate", "idx"],
		order_by="idx asc",
		limit_page_length=0,
	)
	services = get_services(price_list_name)
	rates = {row["item_code"]: row["rate"] for row in services}
	for package in packages:
		package["items"] = [
			{
				"item_code": line.item_code,
				"description": line.description,
				"qty": flt(line.qty) or 1,
				"rate": flt(line.rate) or flt(rates.get(line.item_code)),
			}
			for line in lines
			if line.parent == package.name
		]
	return {
		"packages": packages,
		"services": services,
		"item_groups": sorted({row["item_group"] for row in services if row["item_group"]}),
		"price_list": price_list_name or price_list(),
		"can_edit": can_edit_presets(),
	}


def get_services(price_list_name: str | None = None) -> list[dict]:
	"""Enabled sales items with their category and selling rate: `price_list_name`'s rate where it
	has one, else the selling price list's."""
	Item = frappe.qb.DocType("Item")
	Price = frappe.qb.DocType("Item Price")
	rows = (
		frappe.qb.from_(Item)
		.left_join(Price)
		.on(
			(Price.item_code == Item.name) & (Price.price_list == (price_list() or "")) & (Price.selling == 1)
		)
		.select(
			Item.name,
			Item.item_name,
			Item.item_group,
			Item.description,
			Item.stock_uom,
			Price.price_list_rate,
		)
		.where((Item.disabled == 0) & (Item.is_sales_item == 1) & (Item.has_variants == 0))
		.orderby(Item.item_group)
		.orderby(Item.item_name)
		.limit(MAX_SERVICES)
	).run(as_dict=True)
	own = (
		dict(
			frappe.get_all(
				"Item Price",
				filters={
					"price_list": price_list_name,
					"selling": 1,
					"item_code": ("in", [row.name for row in rows] or [""]),
				},
				fields=["item_code", "price_list_rate"],
				as_list=True,
			)
		)
		if price_list_name and price_list_name != price_list()
		else {}
	)
	seen, services = set(), []
	for row in rows:
		if row.name in seen:
			continue
		seen.add(row.name)
		services.append(
			{
				"item_code": row.name,
				"item_name": row.item_name,
				"item_group": row.item_group,
				"description": frappe.utils.strip_html(row.description or "").strip() or None,
				"uom": row.stock_uom,
				"rate": flt(own.get(row.name, row.price_list_rate)),
			}
		)
	return services


def save_package(values: dict) -> str:
	"""Create or update a package (name, category, description, duration, validity, lines)."""
	require_preset_editor()
	name = (values.get("name") or "").strip()
	doc = (
		frappe.get_doc(PACKAGE, name) if name and frappe.db.exists(PACKAGE, name) else frappe.new_doc(PACKAGE)
	)
	package_name = (values.get("package_name") or name).strip()
	if frappe.db.exists(PACKAGE, {"package_name": package_name, "name": ("!=", doc.name or "")}):
		frappe.throw(
			_("A package called {0} already exists.").format(package_name), frappe.DuplicateEntryError
		)
	doc.update(
		{
			"package_name": package_name,
			"item_group": values.get("item_group") or None,
			"description": values.get("description") or None,
			"project_duration": values.get("project_duration") or None,
			"validity_days": cint(values.get("validity_days")) or None,
			"enabled": cint(values.get("enabled", 1)),
		}
	)
	lines = values.get("items") or []
	if not isinstance(lines, list) or not lines or len(lines) > MAX_LINES:
		frappe.throw(_("A package has 1 to {0} lines.").format(MAX_LINES))
	doc.set("items", [])
	for line in lines:
		doc.append(
			"items",
			{
				"item_code": line.get("item_code"),
				"description": line.get("description") or None,
				"qty": flt(line.get("qty")) or 1,
				"rate": flt(line.get("rate")),
			},
		)
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	return doc.name


def set_service_rate(item_code: str, rate: float, price_list_name: str | None = None) -> float:
	"""The item's selling rate on `price_list_name` (a segment's own list), else on the selling
	price list (an Item Price, created when missing)."""
	require_preset_editor()
	if flt(rate) < 0:
		frappe.throw(_("A rate can't be negative."))
	if not frappe.db.exists("Item", {"name": item_code, "is_sales_item": 1, "disabled": 0}):
		frappe.throw(_("{0} is not a sales item.").format(item_code))
	selling = price_list_name or price_list()
	if not selling:
		frappe.throw(_("Set the default selling price list in Selling Settings first."))
	name = frappe.db.get_value(
		"Item Price", {"item_code": item_code, "price_list": selling, "selling": 1}, "name"
	)
	doc = frappe.get_doc("Item Price", name) if name else frappe.new_doc("Item Price")
	doc.update({"item_code": item_code, "price_list": selling, "price_list_rate": flt(rate)})
	if name:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)
	return flt(doc.price_list_rate)
