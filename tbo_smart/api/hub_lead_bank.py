"""The Lead Bank for the TBO Support hub (docs/hub-leads-api.md §2.21, docs/lead-bank.md).

Any enabled staff member may add a lead and see the leads they added: unlike the leads API, no
sales role is asked for, and nothing here reads the CRM beyond that. The hub's integration user
names the person in `acting_user`; managers (Sales Manager, System Manager, TBO Management) also
get the dashboard and may move referral credit. The referral settings are in
`hub_commission`, with the commission plans.
"""

import frappe

from tbo_smart.lead_bank import access, intake, reports
from tbo_smart.lead_bank import detect_region as detect
from tbo_smart.sales import erpnext_installed


def collector(acting_user: str | None) -> str:
	if not erpnext_installed():
		frappe.throw(frappe._("Leads need ERPNext on this site."))
	return access.collector_user(acting_user)


@frappe.whitelist()
def get_options(acting_user: str | None = None) -> dict:
	"""{regions: [{region, prefix, country, rate, has_department}], cap, currency, is_manager,
	max_photos}."""
	return reports.options(collector(acting_user))


@frappe.whitelist()
def detect_region(
	phones: list | str | None = None,
	latitude: float | str | None = None,
	longitude: float | str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""{region, by}: India, UAE or KSA from the phone numbers' prefix (+91 / +971 / +966), else from
	the GPS fix; region None while neither says (by: "phone" / "gps")."""
	collector(acting_user)
	phones = (
		frappe.parse_json(phones) if isinstance(phones, str) and phones.strip().startswith("[") else phones
	)
	return detect(phones or [], latitude, longitude)


@frappe.whitelist()
def check_duplicate(
	email: str | None = None,
	phone: str | None = None,
	company: str | None = None,
	acting_user: str | None = None,
) -> dict:
	"""{duplicate, mine, message}: whether we already have this business, in plain words. Nothing
	of the lead it matches is returned."""
	return intake.check_duplicate(collector(acting_user), email, phone, company)


@frappe.whitelist(methods=["POST"])
def submit_lead(data: dict | str, photos: list | str, acting_user: str | None = None) -> dict:
	"""Add a lead. `data`: {company_name, contact_name, mobile_no, phone, email_id, website,
	business_type, address, region (blank: detected), state, latitude, longitude, note}, at least a
	name and a phone or email. `photos`: [{filename, content (base64)}], 1 to 5. A business we
	already have is not made again (the first collector keeps the credit; theirs is a note on the
	lead). Returns {created, duplicate, mine, lead, region, department, in_pool, message}."""
	return intake.submit(collector(acting_user), data, photos)


@frappe.whitelist()
def get_my_leads(start: int | str = 0, page_length: int | str = 50, acting_user: str | None = None) -> dict:
	"""The leads the acting user collected, and only those: {rows: [{id, title, collected_on, region,
	status (New / Being called / Meeting / Won / Not interested), earned, pending}], total, summary:
	{leads, won, earned, pending}, currency}."""
	return reports.my_leads(collector(acting_user), start, page_length)


@frappe.whitelist()
def get_dashboard(month: str | None = None, acting_user: str | None = None) -> dict:
	"""Managers only: {month, currency, collected, won, conversion, by_region: [{region, department,
	collected, won}], collectors: [{user, full_name, collected, won}], referral: {earned, paid, due,
	by_collector}, pool: {count, leads}}. `month` is any date in the month (default this month)."""
	return reports.dashboard(month or frappe.utils.today(), collector(acting_user))


@frappe.whitelist(methods=["POST"])
def reassign_credit(lead: str, user: str, reason: str, acting_user: str | None = None) -> dict:
	"""Managers only: move a lead's referral credit to `user`, with a reason (kept as a note on the
	lead). Earlier statements are never rewritten."""
	return reports.reassign_credit(lead, user, reason, collector(acting_user))
