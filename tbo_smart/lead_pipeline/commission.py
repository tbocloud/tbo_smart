"""Sales commission on money collected (docs/commission.md).

Each customer receipt (Payment Entry) is followed to the lead behind it by the Business overview's
tracer (`BusinessOverview.traced_allocations`) and to a project: the project on the Sales Order or
Invoice it pays, else the project the lead's proposal documents were linked to. Who earns on a
project is its TBO Project Commission (shares of 100%), else the owner of the lead. How much is a
progressive slab scheme: the project's own slabs, its chosen plan, or the TBO Commission Plan that
matches the most rules.

The month's figures are always computed live. A TBO Commission Statement keeps them for one person
and month; once approved it is never touched: a receipt cancelled or refunded later counts
negatively in the month it happens.
"""

from itertools import pairwise

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, now_datetime

from tbo_smart.lead_pipeline.business import BusinessOverview, month_start
from tbo_smart.lead_pipeline.dashboard import company_currency
from tbo_smart.lead_pipeline.proposals import DOCTYPE as PROPOSAL
from tbo_smart.lead_pipeline.setup import MANAGEMENT_ROLE
from tbo_smart.sales import erpnext_installed
from tbo_smart.sales.departments import DEPARTMENT, DEPARTMENT_USER

PLAN = "TBO Commission Plan"
SETUP = "TBO Project Commission"
STATEMENT = "TBO Commission Statement"
BASIS_PROJECT = "Project collected"
BASIS_ALL = "Collected (all)"
DRAFT, APPROVED, PAID = "Draft", "Approved", "Paid"
MANAGER_ROLES = frozenset({"System Manager", "Sales Manager", MANAGEMENT_ROLE})
# how much a matched rule counts when the plans are compared: the user outweighs the rest
WEIGHTS = (("user", 8), ("project_type", 4), ("lead_type", 2), ("department", 1))
NO_PROJECT = "No project"
NO_EARNER = "No one earns"
CENT = 0.005


def is_manager(user: str | None = None) -> bool:
	return bool(set(frappe.get_roles(user or frappe.session.user)) & MANAGER_ROLES)


def require_manager() -> None:
	if not is_manager():
		frappe.throw(
			_("Only a Sales Manager, System Manager or TBO Management can do this."), frappe.PermissionError
		)


def parse_month(month) -> object:
	"""The first day of `month` ("2026-10", "2026-10-15" or a date)."""
	text = str(month or "").strip()
	if len(text) == 7:
		text += "-01"
	try:
		return month_start(getdate(text))
	except Exception:
		frappe.throw(_("Give the month as YYYY-MM."))


# ---------- the slab maths ----------


def clean_slabs(slabs) -> list[dict]:
	"""The slab rows (documents or dicts) as sorted [{from_amount, to_amount (None: no limit), rate}]."""
	rows = [
		{
			"from_amount": flt(row.get("from_amount")),
			"to_amount": flt(row.get("to_amount")) or None,
			"rate": flt(row.get("rate")),
		}
		for row in (slabs or [])
	]
	return sorted(rows, key=lambda row: row["from_amount"])


def validate_scheme(slabs, above_target_rate, target) -> None:
	"""Slabs start at or above 0, are bounded the right way round, do not overlap and have rates
	from 0 to 100, as does the above-target rate."""
	rows = clean_slabs(slabs)
	for row in rows:
		if row["from_amount"] < 0:
			frappe.throw(_("A slab cannot start below 0."))
		if row["to_amount"] is not None and row["to_amount"] <= row["from_amount"]:
			frappe.throw(_("A slab must end above where it starts ({0}).").format(row["from_amount"]))
		if not 0 <= row["rate"] <= 100:
			frappe.throw(_("A slab's rate must be between 0 and 100."))
	for earlier, later in pairwise(rows):
		if earlier["to_amount"] is None or earlier["to_amount"] > later["from_amount"]:
			frappe.throw(
				_("The slab from {0} overlaps the slab from {1}.").format(
					earlier["from_amount"], later["from_amount"]
				)
			)
	if not 0 <= flt(above_target_rate) <= 100:
		frappe.throw(_("The above-target rate must be between 0 and 100."))
	if flt(target) < 0:
		frappe.throw(_("The target cannot be negative."))


