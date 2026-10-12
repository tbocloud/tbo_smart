# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

KINDS = ("Discount", "Commission")
LIMIT_FIELDS = {"Discount": "max_discount", "Commission": "max_commission"}


class TBOQuotationApprovalSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from tbo_smart.tbo_smart.doctype.tbo_quotation_approval_level.tbo_quotation_approval_level import (
			TBOQuotationApprovalLevel,
		)

		applies_to: DF.Literal["Discount", "Commission", "Both"]
		enabled: DF.Check
		levels: DF.Table[TBOQuotationApprovalLevel]
	# end: auto-generated types

	def validate(self):
		self.validate_unique("role")
		self.validate_unique("rank")
		self.validate_unique("level")
		self.validate_limits()

	def validate_unique(self, fieldname: str):
		seen = set()
		for row in self.levels:
			value = row.get(fieldname)
			if value in seen:
				frappe.throw(
					_("Row {0}: {1} {2} is used by another level.").format(
						row.idx, _(row.meta.get_label(fieldname)), frappe.bold(value)
					)
				)
			seen.add(value)

	def validate_limits(self):
		for row in self.levels:
			for fieldname in LIMIT_FIELDS.values():
				if not 0 <= flt(row.get(fieldname)) <= 100:
					frappe.throw(
						_("Row {0}: {1} must be between 0 and 100.").format(
							row.idx, _(row.meta.get_label(fieldname))
						)
					)

	def controlled_kinds(self) -> list[str]:
		"""The adjustment kinds that are limited, empty while enforcement is off."""
		if not cint(self.enabled):
			return []
		return list(KINDS) if self.applies_to == "Both" else [self.applies_to]

	def sorted_levels(self) -> list:
		return sorted(self.levels, key=lambda row: cint(row.rank))

	def get_user_level(self, user: str | None = None):
		"""The highest-ranked level whose role `user` holds, or None."""
		roles = set(frappe.get_roles(user or frappe.session.user))
		held = [row for row in self.sorted_levels() if row.role in roles]
		return held[-1] if held else None

	def get_level(self, level_name: str | None):
		return next((row for row in self.levels if row.level == level_name), None)

	def get_next_level(self, rank: int):
		"""The lowest level ranked above `rank`, or None at the top."""
		return next((row for row in self.sorted_levels() if cint(row.rank) > cint(rank)), None)

	@staticmethod
	def get_limit(level, kind: str) -> float | None:
		"""A level's limit for `kind`: None means no limit, 0 means not set (approval always needed)."""
		if not level:
			return 0.0
		if cint(level.unlimited):
			return None
		return flt(level.get(LIMIT_FIELDS[kind]), 2)

	@staticmethod
	def is_within(limit: float | None, percent: float) -> bool:
		return limit is None or flt(percent, 2) <= flt(limit, 2)


def format_limit(limit: float | None) -> str:
	"""A limit as people read it: "5%", "no limit", or "not set" for 0."""
	if limit is None:
		return _("no limit")
	if flt(limit) <= 0:
		return _("not set")
	return f"{flt(limit, 2):g}%"


def get_settings() -> TBOQuotationApprovalSettings:
	return frappe.get_cached_doc("TBO Quotation Approval Settings")
