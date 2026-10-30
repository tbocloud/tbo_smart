"""Proposals and the money after them, for the hub's Leads overview (docs/hub-leads-api.md §2.8).

For the overview's lead owners and period: the proposals sent and their value, the value won,
the advances received, and every payment split still to come in, by month.

Where the money is read from (ERPNext v15):

- **A lead's Sales Orders:** submitted orders whose deal (`opportunity`) was made from the lead,
  or whose items come from one of the lead's quotations (`Sales Order Item.prevdoc_docname`).
- **Advances received** in the period: customer receipts (Payment Entry, Receive, submitted)
  allocated to those orders, by the receipt's posting date.
- **Received on an order:** the larger of its `advance_paid` and what its invoices have been
  paid (each invoice's paid share, split over the orders its items come from). Payment Schedule
  `paid_amount` is left aside: ERPNext fills it only when a receipt names the payment term.
  The received amount settles the order's splits in due order.
- **Splits:** an order's payment schedule; ERPNext doesn't carry a quotation's payment terms
  to its order (`field_no_map`), so an order with only ERPNext's filler row takes its
  quotation's schedule as shares of the order's total. A lead without an order has its latest
  quotation's (sent, with the client or accepted). A schedule ERPNext made up itself (one 100% row with no
  payment term) says nothing about the splits, so the proposal's Payment Terms milestones
  ("Advance on signing | 30%") are used, as the share of the total with no date; without them
  the whole amount is one split (dated on an order, undated on a quotation).
- **Status:** Received (nothing left), Overdue (an order's split past its date), Due (on or
  before a week from today; a quotation's split past its date is expected now, never overdue:
  the client hasn't committed yet) or Upcoming (later, or no date).
"""

import frappe
from frappe.query_builder.functions import IfNull
from frappe.utils import add_days, add_months, flt, get_first_day, getdate, today

from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline.dashboard import company_currency
from tbo_smart.lead_pipeline.overview import LeadOverview
from tbo_smart.lead_pipeline.proposals import DOCTYPE as PROPOSAL
from tbo_smart.lead_pipeline.proposals import payment_milestones
from tbo_smart.lead_pipeline.quotations import ACCEPTED, WITH_CLIENT
from tbo_smart.sales import DEAL, LEAD_LINK

RECEIVED, DUE, OVERDUE, UPCOMING = "Received", "Due", "Overdue", "Upcoming"
SALES_ORDER, QUOTATION = "Sales Order", "Quotation"
EXPECTED_QUOTES = (*WITH_CLIENT, ACCEPTED)
DUE_SOON_DAYS = 7
MONTHS_AHEAD = 6
MAX_SPLITS = 300
TOLERANCE = 0.005


