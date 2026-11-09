"""Products / brands under a lead department (docs/lead-departments.md §10).

A department such as GrowthX can list the names its people call under (PhoebiX, BillaX, ...). A
lead then carries one of them in `tbo_product`. Only managers (System Manager, Sales Manager and
the department's heads) set it; everyone who can see the lead sees it. A department with no
products works exactly as before.
"""

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart.sales import departments

PRODUCT = "TBO Lead Product"
MAX_NAME = 140
MAX_INTRO = 140


def rows_of(department: str) -> list[frappe._dict]:
	"""The department's products in their order, switched-off ones included."""
	return frappe.get_all(
		PRODUCT,
		filters={"parent": department, "parenttype": departments.DEPARTMENT},
		fields=["product_name", "is_active", "intro_line", "website"],
		order_by="idx asc",
	)


def serialize(rows) -> list[dict]:
	return [
		{
			"product_name": row.product_name,
			"is_active": cint(row.is_active),
			"intro_line": row.intro_line or "",
			"website": row.website or "",
		}
		for row in rows
	]


def active_serialized(department: str) -> list[dict]:
	return [row for row in serialize(rows_of(department)) if row["is_active"]]


def find(department: str | None, product: str | None, active_only: bool = True) -> str | None:
	"""The department's product the text names (any case), else None."""
	wanted = " ".join(str(product or "").split()).lower()
	if not department or not wanted:
		return None
	for row in rows_of(department):
		if row.product_name.lower() == wanted and (cint(row.is_active) or not active_only):
			return row.product_name
	return None


def apply(department, rows: list) -> list[tuple[str, str]]:
	"""Replace the department's products with `rows` ({product_name, is_active?, intro_line?,
	website?, previous_name?}); the caller saves it and then calls `carry_renames` with what this
	returns. A row with a `previous_name` that differs from its new name renames that product:
	its leads follow. Names are tidied and must be unique ignoring case."""
	if not isinstance(rows, list | tuple):
		frappe.throw(_("products must be a list."))
	existing = {row.product_name.lower(): row.product_name for row in department.get("products") or []}
	renames = []
	department.set("products", [])
	for row in rows:
		previous = existing.get(" ".join(str(row.get("previous_name") or "").split()).lower())
		new = " ".join(str(row.get("product_name") or "").split())
		if previous and new and previous != new:
			renames.append((previous, new))
			if (department.campaign_product or "").lower() == previous.lower():
				department.campaign_product = new
		department.append(
			"products",
			{
				"product_name": " ".join(str((row or {}).get("product_name") or "").split()),
				"is_active": cint(row.get("is_active", 1)),
				"intro_line": " ".join(str(row.get("intro_line") or "").split()),
				"website": (row.get("website") or "").strip(),
			},
		)
	return renames


def carry_renames(department: str, renames: list[tuple[str, str]]) -> None:
	"""Move the department's leads from each renamed product's old name to its new one."""
	for old, new in renames:
		frappe.db.set_value(
			"Lead",
			{"tbo_department": department, "tbo_product": old},
			"tbo_product",
			new,
			update_modified=False,
		)


def validate_products(department) -> None:
	"""Controller validate: every product has a name and a short intro, and no name repeats."""
	seen = set()
	for row in department.get("products") or []:
		name = (row.product_name or "").strip()
		if not name:
			frappe.throw(_("Give each product a name."))
		if len(name) > MAX_NAME or len(row.intro_line or "") > MAX_INTRO:
			frappe.throw(
				_("{0}: the name and the intro line are at most {1} characters.").format(name, MAX_INTRO)
			)
		if name.lower() in seen:
			frappe.throw(_("{0} is listed more than once in {1}.").format(name, department.name))
		seen.add(name.lower())
		row.product_name = name


def can_change(department: str | None) -> bool:
	"""System Manager, Sales Manager, or a head of the department."""
	return departments.can_manage() or departments.is_head(department)


def require_can_change(department: str | None) -> None:
	if not can_change(department):
		frappe.throw(
			_("Only a manager or the department's head may change a lead's product."), frappe.PermissionError
		)


def clean_for(department: str | None, product: str | None) -> str | None:
	"""The department's active product `product` names, or None for blank. Throws when the
	department has no such active product."""
	text = " ".join(str(product or "").split())
	if not text:
		return None
	if not department:
		frappe.throw(_("Choose the department before the product."))
	found = find(department, text)
	if not found:
		frappe.throw(_("{0} is not an active product of {1}.").format(text, department))
	return found


def validate_lead_product(doc, method=None) -> None:
	"""doc_events: Lead validate. A product must be an active product of the lead's department,
	checked when the product or the department changes (so a lead stays editable after its
	product is switched off); changing it needs a manager unless trusted code saves the lead."""
	if frappe.flags.in_import:
		return
	product = " ".join(str(doc.get("tbo_product") or "").split())
	before = doc.get_doc_before_save()
	was = " ".join(str(before.get("tbo_product") or "").split()) if before else ""
	changed = product != was
	if not product:
		doc.tbo_product = None
		if changed and not doc.flags.ignore_permissions:
			require_can_change(doc.get("tbo_department"))
		return
	if not (changed or doc.is_new() or doc.has_value_changed("tbo_department")):
		return
	doc.tbo_product = clean_for(doc.get("tbo_department"), product)
	if changed and not doc.flags.ignore_permissions:
		require_can_change(doc.get("tbo_department"))


def bulk_value(name: str, product: str | None) -> tuple[str | None, str | None]:
	"""(the product to store on lead `name`, None) or (None, why it is skipped): the acting user
	must be a manager of the lead's department and the product one of its active products ("" clears)."""
	department = frappe.db.get_value("Lead", name, "tbo_department")
	if not can_change(department):
		return None, "not a manager of its department"
	try:
		return clean_for(department, product), None
	except frappe.ValidationError:
		return None, "not a product of its department"


def calling_for(leads: list[str]) -> dict[str, dict]:
	"""{lead: {product, intro_line, calling_for}} for each lead. `calling_for` is the product's
	name, else the department's; both are None for a lead with neither. The intro line is shown
	for a product that has since been switched off too."""
	if not leads:
		return {}
	rows = frappe.get_all(
		"Lead",
		filters={"name": ("in", leads)},
		fields=["name", "tbo_department", "tbo_product"],
		limit_page_length=0,
	)
	by_department = {row.tbo_department for row in rows if row.tbo_product and row.tbo_department}
	intro = {}
	if by_department:
		for product in frappe.get_all(
			PRODUCT,
			filters={"parent": ("in", list(by_department)), "parenttype": departments.DEPARTMENT},
			fields=["parent", "product_name", "intro_line"],
			limit_page_length=0,
		):
			intro[(product.parent, product.product_name)] = product.intro_line or None
	return {
		row.name: {
			"product": row.tbo_product or None,
			"intro_line": intro.get((row.tbo_department, row.tbo_product)) if row.tbo_product else None,
			"calling_for": row.tbo_product or row.tbo_department or None,
		}
		for row in rows
	}


def selling_of(lead: str) -> dict | None:
	"""{name, intro} of the lead's product for the AI's drafts, or None (then the draft is from TBO)."""
	info = calling_for([lead]).get(lead) or {}
	if not info.get("product"):
		return None
	return {"name": info["product"], "intro": info["intro_line"]}
