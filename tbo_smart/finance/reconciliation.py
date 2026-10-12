"""Bank reconciliation checks and verification (docs/crm-finance-workflow.md §5, Phase 4).

ERPNext's Bank Statement Import and Bank Reconciliation Tool stay the accounting screens
(Q10). On top of them:

- duplicate detection: every imported line gets a dedupe key; a second line with the same key
  is flagged (never blocked: two genuine identical payments happen) and raises an exception;
- matching: whoever matches a line in the tool is recorded; a matched voucher total that
  differs from the bank amount beyond the tolerance raises an exception; matching a transfer
  leg links the bank line to the TBO Fund Transfer;
- verification: "nothing is reconciled until verified". A different person (Accounts
  Manager or TBO Finance Approver) verifies a matched line; a verified line is locked unless an
  approved "Correct entry" exception covers it;
- ageing: lines still unmatched after N days raise an exception (nightly).
"""

import hashlib
import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, now_datetime, today

from tbo_smart.finance import get_setting, reconciliation_enabled
from tbo_smart.finance.exceptions import raise_exception

VERIFIER_ROLES = {"Accounts Manager", "TBO Finance Approver"}
LOCKED_FIELDS = ("payment_entries",)


def normalise(text: str | None) -> str:
	return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def dedupe_key(line) -> str:
	"""sha1 of bank account, date, direction, amount and normalised reference or description."""
	direction = "D" if flt(line.deposit) else "W"
	amount = f"{flt(line.deposit) or flt(line.withdrawal):.2f}"
	reference = normalise(line.reference_number) or normalise(line.description)
	raw = "|".join([line.bank_account or "", str(line.date), direction, amount, reference])
	return hashlib.sha1(raw.encode()).hexdigest()


def tolerance() -> float:
	return flt(get_setting("amount_tolerance")) or 1.0


class BankLine:
	"""doc_events entry point for Bank Transaction."""

	def __init__(self, doc):
		self.doc = doc

	# ---------- import ----------

	def before_insert(self):
		if not reconciliation_enabled():
			return
		self.doc.tbo_dedupe_key = dedupe_key(self.doc)
		self.doc.tbo_verification = "Unverified"
		self.doc.tbo_possible_duplicate_of = frappe.db.get_value(
			"Bank Transaction",
			{"tbo_dedupe_key": self.doc.tbo_dedupe_key, "docstatus": ("<", 2)},
			order_by="creation asc",
		)

	def after_insert(self):
		if not reconciliation_enabled() or not self.doc.tbo_possible_duplicate_of:
			return
		raise_exception(
			category="Bank Reconciliation",
			exception_type="Possible duplicate",
			requested_action="Mark duplicate",
			reference_doctype="Bank Transaction",
			reference_name=self.doc.name,
			company=self.doc.company,
			currency=self.doc.currency,
			actual_amount=flt(self.doc.deposit) or flt(self.doc.withdrawal),
			reason=_(
				"Same bank account, date, amount and reference as {0}. Mark it a duplicate, or Not an issue if both are genuine."
			).format(self.doc.tbo_possible_duplicate_of),
			dedupe_key=f"duplicate:{self.doc.name}",
		)

	# ---------- matching ----------

	def before_update_after_submit(self):
		if not reconciliation_enabled():
			return
		before = self.doc.get_doc_before_save()
		if not before or self.vouchers(before) == self.vouchers(self.doc):
			return
		if before.tbo_verification == "Verified":
			self.use_approved_correction()
		self.doc.tbo_matched_by = frappe.session.user
		self.doc.tbo_verification = "Unverified"
		self.doc.tbo_verified_by = None
		self.doc.tbo_verified_on = None

	def use_approved_correction(self):
		"""A verified line changes only against an approved Correct entry, and each approval
		allows one change: it is marked used here."""
		correction = get_unused_correction(self.doc.name)
		if not correction:
			frappe.throw(
				_(
					"Bank line {0} is verified. Raise a Correct entry exception and get it approved before changing its matches."
				).format(self.doc.name)
			)
		# the exception's fields are frozen once decided; used_on is the one written after that
		frappe.db.set_value("TBO Finance Exception", correction, "used_on", now_datetime())

	def on_update_after_submit(self):
		if not reconciliation_enabled():
			return
		self.check_amount()
		self.link_transfers()

	@staticmethod
	def vouchers(doc) -> list:
		return sorted(
			(row.payment_document, row.payment_entry, flt(row.allocated_amount))
			for row in doc.get("payment_entries") or []
		)

	def check_amount(self):
		"""The matched Payment Entries' amounts against the bank amount."""
		entries = [
			row.payment_entry for row in self.doc.payment_entries if row.payment_document == "Payment Entry"
		]
		if not entries:
			return
		total = sum(
			flt(value)
			for value in frappe.get_all(
				"Payment Entry",
				filters={"name": ("in", entries)},
				pluck="received_amount" if flt(self.doc.deposit) else "paid_amount",
			)
		)
		bank = flt(self.doc.deposit) or flt(self.doc.withdrawal)
		if abs(total - bank) <= tolerance():
			return
		raise_exception(
			category="Bank Reconciliation",
			exception_type="Amount mismatch",
			requested_action="Accept difference",
			reference_doctype="Bank Transaction",
			reference_name=self.doc.name,
			company=self.doc.company,
			currency=self.doc.currency,
			expected_amount=total,
			actual_amount=bank,
			reason=_("Bank line {0} is {1}; the matched payments total {2}.").format(
				self.doc.name, bank, total
			),
			dedupe_key=f"mismatch:{self.doc.name}",
		)

	def link_transfers(self):
		"""Matching a transfer leg fills the transfer's bank line (§5.2)."""
		for row in self.doc.payment_entries:
			if row.payment_document != "Payment Entry":
				continue
			for field, line_field in (
				("ksa_payment_entry", "ksa_bank_transaction"),
				("india_payment_entry", "india_bank_transaction"),
			):
				transfer = frappe.db.get_value(
					"TBO Fund Transfer", {field: row.payment_entry, "docstatus": 1}
				)
				if transfer:
					frappe.db.set_value("TBO Fund Transfer", transfer, line_field, self.doc.name)
					refresh_transfer(transfer)


