"""Quotations from the deal (gap G3): the New quotation dialog and its essentials-only edit.

The dialog saves a draft ERPNext Quotation as the session user, linked to the deal (ERPNext's
`opportunity`), so ERPNext's own Quotation permissions and the discount/commission control
(tbo_smart.quotation_control) apply exactly as on the desk form. Anything beyond items, qty,
rate, discount, commission, validity, taxes and terms stays in ERPNext ("Open in ERPNext").
"""

import frappe
from erpnext.controllers.accounts_controller import get_default_taxes_and_charges
from frappe import _
from frappe.utils import add_months, flt, getdate, today

from tbo_smart.finance import get_default_company
from tbo_smart.finance.access import require_deal
from tbo_smart.quotation_control import COMMISSION_FIELD, QuotationControl
from tbo_smart.sales.opportunity import customer_of

MAX_ITEMS = 100
TAX_TEMPLATE = "Sales Taxes and Charges Template"


@frappe.whitelist()
def get_quotation_form(deal: str, quotation: str | None = None) -> dict:
	"""Values for the dialog: a new quotation prefilled from the deal (its items, company, party
	and contact), or an existing draft of this deal for editing."""
	require_deal(deal)
	doc = frappe.get_doc("Opportunity", deal)
	company = doc.get("company") or get_default_company()
	choices = form_choices(company)
	if quotation:
		existing = get_editable_quotation(deal, quotation, "read")
		return {**choices, **serialize_quotation(existing)}
	items = [
		{"item_code": row.item_code, "qty": flt(row.qty) or 1, "price_list_rate": flt(row.rate)}
		for row in doc.items
		if row.item_code
	]
	names = item_names([row["item_code"] for row in items])
	return {
		**choices,
		"name": None,
		"company": company,
		"party": get_party(doc),
		"valid_till": add_months(today(), 1),
		"taxes_and_charges": (get_default_taxes_and_charges(TAX_TEMPLATE, company=company) or {}).get(
			"taxes_and_charges"
		)
		if company
		else None,
		"additional_discount_percentage": 0,
		"commission": 0,
		"terms": None,
		"items": [{**row, "item_name": names.get(row["item_code"])} for row in items],
	}


@frappe.whitelist()
def get_item_price(item_code: str, company: str | None = None) -> dict:
	"""Name, UOM and the selling price list rate of an item, for a new line in the dialog."""
	item = frappe.db.get_value(
		"Item",
		{"name": item_code, "disabled": 0, "is_sales_item": 1},
		["name", "item_name", "stock_uom"],
		as_dict=True,
	)
	if not item or not frappe.has_permission("Item", "read", doc=item.name):
		frappe.throw(_("Item {0} is not available for sale.").format(item_code), frappe.DoesNotExistError)
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
	rate = frappe.db.get_value(
		"Item Price", {"item_code": item.name, "price_list": price_list, "selling": 1}, "price_list_rate"
	)
	return {
		"item_code": item.name,
		"item_name": item.item_name,
		"uom": item.stock_uom,
		"price_list_rate": flt(rate),
	}


@frappe.whitelist(methods=["POST"])
def save_quotation(deal: str, values: dict | str, quotation: str | None = None) -> dict:
	"""Create a draft Quotation for the deal, or update one of its drafts, from the dialog.

	`values`: company, valid_till, taxes_and_charges, additional_discount_percentage,
	commission, terms and items (item_code, qty, price_list_rate, discount_percentage)."""
	require_deal(deal)
	values = parse_values(values)
	if quotation:
		doc = get_editable_quotation(deal, quotation, "write")
	else:
		if not frappe.has_permission("Quotation", "create"):
			frappe.throw(_("You cannot create quotations."), frappe.PermissionError)
		doc = new_quotation(frappe.get_doc("Opportunity", deal), values.get("company"))
	apply_values(doc, values)
	if doc.is_new():
		doc.insert()
	else:
		doc.save()
	return {
		**serialize_quotation(doc),
		"needs_approval": [
			{"kind": kind, "percent": percent, "limit": limit}
			for kind, percent, limit in QuotationControl(doc).uncovered()
		],
	}