def apply_slabs(collected: float, target: float, slabs: list[dict], above_rate: float) -> dict:
	"""Progressive commission: each slab's rate applies to the part of `collected` inside it. The
	part above `target` (when set) earns the above-target rate where that is higher than the slab's.
	Returns {commission, rows: [{slab_no, from_amount, to_amount, amount, slab_rate, rate,
	above_target, commission}], reached: {slab_no, rate, above_target} or None}."""
	collected, target, above_rate = flt(collected), flt(target), flt(above_rate)
	if collected <= 0:
		return {"commission": 0.0, "rows": [], "reached": None}
	cuts = {0.0, collected}
	for slab in slabs:
		cuts.update(
			edge for edge in (slab["from_amount"], slab["to_amount"]) if edge and 0 < edge < collected
		)
	if 0 < target < collected:
		cuts.add(target)
	points = sorted(cuts)
	rows = []
	for low, high in pairwise(points):
		number, slab = next(
			(
				(index, slab)
				for index, slab in enumerate(slabs, 1)
				if slab["from_amount"] <= low and (slab["to_amount"] is None or high <= slab["to_amount"])
			),
			(None, None),
		)
		slab_rate = slab["rate"] if slab else 0.0
		above = target > 0 and low >= target
		rate = max(slab_rate, above_rate) if above else slab_rate
		rows.append(
			{
				"slab_no": number,
				"from_amount": low,
				"to_amount": high,
				"amount": flt(high - low, 2),
				"slab_rate": slab_rate,
				"rate": rate,
				"above_target": 1 if above else 0,
				"commission": flt((high - low) * rate / 100, 2),
			}
		)
	last = rows[-1]
	return {
		"commission": flt(sum(row["commission"] for row in rows), 2),
		"rows": rows,
		"reached": {"slab_no": last["slab_no"], "rate": last["rate"], "above_target": last["above_target"]},
	}


# ---------- plans and project set-ups ----------


def plan_scheme(doc) -> dict:
	return {
		"name": doc.name,
		"basis": doc.basis or BASIS_PROJECT,
		"target": flt(doc.monthly_target),
		"slabs": clean_slabs(doc.slabs),
		"above_rate": flt(doc.above_target_rate),
		"department": doc.department,
		"lead_type": doc.lead_type,
		"project_type": doc.project_type,
		"user": doc.user,
	}


def active_plans() -> list[dict]:
	names = frappe.get_all(PLAN, filters={"is_active": 1}, pluck="name", order_by="name asc")
	return [plan_scheme(frappe.get_doc(PLAN, name)) for name in names]


def match_plan(plans: list[dict], user, department, lead_type, project_type) -> dict | None:
	"""The active plan whose set conditions all hold, the one matching the most (user first); the
	first by name on a tie."""
	values = {"user": user, "department": department, "lead_type": lead_type, "project_type": project_type}
	best = None
	for plan in plans:
		score = 0
		for field, weight in WEIGHTS:
			if plan[field]:
				if plan[field] != values[field]:
					break
				score += weight
		else:
			if best is None or score > best[0]:
				best = (score, plan)
	return best[1] if best else None


def override_scheme(setup) -> dict:
	return {
		"name": _("{0} (own slabs)").format(setup["project"]),
		"basis": BASIS_PROJECT,
		"target": flt(setup["override_target"]),
		"slabs": clean_slabs(setup["override_slabs"]),
		"above_rate": flt(setup["override_above_target_rate"]),
	}


