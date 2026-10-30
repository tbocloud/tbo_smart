"""The rules every first message passes before it is drafted or sent (docs/prospecting.md §10)."""

from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_time, now_datetime, today

from tbo_smart.lead_pipeline.statuses import LOST_STATUSES
from tbo_smart.outreach import APPROVED, EMAIL, LIVE, MESSAGE, SENT, WHATSAPP, daily_cap, setting
from tbo_smart.smart_leads.utils import normalise_phone

DEFAULT_START = "09:30:00"
DEFAULT_END = "18:00:00"
SUNDAY = 6


def never_contact(lead: str) -> str | None:
	"""Why `lead` is never written to, or None: it unsubscribed, or its stage is Lost-type (Closed
	- Lost, Not Interested, Wrong Number)."""
	row = frappe.db.get_value("Lead", lead, ["unsubscribed", "tbo_stage"], as_dict=True)
	if not row:
		return _("The lead does not exist.")
	if cint(row.unsubscribed):
		return _("The lead unsubscribed.")
	if row.tbo_stage in LOST_STATUSES:
		return _("The lead is {0}.").format(_(row.tbo_stage))
	return None


def blocked_reason(lead: str, channel: str) -> str | None:
	"""Why `lead` must not get a first message on `channel` right now, or None: never_contact, or
	no address for the channel."""
	if reason := never_contact(lead):
		return reason
	row = frappe.db.get_value("Lead", lead, ["email_id", "mobile_no", "whatsapp_no", "phone"], as_dict=True)
	if channel == EMAIL and not row.email_id:
		return _("The lead has no email address.")
	if channel == WHATSAPP and not whatsapp_number(row):
		return _("The lead has no usable mobile number.")
	return None


def whatsapp_number(row) -> str | None:
	return next(
		(
			normalise_phone(number)
			for number in (row.mobile_no, row.whatsapp_no, row.phone)
			if normalise_phone(number)
		),
		None,
	)


def live_message(lead: str, channel: str) -> str | None:
	"""The lead's first message on `channel` that is still alive (a draft, approved or sent), if any."""
	return frappe.db.get_value(MESSAGE, {"lead": lead, "channel": channel, "status": ("in", LIVE)}, "name")


def window_times() -> tuple:
	return (
		get_time(setting("outreach_window_start") or DEFAULT_START),
		get_time(setting("outreach_window_end") or DEFAULT_END),
	)


def in_window(moment: datetime | None = None) -> bool:
	"""Working hours: Monday to Saturday between the window's start and end, site time."""
	moment = get_datetime(moment) if moment else now_datetime()
	start, end = window_times()
	return moment.weekday() != SUNDAY and start <= moment.time() < end


def next_window(moment: datetime | None = None) -> datetime:
	"""When the send window next opens (now, if it is open)."""
	moment = get_datetime(moment) if moment else now_datetime()
	if in_window(moment):
		return moment
	start, end = window_times()
	day = moment.date()
	if moment.time() >= end or moment.weekday() == SUNDAY:
		day = day + timedelta(days=1)
	while day.weekday() == SUNDAY:
		day = day + timedelta(days=1)
	return datetime.combine(day, start)


def sent_today(user: str) -> int:
	"""Emails `user` approved that went out today."""
	return frappe.db.count(
		MESSAGE,
		{
			"channel": EMAIL,
			"status": SENT,
			"approved_by": user,
			"sent_on": (">=", today()),
		},
	)


def cap_left(user: str) -> int:
	return max(daily_cap() - sent_today(user), 0)


def queued_count(user: str) -> int:
	return frappe.db.count(MESSAGE, {"channel": EMAIL, "status": APPROVED, "approved_by": user})
