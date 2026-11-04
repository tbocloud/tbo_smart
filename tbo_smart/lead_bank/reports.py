"""What the Lead Bank shows: a collector's own leads, and the managers' dashboard and credit
changes (docs/lead-bank.md §4, §6)."""

import frappe
from frappe import _
from frappe.utils import add_days, add_months, cint, flt, get_fullname, getdate

from tbo_smart.lead_bank import REGION_INFO, REGIONS, access, check_region, department_for, rate, referral
from tbo_smart.lead_bank.intake import add_comment
from tbo_smart.lead_pipeline import commission
from tbo_smart.lead_pipeline.dashboard import company_currency
from tbo_smart.lead_pipeline.statuses import (
	CLOSED_LOST,
	CLOSED_WON,
	FIRST_MEETING,
	MEETING_SCHEDULED,
	NEGOTIATION,
	NEW_LEAD,
	NOT_INTERESTED,
	PROPOSAL_SENT,
	SECOND_MEETING,
	THIRD_MEETING,
	WRONG_NUMBER,
)

NEW, CALLING, MEETING, WON, DECLINED = "New", "Being called", "Meeting", "Won", "Not interested"
# the five words a collector sees for TBO's sixteen stages; any other stage is "Being called"
SIMPLE_STATUS = {
	NEW_LEAD: NEW,
	MEETING_SCHEDULED: MEETING,
	FIRST_MEETING: MEETING,
	SECOND_MEETING: MEETING,
	THIRD_MEETING: MEETING,
	PROPOSAL_SENT: MEETING,
	NEGOTIATION: MEETING,
	CLOSED_WON: WON,
	CLOSED_LOST: DECLINED,
	NOT_INTERESTED: DECLINED,
	WRONG_NUMBER: DECLINED,
}
LEAD_FIELDS = [
	"name",
	"company_name",
	"lead_name",
	"tbo_collected_on",
	"tbo_collected_region",
	"tbo_stage",
	"tbo_collected_by",
	"lead_owner",
	"tbo_department",
]
MAX_PAGE = 100
POOL_LIST = 20


def simple_status(stage: str | None) -> str:
	return SIMPLE_STATUS.get(stage or NEW_LEAD, CALLING)


def history() -> list[dict]:
	"""Every ledger line to date, for what the won leads have earned."""
	return commission.ledger_lines(referral.EPOCH, add_days(getdate(), 1))


def earnings(leads: list[str]) -> dict[str, dict]:
	"""{lead: {earned, pending}}: earned is in Approved or Paid statements, pending is the rest of
	what the lead has earned so far (drafts and what no statement holds yet)."""
	earning = referral.earning_leads(leads)
	entitled = referral.entitled_to_date(history(), earning) if earning else {}
	approved = referral.in_statements(leads, statuses=("Approved", "Paid"))
	return {
		lead: {
			"earned": approved.get(lead, 0.0),
			"pending": flt(max(entitled.get(lead, 0) - approved.get(lead, 0), 0), 2),
		}
		for lead in leads
	}


def my_leads(user: str, start: int | str = 0, page_length: int | str = 50) -> dict:
	"""The leads `user` collected, newest first: only what they need to see, never the CRM. {rows:
	[{id, title, collected_on, region, status, earned, pending}], total, summary: {leads, won,
	earned, pending}, currency}."""
	filters = {"tbo_collected_by": user}
	total = frappe.db.count("Lead", filters)
	rows = frappe.get_all(
		"Lead",
		filters=filters,
		fields=LEAD_FIELDS,
		order_by="tbo_collected_on desc, creation desc",
		start=max(cint(start), 0),
		page_length=min(max(cint(page_length) or 50, 1), MAX_PAGE),
	)
	everything = frappe.get_all("Lead", filters=filters, fields=["name", "tbo_stage"])
	paid = earnings([row.name for row in everything])
	return {
		"rows": [
			{
				"id": row.name,
				"title": row.company_name or row.lead_name,
				"collected_on": row.tbo_collected_on,
				"region": row.tbo_collected_region,
				"status": simple_status(row.tbo_stage),
				**paid[row.name],
			}
			for row in rows
		],
		"total": total,
		"summary": {
			"leads": total,
			"won": sum(1 for row in everything if row.tbo_stage == CLOSED_WON),
			"earned": flt(sum(row["earned"] for row in paid.values()), 2),
			"pending": flt(sum(row["pending"] for row in paid.values()), 2),
		},
		"currency": company_currency(),
	}


