"""Lead scoring: a deterministic base score, an optional AI adjustment, Hot / Warm / Cold
(docs/smart-leads.md §4).

The score goes into the Lead's `tbo_score` (0-100) and the band into its `tbo_temperature`
(Cold / Warm / Hot), with a one-line reason in `tbo_score_reason`. The lead facts read them
back as `lead_score` and `lead_temperature`, the names they had on Frappe CRM's lead.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, getdate, now_datetime

from tbo_smart import ai
from tbo_smart.lead_pipeline.statuses import (
	CONTACTED,
	FIRST_MEETING,
	FOLLOW_UP_REQUIRED,
	MEETING_SCHEDULED,
	NEGOTIATION,
	NEW_LEAD,
	ON_HOLD,
	POSITIVE,
	PROPOSAL_SENT,
	SECOND_MEETING,
	STATUS_NAMES,
	THIRD_MEETING,
	WON_STATUSES,
)
from tbo_smart.sales.opportunity import leads_with_opportunity
from tbo_smart.smart_leads import OPEN_STATUSES, ai_scoring_enabled, scoring_enabled, setting
from tbo_smart.smart_leads.facts import LeadFacts
from tbo_smart.smart_leads.migration import get_migration_notes, original_status_points, priority_points

HOT, WARM, COLD = "Hot", "Warm", "Cold"
HOT_FROM, WARM_FROM = 55, 30
# further along the pipeline scores higher (docs/lead-pipeline.md §2); On Hold is parked
STATUS_POINTS = {
	NEW_LEAD: 10,
	CONTACTED: 18,
	FOLLOW_UP_REQUIRED: 16,
	POSITIVE: 22,
	ON_HOLD: 4,
	MEETING_SCHEDULED: 24,
	FIRST_MEETING: 26,
	SECOND_MEETING: 28,
	THIRD_MEETING: 30,
	PROPOSAL_SENT: 32,
	NEGOTIATION: 34,
}
# fresh leads convert best; the migrated ones count from their original created date
AGE_POINTS = ((7, 12), (30, 8), (90, 4), (365, 1))
TOUCH_POINTS = ((3, 15), (7, 10), (14, 6), (30, 3))
RATE_POINTS = 12
PROJECT_TYPE_POINTS = 6
FORWARD_POINTS, BACKWARD_POINTS = 8, -3
RECENT_CHANGE_DAYS = 30
NO_CONTACT_POINTS = -20
OVERDUE_POINTS = 5
AI_LIMIT = 15
MIN_SAMPLE = 10
RATES_CACHE_KEY = "tbo_smart:lead_conversion_rates"
RATES_CACHE_SECONDS = 6 * 60 * 60
AI_BATCH = 10
SCORE_FIELDS = ("lead_score", "lead_temperature", "tbo_score_reason")
# the Lead's own fields for the facts' names
STORED_AS = {"lead_score": "tbo_score", "lead_temperature": "tbo_temperature"}


def band(score: int) -> str:
	return HOT if score >= HOT_FROM else WARM if score >= WARM_FROM else COLD


def points_for(days: int | None, steps: tuple) -> int:
	if days is None:
		return 0
	return next((points for limit, points in steps if days <= limit), 0)


# ---------- conversion rates on this site ----------


def conversion_rates() -> dict:
	"""{"source": {name: rate}, "project_type": {name: rate}}: share of leads converted to a
	deal (or Closed – Won, as the migrated Converted ones are), for values with at least
	MIN_SAMPLE leads. Cached 6 hours."""
	rates = frappe.cache.get_value(RATES_CACHE_KEY, expires=True)
	if rates is None:
		rates = compute_conversion_rates()
		frappe.cache.set_value(RATES_CACHE_KEY, rates, expires_in_sec=RATES_CACHE_SECONDS)
	return rates


def compute_conversion_rates() -> dict:
	leads = frappe.get_all("Lead", fields=["name", "source", "tbo_stage as status"], limit_page_length=0)
	notes = get_migration_notes([lead.name for lead in leads])
	converted = leads_with_opportunity([lead.name for lead in leads])
	totals: dict[str, dict[str, list[int]]] = {"source": {}, "project_type": {}}
	for lead in leads:
		won = int(lead.name in converted or lead.status in WON_STATUSES)
		for kind, value in (
			("source", lead.source),
			("project_type", notes.get(lead.name, {}).get("project_type")),
		):
			if value:
				count = totals[kind].setdefault(value.strip().lower(), [0, 0])
				count[0] += 1
				count[1] += won
	return {
		kind: {value: round(won / total, 3) for value, (total, won) in counts.items() if total >= MIN_SAMPLE}
		for kind, counts in totals.items()
	}


def rate_points(value: str | None, rates: dict, most: int) -> tuple[int, float | None]:
	"""Points for a value's conversion rate against the best value's, and the rate."""
	rate = rates.get((value or "").strip().lower())
	best = max(rates.values(), default=0)
	if rate is None or not best:
		return 0, rate
	return round(most * rate / best), rate


# ---------- the deterministic score ----------


def base_score(facts: LeadFacts, name: str, rates: dict, status_positions: dict) -> tuple[int, list[str]]:
	"""(score 0-100, reasons best first) from the lead's status and its changes, its source's and
	project type's conversion rates here, its last touch, its age, its status and priority
	before the migration, an overdue follow-up and whether it can be contacted at all."""
	lead = facts.lead(name)
	if not facts.is_open(name):
		return 0, [_("Closed ({0})").format(lead.status)]
	original = facts.migration.get(name, {})
	parts: list[tuple[int, str]] = [(STATUS_POINTS.get(lead.status, 10), lead.status)]

	changes = facts.status_changes.get(name) or []
	if changes and date_diff(facts.today, changes[-1].from_date) <= RECENT_CHANGE_DAYS:
		previous = changes[-2].status if len(changes) > 1 else None
		before = status_positions.get(previous) if previous else None
		now_at = status_positions.get(lead.status)
		if before is not None and now_at is not None and now_at < before:
			parts.append((BACKWARD_POINTS, _("moved back to {0}").format(lead.status)))
		else:
			parts.append((FORWARD_POINTS, _("status changed recently")))

	points, rate = rate_points(lead.source, rates.get("source", {}), RATE_POINTS)
	if rate is not None:
		parts.append((points, _("{0} leads convert {1}%").format(lead.source, round(rate * 100))))
	points, rate = rate_points(
		original.get("project_type"), rates.get("project_type", {}), PROJECT_TYPE_POINTS
	)
	if rate is not None:
		parts.append(
			(points, _("{0} projects convert {1}%").format(original["project_type"], round(rate * 100)))
		)

	days = facts.days_since_touch(name)
	if days is not None:
		label = _("touched today") if days == 0 else _("last touch {0} days ago").format(days)
		parts.append((points_for(days, TOUCH_POINTS), label))
	else:
		parts.append((0, _("no touch since it came in")))

	age = date_diff(facts.today, facts.created_on(name))
	parts.append(
		(points_for(age, AGE_POINTS), _("new this week") if age <= 7 else _("{0} days old").format(age))
	)

	if original.get("status"):
		points = original_status_points(original["status"])
		if points:
			parts.append((points, _("was {0} before the migration").format(original["status"])))
	points = priority_points(original.get("priority"))
	if points:
		parts.append((points, _("{0} priority").format(original["priority"])))

	follow_up = facts.next_follow_up(name)
	if follow_up and getdate(follow_up.due_date) < facts.today:
		parts.append((OVERDUE_POINTS, _("follow-up overdue")))
	if not (lead.mobile_no or lead.phone or lead.email):
		parts.append((NO_CONTACT_POINTS, _("no phone or email")))

	score = max(0, min(100, sum(points for points, _label in parts)))
	# the status first, then what moved the score most
	reasons = [parts[0][1]] + [
		label for points, label in sorted(parts[1:], key=lambda p: -abs(p[0])) if points
	]
	return score, reasons


def reason_line(reasons: list[str], limit: int = 4) -> str:
	return " · ".join(reasons[:limit])


def status_positions() -> dict:
	return {name: position for position, name in enumerate(STATUS_NAMES, start=1)}


# ---------- writing ----------


def lead_score(facts: LeadFacts, name: str, rates: dict, positions: dict) -> tuple[int, str, str]:
	"""(score, band, reason) now: the base score plus the lead's last AI adjustment. The AI's
	reason comes first when there is one, then the main facts."""
	score, reasons = base_score(facts, name, rates, positions)
	lead = facts.lead(name)
	if not facts.is_open(name):
		return score, band(score), reason_line(reasons)
	score = max(0, min(100, score + cint(lead.tbo_ai_adjust)))
	reason = reason_line([lead.tbo_ai_reason, *reasons[:2]] if lead.tbo_ai_reason else reasons)
	return score, band(score), reason


def score_leads(names: list[str], use_ai: bool = False, ai_limit: int = 0) -> dict:
	"""Score these leads and write the changed ones. With `use_ai`, up to `ai_limit` leads whose
	notes or status changed since their last AI read are sent to the AI in batches.
	Returns {"scored": n, "ai": n}."""
	facts = LeadFacts(names)
	rates = conversion_rates()
	positions = status_positions()
	ai_results = score_with_ai(facts, ai_limit) if use_ai and ai_limit else {}
	written = 0
	for name in facts.names:
		lead = facts.lead(name)
		values = {}
		if name in ai_results:
			adjust, reason = ai_results[name]
			values = {
				"tbo_ai_adjust": adjust,
				"tbo_ai_reason": reason,
				"tbo_ai_hash": facts.content_hash(name),
			}
		current = {field: lead.get(field) for field in SCORE_FIELDS}
		lead.update(values)
		score, temperature, reason = lead_score(facts, name, rates, positions)
		values.update({"lead_score": score, "lead_temperature": temperature, "tbo_score_reason": reason})
		if any(values[field] != current[field] for field in SCORE_FIELDS) or name in ai_results:
			stored = {STORED_AS.get(field, field): value for field, value in values.items()}
			frappe.db.set_value(
				"Lead", name, {**stored, "tbo_scored_at": now_datetime()}, update_modified=False
			)
			written += 1
	return {"scored": written, "ai": sum(1 for _adjust, reason in ai_results.values() if reason)}


def score_with_ai(facts: LeadFacts, limit: int) -> dict[str, tuple[int, str | None]]:
	"""{lead: (adjust, reason)} for open leads whose content changed since the last AI read,
	at most `limit`, `AI_BATCH` per call. Every lead of a batch that got an answer counts as
	read, so it isn't sent (and billed) again until it changes: a reason with a number not in
	the lead's facts, an empty one or a missing row gives (0, None). A failed batch is
	skipped: those leads keep their last AI read and are tried again tomorrow."""
	due = [
		name
		for name in facts.names
		if facts.is_open(name) and facts.lead(name).tbo_ai_hash != facts.content_hash(name)
	]
	results = {}
	for start in range(0, min(len(due), limit), AI_BATCH):
		batch = due[start : min(start + AI_BATCH, limit)]
		lead_facts = {name: facts.facts(name) for name in batch}
		try:
			answer = ai.call_ai(
				AI_SCORE_PROMPT,
				frappe.as_json({"today": str(facts.today), "leads": list(lead_facts.values())}),
				"Lead scoring",
			)["response"]
		except ai.AIProviderError:
			continue
		results.update({name: (0, None) for name in batch})
		for item in answer.get("leads") or []:
			name = item.get("id") if isinstance(item, dict) else None
			if name not in lead_facts:
				continue
			reason = ai.guarded(item.get("reason"), facts.known_numbers(name, str(facts.today)))
			if reason is None:
				continue
			adjust = max(-AI_LIMIT, min(AI_LIMIT, cint(item.get("adjust"))))
			results[name] = (adjust, reason[:140])
	return results


AI_SCORE_PROMPT = """You help TBO's sales team in Kerala decide whom to call today.
You get JSON facts about a few leads: status, source, age, last touch, status changes, notes and
calls since, and remarks from before the CRM migration.
For each lead, read the notes and remarks and decide how much they should move its priority:
up for buying intent (asked for a quote, demo or price, has a budget or a timeline, a referral),
down for no intent (not interested, wrong number, bought elsewhere, closed business, unreachable
many times). No signal in the notes: 0.
Answer only JSON: {"leads": [{"id": "<lead id>", "adjust": <integer from -15 to 15>,
"reason": "<one short line for the salesperson, at most 90 characters>"}]}.
Write the reason in plain English. Never write a number, count or date that is not in the facts.
"""


def rescore_lead(lead: str) -> None:
	"""Background job: the lead or its activity changed."""
	if scoring_enabled() and frappe.db.exists("Lead", lead):
		score_leads([lead])


def queue_rescore(lead: str | None) -> None:
	# an import (the CRM cutover) waits for the nightly score
	if lead and scoring_enabled() and not frappe.flags.in_import:
		frappe.enqueue(
			"tbo_smart.smart_leads.scoring.rescore_lead",
			lead=lead,
			queue="short",
			job_id=f"tbo-smart-rescore-{lead}",
			deduplicate=True,
			enqueue_after_commit=True,
			now=frappe.flags.in_test,
		)


def lead_changed(doc, method=None) -> None:
	"""doc_events: Lead on_update."""
	if doc.has_value_changed("tbo_stage") or doc.has_value_changed("lead_owner"):
		queue_rescore(doc.name)


def activity_changed(doc, method=None) -> None:
	"""doc_events: Comment after_insert, Event after_insert, ToDo on_update: a note, a call or
	a follow-up on a lead."""
	if doc.doctype == "Comment" and doc.reference_doctype == "Lead" and doc.comment_type == "Comment":
		queue_rescore(doc.reference_name)
	elif doc.doctype == "ToDo" and doc.reference_type == "Lead":
		queue_rescore(doc.reference_name)
	elif doc.doctype == "Event":
		for row in doc.get("event_participants") or []:
			if row.reference_doctype == "Lead":
				queue_rescore(row.reference_docname)


def score_all() -> None:
	"""Daily: every open lead, plus the AI read of those whose notes changed (capped)."""
	if not scoring_enabled():
		return
	names = frappe.get_all(
		"Lead",
		filters={"tbo_stage": ("in", OPEN_STATUSES)},
		pluck="name",
		order_by="modified desc",
		limit_page_length=0,
	)
	frappe.cache.delete_value(RATES_CACHE_KEY)
	use_ai = ai_scoring_enabled() and ai.ai_ready()
	score_leads(names, use_ai=use_ai, ai_limit=cint(setting("ai_daily_lead_limit")) if use_ai else 0)
