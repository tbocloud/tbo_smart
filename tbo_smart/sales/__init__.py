"""TBO's sales records on ERPNext's own Lead and Opportunity (docs/erpnext-lead-cutover.md).

One home for what every sales feature shares: the doctypes, who may see a lead or a deal
(`permissions`, `hierarchy`), the activity records on them (`activities`: notes are Comments,
calls and logged meetings are Events, follow-ups are ToDos), the stage history (`stage_log`)
and the deal (`opportunity`). The lead pipeline, smart leads, the finance workflow, the mobile
API and the hub API all build on it.

tbo_smart is also installed on sites without ERPNext's CRM records in use (tbo-smart.tbocloud.in),
and must migrate where ERPNext is missing altogether, so everything here checks
`erpnext_installed()` first.
"""

import frappe
from frappe.utils import get_url

LEAD = "Lead"
DEAL = "Opportunity"
ORGANIZATION = "Prospect"
SALES_DOCTYPES = (LEAD, DEAL)
SETTINGS = "TBO Smart Settings"
# the lead a deal or quotation was made from; ERPNext (v15.123+) moves both to the Customer it
# makes from the lead, so their party alone no longer says which lead they belong to
LEAD_LINK = "tbo_lead"


def erpnext_installed() -> bool:
	"""Lead and Opportunity are ERPNext's; without it there is nothing to set up or read."""
	return "erpnext" in frappe.get_installed_apps()


def remember_lead(doc, method=None) -> None:
	"""doc_events: Opportunity and Quotation validate. While made out to a lead, `tbo_lead` is it."""
	party_type = doc.get("opportunity_from") if doc.doctype == DEAL else doc.get("quotation_to")
	if party_type == LEAD and doc.get("party_name"):
		doc.set(LEAD_LINK, doc.party_name)


def owner_field(doctype: str) -> str:
	return "lead_owner" if doctype == LEAD else "opportunity_owner"


def record_label(doctype: str, name: str) -> str | None:
	"""Who a lead or deal is about: the lead's name, the deal's customer or contact."""
	if doctype == LEAD:
		return frappe.db.get_value(LEAD, name, "lead_name")
	if doctype == DEAL:
		row = frappe.db.get_value(DEAL, name, ["customer_name", "contact_display", "title"], as_dict=True)
		return row and (row.customer_name or row.contact_display or row.title)
	return None


def default_currency() -> str | None:
	"""The sales figures' currency: the default company's (Frappe CRM had its own setting)."""
	company = frappe.defaults.get_global_default("company")
	currency = frappe.get_cached_value("Company", company, "default_currency") if company else None
	return currency or frappe.db.get_default("currency")


def lead_url(lead: str | None = None, view: str | None = None) -> str:
	"""Where a link to a lead (or the leads page, or one of its views) opens: TBO Smart Settings →
	Lead page URL (the hub's lead page), else the desk."""
	base = (frappe.db.get_single_value(SETTINGS, "lead_page_url") or "").rstrip("/")
	if not base:
		return get_url(f"/app/lead/{lead}" if lead else "/app/lead")
	if lead:
		return f"{base}/{lead}"
	return f"{base}?view={view}" if view else base