def options(user: str) -> dict:
	"""What the Add a lead screen needs: the regions with their rate and whether they have a
	department, and whether the person manages the Lead Bank."""
	return {
		"regions": [
			{
				"region": region,
				"prefix": f"+{REGION_INFO[region]['prefix']}",
				"country": REGION_INFO[region]["country"],
				"rate": rate(region),
				"has_department": bool(department_for(region)),
			}
			for region in REGIONS
		],
		"cap": referral.cap(),
		"currency": company_currency(),
		"is_manager": commission.is_manager(user),
		"max_photos": 5,
	}


# ---------- managers ----------


def dashboard(month, user: str) -> dict:
	"""The Lead Bank for managers: the month's leads by region and collector, how many are won, the
	unassigned pool and the referral due. Managers only."""
	access.require_manager(user)
	start = commission.parse_month(month)
	end = getdate(add_months(start, 1))
	month_leads = frappe.get_all(
		"Lead",
		filters={
			"tbo_collected_by": ("is", "set"),
			"tbo_collected_on": ("between", [start, add_days(end, -1)]),
		},
		fields=LEAD_FIELDS,
	)
	won = [row for row in month_leads if row.tbo_stage == CLOSED_WON]
	by_region = [
		{
			"region": region,
			"department": department_for(region),
			"collected": sum(1 for row in month_leads if row.tbo_collected_region == region),
			"won": sum(1 for row in won if row.tbo_collected_region == region),
		}
		for region in REGIONS
	]
	return {
		"month": start,
		"currency": company_currency(),
		"collected": len(month_leads),
		"won": len(won),
		"conversion": flt(len(won) / len(month_leads) * 100, 1) if month_leads else 0.0,
		"by_region": by_region,
		"collectors": collectors(month_leads),
		"referral": referral_due(),
		"pool": pool(),
	}


def collectors(month_leads: list) -> list[dict]:
	"""The month's top collectors, most leads first."""
	people: dict[str, dict] = {}
	for row in month_leads:
		person = people.setdefault(
			row.tbo_collected_by, {"user": row.tbo_collected_by, "collected": 0, "won": 0}
		)
		person["collected"] += 1
		person["won"] += 1 if row.tbo_stage == CLOSED_WON else 0
	for person in people.values():
		person["full_name"] = get_fullname(person["user"])
	return sorted(people.values(), key=lambda person: (-person["collected"], -person["won"], person["user"]))


def referral_due() -> dict:
	"""{earned, paid, due, by_collector}: what won leads have earned so far, what Paid statements
	hold, and the difference, whole and per collector."""
	earning = referral.earning_leads()
	entitled = referral.entitled_to_date(history(), earning) if earning else {}
	paid = referral.in_statements(earning, statuses=("Paid",))
	by_collector: dict[str, float] = {}
	for lead, amount in entitled.items():
		due = max(amount - paid.get(lead, 0), 0)
		user = earning[lead].collector
		by_collector[user] = flt(by_collector.get(user, 0) + due, 2)
	return {
		"earned": flt(sum(entitled.values()), 2),
		"paid": flt(sum(paid.values()), 2),
		"due": flt(sum(by_collector.values()), 2),
		"by_collector": [
			{"user": user, "full_name": get_fullname(user), "due": due}
			for user, due in sorted(by_collector.items(), key=lambda item: -item[1])
		],
	}