def project_setups() -> dict:
	"""{project: set-up} of the active TBO Project Commission records."""
	found = {}
	for name in frappe.get_all(SETUP, filters={"is_active": 1}, pluck="name"):
		doc = frappe.get_doc(SETUP, name)
		found[doc.project] = {
			"project": doc.project,
			"lead": doc.lead,
			"project_type": doc.project_type,
			"earners": [(row.user, flt(row.share_percent)) for row in doc.earners],
			"plan": doc.plan,
			"use_override": cint(doc.use_override),
			"override_target": doc.override_target,
			"override_above_target_rate": doc.override_above_target_rate,
			"override_slabs": doc.override_slabs,
		}
	return found


def project_type_of(project: str, setup: dict | None) -> str | None:
	if setup and setup["project_type"]:
		return setup["project_type"]
	if project and frappe.db.exists("DocType", "Project"):
		return frappe.db.get_value("Project", project, "project_type")
	return None


# ---------- the ledger: receipts to lines ----------


def lead_projects(leads) -> dict:
	"""{lead: hub project} from the hub project linked on the lead's documents (the latest)."""
	leads = [lead for lead in set(leads) if lead]
	if not leads:
		return {}
	Doc = frappe.qb.DocType(PROPOSAL)
	rows = (
		frappe.qb.from_(Doc)
		.select(Doc.lead, Doc.hub_project)
		.where(Doc.lead.isin(leads) & (Doc.hub_project.isnotnull()) & (Doc.hub_project != ""))
		.orderby(Doc.modified)
	).run()
	return dict(rows)


def ledger_lines(start=None, end=None, project: str | None = None) -> list[dict]:
	"""The money that moved in [start, end) (all time when not given), one line per receipt and
	lead: {payment_entry, date, kind, amount (negative for refunds and cancelled receipts), customer,
	lead, owner, department, lead_type, project, hub_project}.

	A receipt counts when it was posted; one cancelled later counts again, negatively, on the day it
	was cancelled; a refund (Pay to a customer) counts negatively. Money not allocated to an order
	or invoice, or not traced to a lead, has no lead. Nothing already in a past month changes."""
	tracer = BusinessOverview(getdate(), getdate())  # only for its tracing
	Entry = frappe.qb.DocType("Payment Entry")
	base = (
		(Entry.company == tracer.company)
		& (Entry.party_type == "Customer")
		& Entry.payment_type.isin(("Receive", "Pay"))
	)
	posted = base & Entry.docstatus.isin((1, 2))
	cancelled = base & (Entry.docstatus == 2)
	if start:
		posted &= (Entry.posting_date >= start) & (Entry.posting_date < end)
		cancelled &= (Entry.modified >= start) & (Entry.modified < end)
	entries = (
		frappe.qb.from_(Entry)
		.select(
			Entry.name,
			Entry.payment_type,
			Entry.docstatus,
			Entry.posting_date,
			Entry.modified,
			Entry.party,
			Entry.base_received_amount,
			Entry.base_paid_amount,
		)
		.where(posted | cancelled)
	).run(as_dict=True)
	if not entries:
		return []
	allocations: dict[str, list] = {}
	for row in tracer.traced_allocations(Entry.name.isin([entry.name for entry in entries])):
		allocations.setdefault(row["entry"], []).append(row)
	lines = []
	for entry in entries:
		receipt = entry.payment_type == "Receive"
		total = flt(entry.base_received_amount if receipt else entry.base_paid_amount)
		events = []
		in_range = not start or start <= getdate(entry.posting_date) < end
		if in_range:
			events.append((entry.posting_date, 1 if receipt else -1, "Receipt" if receipt else "Refund"))
		if entry.docstatus == 2 and (not start or start <= getdate(entry.modified) < end):
			events.append(
				(
					getdate(entry.modified),
					-1 if receipt else 1,
					"Cancelled receipt" if receipt else "Cancelled refund",
				)
			)
		traced = allocations.get(entry.name, [])
		allocated = sum(abs(row["amount"]) for row in traced)
		scale = total / allocated if allocated > total > 0 else 1
		leftover = flt(total - allocated * scale, 2) if total > allocated * scale else 0
		for date, sign, kind in events:
			parts = [
				{
					"amount": abs(row["amount"]) * scale,
					"lead": row["lead"],
					"owner": row["owner"],
					"department": row["department"],
					"lead_type": row["lead_type"],
					"project": row["project"],
				}
				for row in traced
			]
			if leftover > CENT:
				parts.append(
					{
						"amount": leftover,
						"lead": None,
						"owner": None,
						"department": None,
						"lead_type": None,
						"project": None,
					}
				)
			for part in parts:
				lines.append(
					{
						"payment_entry": entry.name,
						"date": getdate(date),
						"kind": kind,
						"customer": entry.party,
						**part,
						"amount": flt(sign * part["amount"], 2),
					}
				)
	hub = lead_projects(line["lead"] for line in lines)
	for line in lines:
		line["hub_project"] = hub.get(line["lead"])
	if project:
		lines = [line for line in lines if project in (line["project"], line["hub_project"])]
	return lines


