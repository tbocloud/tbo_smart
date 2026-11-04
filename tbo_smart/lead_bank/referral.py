"""Referral commission (docs/lead-bank.md §5, docs/commission.md §7).

A collected lead that is Won earns its collector a percentage of the money collected on it, by the
region it was collected in, up to a cap per lead. The money is the one the sales commission uses:
`commission.ledger_lines` follows each receipt to its lead. The figures join the monthly
statement as lines of kind "Referral", so they go Draft, Approved and Paid with the rest, and an
approved month is never rewritten.

How one month is worked out. For each earning lead: what has been collected on it up to the end of
the month (cancellations count in the month they happen, as in sales commission), times the rate,
capped, less what earlier months' statements already paid it. The remainder is the month's
referral, never below zero. So a lead that turns Won after its client paid still earns, and the cap
holds across months.
"""

from datetime import date

import frappe
from frappe.utils import flt

from tbo_smart.api.mobile.capabilities import CRM_ROLES
from tbo_smart.lead_bank import cap, rate
from tbo_smart.lead_pipeline.statuses import WON_STATUSES

KIND = "Referral"
PLAN_LABEL = "Referral (Lead Bank)"
LINE = "TBO Commission Statement Line"
STATEMENT = "TBO Commission Statement"
# the roles that already earn sales commission; a collector who holds one and owns the lead has
# their commission from the sale itself
SALES_ROLES = CRM_ROLES | {"Sales Employee"}
EPOCH = date(2000, 1, 1)


def earning_leads(names=None) -> dict[str, frappe._dict]:
	"""{lead: {collector, region}} for the leads that can earn a referral: collected, Won, and not
	owned by or assigned to the sales person who collected them."""
	filters = {"tbo_collected_by": ("is", "set"), "tbo_stage": ("in", WON_STATUSES)}
	if names is not None:
		if not names:
			return {}
		filters["name"] = ("in", list(names))
	rows = frappe.get_all(
		"Lead",
		filters=filters,
		fields=["name", "tbo_collected_by as collector", "tbo_collected_region as region", "lead_owner"],
	)
	if not rows:
		return {}
	assigned = {
		(row.reference_name, row.allocated_to)
		for row in frappe.get_all(
			"ToDo",
			filters={
				"reference_type": "Lead",
				"reference_name": ("in", [row.name for row in rows]),
				"status": ("!=", "Cancelled"),
			},
			fields=["reference_name", "allocated_to"],
		)
	}
	earning = {}
	for row in rows:
		works_it = row.collector == row.lead_owner or (row.name, row.collector) in assigned
		if row.region and not (works_it and SALES_ROLES & set(frappe.get_roles(row.collector))):
			earning[row.name] = row
	return earning


def entitlement(collected: float, region: str) -> float:
	"""What `collected` earns in `region`: the rate, capped; nothing while the net is not above 0."""
	if collected <= 0:
		return 0.0
	amount = collected * rate(region) / 100
	limit = cap()
	return flt(min(amount, limit) if limit > 0 else amount, 2)


def collected_per_lead(lines: list[dict], leads) -> dict[str, float]:
	"""The money (signed) the ledger lines hold for each of `leads`."""
	totals: dict[str, float] = {}
	for line in lines:
		if line["lead"] in leads:
			totals[line["lead"]] = totals.get(line["lead"], 0) + line["amount"]
	return {lead: flt(total, 2) for lead, total in totals.items()}


def in_statements(leads, before=None, statuses=None) -> dict[str, float]:
	"""{lead: referral already in statements}, those of months before `before` and with one of
	`statuses` when given. Every collector's statements count: credit moved to someone else does not
	reset the cap."""
	if not leads:
		return {}
	Line, Statement = frappe.qb.DocType(LINE), frappe.qb.DocType(STATEMENT)
	query = (
		frappe.qb.from_(Line)
		.join(Statement)
		.on(Statement.name == Line.parent)
		.select(Line.lead, Line.commission)
		.where((Line.kind == KIND) & Line.lead.isin(list(leads)))
	)
	if before:
		query = query.where(Statement.month < before)
	if statuses:
		query = query.where(Statement.status.isin(list(statuses)))
	totals: dict[str, float] = {}
	for lead, commission in query.run():
		totals[lead] = totals.get(lead, 0) + flt(commission)
	return {lead: flt(total, 2) for lead, total in totals.items()}


def month_rows(start, lines: list[dict]) -> list[dict]:
	"""The month's referral lines, as statement lines of kind Referral: `lines` is every ledger
	line up to the end of the month. [{user, kind, lead, project, plan, collected, share_percent,
	credited, target, commission}]."""
	leads = earning_leads()
	if not leads:
		return []
	collected = collected_per_lead(lines, leads)
	prior = in_statements(collected, before=start)
	limit = cap()
	rows = []
	for lead, total in sorted(collected.items()):
		info = leads[lead]
		amount = flt(max(entitlement(total, info.region) - prior.get(lead, 0), 0), 2)
		if amount > 0:
			rows.append(
				{
					"user": info.collector,
					"kind": KIND,
					"lead": lead,
					"project": "",
					"plan": f"{info.region} {rate(info.region):g}%",
					"collected": total,
					"share_percent": 100,
					"credited": total,
					"target": limit,
					"commission": amount,
				}
			)
	return rows


def entitled_to_date(lines: list[dict], leads: dict) -> dict[str, float]:
	"""{lead: what it has earned in all, up to now} from every ledger line."""
	collected = collected_per_lead(lines, leads)
	return {lead: entitlement(total, leads[lead].region) for lead, total in collected.items()}