def pool() -> dict:
	"""Collected leads nobody owns and no department has: {count, leads: [{lead, title, region,
	collected_on, collected_by_name}]}."""
	filters = {
		"tbo_collected_by": ("is", "set"),
		"lead_owner": ("is", "not set"),
		"tbo_department": ("is", "not set"),
	}
	rows = frappe.get_all(
		"Lead", filters=filters, fields=LEAD_FIELDS, order_by="tbo_collected_on desc", limit=POOL_LIST
	)
	return {
		"count": frappe.db.count("Lead", filters),
		"leads": [
			{
				"lead": row.name,
				"title": row.company_name or row.lead_name,
				"region": row.tbo_collected_region,
				"collected_on": row.tbo_collected_on,
				"collected_by_name": get_fullname(row.tbo_collected_by),
			}
			for row in rows
		],
	}


def reassign_credit(lead: str, new_collector: str, reason: str, by: str) -> dict:
	"""Move a lead's referral credit to another staff member, with a reason, kept as a note on the
	lead. Earlier statements stay as they were; the new person earns from the next one. Managers
	only."""
	access.require_manager(by)
	reason = " ".join((reason or "").split())
	if not reason:
		frappe.throw(_("Say why the credit is moving."))
	row = frappe.db.get_value("Lead", lead, ["tbo_collected_by", "tbo_collected_region"], as_dict=True)
	if not row or not row.tbo_collected_by:
		frappe.throw(_("{0} was not added through the Lead Bank.").format(lead))
	enabled = frappe.db.get_value("User", new_collector, ["enabled", "user_type"], as_dict=True)
	if not enabled or not enabled.enabled or enabled.user_type != "System User":
		frappe.throw(_("{0} is not an enabled user here.").format(new_collector))
	if new_collector == row.tbo_collected_by:
		frappe.throw(_("{0} already has the credit.").format(get_fullname(new_collector)))
	frappe.db.set_value("Lead", lead, "tbo_collected_by", new_collector)
	add_comment(
		lead,
		by,
		_("Referral credit moved from {0} to {1}. Reason: {2}").format(
			get_fullname(row.tbo_collected_by), get_fullname(new_collector), reason
		),
	)
	return {"lead": lead, "collected_by": new_collector, "previous": row.tbo_collected_by}


def referral_settings() -> dict:
	"""The rates, the cap and the departments of the three regions. Managers only."""
	commission.require_manager()
	return {
		"regions": [
			{
				"region": region,
				"rate": rate(region),
				"department": frappe.db.get_single_value(
					"TBO Smart Settings", REGION_INFO[region]["department"]
				)
				or None,
				"effective_department": department_for(region),
			}
			for region in REGIONS
		],
		"cap": referral.cap(),
		"currency": company_currency(),
		"departments": frappe.get_all("TBO Lead Department", pluck="name", order_by="name asc"),
	}


def save_referral_settings(values) -> dict:
	"""Set the rates (0 to 100), the cap (0: none) and the department of each region (blank: the
	default), as {regions: [{region, rate, department}], cap}. Managers only."""
	commission.require_manager()
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	changes = {}
	for row in values.get("regions") or []:
		region = check_region(row.get("region"))
		info = REGION_INFO[region]
		if "rate" in row:
			if not 0 <= flt(row["rate"]) <= 100:
				frappe.throw(_("A referral rate is 0 to 100."))
			changes[info["rate"]] = flt(row["rate"])
		if "department" in row:
			department = row["department"] or None
			if department and not frappe.db.exists("TBO Lead Department", department):
				frappe.throw(_("{0} is not a lead department.").format(department))
			changes[info["department"]] = department
	if "cap" in values:
		if flt(values["cap"]) < 0:
			frappe.throw(_("The cap cannot be below 0."))
		changes["referral_cap"] = flt(values["cap"])
	settings = frappe.get_doc("TBO Smart Settings")
	settings.update(changes)
	settings.save(ignore_permissions=True)
	return referral_settings()
