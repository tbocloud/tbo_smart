"""One-off backfill of `crm_deal` on existing Sales Orders, Sales Invoices and Payment Entries
(docs/crm-finance-workflow.md risk R4).

Walks the same chains as the live hooks, quotation → sales order → sales invoice → payment
references, in a few set-based queries per doctype. Only blank `crm_deal` values are filled,
so running it again changes nothing. Documents it cannot resolve are skipped and listed in the
`tbo_smart.finance` log (and counted on the console) for Accounts to link by hand.
"""

import frappe

from tbo_smart.finance.deal_links import deals_of, deals_of_quotations, single

PREVIEW = 20


class DealLinkBackfill:
	def __init__(self):
		self.logger = frappe.logger("tbo_smart.finance", allow_site=True)
		self.result = {}

	def run(self) -> dict:
		self.backfill("Sales Order", self.order_sources(), self.resolve_orders)
		self.backfill("Sales Invoice", self.invoice_sources(), self.resolve_invoices)
		self.backfill("Payment Entry", self.payment_sources(), self.resolve_payments)
		return self.result

	def backfill(self, doctype: str, sources: dict[str, set], resolve) -> None:
		deals = resolve(sources)
		for name, deal in deals.items():
			frappe.db.set_value(doctype, name, "crm_deal", deal, update_modified=False)
		unresolved = sorted(set(sources) - set(deals))
		self.result[doctype] = {"linked": len(deals), "unresolved": unresolved}
		self.logger.info(
			f"{doctype}: linked {len(deals)}, unresolved {len(unresolved)}: {', '.join(unresolved)}"
		)
		print(
			f"tbo_smart finance backfill: {doctype} linked {len(deals)}, unresolved {len(unresolved)}"
			+ (f" (first: {', '.join(unresolved[:PREVIEW])})" if unresolved else "")
		)

	# ---------- sources: blank crm_deal → the names it can be read from ----------

	@staticmethod
	def child_sources(parent: str, child: str, field: str, extra=None) -> dict[str, set]:
		Parent = frappe.qb.DocType(parent)
		Child = frappe.qb.DocType(child)
		condition = (Parent.docstatus < 2) & ((Parent.crm_deal.isnull()) | (Parent.crm_deal == ""))
		if extra is not None:
			condition = condition & extra(Parent)
		rows = (
			frappe.qb.from_(Parent)
			.left_join(Child)
			.on((Child.parent == Parent.name) & (Child.parenttype == parent))
			.select(Parent.name, Child[field])
			.where(condition)
		).run()
		sources = {}
		for name, value in rows:
			sources.setdefault(name, set())
			if value:
				sources[name].add(value)
		return sources

	def order_sources(self) -> dict[str, set]:
		return self.child_sources("Sales Order", "Sales Order Item", "prevdoc_docname")

	def invoice_sources(self) -> dict[str, set]:
		return self.child_sources("Sales Invoice", "Sales Invoice Item", "sales_order")

	def payment_sources(self) -> dict[str, set]:
		"""Customer receipts and refunds: (reference doctype, name) pairs."""
		Entry = frappe.qb.DocType("Payment Entry")
		Reference = frappe.qb.DocType("Payment Entry Reference")
		rows = (
			frappe.qb.from_(Entry)
			.left_join(Reference)
			.on((Reference.parent == Entry.name) & (Reference.parenttype == "Payment Entry"))
			.select(Entry.name, Reference.reference_doctype, Reference.reference_name)
			.where(
				(Entry.docstatus < 2)
				& (Entry.party_type == "Customer")
				& ((Entry.crm_deal.isnull()) | (Entry.crm_deal == ""))
			)
		).run()
		sources = {}
		for name, doctype, reference in rows:
			sources.setdefault(name, set())
			if reference:
				sources[name].add((doctype, reference))
		return sources

	# ---------- resolution ----------

	@staticmethod
	def resolve_orders(sources: dict[str, set]) -> dict[str, str]:
		quotation_deals = deals_of_quotations({q for names in sources.values() for q in names})
		return pick(sources, lambda quotation: quotation_deals.get(quotation))

	@staticmethod
	def resolve_invoices(sources: dict[str, set]) -> dict[str, str]:
		order_deals = deals_of("Sales Order", {o for names in sources.values() for o in names})
		return pick(sources, lambda order: order_deals.get(order))

	@staticmethod
	def resolve_payments(sources: dict[str, set]) -> dict[str, str]:
		wanted = {}
		for pairs in sources.values():
			for doctype, name in pairs:
				wanted.setdefault(doctype, set()).add(name)
		deals = {
			(doctype, name): deal
			for doctype in ("Sales Invoice", "Sales Order")
			for name, deal in deals_of(doctype, wanted.get(doctype, set())).items()
		}
		return pick(sources, lambda pair: deals.get(pair))


def pick(sources: dict[str, set], lookup) -> dict[str, str]:
	"""Documents whose every source resolves to the same single deal."""
	resolved = {}
	for name, keys in sources.items():
		found = [lookup(key) for key in keys]
		if found and all(found):
			deal = single(found)
			if deal:
				resolved[name] = deal
	return resolved


def run() -> dict:
	return DealLinkBackfill().run()
