"""One-time AI triage of stale leads (docs/smart-leads.md §5.6).

An admin action in two steps, recorded as a **TBO Lead Triage**:

1. **Preview** (`start`, a background job): open leads with no open follow-up that are New or
   untouched for `lead_stale_days`, best score first, at most `triage_max_leads` per run, and
   none triaged in the last 30 days. The AI puts each in **Call again** or **Suggest close**
   with a reason (number-guarded). Call again leads get a planned date: from the next working
   day on, at most `triage_daily_capacity` follow-ups per person per day, counting the ones
   they already have. Nothing on any lead changes.
2. **Apply**: a "Sales follow-up" CRM Task for every Call again lead, dated again from the next
   working day. Suggest close leads are only listed; a person closes each one they agree with
   (`close_leads`). **The triage never closes a lead.**
"""

import frappe
from frappe import _
from frappe.query_builder.functions import Count, Date, Sum
from frappe.utils import add_days, add_to_date, cint, flt, getdate, now_datetime

from tbo_smart import ai
from tbo_smart.api.mobile.crm import add_follow_up_task
from tbo_smart.smart_leads import FOLLOW_UP_TYPE, OPEN_STATUSES, setting
from tbo_smart.smart_leads.facts import LeadFacts
from tbo_smart.smart_leads.notes import set_lead_status
from tbo_smart.smart_leads.utils import WorkCalendar, enabled_users

DOCTYPE = "TBO Lead Triage"
CALL_AGAIN, SUGGEST_CLOSE = "Call again", "Suggest close"
BUCKETS = {"call_again": CALL_AGAIN, "suggest_close": SUGGEST_CLOSE}
BATCH = 20
MAX_LEADS = 1000
RECENT_DAYS = 30
STUCK_HOURS = 2
CLOSE_STATUSES = ("Junk", "Unqualified")


def max_leads() -> int:
	return min(max(cint(setting("triage_max_leads")) or 300, 1), MAX_LEADS)


def capacity() -> int:
	return max(cint(setting("triage_daily_capacity")) or 15, 1)


def start() -> str:
	"""Queue a preview run, or return the one already queued or running. A run stuck for
	STUCK_HOURS (its job died with a worker) is marked Failed so a new one can start."""
	frappe.db.set_value(
		DOCTYPE,
		{
			"status": ("in", ("Queued", "Running")),
			"modified": ("<", add_to_date(now_datetime(), hours=-STUCK_HOURS)),
		},
		{"status": "Failed", "error": _("The preview job stopped without finishing.")},
	)
	running = frappe.db.get_value(DOCTYPE, {"status": ("in", ("Queued", "Running"))}, "name")
	if running:
		return running
	run = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"status": "Queued",
			"requested_by": frappe.session.user,
			"lead_limit": max_leads(),
		}
	).insert(ignore_permissions=True)
	frappe.enqueue(
		"tbo_smart.smart_leads.triage.run_preview",
		run=run.name,
		queue="long",
		timeout=3600,
		enqueue_after_commit=True,
		now=frappe.flags.in_test,
	)
	return run.name


def candidates(limit: int) -> list[str]:
	"""Open leads with no open follow-up, New or untouched for the stale days, best score
	first, not triaged in the last 30 days."""
	Lead = frappe.qb.DocType("CRM Lead")
	Task = frappe.qb.DocType("CRM Task")
	Row = frappe.qb.DocType("TBO Lead Triage Row")
	Run = frappe.qb.DocType(DOCTYPE)
	recent = (
		frappe.qb.from_(Row)
		.join(Run)
		.on(Run.name == Row.parent)
		.select(Row.lead)
		.where(
			(Run.creation >= add_days(now_datetime(), -RECENT_DAYS)) & (Run.status.isin(("Ready", "Applied")))
		)
	)
	planned = (
		frappe.qb.from_(Task)
		.select(Task.reference_docname)
		.where(
			(Task.reference_doctype == "CRM Lead")
			& Task.status.notin(("Done", "Canceled"))
			& Task.due_date.isnotnull()
		)
	)
	names = (
		frappe.qb.from_(Lead)
		.select(Lead.name)
		.where(
			Lead.status.isin(OPEN_STATUSES)
			& (Lead.converted == 0)
			& Lead.name.notin(planned)
			& Lead.name.notin(recent)
		)
		.orderby(Lead.lead_score, order=frappe.qb.desc)
		.orderby(Lead.creation, order=frappe.qb.desc)
	).run(pluck=True)
	facts = LeadFacts(names)
	stale = cint(setting("lead_stale_days")) or 21
	keep = []
	for name in facts.names:
		days = facts.days_since_touch(name)
		if facts.lead(name).status == "New" or days is None or days >= stale:
			keep.append(name)
	order = {name: index for index, name in enumerate(names)}
	return sorted(keep, key=order.get)[:limit]


