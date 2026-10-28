"""Mail logged to leads by BCC (docs/lead-departments.md §8).

TBO Smart Settings → Lead email log mailbox names an Email Account (e.g. log@teambackoffice.com)
that salespeople BCC on mails to clients. Every Communication that mailbox receives is attached
to the lead whose email is any outside address on it (sender, recipients, cc or bcc), so it shows
on the lead's timeline. Several matches: the most recently changed open lead. No match: the
Communication stays where it is, unlinked.
"""

import frappe
from frappe.utils import get_datetime

from tbo_smart.lead_pipeline.statuses import OPEN_STATUSES
from tbo_smart.sales import LEAD, SETTINGS, erpnext_installed


def log_to_lead(doc, method=None) -> None:
	"""doc_events: Communication after_insert."""
	account = frappe.db.get_single_value(SETTINGS, "lead_log_email_account")
	if not (account and erpnext_installed() and doc.get("email_account") == account):
		return
	if doc.get("reference_doctype") and doc.reference_doctype != LEAD:
		return
	lead = matching_lead(outside_addresses(doc, account))
	if lead:
		doc.db_set({"reference_doctype": LEAD, "reference_name": lead}, update_modified=False)


def addresses(doc) -> set[str]:
	found = set()
	for field in ("sender", "recipients", "cc", "bcc"):
		for part in (doc.get(field) or "").replace(";", ",").split(","):
			address = frappe.utils.parse_addr(part)[1] or ""
			if "@" in address:
				found.add(address.strip().lower())
	return found


def outside_addresses(doc, account: str) -> list[str]:
	"""The addresses on the mail that are not the team's: not a system user's, not the mailbox
	itself or another Email Account of ours."""
	found = addresses(doc)
	if not found:
		return []
	internal = set(
		frappe.get_all(
			"User", filters={"email": ("in", list(found)), "user_type": "System User"}, pluck="email"
		)
	)
	internal |= set(
		frappe.get_all("Email Account", filters={"email_id": ("in", list(found))}, pluck="email_id")
	)
	return sorted(found - {address.lower() for address in internal})


def matching_lead(outside: list[str]) -> str | None:
	"""The most recently changed open lead whose email is one of `outside`."""
	if not outside:
		return None
	Lead = frappe.qb.DocType(LEAD)
	rows = (
		frappe.qb.from_(Lead)
		.select(Lead.name, Lead.modified)
		.where(Lead.email_id.isin(outside) & Lead.tbo_stage.isin(list(OPEN_STATUSES)))
		.run()
	)
	return max(rows, key=lambda row: get_datetime(row[1]))[0] if rows else None
