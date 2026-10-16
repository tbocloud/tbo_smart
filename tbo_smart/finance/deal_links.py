"""Which deal (Opportunity) a Sales Order, Sales Invoice or Payment Entry belongs to (gap G1).

The deal is read along the document chain: quotation → sales order → sales invoice, and the
payment's references for a payment entry. ERPNext's mappers already copy `opportunity` from a
quotation or order onto the next document (same fieldname); these resolvers fill it when it
is missing, for example on a document typed by hand that still references its source.
"""

import frappe


def deals_of_quotations(names) -> dict[str, str]:
	"""{quotation: deal} for quotations linked to a deal (ERPNext's `opportunity`)."""
	return deals_of("Quotation", names)


def deals_of(doctype: str, names) -> dict[str, str]:
	"""{name: opportunity} for Quotations, Sales Orders or Sales Invoices that carry a deal."""
	names = list({name for name in names if name})
	if not names:
		return {}
	Table = frappe.qb.DocType(doctype)
	rows = (
		frappe.qb.from_(Table)
		.select(Table.name, Table.opportunity)
		.where(Table.name.isin(names) & Table.opportunity.isnotnull() & (Table.opportunity != ""))
	).run()
	return dict(rows)


def single(deals) -> str | None:
	"""The one deal all sources agree on, else None."""
	unique = {deal for deal in deals if deal}
	return unique.pop() if len(unique) == 1 else None


def source_quotations(doc) -> list[str]:
	"""Quotations behind a Sales Order (item prevdoc) or a Sales Invoice (through its orders)."""
	if doc.doctype == "Sales Order":
		return list({item.prevdoc_docname for item in doc.items if item.get("prevdoc_docname")})
	orders = list({item.sales_order for item in doc.items if item.get("sales_order")})
	if not orders:
		return []
	Item = frappe.qb.DocType("Sales Order Item")
	return (
		frappe.qb.from_(Item)
		.select(Item.prevdoc_docname)
		.distinct()
		.where(Item.parent.isin(orders) & Item.prevdoc_docname.isnotnull() & (Item.prevdoc_docname != ""))
	).run(pluck=True)


def resolve_sales_order(doc) -> str | None:
	return single(deals_of_quotations(source_quotations(doc)).values())


def resolve_sales_invoice(doc) -> str | None:
	orders = {item.sales_order for item in doc.items if item.get("sales_order")}
	deal = single(deals_of("Sales Order", orders).values())
	return deal or single(deals_of_quotations(source_quotations(doc)).values())


def resolve_payment_entry(doc) -> str | None:
	"""The deal shared by every Sales Invoice / Sales Order reference; None when they differ,
	when one has no deal, or when there are no such references."""
	by_doctype = {}
	for row in doc.get("references") or []:
		if row.reference_doctype in ("Sales Invoice", "Sales Order"):
			by_doctype.setdefault(row.reference_doctype, set()).add(row.reference_name)
	if not by_doctype:
		return None
	deals = []
	for doctype, names in by_doctype.items():
		found = deals_of(doctype, names)
		if len(found) < len(names):
			return None
		deals += found.values()
	return single(deals)