# ---------- the month ----------


def attribute(lines: list[dict]) -> tuple[dict, list[dict]]:
	"""Who earns what on `lines`: ({user: [piece]}, unattributed lines with a reason).

	A piece is one project (or "" when the line has none) of one earner:
	{project, share, scheme, lines}. A project-basis scheme needs a project; a line without one
	is Unattributed ("No project") unless the plan that applies is on all collections."""
	setups, plans = project_setups(), active_plans()
	pieces: dict[tuple, dict] = {}
	unattributed = []
	for line in lines:
		key = next((p for p in (line["project"], line["hub_project"]) if p in setups), None)
		setup = setups.get(key) if key else None
		project = key or line["project"] or line["hub_project"] or ""
		line["project"] = project
		if setup and setup["earners"]:
			earners = setup["earners"]
		elif line["owner"]:
			earners = [(line["owner"], 100.0)]
		else:
			unattributed.append({**line, "reason": NO_EARNER})
			continue
		department, lead_type = line["department"], line["lead_type"]
		if setup and setup["lead"] and not line["lead"]:
			department, lead_type = frappe.db.get_value(
				"Lead", setup["lead"], ["tbo_department", "tbo_lead_type"]
			) or (None, None)
		kept = False
		for user, share in earners:
			scheme = scheme_for(
				setup, plans, user, department, lead_type, project, project_type_of(project, setup)
			)
			if not project and not (scheme and scheme["basis"] == BASIS_ALL):
				continue
			piece = pieces.setdefault(
				(user, project, scheme["name"] if scheme else None),
				{"user": user, "project": project, "share": share, "scheme": scheme, "lines": []},
			)
			piece["lines"].append(line)
			kept = True
		if not kept:
			unattributed.append({**line, "reason": NO_PROJECT})
	by_user: dict[str, list] = {}
	for piece in pieces.values():
		by_user.setdefault(piece["user"], []).append(piece)
	return by_user, unattributed


def scheme_for(setup, plans, user, department, lead_type, project, project_type) -> dict | None:
	if setup and setup["use_override"]:
		return override_scheme(setup)
	if setup and setup["plan"]:
		return next((plan for plan in plans if plan["name"] == setup["plan"]), None)
	return match_plan(plans, user, department, lead_type, project_type)


