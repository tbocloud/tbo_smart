"""AI runs leads (docs/smart-leads.md §11): the AI reads every open lead, people approve.

Behind TBO Smart Settings → AI runs leads (`enable_ai_leads`), off by default. On the hub's
ticket pages the AI triages, sums up and suggests a reply that an agent sends; here it does
the same for a lead:

- **Insight** (`refresh_insight`), when the lead or its activity changes: a short summary, the
  customer's sentiment and buying intent read from the notes, calls and emails, the next best
  action (what, why, channel, by when) and a stage to suggest. Stored on the lead, so pages
  never wait for the AI. The next action becomes the lead's follow-up when none is planned.
- **Health** (`lead_health`): Healthy, Watch or At risk from fixed rules (last touch, the
  follow-up, sentiment, a way to reach them), like the hub's customer health. No AI.
- **Duplicates** (`find_duplicates`): other leads with the same phone, email or company.
- **Daily plan** (`daily_plan`): Call today, overdue follow-ups and hot leads, each with its
  next action.
- **Gone quiet** (`nudge_stale_leads`, daily): the AI drafts a re-engagement message for leads
  nobody touched in `lead_stale_days`, and the owner is told once.
- **Drafts** (`draft_message`): an email or WhatsApp text the salesperson edits and sends from
  their own mail or WhatsApp.

The AI only proposes. It never sends anything to a customer, never moves a lead to Won or Lost
and never changes a stage by itself: a suggested stage waits for a person's Apply. What it
did is written on the lead's timeline as an "Info" comment by "TBO AI". Every text it writes
passes the number guard (`ai.guarded`): a number that isn't in the lead's facts drops it.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.query_builder.functions import Count
from frappe.utils import add_days, cint, date_diff, getdate, now_datetime, strip_html, today

from tbo_smart import ai
from tbo_smart.lead_pipeline.statuses import OPEN_STATUSES, status_type
from tbo_smart.sales.activities import add_follow_up_task, next_follow_ups
from tbo_smart.sales.stages import set_lead_stage
from tbo_smart.smart_leads import (
	FOLLOW_UP_TYPE,
	ai_leads_enabled,
	ai_scoring_enabled,
	call_today_size,
	scoring_enabled,
	setting,
)
from tbo_smart.smart_leads.facts import LeadFacts, clip
from tbo_smart.smart_leads.notes import draft_whatsapp
from tbo_smart.smart_leads.utils import normalise_phone

AI_AUTHOR = "TBO AI"
SENTIMENTS = ("Positive", "Neutral", "Negative")
INTENTS = ("High", "Medium", "Low", "None")
CHANNELS = ("call", "whatsapp", "email", "meeting")
MESSAGE_CHANNELS = ("whatsapp", "email")
ACTION_MAX_DAYS = 30
EMAILS_PER_LEAD = 3
EMAIL_CHARS = 400
TEXT_CHARS = 220
MESSAGE_CHARS = 900
DAILY_COUNTER = "tbo_smart:ai_leads:{day}"
REFRESH_LOCK = "tbo_smart:ai_leads:refresh:{lead}"
REFRESH_COOLDOWN = 60
PLAN_HOT = 5
NUDGE_PER_OWNER = 5
NUDGE_AGAIN_DAYS = 7
DUPLICATES = 5
DEFAULT_STALE_DAYS = 21
HEALTHY, WATCH, AT_RISK = "healthy", "watch", "at_risk"
INSIGHT_FIELDS = (
	"tbo_ai_insight",
	"tbo_ai_insight_hash",
	"tbo_ai_insight_at",
	"tbo_ai_sentiment",
	"tbo_ai_intent",
	"tbo_ai_next_action",
	"tbo_ai_next_due",
	"tbo_ai_stage",
)


def stale_days() -> int:
	return cint(setting("lead_stale_days")) or DEFAULT_STALE_DAYS


def daily_limit() -> int:
	return cint(setting("ai_daily_lead_limit")) or 200


# ---------- what the AI reads ----------


def recent_emails(lead: str) -> list[dict]:
	"""The lead's newest emails, in and out, cut short: what the AI reads besides the notes."""
	Communication = frappe.qb.DocType("Communication")
	rows = (
		frappe.qb.from_(Communication)
		.select(
			Communication.sent_or_received,
			Communication.subject,
			Communication.content,
			Communication.communication_date,
		)
		.where(
			(Communication.reference_doctype == "Lead")
			& (Communication.reference_name == lead)
			& (Communication.communication_medium == "Email")
		)
		.orderby(Communication.communication_date, order=frappe.qb.desc)
		.limit(EMAILS_PER_LEAD)
	).run(as_dict=True)
	return [
		{
			"on": str(getdate(row.communication_date)),
			"direction": "from them" if row.sent_or_received == "Received" else "from us",
			"subject": clip(row.subject, 120),
			"text": clip(strip_html(row.content or ""), EMAIL_CHARS),
		}
		for row in rows
	]


