"""What a salesperson is calling for (docs/call-batches.md §10): one department, optionally one of
its products and one district, kept in a TBO BD Focus per person.

The choice only narrows what the person is handed: a manager's campaign product or district on
the department stays a limit, and a person can only pick from the departments they are a member
of that hand out batches, and from those departments' active products.
"""

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart.sales import products, stale
from tbo_smart.sales.call_batches import batch_departments

FOCUS = "TBO BD Focus"
MAX_DISTRICTS = 200


def clean(value) -> str:
	return " ".join(str(value or "").split())


def districts_of(department, user: str) -> list[str]:
	"""The districts of the leads `user` could be handed in `department` (their own open leads and,
	when the department pulls unassigned ones, those), most leads first."""
	if not frappe.db.has_column("Lead", "tbo_district"):
		return []
	Lead = frappe.qb.DocType("Lead")
	mine = Lead.lead_owner == user
	if cint(department.pull_unassigned):
		mine = mine | Lead.lead_owner.isnull() | (Lead.lead_owner == "")
	rows = (
		frappe.qb.from_(Lead)
		.select(Lead.tbo_district)
		.where(
			(Lead.tbo_department == department.name)
			& mine
			& Lead.tbo_stage.isin(list(stale.STALE_STAGES))
			& Lead.tbo_district.isnotnull()
			& (Lead.tbo_district != "")
		)
		.run()
	)
	counts: dict[str, int] = {}
	names: dict[str, str] = {}
	for (district,) in rows:
		key = clean(district).lower()
		names.setdefault(key, clean(district))
		counts[key] = counts.get(key, 0) + 1
	ordered = sorted(counts, key=lambda key: (-counts[key], key))
	return [names[key] for key in ordered][:MAX_DISTRICTS]


def option_of(department, user: str) -> dict:
	"""{department, products: [{product_name, intro_line}], districts, locked_product,
	locked_district}: what `user` may pick under `department`. A department's campaign product
	or district, or the product of the person's call plan (§11), is a limit set by a manager: it
	is the only choice and `locked_*` says so."""
	from tbo_smart.sales.call_batches.plans import plan_product

	locked_product = clean(department.campaign_product) or plan_product(department, user)
	locked_district = clean(department.campaign_district)
	listed = [
		{"product_name": row["product_name"], "intro_line": row["intro_line"]}
		for row in products.active_serialized(department.name)
	]
	if locked_product:
		listed = [row for row in listed if row["product_name"].lower() == locked_product.lower()]
	return {
		"department": department.name,
		"products": listed,
		"districts": [locked_district] if locked_district else districts_of(department, user),
		"locked_product": locked_product or None,
		"locked_district": locked_district or None,
	}


def options_for(user: str) -> list[dict]:
	return [option_of(department, user) for department in batch_departments(user)]


def get_focus(user: str) -> dict | None:
	"""{department, product, district} the person chose, or None. A choice that no longer holds (the
	department switched batches off, the person left it, the product was switched off or is not
	the manager's campaign product any more) is dropped part by part."""
	saved = frappe.db.get_value(FOCUS, user, ["department", "product", "district"], as_dict=True)
	if not saved:
		return None
	option = next((o for o in options_for(user) if o["department"] == saved.department), None)
	if not option:
		return None
	product = next(
		(
			row["product_name"]
			for row in option["products"]
			if row["product_name"].lower() == clean(saved.product).lower()
		),
		None,
	)
	district = option["locked_district"] or clean(saved.district) or None
	return {"department": saved.department, "product": product, "district": district}


def set_focus(
	user: str, department: str | None, product: str | None = None, district: str | None = None
) -> dict:
	"""Save what `user` is calling for and return it. Throws when the department is not one of
	theirs that hands out batches, or the product is not one of its choices. A blank department
	clears the choice (None is returned)."""
	department = clean(department)
	if not department:
		frappe.db.delete(FOCUS, {"name": user})
		return None
	option = next((o for o in options_for(user) if o["department"] == department), None)
	if not option:
		frappe.throw(
			_("You cannot call for {0}: it is not one of your departments with daily calls.").format(
				department
			)
		)
	wanted = clean(product)
	chosen = None
	if wanted:
		chosen = next(
			(
				row["product_name"]
				for row in option["products"]
				if row["product_name"].lower() == wanted.lower()
			),
			None,
		)
		if not chosen:
			frappe.throw(_("{0} is not a product you can call for in {1}.").format(wanted, department))
	elif option["locked_product"]:
		chosen = option["products"][0]["product_name"] if option["products"] else None
	place = option["locked_district"] or clean(district)
	values = {"department": department, "product": chosen or "", "district": place}
	if frappe.db.exists(FOCUS, user):
		frappe.db.set_value(FOCUS, user, values)
	else:
		frappe.get_doc({"doctype": FOCUS, "user": user, **values}).insert(ignore_permissions=True)
	return {"department": department, "product": chosen, "district": place or None}


def describe(focus: dict | None) -> str:
	"""The focus in plain words: "Hotels (StayVeX) in Kochi"."""
	if not focus:
		return ""
	label = f"{focus['product']} ({focus['department']})" if focus.get("product") else focus["department"]
	return f"{label} in {focus['district']}" if focus.get("district") else label
