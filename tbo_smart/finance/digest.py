"""Daily receivables digest for management and the Finance Manager (docs/crm-finance-workflow.md
§6.2, Phase 2).

`build_digest` returns structured data, so a Microsoft Teams post can reuse it later (Q11);
today it goes out as one email to TBO Smart Settings → Daily digest recipients and as an
in-app notification to the Finance Manager.
"""

import re

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
from frappe.utils import add_days, date_diff, flt, fmt_money, formatdate, getdate, today

from tbo_smart.finance import get_setting, receivables_enabled
from tbo_smart.finance.reminders import ReminderRun

FINAL_NOTICE_DAYS = 30


def build_digest(on_date=None) -> dict:
	"""Open receivables as of `on_date`: totals per currency, terms that fell overdue since the
	day before, terms 30+ days overdue, and receivable exceptions waiting or open."""
	day = getdate(on_date or today())
	rows = ReminderRun(day).load_rows()
	totals = {}
	newly_overdue, final_notice = [], []
	for row in rows:
		days = date_diff(day, row.due_date)
		bucket = totals.setdefault(
			row.currency, {"currency": row.currency, "outstanding": 0.0, "overdue": 0.0}
		)
		bucket["outstanding"] += flt(row.outstanding)
		if days > 0:
			bucket["overdue"] += flt(row.outstanding)
		item = {
			"invoice": row.invoice,
			"customer": row.customer_name,
			"due_date": row.due_date,
			"days_overdue": days,
			"outstanding": flt(row.outstanding),
			"currency": row.currency,
		}
		if 0 < days <= 1:
			newly_overdue.append(item)
		if days >= FINAL_NOTICE_DAYS:
			final_notice.append(item)
	exceptions = frappe.get_all(
		"TBO Finance Exception",
		filters={"status": ("in", ["Open", "Pending Approval"])},
		fields=["status", "count(name) as count"],
		group_by="status",
	)
	return {
		"date": day,
		"since": add_days(day, -1),
		"totals": list(totals.values()),
		"newly_overdue": newly_overdue,
		"final_notice": sorted(final_notice, key=lambda item: -item["days_overdue"]),
		"exceptions": {row.status: row["count"] for row in exceptions},
	}


def render_row(item: dict) -> str:
	cells = (
		item["invoice"],
		frappe.utils.escape_html(item["customer"] or ""),
		formatdate(item["due_date"]),
		item["days_overdue"],
		fmt_money(item["outstanding"], currency=item["currency"]),
	)
	return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def render_table(items: list[dict]) -> str:
	if not items:
		return f"<p>{_('None.')}</p>"
	headers = (_("Invoice"), _("Customer"), _("Due"), _("Days overdue"), _("Outstanding"))
	head = "<tr>" + "".join(f"<th>{header}</th>" for header in headers) + "</tr>"
	body = "".join(render_row(item) for item in items)
	return f"<table border='1' cellpadding='4' cellspacing='0'>{head}{body}</table>"


def render_total(row: dict) -> str:
	outstanding = fmt_money(row["outstanding"], currency=row["currency"])
	overdue = fmt_money(row["overdue"], currency=row["currency"])
	return f"<li>{outstanding} {_('outstanding')}, {overdue} {_('overdue')}</li>"


def render_digest(digest: dict) -> str:
	totals = "".join(render_total(row) for row in digest["totals"]) or f"<li>{_('Nothing outstanding.')}</li>"
	exceptions = digest["exceptions"]
	waiting = _("Exceptions waiting for approval: {0}. Open: {1}.").format(
		exceptions.get("Pending Approval", 0), exceptions.get("Open", 0)
	)
	return "".join(
		[
			f"<p>{_('Receivables on {0}').format(formatdate(digest['date']))}</p><ul>{totals}</ul>",
			f"<h4>{_('Fell overdue since yesterday')}</h4>{render_table(digest['newly_overdue'])}",
			f"<h4>{_('30 or more days overdue')}</h4>{render_table(digest['final_notice'])}",
			f"<p>{waiting}</p>",
		]
	)


def get_recipients() -> list[str]:
	value = get_setting("management_digest_recipients") or ""
	return [address for address in re.split(r"[\s,;]+", value) if address]


def send_digest() -> None:
	"""Scheduler entry point (daily, 08:00)."""
	if not receivables_enabled():
		return
	digest = build_digest()
	recipients = get_recipients()
	if recipients:
		frappe.sendmail(
			recipients=recipients,
			subject=_("TBO receivables digest, {0}").format(formatdate(digest["date"])),
			message=render_digest(digest),
		)
	manager = get_setting("escalation_finance_manager")
	if manager:
		enqueue_create_notification(
			manager,
			{
				"type": "Alert",
				"subject": _("Receivables digest: {0} terms 30+ days overdue, {1} exceptions waiting").format(
					len(digest["final_notice"]), digest["exceptions"].get("Pending Approval", 0)
				),
				"from_user": "Administrator",
			},
		)