def read_hash(facts: LeadFacts, lead: str, emails: list[dict]) -> str:
	"""Changes when what a person wrote or decided changes, or an email comes in."""
	payload = {"facts": facts.content_hash(lead), "emails": emails}
	return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:20]


# ---------- the insight ----------


def refresh_insight(lead: str, force: bool = False) -> dict | None:
	"""Ask the AI to read an open lead again and store what it says; schedule its next action as
	the follow-up when the lead has none. Skipped when nothing changed since the last read
	(unless `force`), for a closed lead, or past the day's AI limit. Returns the stored insight."""
	if not frappe.db.exists("Lead", lead):
		return None
	facts = LeadFacts([lead])
	if not facts.is_open(lead):
		return None
	emails = recent_emails(lead)
	digest = read_hash(facts, lead, emails)
	row = frappe.db.get_value("Lead", lead, ["tbo_ai_insight_hash", "lead_owner", "tbo_stage"], as_dict=True)
	if not force and row.tbo_ai_insight_hash == digest:
		return None
	if not take_daily_slot():
		return None
	insight = ask_insight(facts, lead, emails, row.lead_owner)
	if insight is None:
		return None
	store_insight(lead, insight, digest)
	scheduled = schedule_next_action(lead, row.lead_owner, insight)
	log_on_timeline(lead, insight_note(insight, scheduled))
	return insight


def take_daily_slot() -> bool:
	"""At most `ai_daily_lead_limit` lead reads a day for the autopilot, so cost stays capped."""
	# one atomic increment, so jobs running side by side can't all slip under the cap
	key = frappe.cache.make_key(DAILY_COUNTER.format(day=today()))
	used = frappe.cache.incr(key)
	if used == 1:
		frappe.cache.expire(key, 26 * 60 * 60)
	return used <= daily_limit()


def ask_insight(facts: LeadFacts, lead: str, emails: list[dict], owner: str | None) -> dict | None:
	"""The AI's read of the lead, every text through the number guard; None when the AI
	failed (tried again on the next change)."""
	stages = list(OPEN_STATUSES)
	now = getdate()
	sender = frappe.utils.get_fullname(owner) if owner else ""
	try:
		answer = ai.call_ai(
			INSIGHT_PROMPT.format(stages=", ".join(stages), channels=", ".join(CHANNELS)),
			frappe.as_json(
				{"today": str(now), "salesperson": sender, "lead": facts.facts(lead), "emails": emails}
			),
			"Lead insight",
			("Lead", lead),
		)["response"]
	except (ai.AIProviderError, ai.AINotConfigured):
		return None
	known = facts.known_numbers(lead, str(now), sender, emails)
	action = answer.get("next_action") if isinstance(answer.get("next_action"), dict) else {}
	due = action_due(action.get("due"), now)
	known_with_due = known | ai.fact_numbers(str(due))
	what = guarded(action.get("what"), known_with_due)
	stage = answer.get("suggested_stage")
	current = facts.lead(lead).status
	stage = stage if stage in stages and stage != current else None
	message = answer.get("message") if isinstance(answer.get("message"), dict) else {}
	message_text = guarded(message.get("text"), known, MESSAGE_CHARS)
	return {
		"summary": guarded(answer.get("summary"), known),
		"sentiment": pick(answer.get("sentiment"), SENTIMENTS, "Neutral"),
		"intent": pick(answer.get("intent"), INTENTS, "None"),
		"signals": [
			text
			for text in (guarded(signal, known, 120) for signal in (answer.get("signals") or [])[:4])
			if text
		],
		"next_action": {
			"what": what,
			"why": guarded(action.get("why"), known_with_due),
			"channel": action.get("channel") if action.get("channel") in CHANNELS else "call",
			"due": str(due),
		}
		if what
		else None,
		"suggested_stage": stage,
		"stage_reason": guarded(answer.get("stage_reason"), known) if stage else None,
		"message": {
			"channel": message.get("channel") if message.get("channel") in MESSAGE_CHANNELS else "whatsapp",
			"subject": guarded(message.get("subject"), known, 120),
			"text": message_text,
		}
		if message_text
		else None,
	}