def person_figures(user: str, pieces: list[dict]) -> dict:
	"""One person's month from their pieces: totals, a line per project, the slab rows and the
	payments. Project-basis schemes run each project's money through the slabs and the person gets
	their share; all-collections schemes run the person's credited money through them once and the
	commission is spread over the projects by what each credited."""
	lines, breakdown, payments = [], [], []
	pooled: dict[str, dict] = {}
	for piece in pieces:
		scheme, share = piece["scheme"], piece["share"] / 100
		collected = flt(sum(line["amount"] for line in piece["lines"]), 2)
		row = {
			"project": piece["project"],
			"plan": scheme["name"] if scheme else None,
			"collected": collected,
			"share_percent": piece["share"],
			"credited": flt(collected * share, 2),
			"target": 0.0,
			"commission": 0.0,
		}
		lines.append(row)
		payments.extend({**line, "project": piece["project"]} for line in piece["lines"])
		if not scheme:
			continue
		if scheme["basis"] == BASIS_PROJECT:
			result = apply_slabs(collected, scheme["target"], scheme["slabs"], scheme["above_rate"])
			row["target"] = flt(scheme["target"] * share, 2)
			row["commission"] = flt(result["commission"] * share, 2)
			breakdown.extend(
				{
					**r,
					"project": piece["project"],
					"amount": flt(r["amount"] * share, 2),
					"commission": flt(r["commission"] * share, 2),
				}
				for r in result["rows"]
			)
		else:
			pooled.setdefault(scheme["name"], {"scheme": scheme, "rows": []})["rows"].append(row)
	reached = None
	for group in pooled.values():
		scheme, rows = group["scheme"], group["rows"]
		credited = flt(sum(r["credited"] for r in rows), 2)
		result = apply_slabs(credited, scheme["target"], scheme["slabs"], scheme["above_rate"])
		for r in rows:
			weight = r["credited"] / credited if credited > 0 else 0
			r["commission"] = flt(result["commission"] * weight, 2) if credited > 0 else 0.0
		if rows:
			rows[0]["target"] = flt(scheme["target"], 2)
		breakdown.extend({**r, "project": ""} for r in result["rows"])
	if breakdown:
		last = breakdown[-1]
		reached = {"slab_no": last["slab_no"], "rate": last["rate"], "above_target": last["above_target"]}
	collected = flt(sum(r["credited"] for r in lines), 2)
	target = flt(sum(r["target"] for r in lines), 2)
	return {
		"user": user,
		"plans": sorted({r["plan"] for r in lines if r["plan"]}),
		"collected": collected,
		"target": target,
		"achievement": flt(collected / target * 100, 1) if target > 0 else 0.0,
		"reached": reached,
		"commission": flt(sum(r["commission"] for r in lines), 2),
		"projects": sorted(lines, key=lambda r: r["project"]),
		"breakdown": breakdown,
		"payments": sorted(payments, key=lambda p: (p["date"], p["payment_entry"])),
	}


def month_figures(month) -> tuple[dict, list[dict]]:
	"""({user: figures}, unattributed lines) for the month, live."""
	start = parse_month(month)
	by_user, unattributed = attribute(ledger_lines(start, getdate(add_months(start, 1))))
	return {user: person_figures(user, pieces) for user, pieces in by_user.items()}, unattributed


# ---------- statements ----------


def statement_figures(doc) -> dict:
	"""A statement's frozen figures in the same shape as `person_figures`."""
	breakdown = [row.as_dict() for row in doc.breakdown]
	last = breakdown[-1] if breakdown else None
	return {
		"user": doc.user,
		"plans": [name for name in (doc.plans or "").split(", ") if name],
		"collected": flt(doc.collected, 2),
		"target": flt(doc.target, 2),
		"achievement": flt(doc.achievement, 1),
		"reached": last
		and {"slab_no": last["slab_no"], "rate": last["rate"], "above_target": last["above_target"]},
		"commission": flt(doc.commission, 2),
		"projects": [row.as_dict() for row in doc.lines],
		"breakdown": breakdown,
		"payments": [
			{
				"payment_entry": row.payment_entry,
				"date": row.posting_date,
				"kind": row.kind,
				"amount": row.amount,
				"project": row.project,
				"lead": row.lead,
				"customer": row.customer,
			}
			for row in doc.payments
		],
	}


