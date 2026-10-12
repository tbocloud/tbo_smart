# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

"""One discount or commission adjustment on a Quotation and its approval trail.

Rows are written only by tbo_smart.quotation_control and tbo_smart.api.quotation_adjustment,
never through the form. The key fields and every step already logged are immutable.
"""

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime

from tbo_smart.tbo_smart.doctype.tbo_quotation_approval_settings.tbo_quotation_approval_settings import (
	format_limit,
	get_settings,
)

OPEN_STATUSES = ("Pending", "Escalated")
GRANTED_STATUSES = ("Approved", "Overridden")
ACTIONS = ("approve", "reject", "forward", "override")
IMMUTABLE_FIELDS = ("quotation", "kind", "requested_by", "requested_percent", "previous_percent", "reason")
STEP_FIELDS = ("action", "by_user", "level", "percent", "at", "remarks")


class TBOQuotationAdjustment(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from tbo_smart.tbo_smart.doctype.tbo_quotation_adjustment_step.tbo_quotation_adjustment_step import (
			TBOQuotationAdjustmentStep,
		)

		approved_by: DF.Link | None
		approved_percent: DF.Percent
		current_level: DF.Data | None
		current_rank: DF.Int
		customer: DF.Data | None
		deal: DF.Link | None
		kind: DF.Literal["Discount", "Commission"]
		previous_percent: DF.Percent
		quotation: DF.Link
		quotation_value: DF.Currency
		reason: DF.SmallText | None
		remarks: DF.SmallText | None
		requested_by: DF.Link | None
		requested_percent: DF.Percent
		status: DF.Literal["Pending", "Approved", "Rejected", "Escalated", "Overridden"]
		steps: DF.Table[TBOQuotationAdjustmentStep]
		voided: DF.Check
		within_limit: DF.Check
	# end: auto-generated types

	def validate(self):
		self.validate_immutable_fields()
		self.validate_append_only_steps()

	def on_update(self):
		self.comment_on_quotation()

	# ---------- audit ----------

	def validate_immutable_fields(self):
		before = self.get_doc_before_save()
		if not before:
			return
		changed = [field for field in IMMUTABLE_FIELDS if before.get(field) != self.get(field)]
		if changed:
			frappe.throw(_("{0} cannot change after the request is made.").format(", ".join(changed)))

	def validate_append_only_steps(self):
		before = self.get_doc_before_save()
		if not before:
			return
		if len(self.steps) < len(before.steps):
			frappe.throw(_("Audit steps cannot be removed."))
		for old, new in zip(before.steps, self.steps, strict=False):
			if any(str(old.get(field) or "") != str(new.get(field) or "") for field in STEP_FIELDS):
				frappe.throw(_("Audit step {0} cannot be changed.").format(old.idx))

	def add_step(
		self, action: str, level: str | None, percent: float | None = None, remarks: str | None = None
	):
		self.append(
			"steps",
			{
				"action": action,
				"by_user": frappe.session.user,
				"level": level,
				"percent": percent,
				"at": now_datetime(),
				"remarks": remarks,
			},
		)

	def comment_on_quotation(self):
		"""Mirror the newest step on the Quotation's timeline, so the history shows there too."""
		if not self.steps:
			return
		step = self.steps[-1]
		if self.get_doc_before_save() and len(self.get_doc_before_save().steps) == len(self.steps):
			return
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "Quotation",
				"reference_name": self.quotation,
				"content": _("{0} {1} {2}% ({3}){4}").format(
					_(self.kind),
					_(step.action).lower(),
					flt(step.percent, 2),
					self.name,
					f": {step.remarks}" if step.remarks else "",
				),
			}
		).insert(ignore_permissions=True)

	# ---------- state ----------

	def is_open(self) -> bool:
		return self.status in OPEN_STATUSES

	def is_granted(self) -> bool:
		return self.status in GRANTED_STATUSES and not cint(self.voided)

	def void(self, remarks: str):
		self.voided = 1
		self.add_step("Voided", None, self.approved_percent, remarks)

	# ---------- decisions (the caller saves) ----------

	def check_can_decide(self, level, action: str):
		"""Refuse anyone but an approver at the waiting level (or above it with authority)."""
		if action not in ACTIONS:
			frappe.throw(_("Action must be one of {0}.").format(", ".join(ACTIONS)))
		if self.requested_by == frappe.session.user:
			frappe.throw(_("You cannot decide your own request."), frappe.PermissionError)
		if not level:
			frappe.throw(_("You have no quotation approval level."), frappe.PermissionError)
		if cint(frappe.db.get_value("Quotation", self.quotation, "docstatus")) != 0:
			frappe.throw(_("Quotation {0} is no longer a draft.").format(self.quotation))
		if action == "override":
			if not cint(level.unlimited):
				frappe.throw(_("Only a level with no limit can override."), frappe.PermissionError)
			return
		if not self.is_open():
			frappe.throw(_("{0} is already {1}.").format(self.name, _(self.status).lower()))
		if not self.is_waiting_for(level):
			frappe.throw(
				_("{0} is waiting for {1}, not for you.").format(self.name, self.current_level),
				frappe.PermissionError,
			)

	def is_waiting_for(self, level) -> bool:
		if level.level == self.current_level:
			return True
		return bool(
			(cint(level.approves_directly) or cint(level.unlimited)) and cint(level.rank) > self.current_rank
		)

	def approve(self, level, percent: float | None, remarks: str | None, quotation_value: float):
		settings = get_settings()
		percent = flt(percent or self.requested_percent, 2)
		if percent <= 0 or percent > flt(self.requested_percent, 2):
			frappe.throw(
				_("Approve between 0 and the requested {0}%.").format(flt(self.requested_percent, 2))
			)
		limit = settings.get_limit(level, self.kind)
		if not settings.is_within(limit, percent):
			frappe.throw(
				_("{0}% is above your limit ({1}). Approve less or forward it.").format(
					percent, format_limit(limit)
				),
				frappe.PermissionError,
			)
		self.grant("Approved", level, percent, remarks, quotation_value)

	def reject(self, level, remarks: str | None):
		settings = get_settings()
		if not (remarks or "").strip():
			frappe.throw(_("Give a reason for the rejection."))
		if not settings.is_within(settings.get_limit(level, self.kind), self.requested_percent):
			frappe.throw(
				_("The request is above your limit, so forward it to the next level."),
				frappe.PermissionError,
			)
		self.status = "Rejected"
		self.approved_by = frappe.session.user
		self.remarks = remarks
		self.add_step("Rejected", level.level, self.requested_percent, remarks)

	def forward(self, level, remarks: str | None):
		next_level = get_settings().get_next_level(max(self.current_rank, cint(level.rank)))
		if not next_level:
			frappe.throw(_("There is no higher level to forward to."))
		self.status = "Escalated"
		self.current_level = next_level.level
		self.current_rank = cint(next_level.rank)
		self.add_step("Forwarded", level.level, self.requested_percent, remarks)

	def override(self, level, percent: float | None, remarks: str | None, quotation_value: float):
		if not (remarks or "").strip():
			frappe.throw(_("An override needs a reason."))
		percent = flt(percent if percent is not None else self.requested_percent, 2)
		if not 0 <= percent <= 100:
			frappe.throw(_("Percent must be between 0 and 100."))
		self.voided = 0
		self.grant("Overridden", level, percent, remarks, quotation_value)

	def grant(self, status: str, level, percent: float, remarks: str | None, quotation_value: float):
		self.status = status
		self.approved_by = frappe.session.user
		self.approved_percent = percent
		self.remarks = remarks
		self.quotation_value = quotation_value
		self.add_step(status, level.level, percent, remarks)

	# ---------- notifications ----------

	def notify_level(self, level):
		"""Tell everyone holding the waiting level's role that a request needs them."""
		users = get_level_users(level.role, exclude=self.requested_by)
		self.notify(
			users,
			_("{0} approval needed: {1}% on {2}").format(
				_(self.kind), flt(self.requested_percent, 2), self.quotation
			),
		)

	def notify_requester(self):
		self.notify(
			[self.requested_by],
			_("{0} {1}: {2}").format(_(self.kind), _(self.status).lower(), self.quotation),
		)

	def notify(self, users: list[str], subject: str):
		users = [user for user in users if user and user != frappe.session.user]
		if not users:
			return
		enqueue_create_notification(
			users,
			{
				"type": "Alert",
				"document_type": self.doctype,
				"document_name": self.name,
				"subject": subject,
				"email_content": self.reason or "",
				"from_user": frappe.session.user,
			},
		)


def get_level_users(role: str, exclude: str | None = None) -> list[str]:
	"""Enabled system users who hold `role` directly (Administrator and `exclude` left out)."""
	HasRole = frappe.qb.DocType("Has Role")
	User = frappe.qb.DocType("User")
	rows = (
		frappe.qb.from_(HasRole)
		.join(User)
		.on(User.name == HasRole.parent)
		.select(User.name)
		.distinct()
		.where(
			(HasRole.parenttype == "User")
			& (HasRole.role == role)
			& (User.enabled == 1)
			& (User.user_type == "System User")
			& (User.name.notin(["Administrator", "Guest", exclude or ""]))
		)
		.run(pluck=True)
	)
	return list(rows)