def guarded(text, known: set, limit: int = TEXT_CHARS) -> str | None:
	"""The AI's text when every number in it is a fact, cut to `limit`; else None."""
	if not isinstance(text, str) or not text.strip():
		return None
	checked = ai.guarded(text.strip(), known)
	return checked[:limit] if checked else None


def pick(value, allowed: tuple, default: str) -> str:
	value = str(value or "").strip().title()
	return value if value in allowed else default


def action_due(value, now):
	"""The action's day: from today to 30 days on, else the next day."""
	try:
		day = getdate(str(value)[:10]) if value else None
	except Exception:
		day = None
	if day and now <= day <= getdate(add_days(now, ACTION_MAX_DAYS)):
		return day
	return getdate(add_days(now, 1))


def store_insight(lead: str, insight: dict, digest: str) -> None:
	action = insight["next_action"] or {}
	frappe.db.set_value(
		"Lead",
		lead,
		{
			"tbo_ai_insight": json.dumps(insight),
			"tbo_ai_insight_hash": digest,
			"tbo_ai_insight_at": now_datetime(),
			"tbo_ai_sentiment": insight["sentiment"],
			"tbo_ai_intent": insight["intent"],
			"tbo_ai_next_action": action.get("what"),
			"tbo_ai_next_due": action.get("due"),
			"tbo_ai_stage": insight["suggested_stage"],
		},
		update_modified=False,
	)


def schedule_next_action(lead: str, owner: str | None, insight: dict) -> bool:
	"""The next action as the owner's Sales follow-up, only when nothing is planned: a person's
	own plan always wins."""
	action = insight["next_action"]
	if not action or not owner or next_follow_ups("Lead", [lead]).get(lead):
		return False
	add_follow_up_task(
		"Lead",
		lead,
		action["due"],
		title=_("AI: {0}").format(action["what"]),
		assigned_to=owner,
		description=action.get("why"),
		task_type=FOLLOW_UP_TYPE,
	)
	return True


def insight_note(insight: dict, scheduled: bool) -> str:
	parts = [_("Read the lead: {0} sentiment, {1} intent.").format(insight["sentiment"], insight["intent"])]
	action = insight["next_action"]
	if action:
		parts.append(
			_("Suggests: {0} (by {1}).").format(action["what"], frappe.utils.formatdate(action["due"]))
		)
		if scheduled:
			parts.append(_("Scheduled it as the follow-up."))
	if insight["suggested_stage"]:
		parts.append(_("Suggests moving it to {0}.").format(insight["suggested_stage"]))
	if insight["message"]:
		parts.append(_("Drafted a message to check and send."))
	return " ".join(parts)


def log_on_timeline(lead: str, text: str) -> None:
	"""What the AI did, on the lead's timeline, by TBO AI (an Info comment: not a note, so it
	never counts as a touch or wakes the AI again)."""
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "Lead",
			"reference_name": lead,
			"content": frappe.utils.escape_html(text),
			"comment_by": AI_AUTHOR,
		}
	).insert(ignore_permissions=True)


def stored_insight(lead: str) -> dict | None:
	raw = frappe.db.get_value("Lead", lead, "tbo_ai_insight")
	try:
		return json.loads(raw) if raw else None
	except ValueError:
		return None


INSIGHT_PROMPT = """You help a TBO salesperson run one sales lead. TBO (Team Back Office) builds
software and runs back-office services in Kerala, India.
You get JSON facts about the lead: its stage, source, age, last touch, stage changes, notes and
call notes (rough, in English, Malayalam or Manglish), its newest emails, and what the old CRM
recorded before the migration.
Answer only JSON:
{{"summary": "<who they are, what they want, where it stands; at most 2 short sentences>",
"sentiment": "Positive" | "Neutral" | "Negative" (how the customer feels, from their words),
"intent": "High" | "Medium" | "Low" | "None" (how likely they are to buy soon),
"signals": ["<a short phrase from the notes or emails that shows it>", ...at most 4],
"next_action": {{"what": "<the one next step, a few words>", "why": "<one short line>",
  "channel": one of {channels}, "due": "YYYY-MM-DD", today to 30 days after today}},
"suggested_stage": one of [{stages}] when the notes show the lead has moved on, else null,
"stage_reason": "<why, one short line>" or null,
"message": {{"channel": "whatsapp" | "email", "subject": "<for email>",
  "text": "<2-4 sentences from the salesperson to the lead for the next step, warm, no emojis,
  signed with the salesperson's first name>"}} or null when a call or meeting comes first}}
Never suggest closing the lead as won or lost: people decide that.
Write in plain English. Never write a number, price, count or date that is not in the facts,
except next_action.due.
"""