def run_preview(run: str) -> None:
	"""Background job: ask the AI about each candidate, plan the dates, mark the run Ready."""
	doc = frappe.get_doc(DOCTYPE, run)
	doc.db_set("status", "Running")
	frappe.db.savepoint("tbo_lead_triage")
	try:
		names = candidates(cint(doc.lead_limit) or max_leads())
		facts = LeadFacts(names)
		answers = classify(facts, run)
		fallback = setting("lead_fallback_owner")
		active = enabled_users({facts.lead(name).lead_owner for name in facts.names} | {fallback})
		doc.rows = []
		for name in facts.names:
			if name not in answers:
				continue
			lead = facts.lead(name)
			bucket, reason = answers[name]
			owner = (
				lead.lead_owner if lead.lead_owner in active else (fallback if fallback in active else None)
			)
			doc.append(
				"rows",
				{
					"lead": name,
					"lead_name": lead.lead_name,
					"lead_owner": lead.lead_owner,
					"status": lead.status,
					"score": cint(lead.lead_score),
					"bucket": bucket,
					"reason": reason,
					"assign_to": owner if bucket == CALL_AGAIN else None,
				},
			)
		plan_dates(doc.rows)
		doc.update(
			{
				"status": "Ready",
				"lead_count": len(doc.rows),
				"call_again": sum(row.bucket == CALL_AGAIN for row in doc.rows),
				"suggest_close": sum(row.bucket == SUGGEST_CLOSE for row in doc.rows),
				"skipped": len(facts.names) - len(doc.rows),
				"estimated_cost_usd": run_cost(run),
			}
		)
		doc.save(ignore_permissions=True)
	except Exception as e:
		frappe.db.rollback(save_point="tbo_lead_triage")
		frappe.log_error(title=f"TBO lead triage {run} failed")
		frappe.db.set_value(DOCTYPE, run, {"status": "Failed", "error": str(e)[:500]})


def classify(facts: LeadFacts, run: str) -> dict[str, tuple[str, str]]:
	"""{lead: (bucket, reason)} from the AI in batches; a batch that fails is skipped, and an
	answer whose reason invents a number is dropped."""
	today = str(getdate())
	results = {}
	for start_at in range(0, len(facts.names), BATCH):
		batch = facts.names[start_at : start_at + BATCH]
		lead_facts = {name: facts.facts(name) for name in batch}
		try:
			answer = ai.call_ai(
				TRIAGE_PROMPT,
				frappe.as_json({"today": today, "leads": list(lead_facts.values())}),
				"Lead triage",
				(DOCTYPE, run),
			)["response"]
		except ai.AIProviderError:
			continue
		for item in answer.get("leads") or []:
			name = item.get("id") if isinstance(item, dict) else None
			bucket = BUCKETS.get(item.get("bucket")) if name in lead_facts else None
			reason = ai.guarded(item.get("reason"), facts.known_numbers(name, today)) if bucket else None
			if bucket and reason:
				results[name] = (bucket, reason[:200])
	return results


def plan_dates(rows, start_from=None) -> None:
	"""Give each Call again row with someone to call a date: from the next working day, best
	score first, at most `capacity()` per person per day including their open follow-ups."""
	calendar = WorkCalendar()
	first = calendar.next_working_day(start_from or getdate())
	people = {row.assign_to for row in rows if row.assign_to and row.bucket == CALL_AGAIN}
	booked = existing_load(people, first)
	limit = capacity()
	for row in sorted(rows, key=lambda row: -cint(row.score)):
		if row.bucket != CALL_AGAIN or not row.assign_to:
			row.follow_up_on = None
			continue
		day = first
		while booked.get((row.assign_to, day), 0) >= limit:
			day = calendar.next_working_day(day)
		booked[(row.assign_to, day)] = booked.get((row.assign_to, day), 0) + 1
		row.follow_up_on = day


