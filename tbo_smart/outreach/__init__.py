"""Outreach: an AI first message that a person approves before it goes, and replies sorted
automatically (docs/prospecting.md Phase 2).

Nothing here sends without a person's approval. `drafting` writes TBO Outreach Messages, `outbox`
lists them, `sending` approves and delivers them (email through the default outgoing Email
Account, WhatsApp by hand), `guards` holds the rules every send passes, and `replies` sorts an
incoming mail on a lead and acts on it.
"""

import frappe
from frappe.utils import cint

SETTINGS = "TBO Smart Settings"
MESSAGE = "TBO Outreach Message"

EMAIL = "Email"
WHATSAPP = "WhatsApp"
CHANNELS = (EMAIL, WHATSAPP)

DRAFT = "Draft"
APPROVED = "Approved"
SENT = "Sent"
FAILED = "Failed"
CANCELLED = "Cancelled"
STATUSES = (DRAFT, APPROVED, SENT, FAILED, CANCELLED)
# a first message already exists for the lead on that channel (a new draft would be a second one)
LIVE = (DRAFT, APPROVED, SENT)
# a person may still change or withdraw these
OPEN = (DRAFT, APPROVED, FAILED)

AI = "AI"
USER = "User"

# what a reply to a first message can be, and the flag for a person when the AI cannot sort it
REPLY_CLASSES = (
	"Interested",
	"Question",
	"Objection",
	"Not now",
	"Not interested",
	"Unsubscribe",
	"Out of office",
	"New reply",
)
NEW_REPLY = "New reply"

DEFAULT_DAILY_CAP = 30
DEFAULT_MAX_BATCH = 50
MAX_BATCH = 200
# the opt-out line added to every email when it is sent
OPT_OUT = 'If you would rather not hear from us, just reply "unsubscribe" and we will not write again.'


def setting(fieldname: str):
	return frappe.db.get_single_value(SETTINGS, fieldname)


def enabled() -> bool:
	return bool(cint(setting("enable_outreach")))


def require_enabled() -> None:
	if not enabled():
		frappe.throw(frappe._("Outreach is switched off in TBO Smart Settings."))


def number_setting(fieldname: str, default: int) -> int:
	value = setting(fieldname)
	return default if value in (None, "") else max(cint(value), 0)


def daily_cap() -> int:
	return number_setting("outreach_daily_cap", DEFAULT_DAILY_CAP)


def batch_limit() -> int:
	return min(number_setting("outreach_max_batch", DEFAULT_MAX_BATCH) or DEFAULT_MAX_BATCH, MAX_BATCH)