# ---------- health (no AI) ----------


def lead_health(facts: LeadFacts, lead: str, sentiment: str | None) -> dict:
	"""{status, signals}: Healthy, Watch or At risk from fixed rules, like the hub's customer
	health. An At risk signal, or two Watch ones, make the lead At risk."""
	signals = []
	days = facts.days_since_touch(lead)
	quiet = days if days is not None else date_diff(facts.today, facts.created_on(lead))
	limit = stale_days()
	if quiet >= limit:
		signals.append({"key": "touch", "state": AT_RISK, "text": _("No touch for {0} days").format(quiet)})
	elif quiet >= limit // 2:
		signals.append({"key": "touch", "state": WATCH, "text": _("No touch for {0} days").format(quiet)})
	follow_up = facts.next_follow_up(lead)
	if not follow_up:
		signals.append({"key": "follow_up", "state": WATCH, "text": _("No next follow-up planned")})
	else:
		late = date_diff(facts.today, follow_up.due_date)
		if late >= 3:
			signals.append(
				{"key": "follow_up", "state": AT_RISK, "text": _("Follow-up {0} days overdue").format(late)}
			)
		elif late >= 1:
			signals.append({"key": "follow_up", "state": WATCH, "text": _("Follow-up overdue")})
	if sentiment == "Negative":
		signals.append({"key": "sentiment", "state": WATCH, "text": _("The customer sounds unhappy")})
	lead_row = facts.lead(lead)
	if not (lead_row.mobile_no or lead_row.phone or lead_row.email):
		signals.append({"key": "contact", "state": AT_RISK, "text": _("No phone or email")})
	states = [signal["state"] for signal in signals]
	status = (
		AT_RISK if AT_RISK in states or states.count(WATCH) >= 2 else WATCH if WATCH in states else HEALTHY
	)
	return {"status": status, "signals": signals}


# ---------- duplicates (no AI) ----------


def find_duplicates(lead: str) -> list[dict]:
	"""Other leads the session user may see with the same mobile (last ten digits), email or
	company name: likely the same customer."""
	row = frappe.db.get_value(
		"Lead", lead, ["mobile_no", "phone", "whatsapp_no", "email_id", "company_name"], as_dict=True
	)
	digits = {
		normalise_phone(number)[-10:]
		for number in (row.mobile_no, row.phone, row.whatsapp_no)
		if normalise_phone(number)
	}
	matches = []
	for number in digits:
		matches += [["mobile_no", "like", f"%{number}"], ["phone", "like", f"%{number}"]]
	if row.email_id:
		matches.append(["email_id", "=", row.email_id])
	company = (row.company_name or "").strip()
	if len(company) >= 4:
		matches.append(["company_name", "=", company])
	if not matches:
		return []
	rows = frappe.get_list(
		"Lead",
		filters={"name": ("!=", lead)},
		or_filters=matches,
		fields=[
			"name",
			"lead_name",
			"company_name",
			"tbo_stage",
			"lead_owner",
			"mobile_no",
			"phone",
			"email_id",
		],
		order_by="modified desc",
		limit_page_length=DUPLICATES,
	)
	result = []
	for other in rows:
		why = []
		other_digits = {
			normalise_phone(number)[-10:]
			for number in (other.mobile_no, other.phone)
			if normalise_phone(number)
		}
		if digits & other_digits:
			why.append("phone")
		if row.email_id and (other.email_id or "").lower() == row.email_id.lower():
			why.append("email")
		if company and (other.company_name or "").strip().lower() == company.lower():
			why.append("company")
		result.append(
			{
				"name": other.name,
				"lead_name": other.lead_name,
				"company_name": other.company_name,
				"tbo_stage": other.tbo_stage,
				"lead_owner": other.lead_owner,
				"owner_name": frappe.utils.get_fullname(other.lead_owner) if other.lead_owner else None,
				"match": why,
			}
		)
	return result


