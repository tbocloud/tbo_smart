# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

"""One thing in the finance workflow that needs a decision (docs/crm-finance-workflow.md §3.5).

Receivable requests (extend, pause, waive, dispute) and, from later phases, allocation,
transfer and bank discrepancies share this one doctype. The status moves only through
`apply_action` (called by tbo_smart.api.finance.exceptions): Open → Pending Approval →
Approved / Rejected, and Rejected → Open. Maker-checker: the person who raised or sent a
request for approval can never decide it.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate, now_datetime, today

from tbo_smart.finance import get_setting, publish_deal_update, reconciliation_enabled
from tbo_smart.finance.stage import update_stage

APPROVER_ROLES = ("TBO Finance Approver",)
TRANSITIONS = {
	"request_approval": ("Open", "Pending Approval"),
	"approve": ("Pending Approval", "Approved"),
	"reject": ("Pending Approval", "Rejected"),
	"reopen": ("Rejected", "Open"),
}
DECISIONS = ("approve", "reject")
# what the approver decides on; frozen once the exception leaves Open
LOCKED_FIELDS = (
	# the audit trail: who asked and who decided, set only by apply_action
	"source",
	"requested_by",
	"first_approved_by",
	"first_approved_on",
	"approved_by",
	"approved_on",
	"decision_remarks",
	"dedupe_key",
	# the request itself
	"category",
	"exception_type",
	"requested_action",
	"reference_doctype",
	"reference_name",
	"payment_schedule",
	"company",
	"customer",
	"crm_deal",
	"currency",
	"expected_amount",
	"actual_amount",
	"reason",
	"new_due_date",
	"pause_until",
	"resolution_reference_doctype",
	"resolution_reference_name",
)


class TBOFinanceException(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		actual_amount: DF.Currency
		approved_by: DF.Link | None
		approved_on: DF.Datetime | None
		category: DF.Literal["Receivable", "Allocation", "Transfer", "Bank Reconciliation"]
		company: DF.Link | None
		crm_deal: DF.Link | None
		currency: DF.Link | None
		customer: DF.Link | None
		decision_remarks: DF.SmallText | None
		dedupe_key: DF.Data | None
		difference: DF.Currency
		exception_type: DF.Literal[
			"Overpayment",
			"Payment to wrong entity",
			"Client dispute",
			"Extension request",
			"Pause request",
			"Write-off request",
			"Receipt via Journal Entry",
			"Receipt cancelled after transfer",
			"VAT ratio changed",
			"Short transfer",
			"Excess transfer",
			"Transfer pending",
			"Possible duplicate",
			"Unmatched bank line",
			"Amount mismatch",
		]
		expected_amount: DF.Currency
		first_approved_by: DF.Link | None
		first_approved_on: DF.Datetime | None
		new_due_date: DF.Date | None
		pause_until: DF.Date | None
		payment_schedule: DF.Data | None
		reason: DF.SmallText
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		requested_by: DF.Link | None
		requested_action: DF.Literal[
			"",
			"Extend due date",
			"Pause reminders",
			"Waive balance",
			"Accept difference",
			"Correct entry",
			"Mark duplicate",
			"Not an issue",
		]
		resolution_reference_doctype: DF.Link | None
		resolution_reference_name: DF.DynamicLink | None
		source: DF.Literal["User", "System"]
		status: DF.Literal["Open", "Pending Approval", "Approved", "Rejected"]
	# end: auto-generated types

	def validate(self):
		self.set_difference()
		self.validate_dates()
		self.validate_status_unchanged()

	def on_update(self):
		self.notify_deal()

	# ---------- validation ----------

	def set_difference(self):
		if self.expected_amount or self.actual_amount:
			self.difference = flt(self.actual_amount) - flt(self.expected_amount)

	def validate_dates(self):
		if self.requested_action == "Extend due date" and not self.new_due_date:
			frappe.throw(_("Choose the new due date."))
		if self.requested_action == "Pause reminders" and not self.pause_until:
			frappe.throw(_("Choose until when reminders pause."))
		for field in ("new_due_date", "pause_until"):
			if (
				self.has_value_changed(field)
				and self.get(field)
				and getdate(self.get(field)) < getdate(today())
			):
				frappe.throw(_("{0} cannot be in the past.").format(_(self.meta.get_label(field))))

	def validate_status_unchanged(self):
		"""Status changes only through apply_action, never by editing the form or the API."""
		if self.flags.from_action or self.is_new():
			if self.is_new() and not self.flags.from_action:
				self.status = "Open"
			return
		if self.has_value_changed("status"):
			frappe.throw(
				_("Use the Request approval, Approve, Reject or Reopen actions to change the status.")
			)
		self.validate_request_locked()

	def validate_request_locked(self):
		"""Maker-checker: once sent for approval the request is frozen, so what the approver
		reads is what gets approved. To change it, reject and reopen it."""
		before = self.get_doc_before_save()
		if not before or before.status == "Open":
			return
		changed = [
			field for field in LOCKED_FIELDS if str(before.get(field) or "") != str(self.get(field) or "")
		]
		if changed:
			frappe.throw(
				_(
					"{0} cannot change once the exception is sent for approval. Reject and reopen it to change the request."
				).format(", ".join(_(self.meta.get_label(field)) for field in changed))
			)

	# ---------- actions ----------

	def apply_action(self, action: str, remarks: str | None = None) -> None:
		"""Move the exception along; checks who may do it. The caller saves nothing else."""
		if action not in TRANSITIONS:
			frappe.throw(_("Unknown action {0}.").format(action))
		source, target = TRANSITIONS[action]
		if self.status != source:
			frappe.throw(_("This exception is {0}; it cannot be {1} now.").format(_(self.status), _(action)))
		if action in DECISIONS:
			self.check_decider()
			if action == "reject" and not (remarks or "").strip():
				frappe.throw(_("Give a reason for the rejection."))
			if action == "approve":
				self.check_ready_to_approve()
				if self.needs_second_approval() and not self.first_approved_by:
					self.record_first_approval(remarks)
					return
				if self.first_approved_by == frappe.session.user:
					frappe.throw(
						_("A second, different approver must approve this one."), frappe.PermissionError
					)
			self.approved_by = frappe.session.user
			self.approved_on = now_datetime()
			self.decision_remarks = remarks
		else:
			self.check_requester()
		if action == "request_approval":
			self.requested_by = frappe.session.user
		self.status = target
		self.flags.from_action = True
		self.save(ignore_permissions=True)
		self.add_comment("Info", self.describe_action(action, remarks))
		if action == "approve":
			self.apply_approval()

	def needs_second_approval(self) -> bool:
		"""Q5: above TBO Smart Settings → Second approval above, two different approvers."""
		if not reconciliation_enabled():
			return False
		threshold = flt(get_setting("second_approval_amount"))
		amount = max(abs(flt(self.difference)), abs(flt(self.expected_amount)), abs(flt(self.actual_amount)))
		return bool(threshold) and amount > threshold

	def record_first_approval(self, remarks: str | None):
		self.first_approved_by = frappe.session.user
		self.first_approved_on = now_datetime()
		self.flags.from_action = True
		self.save(ignore_permissions=True)
		self.add_comment(
			"Info", _("First approval; a second approver is needed. {0}").format(remarks or "").strip()
		)

	def check_requester(self):
		if not frappe.has_permission(self.doctype, "write", doc=self) and self.owner != frappe.session.user:
			frappe.throw(
				_("Only the requester or finance can send or reopen this exception."), frappe.PermissionError
			)

	def check_decider(self):
		if not is_approver():
			frappe.throw(_("Only a TBO Finance Approver can decide exceptions."), frappe.PermissionError)
		if frappe.session.user in self.get_requesters():
			frappe.throw(
				_("You raised or sent this exception for approval, so someone else must decide it."),
				frappe.PermissionError,
			)

	def get_requesters(self) -> set[str]:
		"""The person who raised it and the person who sent it for approval."""
		return {user for user in (self.owner, self.requested_by) if user}

	def check_ready_to_approve(self):
		if self.requested_action == "Waive balance":
			self.check_write_off_document()

	def check_write_off_document(self):
		"""A waiver needs a submitted credit note against the invoice, or a submitted journal
		entry for this customer, linked as the resolution."""
		doctype, name = self.resolution_reference_doctype, self.resolution_reference_name
		if not name or doctype not in ("Sales Invoice", "Journal Entry"):
			frappe.throw(_("Link the credit note or write-off journal entry before approving a waiver."))
		if doctype == "Sales Invoice":
			note = frappe.db.get_value(
				doctype, name, ["docstatus", "is_return", "return_against"], as_dict=True
			)
			if (
				not note
				or note.docstatus != 1
				or not note.is_return
				or note.return_against != self.reference_name
			):
				frappe.throw(
					_("{0} is not a submitted credit note against {1}.").format(name, self.reference_name)
				)
			return
		submitted = frappe.db.get_value(doctype, name, "docstatus") == 1
		for_customer = frappe.db.exists(
			"Journal Entry Account", {"parent": name, "party_type": "Customer", "party": self.customer}
		)
		if not (submitted and for_customer):
			frappe.throw(_("{0} is not a submitted journal entry for {1}.").format(name, self.customer))

	def apply_approval(self):
		"""An approved extension restarts the reminder steps from the new due date; the invoice
		itself is not changed (its payment terms stay as booked)."""
		if self.requested_action != "Extend due date" or self.reference_doctype != "Sales Invoice":
			return
		filters = {"parenttype": "Sales Invoice", "parent": self.reference_name}
		if self.payment_schedule:
			filters["name"] = self.payment_schedule
		for term in frappe.get_all("Payment Schedule", filters=filters, pluck="name"):
			frappe.db.set_value(
				"Payment Schedule",
				term,
				{"tbo_last_reminder": None, "tbo_last_reminder_on": None},
				update_modified=False,
			)

	def describe_action(self, action: str, remarks: str | None) -> str:
		text = {
			"request_approval": _("Sent for approval."),
			"approve": _("Approved."),
			"reject": _("Rejected."),
			"reopen": _("Reopened."),
		}[action]
		return f"{text} {remarks}".strip() if remarks else text

	def notify_deal(self):
		if self.crm_deal:
			publish_deal_update([self.crm_deal])
			if reconciliation_enabled():
				# Phase 4: an open exception puts the deal On Exception
				update_stage(self.crm_deal)


def is_approver(user: str | None = None) -> bool:
	roles = set(frappe.get_roles(user or frappe.session.user))
	return bool(roles & set(APPROVER_ROLES)) or "System Manager" in roles