@frappe.whitelist(methods=["POST"])
def submit_quotation(deal: str, quotation: str) -> dict:
	"""Submit a draft quotation of the deal. The discount/commission control refuses it while an
	approval is missing, exactly as on the desk form."""
	require_deal(deal)
	doc = get_editable_quotation(deal, quotation, "submit")
	doc.submit()
	return serialize_quotation(doc)


# ---------- helpers ----------


def form_choices(company: str | None) -> dict:
	"""The dialog's pick lists: companies, the company's tax templates with their rates, payment
	terms templates and terms and conditions."""
	templates = get_tax_templates(company)
	return {
		"companies": frappe.get_list("Company", pluck="name", limit_page_length=0),
		"tax_templates": templates,
		"tax_rates": get_tax_rates(templates),
		"payment_terms_templates": frappe.get_list(
			"Payment Terms Template", pluck="name", limit_page_length=0
		),
		"terms_templates": frappe.get_list(
			"Terms and Conditions", filters={"disabled": 0, "selling": 1}, pluck="name", limit_page_length=0
		),
	}


def get_editable_quotation(deal: str, quotation: str, ptype: str):
	if not frappe.db.exists("Quotation", quotation):
		frappe.throw(_("Quotation {0} not found.").format(quotation), frappe.DoesNotExistError)
	doc = frappe.get_doc("Quotation", quotation)
	if not belongs_to_deal(doc, deal):
		frappe.throw(
			_("Quotation {0} does not belong to this deal.").format(quotation), frappe.PermissionError
		)
	doc.check_permission(ptype)
	if ptype != "read" and doc.docstatus != 0:
		frappe.throw(_("Only a draft quotation can be changed here. Open it in ERPNext."))
	return doc


def belongs_to_deal(doc, deal: str) -> bool:
	"""Linked to the deal by ERPNext's `opportunity` (as the Finance view lists)."""
	return doc.get("opportunity") == deal


def get_party(deal) -> dict:
	"""The deal's ERPNext Customer when it has one, else the deal's own party (the lead or the
	prospect), from which ERPNext makes the Customer with the Sales Order."""
	customer = customer_of(deal)
	if customer:
		return {"quotation_to": "Customer", "party_name": customer, "label": customer}
	return {
		"quotation_to": deal.opportunity_from,
		"party_name": deal.party_name,
		"label": deal.get("customer_name") or deal.get("contact_display") or deal.party_name,
	}


def new_quotation(deal, company: str | None):
	party = get_party(deal)
	doc = frappe.new_doc("Quotation")
	doc.update(
		{
			"quotation_to": party["quotation_to"],
			"party_name": party["party_name"],
			"opportunity": deal.name,
			"company": company or deal.get("company") or get_default_company(),
			"transaction_date": today(),
			"contact_person": deal.get("contact_person"),
			"customer_address": deal.get("customer_address"),
		}
	)
	if party["quotation_to"] != "Customer":
		doc.customer_name = party["label"]
	return doc


def parse_values(values) -> frappe._dict:
	if isinstance(values, str):
		try:
			values = frappe.parse_json(values)
		except ValueError:
			values = None
	if not isinstance(values, dict):
		frappe.throw(_("values must be an object."))
	values = frappe._dict(values)
	items = values.get("items")
	if not isinstance(items, list) or not items:
		frappe.throw(_("Add at least one item."))
	if len(items) > MAX_ITEMS:
		frappe.throw(_("A quotation from here can have at most {0} items.").format(MAX_ITEMS))
	for idx, row in enumerate(items, start=1):
		if not isinstance(row, dict) or not row.get("item_code"):
			frappe.throw(_("Row {0}: choose an item.").format(idx))
		if flt(row.get("qty")) <= 0:
			frappe.throw(_("Row {0}: quantity must be more than 0.").format(idx))
		if flt(row.get("price_list_rate")) < 0:
			frappe.throw(_("Row {0}: rate cannot be negative.").format(idx))
		if not 0 <= flt(row.get("discount_percentage")) <= 100:
			frappe.throw(_("Row {0}: discount must be between 0 and 100%.").format(idx))
	for field in ("additional_discount_percentage", "commission"):
		if not 0 <= flt(values.get(field)) <= 100:
			frappe.throw(_("{0} must be between 0 and 100%.").format(field.replace("_", " ").capitalize()))
	if values.get("valid_till") and getdate(values.valid_till) < getdate(today()):
		frappe.throw(_("Valid till cannot be in the past."))
	return values