def fill_statement(doc, figures: dict) -> None:
	doc.plans = ", ".join(figures["plans"])
	doc.collected, doc.target = figures["collected"], figures["target"]
	doc.achievement, doc.commission = figures["achievement"], figures["commission"]
	doc.set("lines", [])
	doc.set("breakdown", [])
	doc.set("payments", [])
	for row in figures["projects"]:
		doc.append(
			"lines",
			{
				key: row[key]
				for key in (
					"project",
					"plan",
					"collected",
					"share_percent",
					"credited",
					"target",
					"commission",
				)
			},
		)
	for row in figures["breakdown"]:
		doc.append(
			"breakdown",
			{
				key: row[key]
				for key in (
					"project",
					"slab_no",
					"from_amount",
					"to_amount",
					"amount",
					"slab_rate",
					"rate",
					"above_target",
					"commission",
				)
			},
		)
	for row in figures["payments"]:
		doc.append(
			"payments",
			{
				"payment_entry": row["payment_entry"],
				"posting_date": row["date"],
				"kind": row["kind"],
				"amount": row["amount"],
				"project": row["project"],
				"lead": row["lead"],
				"customer": row["customer"],
			},
		)


def statements_of(month) -> dict:
	return {
		doc.user: doc
		for doc in (
			frappe.get_doc(STATEMENT, name)
			for name in frappe.get_all(STATEMENT, filters={"month": parse_month(month)}, pluck="name")
		)
	}


def generate(month) -> list[dict]:
	"""Create or refresh the Draft statement of everyone who earned or lost money in `month`.
	Approved and Paid statements are never touched. Returns [{user, name, status, commission,
	skipped}]."""
	start = parse_month(month)
	figures, _unattributed = month_figures(start)
	existing = statements_of(start)
	empty = {
		"plans": [],
		"collected": 0,
		"target": 0,
		"achievement": 0,
		"commission": 0,
		"projects": [],
		"breakdown": [],
		"payments": [],
	}
	for user, doc in existing.items():
		if doc.status == DRAFT:
			figures.setdefault(user, empty)
	result = []
	for user, data in sorted(figures.items()):
		doc = existing.get(user)
		if doc and doc.status != DRAFT:
			result.append(
				{
					"user": user,
					"name": doc.name,
					"status": doc.status,
					"commission": doc.commission,
					"skipped": 1,
				}
			)
			continue
		if not doc:
			doc = frappe.new_doc(STATEMENT)
			doc.user, doc.month, doc.status = user, start, DRAFT
		fill_statement(doc, data)
		doc.save(ignore_permissions=True)
		result.append(
			{"user": user, "name": doc.name, "status": DRAFT, "commission": doc.commission, "skipped": 0}
		)
	return result


def generate_previous_month() -> None:
	"""Scheduler, the 1st of each month: the Drafts of the month that just ended."""
	if erpnext_installed():
		generate(add_months(month_start(getdate()), -1))


def set_status(name: str, current: str, new: str) -> object:
	doc = frappe.get_doc(STATEMENT, name)
	if doc.status != current:
		frappe.throw(
			_("{0} is {1}; only a {2} statement can become {3}.").format(name, doc.status, current, new)
		)
	doc.status = new
	return doc


def approve(name: str):
	require_manager()
	doc = set_status(name, DRAFT, APPROVED)
	if doc.user == frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(_("You cannot approve your own commission."), frappe.PermissionError)
	doc.approved_by, doc.approved_on = frappe.session.user, now_datetime()
	doc.save(ignore_permissions=True)
	return doc


def mark_paid(name: str):
	require_manager()
	doc = set_status(name, APPROVED, PAID)
	doc.paid_on = now_datetime()
	doc.save(ignore_permissions=True)
	return doc


# ---------- who sees whom ----------


def scope_users(user: str) -> tuple[str, set | None]:
	"""(scope, users): "all" (None), "department" (the people of the departments `user` heads) or
	"own"."""
	if is_manager(user):
		return "all", None
	Person, Department = frappe.qb.DocType(DEPARTMENT_USER), frappe.qb.DocType(DEPARTMENT)
	headed = set(
		frappe.qb.from_(Person)
		.join(Department)
		.on(Department.name == Person.parent)
		.select(Person.parent)
		.where((Person.user == user) & (Person.parentfield == "heads") & (Department.is_active == 1))
		.run(pluck=True)
	)
	if not headed:
		return "own", {user}
	people = set(frappe.get_all(DEPARTMENT_USER, filters={"parent": ("in", list(headed))}, pluck="user"))
	return "department", people | {user}


