"""The AI call summary (docs/telephony-myoperator.md §5).

For an answered call on a lead, at least `call_summary_min_seconds` long, while TBO Smart
Settings → AI call summary is on: the recording is transcribed by the OpenAI section's speech
model (Malayalam, English or both; the model detects it), then one chat call sums it up: what
was discussed, interest and intent, objections, the next step and its day. The summary goes on
the call Event and the lead's timeline as TBO AI. With AI runs leads on, it also becomes the
lead's AI read (sentiment, intent, next action, the follow-up when none is planned), so the
autopilot doesn't read the lead again for this call. Each call takes one of the AI's daily lead
reads; the transcript stays on the TBO Call (System Manager only).
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, escape_html, flt, getdate, now_datetime

from tbo_smart import ai
from tbo_smart.smart_leads import ai_leads_enabled, autopilot
from tbo_smart.smart_leads.facts import LeadFacts
from tbo_smart.telephony import DEFAULT_TRANSCRIPTION_MODEL, setting, summary_enabled

DOCTYPE = "TBO Call"
TRANSCRIPT_CHARS = 12000
TEXT_CHARS = 400
OBJECTIONS = 4
DONE, SKIPPED, FAILED = "Done", "Skipped", "Failed"

SUMMARY_PROMPT = """You sum up one recorded phone call between a TBO salesperson and a lead.
TBO (Team Back Office) builds software and runs back-office services in Kerala, India.
You get JSON: today's date, the salesperson, the lead, the call's direction and length, and the
transcript (Malayalam, English or both; it may have transcription errors).
Answer only JSON, in plain English whatever the call's language:
{"summary": "<what was discussed, 1-3 short sentences>",
"language": "Malayalam" | "English" | "Mixed",
"interest": "<what they are interested in, a few words>" or null,
"sentiment": "Positive" | "Neutral" | "Negative",
"intent": "High" | "Medium" | "Low" | "None" (how likely they are to buy soon),
"objections": ["<a concern they raised, a few words>", ...at most 4],
"next_step": {"what": "<the agreed or best next step, a few words>",
  "channel": "call" | "whatsapp" | "email" | "meeting", "due": "YYYY-MM-DD", today to 30 days after
  today} or null}