# ---------- one lead, for the hub ----------


def lead_ai(lead: str) -> dict:
	"""Everything the hub's "AI suggests" block shows for a lead the session user may read."""
	frappe.has_permission("Lead", "read", lead, throw=True)
	facts = LeadFacts([lead])
	row = frappe.db.get_value(
		"Lead",
		lead,
		[
			"tbo_ai_insight_at",
			"tbo_ai_insight_hash",
			"tbo_ai_sentiment",
			"tbo_score",
			"tbo_temperature",
			"tbo_score_reason",
		],
		as_dict=True,
	)
	contact = frappe.db.get_value(
		"Lead", lead, ["mobile_no", "whatsapp_no", "phone", "email_id"], as_dict=True
	)
	insight = stored_insight(lead)
	if insight and insight.get("suggested_stage") and insight["suggested_stage"] == facts.lead(lead).status:
		insight["suggested_stage"] = None
	return {
		"enabled": ai_leads_enabled(),
		"ai_ready": ai.ai_ready(),
		"open": facts.is_open(lead),
		"insight": insight,
		"at": row.tbo_ai_insight_at,
		"stale": bool(insight) and row.tbo_ai_insight_hash != read_hash(facts, lead, recent_emails(lead)),
		"score": {
			"score": cint(row.tbo_score),
			"temperature": row.tbo_temperature,
			"reason": row.tbo_score_reason,
		}
		if scoring_enabled()
		else None,
		"health": lead_health(facts, lead, row.tbo_ai_sentiment) if facts.is_open(lead) else None,
		"duplicates": find_duplicates(lead),
		# where the person sends a draft from their own WhatsApp or mail
		"contact": {
			"whatsapp": next(
				(
					normalise_phone(n)
					for n in (contact.mobile_no, contact.whatsapp_no, contact.phone)
					if normalise_phone(n)
				),
				None,
			),
			"email": contact.email_id,
		},
	}


def refresh_now(lead: str) -> None:
	"""A person asked for a fresh read (needs write on the lead, once a minute per lead)."""
	frappe.has_permission("Lead", "write", lead, throw=True)
	key = REFRESH_LOCK.format(lead=lead)
	if frappe.cache.get_value(key):
		frappe.throw(_("The AI read this lead less than a minute ago."))
	frappe.cache.set_value(key, 1, expires_in_sec=REFRESH_COOLDOWN)
	if refresh_insight(lead, force=True) is None and frappe.db.exists("Lead", lead):
		frappe.throw(_("The AI couldn't read this lead right now. Try again later."))


def apply_stage(lead: str) -> None:
	"""Move the lead to the stage the AI suggested, as the session user. Won and Lost are never
	suggested and never applied here."""
	suggested = (stored_insight(lead) or {}).get("suggested_stage")
	if not suggested or status_type(suggested) in ("Won", "Lost"):
		frappe.throw(_("There is no stage suggestion to apply."))
	set_lead_stage(lead, suggested)
	clear_stage(lead)


def clear_stage(lead: str) -> None:
	"""Drop the stage suggestion (applied or dismissed); needs write on the lead."""
	frappe.has_permission("Lead", "write", lead, throw=True)
	insight = stored_insight(lead)
	if insight:
		insight["suggested_stage"], insight["stage_reason"] = None, None
		frappe.db.set_value(
			"Lead", lead, {"tbo_ai_insight": json.dumps(insight), "tbo_ai_stage": None}, update_modified=False
		)


def draft_message(lead: str, channel: str, language: str = "en") -> dict:
	"""A message for the salesperson to edit and send themselves: {channel, subject, text, url}
	(a wa.me link or none). Nothing is sent."""
	if channel not in MESSAGE_CHANNELS:
		frappe.throw(_("channel must be whatsapp or email."))
	frappe.has_permission("Lead", "read", lead, throw=True)
	if channel == "whatsapp":
		result = draft_whatsapp(lead, language)
		return {"channel": channel, "subject": None, "text": result["message"], "url": result["url"]}
	facts = LeadFacts([lead])
	email = frappe.db.get_value("Lead", lead, "email_id")
	if not email:
		frappe.throw(_("This lead has no email address."))
	sender = frappe.utils.get_fullname(frappe.session.user)
	emails = recent_emails(lead)
	answer = ai.call_ai(
		EMAIL_PROMPT,
		frappe.as_json(
			{"today": str(getdate()), "from": sender, "lead": facts.facts(lead), "emails": emails}
		),
		"Lead email draft",
		("Lead", lead),
	)["response"]
	known = facts.known_numbers(lead, sender, str(getdate()), emails)
	text = guarded(answer.get("text"), known, MESSAGE_CHARS * 2)
	if not text:
		frappe.throw(_("The AI's draft had details that aren't on the lead. Try again."))
	return {
		"channel": channel,
		"subject": guarded(answer.get("subject"), known, 120) or "",
		"text": text,
		"to": email,
		"url": None,
	}