def department_users(department: str) -> set:
	return set(frappe.get_all(DEPARTMENT_USER, filters={"parent": department}, pluck="user"))


def full_names(users) -> dict:
	users = list(users)
	if not users:
		return {}
	return dict(
		frappe.get_all("User", filters={"name": ("in", users)}, fields=["name", "full_name"], as_list=True)
	)


def build_month(month, user: str | None, department: str | None) -> dict:
	"""The month for the session user: everyone they may see, frozen where a statement is approved,
	and (managers) the Unattributed money."""
	start = parse_month(month)
	me = frappe.session.user
	scope, allowed = scope_users(me)
	if user:
		if allowed is not None and user not in allowed:
			frappe.throw(_("You cannot see {0}'s commission.").format(user), frappe.PermissionError)
		allowed = {user}
	if department:
		if not frappe.db.exists(DEPARTMENT, department):
			frappe.throw(_("{0} is not a lead department.").format(department), frappe.ValidationError)
		people = department_users(department)
		allowed = people if allowed is None else allowed & people
	figures, unattributed = month_figures(start)
	statements = statements_of(start)
	for name, doc in statements.items():
		if doc.status != DRAFT:
			figures[name] = statement_figures(doc)
	people = sorted(user for user in figures if allowed is None or user in allowed)
	names = full_names(people)
	rows = []
	for person in people:
		doc = statements.get(person)
		data = figures[person]
		rows.append(
			{
				**data,
				"full_name": names.get(person) or person,
				"statement": doc.name if doc else None,
				"status": doc.status if doc else None,
				"frozen": 1 if doc and doc.status != DRAFT else 0,
				"approved_by": doc.approved_by if doc else None,
				"approved_on": doc.approved_on if doc else None,
				"paid_on": doc.paid_on if doc else None,
			}
		)
	manager = scope == "all"
	return {
		"month": start,
		"currency": company_currency(),
		"scope": scope,
		"can_manage": manager,
		"rows": rows,
		"totals": {
			"collected": flt(sum(row["collected"] for row in rows), 2),
			"commission": flt(sum(row["commission"] for row in rows), 2),
		},
		"unattributed": unattributed_summary(unattributed)
		if manager and not user and not department
		else None,
	}


def unattributed_summary(lines: list[dict]) -> dict:
	reasons: dict[str, float] = {}
	for line in lines:
		reasons[line["reason"]] = flt(reasons.get(line["reason"], 0) + line["amount"], 2)
	return {
		"amount": flt(sum(line["amount"] for line in lines), 2),
		"count": len({line["payment_entry"] for line in lines}),
		"reasons": reasons,
		"payments": [
			{
				key: line[key]
				for key in (
					"payment_entry",
					"date",
					"kind",
					"amount",
					"customer",
					"lead",
					"project",
					"reason",
				)
			}
			for line in sorted(lines, key=lambda line: (line["date"], line["payment_entry"]))
		],
	}


# ---------- plans and project set-ups for the hub ----------

PLAN_FIELDS = (
	"plan_name",
	"is_active",
	"basis",
	"department",
	"lead_type",
	"project_type",
	"user",
	"monthly_target",
	"above_target_rate",
	"slabs",
)


def serialize_plan(doc) -> dict:
	return {
		"name": doc.name,
		"plan_name": doc.plan_name,
		"is_active": cint(doc.is_active),
		"basis": doc.basis,
		"department": doc.department,
		"lead_type": doc.lead_type,
		"project_type": doc.project_type,
		"user": doc.user,
		"monthly_target": flt(doc.monthly_target),
		"above_target_rate": flt(doc.above_target_rate),
		"slabs": [
			{
				"from_amount": flt(row.from_amount),
				"to_amount": flt(row.to_amount) or None,
				"rate": flt(row.rate),
			}
			for row in doc.slabs
		],
	}