Never write a number, price, count or date that was not said in the call or given in the facts,
except next_step.due.
"""


def summarise_call(call: str) -> None:
	"""Background job: transcribe and sum up the call, once."""
	doc = frappe.get_doc(DOCTYPE, call, for_update=True)
	if doc.ai_status != "Pending" or not (doc.recording and doc.lead):
		return
	if not summary_enabled() or not ai.ai_ready() or not autopilot.take_daily_slot():
		doc.db_set("ai_status", SKIPPED)
		return
	reference = ("Lead", doc.lead)
	try:
		transcript = transcribe(doc, reference)
		answer = ai.call_ai(SUMMARY_PROMPT, facts_for(doc, transcript), "Call summary", reference)["response"]
	except (ai.AIProviderError, ai.AINotConfigured):
		doc.db_set("ai_status", FAILED)
		return
	summary = build_summary(answer, doc, transcript)
	if not summary:
		doc.db_set({"ai_status": FAILED, "transcript": transcript})
		return
	doc.db_set({"ai_status": DONE, "transcript": transcript, "summary": json.dumps(summary)})
	if doc.event:
		frappe.db.set_value("Event", doc.event, "description", escape_html(summary_text(summary)))
	scheduled = feed_autopilot(doc.lead, summary)
	autopilot.log_on_timeline(doc.lead, timeline_note(summary, scheduled))


def transcribe(doc, reference: tuple) -> str:
	recording = frappe.get_doc("File", {"file_url": doc.recording, "attached_to_doctype": DOCTYPE})
	return ai.transcribe(
		recording.get_content(),
		recording.file_name,
		(setting("transcription_model") or "").strip() or DEFAULT_TRANSCRIPTION_MODEL,
		cint(doc.duration or doc.talk_seconds) / 60,
		flt(setting("transcription_price_per_minute")),
		"Call transcription",
		reference,
	)


def facts_for(doc, transcript: str) -> str:
	lead = frappe.db.get_value("Lead", doc.lead, ["lead_name", "company_name"], as_dict=True)
	return frappe.as_json(
		{
			"today": str(getdate()),
			"salesperson": frappe.utils.get_fullname(doc.agent) if doc.agent else None,
			"lead": {"name": lead.lead_name, "organization": lead.company_name},
			"direction": (doc.direction or "").lower(),
			"seconds": cint(doc.talk_seconds or doc.duration),
			"transcript": transcript[:TRANSCRIPT_CHARS],
		}
	)


def build_summary(answer: dict, doc, transcript: str) -> dict | None:
	"""The AI's summary with every text through the number guard; None when the summary itself
	didn't pass."""
	today = getdate()
	known = ai.fact_numbers(transcript, str(today), cint(doc.talk_seconds or doc.duration))
	text = guard(answer.get("summary"), known)
	if not text:
		return None
	step = answer.get("next_step") if isinstance(answer.get("next_step"), dict) else {}
	due = autopilot.action_due(step.get("due"), today)
	what = guard(step.get("what"), known | ai.fact_numbers(str(due)), 140)
	objections = answer.get("objections") if isinstance(answer.get("objections"), list) else []
	return {
		"text": text,
		"language": autopilot.pick(answer.get("language"), ("Malayalam", "English", "Mixed"), "Mixed"),
		"interest": guard(answer.get("interest"), known, 140),
		"sentiment": autopilot.pick(answer.get("sentiment"), autopilot.SENTIMENTS, "Neutral"),
		"intent": autopilot.pick(answer.get("intent"), autopilot.INTENTS, "None"),
		"objections": [item for item in (guard(o, known, 120) for o in objections[:OBJECTIONS]) if item],
		"next_step": {
			"what": what,
			"channel": step.get("channel") if step.get("channel") in autopilot.CHANNELS else "call",
			"due": str(due),
		}
		if what
		else None,
		"by": autopilot.AI_AUTHOR,
		"at": str(now_datetime()),
	}


def guard(text, known: set, limit: int = TEXT_CHARS) -> str | None:
	return autopilot.guarded(text, known, limit)


def summary_text(summary: dict) -> str:
	"""The summary as the call Event's description (what the lead's timeline shows)."""
	lines = [summary["text"]]
	if summary.get("objections"):
		lines.append(_("Concerns: {0}").format("; ".join(summary["objections"])))
	step = summary.get("next_step")
	if step:
		lines.append(_("Next: {0} (by {1})").format(step["what"], frappe.utils.formatdate(step["due"])))
	return "\n".join(lines)


def feed_autopilot(lead: str, summary: dict) -> bool:
	"""With AI runs leads on, the call's read becomes the lead's AI read (no second AI call)
	and its next step the follow-up when none is planned. Returns whether it was scheduled."""
	if not ai_leads_enabled():
		return False
	facts = LeadFacts([lead])
	if not facts.is_open(lead):
		return False
	step = summary.get("next_step")
	insight = {
		"summary": None,
		"signals": [],
		"suggested_stage": None,
		"stage_reason": None,
		"message": None,
		**(autopilot.stored_insight(lead) or {}),
		"sentiment": summary["sentiment"],
		"intent": summary["intent"],
		"next_action": {
			"what": step["what"],
			"why": _("Agreed on the call"),
			"channel": step["channel"],
			"due": step["due"],
		}
		if step
		else None,
	}
	insight["summary"] = insight["summary"] or summary["text"]
	autopilot.store_insight(lead, insight, autopilot.read_hash(facts, lead, autopilot.recent_emails(lead)))
	owner = frappe.db.get_value("Lead", lead, "lead_owner")
	return autopilot.schedule_next_action(lead, owner, insight)


def timeline_note(summary: dict, scheduled: bool) -> str:
	parts = [
		_("Summed up the call: {0} sentiment, {1} intent.").format(summary["sentiment"], summary["intent"])
	]
	step = summary.get("next_step")
	if step:
		parts.append(_("Next: {0} (by {1}).").format(step["what"], frappe.utils.formatdate(step["due"])))
		if scheduled:
			parts.append(_("Scheduled it as the follow-up."))
	return " ".join(parts)