EMAIL_PROMPT = """You write a short email from a TBO salesperson to a lead.
TBO (Team Back Office) builds software and runs back-office services in Kerala, India.
Use the lead's facts and emails to pick up where things were left (a demo, a quote, a question).
Keep it to 3-6 sentences, plain and warm, no emojis, no links, signed with the salesperson's
first name. Answer only JSON: {"subject": "...", "text": "..."}.
Never write a number, price, count or date that is not in the facts.
"""


# ---------- the daily plan ----------


def daily_plan(user: str) -> dict:
	"""The person's plan for today: their Call today list (with lead scoring on), else their
	follow-ups due by today, then their hottest open leads, each with the AI's next action."""
	from tbo_smart.smart_leads.call_today import BEST, DUE_TODAY, OVERDUE, build_call_today

	reasons = {
		OVERDUE: _("Follow-up overdue"),
		DUE_TODAY: _("Follow-up due today"),
		BEST: _("Best score, nothing planned"),
	}
	names, why = [], {}
	if scoring_enabled():
		for row in build_call_today(user, size=call_today_size())["data"]:
			names.append(row["id"])
			why[row["id"]] = reasons.get(row["why"]) or row.get("reason")
	else:
		ToDo = frappe.qb.DocType("ToDo")
		for lead, due in (
			frappe.qb.from_(ToDo)
			.select(ToDo.reference_name, ToDo.date)
			.where(
				(ToDo.reference_type == "Lead")
				& (ToDo.allocated_to == user)
				& (ToDo.status == "Open")
				& (ToDo.date <= getdate())
			)
			.orderby(ToDo.date)
			.limit(call_today_size())
		).run():
			if lead not in why:
				names.append(lead)
				why[lead] = (
					_("Follow-up was due {0}").format(frappe.utils.formatdate(due))
					if getdate(due) < getdate()
					else _("Follow-up due today")
				)
	hot = frappe.get_list(
		"Lead",
		filters={
			"lead_owner": user,
			"tbo_stage": ("in", OPEN_STATUSES),
			"tbo_temperature": "Hot",
			"name": ("not in", names or [""]),
		},
		pluck="name",
		order_by="tbo_score desc",
		limit_page_length=PLAN_HOT,
	)
	for name in hot:
		names.append(name)
		why[name] = _("Hot lead")
	if not names:
		return {"user": user, "rows": []}
	rows = frappe.get_list(
		"Lead",
		filters={"name": ("in", names)},
		fields=[
			"name",
			"lead_name",
			"company_name",
			"tbo_stage",
			"tbo_score",
			"tbo_temperature",
			"mobile_no",
			"tbo_ai_next_action",
			"tbo_ai_next_due",
			"tbo_ai_intent",
		],
	)
	by_name = {row.name: row for row in rows}
	return {
		"user": user,
		"rows": [
			{
				"name": name,
				"lead_name": by_name[name].lead_name,
				"company_name": by_name[name].company_name,
				"tbo_stage": by_name[name].tbo_stage,
				"score": cint(by_name[name].tbo_score),
				"temperature": by_name[name].tbo_temperature,
				"why": why.get(name),
				"next_action": by_name[name].tbo_ai_next_action,
				"next_due": by_name[name].tbo_ai_next_due,
				"intent": by_name[name].tbo_ai_intent,
			}
			for name in names
			if name in by_name
		],
	}