def apply_values(doc, values: frappe._dict) -> None:
	"""Only the essentials; everything else on the quotation is left as it is."""
	if values.get("company") and doc.is_new():
		doc.company = values.company
	template = values.get("taxes_and_charges") or None
	if template != doc.get("taxes_and_charges") or doc.is_new():
		doc.taxes_and_charges = template
		doc.set("taxes", [])
		if template:
			doc.append_taxes_from_master(TAX_TEMPLATE)
	doc.valid_till = values.get("valid_till") or doc.valid_till
	doc.additional_discount_percentage = flt(values.get("additional_discount_percentage"))
	doc.discount_amount = 0
	doc.set(COMMISSION_FIELD, flt(values.get("commission")))
	if "tc_name" in values:
		doc.tc_name = values.get("tc_name") or None
	doc.terms = values.get("terms") or (
		frappe.db.get_value("Terms and Conditions", doc.tc_name, "terms") if doc.get("tc_name") else None
	)
	if "payment_terms_template" in values and values.get("payment_terms_template") != doc.get(
		"payment_terms_template"
	):
		# ERPNext rebuilds the schedule from the template on validate
		doc.payment_terms_template = values.get("payment_terms_template") or None
		doc.set("payment_schedule", [])
	doc.set("items", [])
	for row in values["items"]:
		list_rate = flt(row.get("price_list_rate"))
		discount = flt(row.get("discount_percentage"))
		rate = flt(list_rate * (1 - discount / 100), 2)
		line = {
			"item_code": row["item_code"],
			"qty": flt(row["qty"]),
			"price_list_rate": list_rate,
			"discount_percentage": discount,
			"discount_amount": flt(list_rate - rate, 2),
			"rate": rate,
		}
		if (row.get("description") or "").strip():
			line["description"] = row["description"].strip()
		doc.append("items", line)


def item_names(codes: list[str]) -> dict[str, str]:
	if not codes:
		return {}
	return dict(
		frappe.get_all("Item", filters={"name": ("in", codes)}, fields=["name", "item_name"], as_list=True)
	)


def get_tax_rates(templates: list[str]) -> dict[str, float]:
	"""{template: total % of its "On Net Total" rows}, for the dialog's live tax estimate; the
	saved quotation's own taxes are the real figure."""
	if not templates:
		return {}
	Tax = frappe.qb.DocType("Sales Taxes and Charges")
	rows = (
		frappe.qb.from_(Tax)
		.select(Tax.parent, Tax.rate)
		.where(
			(Tax.parenttype == TAX_TEMPLATE)
			& Tax.parent.isin(templates)
			& (Tax.charge_type == "On Net Total")
			& (Tax.included_in_print_rate == 0)
		)
	).run(as_dict=True)
	rates: dict[str, float] = {}
	for row in rows:
		rates[row.parent] = flt(rates.get(row.parent)) + flt(row.rate)
	return rates


def get_tax_templates(company: str | None) -> list[str]:
	if not company:
		return []
	return frappe.get_list(
		TAX_TEMPLATE, filters={"company": company, "disabled": 0}, pluck="name", limit_page_length=0
	)


def serialize_quotation(doc) -> dict:
	return {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"status": doc.status,
		"company": doc.company,
		"currency": doc.currency,
		"party": {
			"quotation_to": doc.quotation_to,
			"party_name": doc.party_name,
			"label": doc.customer_name or doc.party_name,
		},
		"valid_till": doc.valid_till,
		"taxes_and_charges": doc.get("taxes_and_charges"),
		"additional_discount_percentage": flt(doc.additional_discount_percentage),
		"commission": flt(doc.get(COMMISSION_FIELD)),
		"terms": doc.get("terms"),
		"tc_name": doc.get("tc_name"),
		"payment_terms_template": doc.get("payment_terms_template"),
		"total": flt(doc.total),
		"discount_amount": flt(doc.discount_amount),
		"net_total": flt(doc.net_total),
		"total_taxes_and_charges": flt(doc.total_taxes_and_charges),
		"grand_total": flt(doc.grand_total),
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"price_list_rate": flt(row.price_list_rate) or flt(row.rate),
				"discount_percentage": flt(row.discount_percentage),
				"description": row.description,
				"amount": flt(row.amount),
			}
			for row in doc.items
		],
	}