def get_plans() -> dict:
	require_manager()
	return {
		"plans": [
			serialize_plan(frappe.get_doc(PLAN, name))
			for name in frappe.get_all(PLAN, pluck="name", order_by="plan_name asc")
		],
		"currency": company_currency(),
	}


def save_plan(values: dict, name: str | None = None) -> dict:
	require_manager()
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	unknown = sorted(set(values) - set(PLAN_FIELDS))
	if unknown:
		frappe.throw(_("Unknown fields: {0}").format(", ".join(unknown)))
	if name:
		doc = frappe.get_doc(PLAN, name)
		if values.get("plan_name") not in (None, name):
			frappe.throw(_("A plan cannot be renamed."))
	else:
		if not (values.get("plan_name") or "").strip():
			frappe.throw(_("Give the plan a name."))
		doc = frappe.new_doc(PLAN)
		doc.plan_name = values["plan_name"].strip()
	for field in PLAN_FIELDS[1:-1]:
		if field in values:
			doc.set(field, values[field] if values[field] not in ("",) else None)
	if "slabs" in values:
		doc.set("slabs", [])
		for row in values["slabs"] or []:
			doc.append("slabs", {k: row.get(k) for k in ("from_amount", "to_amount", "rate")})
	doc.save(ignore_permissions=True)
	return serialize_plan(doc)


def get_project_setup(project: str) -> dict:
	"""The project's commission set-up (None when it has none) with what was collected on it so far
	and the commission credited on it in statements."""
	require_manager()
	project = (project or "").strip()
	if not project:
		frappe.throw(_("Name the project."))
	name = frappe.db.exists(SETUP, project)
	setup = None
	if name:
		doc = frappe.get_doc(SETUP, name)
		setup = {
			"project": doc.project,
			"is_active": cint(doc.is_active),
			"lead": doc.lead,
			"project_type": doc.project_type,
			"earners": [{"user": row.user, "share_percent": flt(row.share_percent)} for row in doc.earners],
			"plan": doc.plan,
			"use_override": cint(doc.use_override),
			"override_target": flt(doc.override_target),
			"override_above_target_rate": flt(doc.override_above_target_rate),
			"override_slabs": [
				{
					"from_amount": flt(r.from_amount),
					"to_amount": flt(r.to_amount) or None,
					"rate": flt(r.rate),
				}
				for r in doc.override_slabs
			],
		}
	lines = ledger_lines(project=project)
	credited = frappe.get_all(
		"TBO Commission Statement Line",
		filters={"parenttype": STATEMENT, "project": project},
		fields=["parent", "commission"],
	)
	return {
		"setup": setup,
		"collected": flt(sum(line["amount"] for line in lines), 2),
		"payments": len({line["payment_entry"] for line in lines}),
		"commission": flt(sum(row.commission for row in credited), 2),
		"currency": company_currency(),
	}


def save_project_setup(values: dict) -> dict:
	require_manager()
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	project = (values.get("project") or "").strip()
	if not project:
		frappe.throw(_("Name the project."))
	doc = frappe.get_doc(SETUP, project) if frappe.db.exists(SETUP, project) else frappe.new_doc(SETUP)
	doc.project = project
	for field in (
		"is_active",
		"lead",
		"project_type",
		"plan",
		"use_override",
		"override_target",
		"override_above_target_rate",
	):
		if field in values:
			doc.set(field, values[field] if values[field] not in ("",) else None)
	if "earners" in values:
		doc.set("earners", [])
		for row in values["earners"] or []:
			doc.append("earners", {"user": row.get("user"), "share_percent": row.get("share_percent")})
	if "override_slabs" in values:
		doc.set("override_slabs", [])
		for row in values["override_slabs"] or []:
			doc.append("override_slabs", {k: row.get(k) for k in ("from_amount", "to_amount", "rate")})
	doc.save(ignore_permissions=True)
	return get_project_setup(doc.project)