class ProposalMoney(LeadOverview):
	"""The money figures for `owners` (None: everyone) from `from_date` to `to_date`."""

	def build_money(self) -> dict:
		self.today = getdate(today())
		orders = self.lead_orders()
		splits = self.splits(orders)
		sent = self.sent_proposals()
		won = [row.name for row in self.closed_rows("Won")]
		return {
			"from_date": self.from_date,
			"to_date": self.to_date,
			"currency": company_currency(),
			"totals": {
				"proposals_sent": sent["count"],
				"proposals_value": sent["value"],
				"unpriced_sent": sent["unpriced"],
				"won": len(won),
				"won_value": self.lead_value(won),
				"advances": self.advances(list(orders)),
				"to_collect": flt(sum(row["outstanding"] for row in splits), 2),
				"overdue": flt(sum(row["outstanding"] for row in splits if row["status"] == OVERDUE), 2),
			},
			"by_month": self.by_month(splits),
			"splits": splits[:MAX_SPLITS],
			"splits_total": len(splits),
		}

	# ---------- proposals ----------

	def sent_proposals(self) -> dict:
		"""Leads a proposal went to in the period (`sent_leads`), each once, and the value of
		the quotations sent in it, each lead at the last one sent."""
		Quotation = self.Quotation
		rows = (
			self.quotation_query()
			.select(Quotation.tbo_lead, Quotation.base_grand_total, Quotation.tbo_sent_on)
			.where((Quotation.docstatus != 2) & self.in_range(Quotation.tbo_sent_on) & self.lead_criterion())
			.orderby(Quotation.tbo_sent_on)
		).run(as_dict=True)
		last = {row.tbo_lead: flt(row.base_grand_total) for row in rows}
		sent = self.sent_leads()
		return {
			"count": len(sent),
			"value": flt(sum(last.values()), 2),
			"unpriced": len(sent - set(last)),
		}

	# ---------- orders and receipts ----------

	def lead_orders(self) -> dict:
		"""{sales order: its row and lead} for the leads in scope: through the deal made from the
		lead, or through the lead's quotation on its items."""
		Order = frappe.qb.DocType(SALES_ORDER)
		Item = frappe.qb.DocType("Sales Order Item")
		Deal = frappe.qb.DocType(DEAL)
		Quotation, Lead = self.Quotation, self.Lead
		fields = (
			Order.name,
			Order.transaction_date,
			Order.base_grand_total,
			Order.advance_paid,
			Order.conversion_rate,
			Lead.name.as_("lead"),
		)
		by_deal = (
			frappe.qb.from_(Order)
			.join(Deal)
			.on(Deal.name == Order.opportunity)
			.join(Lead)
			.on(Lead.name == Deal[LEAD_LINK])
			.select(*fields)
			.where((Order.docstatus == 1) & self.lead_criterion())
		).run(as_dict=True)
		by_quote = (
			frappe.qb.from_(Order)
			.join(Item)
			.on(Item.parent == Order.name)
			.join(Quotation)
			.on(Quotation.name == Item.prevdoc_docname)
			.join(Lead)
			.on(Lead.name == Quotation[LEAD_LINK])
			.select(*fields)
			.distinct()
			.where((Order.docstatus == 1) & self.lead_criterion())
		).run(as_dict=True)
		return {row.name: row for row in (*by_quote, *by_deal)}

	def advances(self, orders: list[str]) -> float:
		"""Receipts allocated to these orders, posted in the period, in the company currency."""
		if not orders:
			return 0.0
		Entry = frappe.qb.DocType("Payment Entry")
		Reference = frappe.qb.DocType("Payment Entry Reference")
		rows = (
			frappe.qb.from_(Reference)
			.join(Entry)
			.on(Entry.name == Reference.parent)
			.select(Reference.allocated_amount, Entry.source_exchange_rate)
			.where(
				(Reference.parenttype == "Payment Entry")
				& (Reference.reference_doctype == SALES_ORDER)
				& Reference.reference_name.isin(orders)
				& (Entry.docstatus == 1)
				& (Entry.payment_type == "Receive")
				& self.in_range(Entry.posting_date)
			)
		).run()
		return flt(sum(flt(amount) * (flt(rate) or 1) for amount, rate in rows), 2)

	def received(self, orders: dict) -> dict:
		"""{order: received so far, company currency}: the larger of its advances and what its
		invoices have been paid (an invoice's paid share split over its orders by their lines)."""
		received = {
			name: flt(row.advance_paid) * (flt(row.conversion_rate) or 1) for name, row in orders.items()
		}
		if not orders:
			return received
		Invoice = frappe.qb.DocType("Sales Invoice")
		Item = frappe.qb.DocType("Sales Invoice Item")
		billed = dict.fromkeys(orders, 0.0)
		for row in (
			frappe.qb.from_(Item)
			.join(Invoice)
			.on(Invoice.name == Item.parent)
			.select(
				Item.sales_order,
				Item.base_net_amount,
				Invoice.base_net_total,
				Invoice.grand_total,
				Invoice.rounded_total,
				Invoice.outstanding_amount,
				Invoice.conversion_rate,
			)
			.where(Item.sales_order.isin(list(orders)) & (Invoice.docstatus == 1) & (Invoice.is_return == 0))
		).run(as_dict=True):
			total = flt(row.rounded_total) or flt(row.grand_total)
			if not (total and flt(row.base_net_total)):
				continue
			paid = (total - flt(row.outstanding_amount)) * (flt(row.conversion_rate) or 1)
			billed[row.sales_order] += paid * flt(row.base_net_amount) / flt(row.base_net_total)
		return {name: max(received[name], billed[name]) for name in orders}

	# ---------- the splits ----------

	def splits(self, orders: dict) -> list[dict]:
		"""Every payment split of the scope's orders still being paid, and of the latest
		quotation of each lead without an order, by due date (undated last)."""
		quotes = self.expected_quotations(exclude={row.lead for row in orders.values()})
		sources = self.order_quotations(list(orders))
		schedules = self.schedules(list(orders) + list(quotes) + list(sources.values()))
		milestones = self.milestones(list(quotes) + list(sources.values()))
		received = self.received(orders)
		rows = []
		for name, order in orders.items():
			splits = self.document_splits(
				SALES_ORDER,
				name,
				order.base_grand_total,
				schedules.get(name, []),
				milestones.get(sources.get(name), []),
				schedules.get(sources.get(name), []),
			)
			settle(splits, received.get(name, 0.0))
			if any(row["outstanding"] > TOLERANCE for row in splits):
				rows += [{**row, "lead": order.lead} for row in splits]
		for name, quote in quotes.items():
			splits = self.document_splits(
				QUOTATION, name, quote.base_grand_total, schedules.get(name, []), milestones.get(name, [])
			)
			settle(splits, 0.0)
			rows += [{**row, "lead": quote.lead, "quote_status": quote.tbo_quote_status} for row in splits]
		for row in rows:
			row["status"] = self.split_status(row)
		self.add_leads(rows)
		return sorted(
			rows, key=lambda row: (row["due_date"] or getdate("2999-12-31"), row["lead"], row["idx"])
		)

	def expected_quotations(self, exclude: set) -> dict:
		"""{quotation: row} of each lead's newest latest-revision quotation that is with the client
		or accepted, for the leads without an order."""
		Quotation = self.Quotation
		rows = (
			self.quotation_query()
			.select(
				Quotation.name,
				Quotation.tbo_lead.as_("lead"),
				Quotation.base_grand_total,
				Quotation.tbo_quote_status,
			)
			.where(
				(Quotation.docstatus == 1)
				& (Quotation.tbo_is_latest == 1)
				& Quotation.tbo_quote_status.isin(EXPECTED_QUOTES)
				& self.lead_criterion()
			)
			.orderby(Quotation.creation, order=frappe.qb.desc)
		).run(as_dict=True)
		found, seen = {}, set(exclude)
		for row in rows:
			if row.lead not in seen:
				seen.add(row.lead)
				found[row.name] = row
		return found

	def schedules(self, parents: list[str]) -> dict:
		"""{order or quotation: its payment schedule rows, in order}."""
		if not parents:
			return {}
		Term = frappe.qb.DocType("Payment Schedule")
		found = {}
		for row in (
			frappe.qb.from_(Term)
			.select(
				Term.parent,
				Term.idx,
				Term.payment_term,
				Term.description,
				Term.due_date,
				Term.invoice_portion,
				Term.base_payment_amount,
			)
			.where(Term.parenttype.isin((SALES_ORDER, QUOTATION)) & Term.parent.isin(parents))
			.orderby(Term.idx)
		).run(as_dict=True):
			found.setdefault(row.parent, []).append(row)
		return found

	def order_quotations(self, orders: list[str]) -> dict:
		"""{order: the quotation its first line comes from}."""
		if not orders:
			return {}
		Item = frappe.qb.DocType("Sales Order Item")
		found = {}
		for order, quotation in (
			frappe.qb.from_(Item)
			.select(Item.parent, Item.prevdoc_docname)
			.where(Item.parent.isin(orders) & (IfNull(Item.prevdoc_docname, "") != ""))
			.orderby(Item.idx)
		).run():
			found.setdefault(order, quotation)
		return found

	def milestones(self, quotations: list[str]) -> dict:
		"""{quotation: the (milestone, share %) rows of the Payment Terms of the newest proposal or
		quotation document it prices}."""
		quotations = [name for name in set(quotations) if name]
		if not quotations:
			return {}
		Proposal = frappe.qb.DocType(PROPOSAL)
		Section = frappe.qb.DocType("TBO Proposal Section")
		found = {}
		for quotation, body in (
			frappe.qb.from_(Proposal)
			.join(Section)
			.on((Section.parent == Proposal.name) & (Section.parenttype == PROPOSAL))
			.select(Proposal.quotation, Section.body)
			.where(
				Proposal.quotation.isin(quotations)
				& Proposal.document_type.isin((kinds.PROPOSAL, kinds.QUOTATION))
				& (Section.section_type == "Payment Terms")
				& (Section.enabled == 1)
			)
			.orderby(Proposal.creation, order=frappe.qb.desc)
		).run():
			rows = payment_milestones(body)
			if rows and quotation not in found:
				found[quotation] = rows
		return found

	@staticmethod
	def document_splits(
		doctype: str, name: str, total, schedule: list, milestones: list, quoted: list = ()
	) -> list[dict]:
		"""The document's splits: its own schedule when a person set one (a payment term, or more
		than one row); for an order, else its quotation's (ERPNext doesn't carry the quotation's
		payment terms to the order), as shares of the order's total; else the proposal's
		milestones, undated; else the whole amount."""

		def split(idx, label, share, amount, due, source):
			return {
				"document_type": doctype,
				"document": name,
				"idx": idx,
				"label": label,
				"percent": flt(share, 2),
				"amount": flt(amount, 2),
				"due_date": getdate(due) if due else None,
				"source": source,
			}

		if not set_by_person(schedule) and set_by_person(quoted):
			return [
				split(
					row.idx,
					row.payment_term or (row.description or "").strip() or None,
					row.invoice_portion,
					flt(total) * flt(row.invoice_portion) / 100,
					row.due_date,
					"quotation",
				)
				for row in quoted
			]
		if set_by_person(schedule):
			return [
				split(
					row.idx,
					row.payment_term or (row.description or "").strip() or None,
					row.invoice_portion,
					row.base_payment_amount,
					row.due_date,
					"schedule",
				)
				for row in schedule
			]
		if milestones:
			return [
				split(idx, label, share, flt(total) * share / 100, None, "proposal")
				for idx, (label, share) in enumerate(milestones, 1)
			]
		due = schedule[0].due_date if (schedule and doctype == SALES_ORDER) else None
		return [split(1, None, 100, total, due, "whole")]

	def split_status(self, row: dict) -> str:
		if row["outstanding"] <= TOLERANCE:
			return RECEIVED
		due = row["due_date"]
		if not due:
			return UPCOMING
		if due < self.today and row["document_type"] == SALES_ORDER:
			return OVERDUE
		return DUE if due <= add_days(self.today, DUE_SOON_DAYS) else UPCOMING

	def add_leads(self, rows: list[dict]) -> None:
		leads = {row["lead"] for row in rows}
		if not leads:
			return
		info = {
			row.name: row
			for row in frappe.get_all(
				"Lead",
				filters={"name": ("in", list(leads))},
				fields=["name", "lead_name", "company_name", "lead_owner"],
				limit_page_length=0,
			)
		}
		names = get_full_names({row.lead_owner for row in info.values() if row.lead_owner})
		for row in rows:
			lead = info.get(row["lead"]) or frappe._dict()
			row.update(
				{
					"lead_name": lead.lead_name,
					"company_name": lead.company_name,
					"lead_owner": lead.lead_owner,
					"owner_name": names.get(lead.lead_owner),
				}
			)

	def by_month(self, splits: list[dict]) -> dict:
		"""What is still to come in: overdue, each of the next six months (this one first; a
		quotation's split past its date is expected this month), later, and undated."""
		first = getdate(get_first_day(self.today))
		months = [getdate(add_months(first, step)) for step in range(MONTHS_AHEAD)]
		amounts = dict.fromkeys(months, 0.0)
		result = {"overdue": 0.0, "later": 0.0, "undated": 0.0}
		for row in splits:
			left = row["outstanding"]
			if left <= TOLERANCE:
				continue
			if row["status"] == OVERDUE:
				result["overdue"] += left
			elif not row["due_date"]:
				result["undated"] += left
			else:
				month = max(getdate(get_first_day(row["due_date"])), first)
				if month in amounts:
					amounts[month] += left
				else:
					result["later"] += left
		return {
			**{key: flt(value, 2) for key, value in result.items()},
			"months": [{"month": month, "amount": flt(amounts[month], 2)} for month in months],
		}


def set_by_person(schedule) -> bool:
	"""A payment schedule someone set: a payment term on a row, or more than one row. ERPNext's
	own filler is one 100% row with no term."""
	return len(schedule) > 1 or any(row.payment_term for row in schedule)


def settle(splits: list[dict], received: float) -> None:
	"""Spread what was received over the splits in due order (undated last)."""
	left = flt(received)
	for row in sorted(splits, key=lambda row: (row["due_date"] or getdate("2999-12-31"), row["idx"])):
		paid = min(max(left, 0.0), row["amount"])
		row["received"] = flt(paid, 2)
		row["outstanding"] = flt(row["amount"] - paid, 2)
		left -= paid