def existing_load(people: set, from_date) -> dict:
	"""{(user, date): open tasks due that day} from `from_date` on."""
	if not people:
		return {}
	Task = frappe.qb.DocType("CRM Task")
	day = Date(Task.due_date)
	rows = (
		frappe.qb.from_(Task)
		.select(Task.assigned_to, day.as_("day"), Count(Task.name))
		.where(
			Task.assigned_to.isin(list(people))
			& Task.status.notin(("Done", "Canceled"))
			& (Task.due_date >= from_date)
		)
		.groupby(Task.assigned_to, day)
	).run()
	return {(user, getdate(on)): count for user, on, count in rows}


def run_cost(run: str) -> float:
	Log = frappe.qb.DocType("TBO AI Usage Log")
	total = (
		frappe.qb.from_(Log)
		.select(Sum(Log.estimated_cost_usd))
		.where((Log.reference_doctype == DOCTYPE) & (Log.reference_name == run))
	).run()[0][0]
	return round(flt(total), 4)


def apply(run: str) -> dict:
	"""Create the follow-ups of a Ready run. Never changes a lead's status. The run row is
	locked first, so a second apply waits and then finds it Applied."""
	frappe.db.get_value(DOCTYPE, run, "status", for_update=True)
	doc = frappe.get_doc(DOCTYPE, run)
	if doc.status != "Ready":
		frappe.throw(_("Only a run that is Ready can be applied (this one is {0}).").format(doc.status))
	plan_dates(doc.rows)
	created = 0
	for row in doc.rows:
		if row.bucket != CALL_AGAIN or not row.assign_to or row.task or not row.follow_up_on:
			continue
		if frappe.db.get_value("CRM Lead", row.lead, "status") not in OPEN_STATUSES:
			continue
		task = add_follow_up_task(
			"CRM Lead",
			row.lead,
			f"{row.follow_up_on} 10:00:00",
			title=_("Call again: {0}").format(row.reason)[:140],
			assigned_to=row.assign_to,
			description=row.reason,
			task_type=FOLLOW_UP_TYPE,
		)
		row.task = task.name
		created += 1
	doc.update({"status": "Applied", "applied_by": frappe.session.user, "applied_on": now_datetime()})
	doc.save(ignore_permissions=True)
	return {"created": created}


def close_leads(run: str, leads: list[str], status: str) -> dict:
	"""A person confirms closing some Suggest close leads, under their own permissions."""
	if status not in CLOSE_STATUSES:
		frappe.throw(_("Close as Junk or Unqualified."))
	frappe.db.get_value(DOCTYPE, run, "status", for_update=True)
	doc = frappe.get_doc(DOCTYPE, run)
	rows = {row.lead: row for row in doc.rows if row.bucket == SUGGEST_CLOSE}
	closed = []
	for lead in leads:
		row = rows.get(lead)
		if not row or row.closed_as:
			continue
		set_lead_status(lead, status, _("Closed after the lead triage: {0}").format(row.reason))
		row.closed_as = status
		closed.append(lead)
	doc.save(ignore_permissions=True)
	return {"closed": closed}


TRIAGE_PROMPT = """You help TBO's sales head clean up old leads after a CRM migration.
For each lead you get JSON facts: status, source, age, last touch, status changes, notes, and what
the old CRM recorded (status, priority, project type, remarks).
Put each lead in one bucket:
- "call_again": there is any sign of interest or an unanswered question, or no information at
  all that says otherwise (when in doubt, call again);
- "suggest_close": the notes or remarks clearly say no interest, wrong or invalid number,
  duplicate, bought elsewhere, closed business, or the old status was "Do Not Contact".
A person reviews every suggestion; nothing is closed automatically.
Answer only JSON: {"leads": [{"id": "<lead id>", "bucket": "call_again" | "suggest_close",
"reason": "<one short line, at most 100 characters>"}]}.
Plain English. Never write a number, count or date that is not in the facts.
"""