def get_unused_correction(line: str) -> str | None:
	return frappe.db.get_value(
		"TBO Finance Exception",
		{
			"reference_doctype": "Bank Transaction",
			"reference_name": line,
			"requested_action": "Correct entry",
			"status": "Approved",
			"used_on": ("is", "not set"),
		},
		order_by="approved_on asc",
	)


def refresh_transfer(name: str) -> None:
	transfer = frappe.get_doc("TBO Fund Transfer", name)
	transfer.set_status(save=True)


def verify_line(name: str) -> dict:
	"""Verify a matched bank line: a different person from whoever matched it, holding Accounts
	Manager or TBO Finance Approver."""
	if not reconciliation_enabled():
		frappe.throw(_("Reconciliation is not switched on."), frappe.ValidationError)
	if not set(frappe.get_roles()) & VERIFIER_ROLES:
		frappe.throw(
			_("Only an Accounts Manager or TBO Finance Approver can verify bank lines."),
			frappe.PermissionError,
		)
	line = frappe.get_doc("Bank Transaction", name)
	if line.docstatus != 1 or line.status != "Reconciled":
		frappe.throw(_("Only a fully matched (Reconciled) bank line can be verified."))
	if line.tbo_verification == "Verified":
		return {"name": name, "verification": "Verified"}
	if line.tbo_matched_by == frappe.session.user or (
		not line.tbo_matched_by and line.modified_by == frappe.session.user
	):
		frappe.throw(_("You matched this line, so someone else must verify it."), frappe.PermissionError)
	line.db_set(
		{
			"tbo_verification": "Verified",
			"tbo_verified_by": frappe.session.user,
			"tbo_verified_on": now_datetime(),
		}
	)
	line.add_comment("Info", _("Verified."))
	for field in ("ksa_bank_transaction", "india_bank_transaction"):
		for transfer in frappe.get_all(
			"TBO Fund Transfer", filters={field: name, "docstatus": 1}, pluck="name"
		):
			refresh_transfer(transfer)
	return {"name": name, "verification": "Verified"}


def flag_unmatched_lines() -> None:
	"""Nightly: lines still not reconciled after `unmatched_alert_days` raise one exception each."""
	if not reconciliation_enabled():
		return
	days = cint(get_setting("unmatched_alert_days")) or 3
	for line in frappe.get_all(
		"Bank Transaction",
		filters={
			"docstatus": 1,
			"status": ("in", ["Pending", "Unreconciled"]),
			"date": ("<", add_days(today(), -days)),
		},
		fields=["name", "company", "currency", "deposit", "withdrawal", "date", "unallocated_amount"],
		limit_page_length=1000,
	):
		raise_exception(
			category="Bank Reconciliation",
			exception_type="Unmatched bank line",
			reference_doctype="Bank Transaction",
			reference_name=line.name,
			company=line.company,
			currency=line.currency,
			actual_amount=flt(line.unallocated_amount),
			reason=_("Bank line {0} of {1} is still not matched after {2} days.").format(
				line.name, line.date, days
			),
			dedupe_key=f"unmatched:{line.name}",
		)


def line_before_insert(doc, method=None):
	BankLine(doc).before_insert()


def line_after_insert(doc, method=None):
	BankLine(doc).after_insert()


def line_before_update_after_submit(doc, method=None):
	BankLine(doc).before_update_after_submit()


def line_on_update_after_submit(doc, method=None):
	BankLine(doc).on_update_after_submit()
