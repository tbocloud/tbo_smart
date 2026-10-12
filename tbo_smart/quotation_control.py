"""Discount and commission limits on ERPNext Quotations (TBO Quotation Approval Settings).

doc_events for Quotation call the hook functions at the bottom; each one only calls
QuotationControl methods. A quotation above the saving user's limit can be saved as a draft,
but it cannot be submitted (and so cannot become a sales order or invoice) until a granted
TBO Quotation Adjustment covers its percentage and its list value.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from tbo_smart.tbo_smart.doctype.tbo_quotation_adjustment.tbo_quotation_adjustment import (
	GRANTED_STATUSES,
	OPEN_STATUSES,
)
from tbo_smart.tbo_smart.doctype.tbo_quotation_approval_settings.tbo_quotation_approval_settings import (
	format_limit,
	get_settings,
)

COMMISSION_FIELD = "tbo_commission_rate"
VALUE_TOLERANCE = 0.005


class AdjustmentApprovalRequired(frappe.ValidationError):
	"""The quotation's discount or commission is above the user's limit and not approved."""


def get_list_value(doc) -> float:
	"""The quotation's value before any discount: each line at the higher of its price list
	rate, its rate with margin and its rate (alternative items left out, as in the totals)."""
	value = 0.0
	for item in doc.get("items") or []:
		if cint(item.get("is_alternative")):
			continue
		unit = max(flt(item.price_list_rate), flt(item.get("rate_with_margin")), flt(item.rate))
		value += unit * flt(item.qty)
	return flt(value, 2)


def get_discount_percent(doc) -> float:
	"""The share of the list value taken off the net total: item discounts, manual rate cuts and
	the additional discount together, so moving a discount between them changes nothing."""
	value = get_list_value(doc)
	if value <= 0:
		return 0.0
	return max(flt((value - flt(doc.net_total)) * 100 / value, 2), 0.0)


def get_percent(doc, kind: str) -> float:
	if kind == "Commission":
		return flt(doc.get(COMMISSION_FIELD), 2)
	return get_discount_percent(doc)


def get_granted(quotation: str, kind: str) -> list:
	"""Granted, not voided adjustments for a quotation and kind, newest first."""
	return frappe.get_all(
		"TBO Quotation Adjustment",
		filters={
			"quotation": quotation,
			"kind": kind,
			"status": ("in", GRANTED_STATUSES),
			"voided": 0,
		},
		fields=["name", "approved_percent", "quotation_value"],
		order_by="creation desc",
		limit_page_length=0,
	)


def get_outgrown_requests(quotation: str, kind: str, percent: float) -> list[str]:
	"""Open, not voided requests that ask for less than the quotation now gives."""
	return frappe.get_all(
		"TBO Quotation Adjustment",
		filters={
			"quotation": quotation,
			"kind": kind,
			"status": ("in", OPEN_STATUSES),
			"voided": 0,
			"requested_percent": ("<", flt(percent, 2)),
		},
		pluck="name",
	)


def covers(adjustment, percent: float, value: float) -> bool:
	return flt(percent, 2) <= flt(adjustment.approved_percent, 2) and (
		abs(flt(adjustment.quotation_value) - flt(value)) < VALUE_TOLERANCE
	)


def get_covering(quotation: str, kind: str, percent: float, value: float):
	return next((row for row in get_granted(quotation, kind) if covers(row, percent, value)), None)


def has_open_request(quotation: str, kind: str) -> bool:
	return bool(
		frappe.db.exists(
			"TBO Quotation Adjustment",
			{"quotation": quotation, "kind": kind, "status": ("in", OPEN_STATUSES), "voided": 0},
		)
	)


def has_voided(quotation: str, kind: str) -> bool:
	return bool(
		frappe.db.exists("TBO Quotation Adjustment", {"quotation": quotation, "kind": kind, "voided": 1})
	)


def get_unapproved(quotation: str) -> list[str]:
	"""Why a quotation may not yet become a Sales Order or Sales Invoice, read from its
	adjustment records: a request still open, or a percentage no granted adjustment covers on a
	draft or after an approval was voided. Empty when enforcement is off or nothing is
	missing. A quotation submitted while enforcement was off, with no adjustments, passes."""
	kinds = get_settings().controlled_kinds()
	if not kinds or not frappe.db.exists("Quotation", quotation):
		return []
	doc = frappe.get_doc("Quotation", quotation)
	value = get_list_value(doc)
	reasons = []
	for kind in kinds:
		percent = get_percent(doc, kind)
		if percent <= 0:
			continue
		if has_open_request(quotation, kind):
			reasons.append(_("{0} {1}% on {2} is waiting for approval.").format(_(kind), percent, quotation))
		elif not get_covering(quotation, kind, percent, value) and (
			doc.docstatus == 0 or has_voided(quotation, kind)
		):
			reasons.append(_("{0} {1}% on {2} has no valid approval.").format(_(kind), percent, quotation))
	return reasons


class QuotationControl:
	def __init__(self, doc):
		self.doc = doc
		self.settings = get_settings()
		self.kinds = self.settings.controlled_kinds()
		self.level = self.settings.get_user_level() if self.kinds else None

	def validate(self):
		if self.doc.docstatus == 1:
			self.block_unapproved_submit()
		else:
			self.warn_about_unapproved()

	def after_save(self):
		self.void_stale_decisions()
		self.record_applied()

	def uncovered(self) -> list[tuple[str, float, float | None]]:
		"""(kind, percent, my limit) for each controlled kind that neither my limit nor a granted
		adjustment covers."""
		value = get_list_value(self.doc)
		rows = []
		for kind in self.kinds:
			percent = get_percent(self.doc, kind)
			if percent <= 0:
				continue
			limit = self.settings.get_limit(self.level, kind)
			if self.settings.is_within(limit, percent):
				continue
			if not self.doc.is_new() and get_covering(self.doc.name, kind, percent, value):
				continue
			rows.append((kind, percent, limit))
		return rows

	def block_unapproved_submit(self):
		missing = self.uncovered()
		if not missing:
			return
		frappe.throw(
			"<br>".join(
				_("{0} {1}% is above your limit ({2}) and has no approval.").format(
					_(kind), percent, format_limit(limit)
				)
				for kind, percent, limit in missing
			)
			+ "<br>"
			+ _("Request approval, or bring it within the approved percentage."),
			AdjustmentApprovalRequired,
			title=_("Approval required"),
		)

	def warn_about_unapproved(self):
		for kind, percent, limit in self.uncovered():
			frappe.msgprint(
				_(
					"{0} {1}% is above your limit ({2}). You can save the draft, but it needs approval before it can be submitted."
				).format(_(kind), percent, format_limit(limit)),
				indicator="orange",
				alert=True,
			)

	def void_stale_decisions(self):
		"""A granted adjustment stops counting once the quotation goes above it or its list
		value changes; lowering the percentage keeps it. An open request is voided once the
		quotation goes above what it asks for, so a new request can be made."""
		value = get_list_value(self.doc)
		for kind in self.kinds:
			percent = get_percent(self.doc, kind)
			for row in get_granted(self.doc.name, kind):
				if covers(row, percent, value) or flt(row.approved_percent) <= 0:
					continue
				self.void(
					row.name,
					_("Quotation changed to {0}% on a list value of {1} after the decision."),
					percent,
					value,
				)
			for name in get_outgrown_requests(self.doc.name, kind, percent):
				self.void(
					name,
					_("Quotation changed to {0}% on a list value of {1}, above this request."),
					percent,
					value,
				)

	@staticmethod
	def void(name: str, message: str, percent: float, value: float):
		adjustment = frappe.get_doc("TBO Quotation Adjustment", name)
		adjustment.void(message.format(percent, value))
		adjustment.save(ignore_permissions=True)

	def record_applied(self):
		"""Log an adjustment the saving user made inside their own limit, unless an earlier
		adjustment already covers it."""
		value = get_list_value(self.doc)
		for kind in self.kinds:
			percent = get_percent(self.doc, kind)
			if percent <= 0 or not self.settings.is_within(
				self.settings.get_limit(self.level, kind), percent
			):
				continue
			if get_covering(self.doc.name, kind, percent, value):
				continue
			adjustment = new_adjustment(self.doc, kind, percent, value)
			adjustment.update(
				{
					"status": "Approved",
					"within_limit": 1,
					"approved_by": frappe.session.user,
					"approved_percent": percent,
					"current_level": self.level.level if self.level else None,
					"current_rank": cint(self.level.rank) if self.level else 0,
				}
			)
			adjustment.add_step("Applied", adjustment.current_level, percent, _("Within own limit"))
			adjustment.insert(ignore_permissions=True)


def new_adjustment(doc, kind: str, percent: float, value: float):
	"""An unsaved TBO Quotation Adjustment for `doc` with the quotation details filled in."""
	previous = frappe.get_all(
		"TBO Quotation Adjustment",
		filters={"quotation": doc.name, "kind": kind, "status": ("in", GRANTED_STATUSES)},
		pluck="approved_percent",
		order_by="creation desc",
		limit_page_length=1,
	)
	return frappe.get_doc(
		{
			"doctype": "TBO Quotation Adjustment",
			"quotation": doc.name,
			"customer": doc.get("customer_name") or doc.get("party_name"),
			"deal": get_deal(doc),
			"kind": kind,
			"previous_percent": flt(previous[0]) if previous else 0,
			"requested_percent": percent,
			"requested_by": frappe.session.user,
			"quotation_value": value,
		}
	)


def get_deal(doc) -> str | None:
	"""The CRM Deal behind the quotation (Frappe CRM's crm_deal field, or a deal as the party)."""
	deal = doc.get("crm_deal") or (doc.party_name if doc.get("quotation_to") == "CRM Deal" else None)
	return deal if deal and frappe.db.exists("CRM Deal", deal) else None


# ---------- doc_events (Quotation) ----------


def validate(doc, method=None):
	QuotationControl(doc).validate()


def on_update(doc, method=None):
	QuotationControl(doc).after_save()


def on_submit(doc, method=None):
	QuotationControl(doc).after_save()