def overview_ai(owners: list[str] | None, user: str) -> dict:
	"""The overview's AI part: the person's plan for today and what the AI sees across the
	scope's open leads (high intent, unhappy customers, stage moves waiting for a person)."""
	if not ai_leads_enabled():
		return {"enabled": False}
	from tbo_smart.lead_pipeline.overview import AI_METRICS

	Lead = frappe.qb.DocType("Lead")
	criterion = Lead.tbo_stage.isin(OPEN_STATUSES)
	if owners is not None:
		criterion &= Lead.lead_owner.isin(owners or [""])

	def count(extra) -> int:
		return cint((frappe.qb.from_(Lead).select(Count(Lead.name)).where(criterion & extra)).run()[0][0])

	return {
		"enabled": True,
		"ai_ready": ai.ai_ready(),
		"plan": daily_plan(user),
		**{metric: count(rule(Lead)) for metric, rule in AI_METRICS.items()},
		"read": count(Lead.tbo_ai_insight_at.isnotnull()),
	}


# ---------- when leads change, and every morning ----------


def queue_insight(lead: str | None) -> None:
	if lead and ai_leads_enabled() and not frappe.flags.in_import:
		frappe.enqueue(
			"tbo_smart.smart_leads.autopilot.insight_job",
			lead=lead,
			queue="short",
			job_id=f"tbo-smart-ai-lead-{lead}",
			deduplicate=True,
			enqueue_after_commit=True,
			now=frappe.flags.in_test,
		)


def insight_job(lead: str) -> None:
	"""Background job: score the lead (its base score; the AI adjustment stays nightly, inside its
	own cap) and read it."""
	if not ai_leads_enabled() or not ai.ai_ready():
		return
	if scoring_enabled():
		from tbo_smart.smart_leads.scoring import score_leads

		score_leads([lead])
	refresh_insight(lead)


def lead_changed(doc, method=None) -> None:
	"""doc_events: Lead after_insert and on_update."""
	if method == "after_insert" or doc.has_value_changed("tbo_stage") or doc.has_value_changed("lead_owner"):
		queue_insight(doc.name)


def activity_changed(doc, method=None) -> None:
	"""doc_events: a note (Comment), a call or logged activity (Event) or an email
	(Communication) on a lead."""
	if doc.doctype == "Comment":
		if doc.reference_doctype == "Lead" and doc.comment_type == "Comment":
			queue_insight(doc.reference_name)
	elif doc.doctype == "Communication":
		if doc.reference_doctype == "Lead" and doc.communication_medium == "Email":
			queue_insight(doc.reference_name)
	elif doc.doctype == "Event":
		for row in doc.get("event_participants") or []:
			if row.reference_doctype == "Lead":
				queue_insight(row.reference_docname)


def nudge_stale_leads() -> dict | None:
	"""Daily: for open leads nobody touched in `lead_stale_days` with no follow-up planned, the
	AI drafts a re-engagement message (stored on the lead) and each owner is told once, with how
	many. Each lead at most once a week, a few per owner a day; nothing goes to the customer."""
	if not ai_leads_enabled() or not ai.ai_ready():
		return None
	from tbo_smart.smart_leads.notify import notify

	cutoff = getdate(add_days(getdate(), -NUDGE_AGAIN_DAYS))
	candidates = frappe.get_all(
		"Lead",
		filters={
			"tbo_stage": ("in", OPEN_STATUSES),
			"lead_owner": ("is", "set"),
		},
		or_filters=[["tbo_ai_nudged_on", "is", "not set"], ["tbo_ai_nudged_on", "<", cutoff]],
		fields=["name", "lead_owner"],
		order_by="tbo_score desc",
		limit_page_length=0,
	)
	facts = LeadFacts([row.name for row in candidates])
	limit = stale_days()
	per_owner: dict[str, list[str]] = {}
	for row in candidates:
		days = facts.days_since_touch(row.name)
		quiet = days if days is not None else date_diff(facts.today, facts.created_on(row.name))
		if quiet < limit or facts.next_follow_up(row.name):
			continue
		picked = per_owner.setdefault(row.lead_owner, [])
		if len(picked) < NUDGE_PER_OWNER:
			picked.append(row.name)
	nudged = 0
	for owner, leads in per_owner.items():
		drafted = []
		for lead in leads:
			if refresh_insight(lead, force=True) is None:
				continue  # the AI failed or the day's limit is reached: tried again tomorrow
			frappe.db.set_value("Lead", lead, "tbo_ai_nudged_on", getdate(), update_modified=False)
			drafted.append(lead)
		if drafted:
			nudged += len(drafted)
			notify(
				[owner],
				_(
					"{0} of your leads have gone quiet. AI drafted what to send; check it and press Send."
				).format(len(drafted)),
				"Lead",
				drafted[0],
			)
	return {"nudged": nudged, "owners": len(per_owner)}
