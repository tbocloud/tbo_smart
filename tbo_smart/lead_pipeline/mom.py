"""Minutes of meeting: saving, submitting and the AI draft (docs/lead-pipeline.md §5.3).

The AI only proposes: "Draft MOM from my notes" turns rough English or Malayalam notes into
the MOM fields, and the BDE edits and submits them. Every text the AI writes passes the number
guard (tbo_smart.ai.guarded): a field with a number that isn't in the notes, the meeting or the
lead's facts is dropped, with a warning.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime, strip_html

from tbo_smart import ai
from tbo_smart.lead_pipeline.meetings import FINAL, MOM_SUBMITTED
from tbo_smart.smart_leads.facts import LeadFacts

TEXT_FIELDS = (
	"agenda",
	"discussion_points",
	"client_requirements",
	"decisions",
	"objections",
	"quotation_feedback",
	"next_meeting_details",
)
MOM_FIELDS = (*TEXT_FIELDS, "client_interest", "next_follow_up_on")
INTERESTS = ("Positive", "Neutral", "Negative")
MAX_TEXT = 4000
MAX_ACTIONS = 30
MAX_NOTES = 6000
FOLLOW_UP_MAX_DAYS = 180


def apply_mom(doc, values: dict) -> None:
	"""Copy the MOM fields and action items from `values` onto the meeting (nothing saved)."""
	for field in TEXT_FIELDS:
		if field in values:
			doc.set(field, clean_text(values.get(field)))
	if "client_interest" in values:
		interest = values.get("client_interest") or None
		if interest and interest not in INTERESTS:
			frappe.throw(_("Client interest is Positive, Neutral or Negative."))
		doc.client_interest = interest
	if "next_follow_up_on" in values:
		day = values.get("next_follow_up_on") or None
		if day and getdate(day) < getdate():
			frappe.throw(_("The next follow-up can't be in the past."))
		doc.next_follow_up_on = day
	if "action_items" in values:
		items = values.get("action_items") or []
		if not isinstance(items, list) or len(items) > MAX_ACTIONS:
			frappe.throw(_("At most {0} action items.").format(MAX_ACTIONS))
		doc.set("action_items", [])
		for row in items:
			item = clean_text((row or {}).get("item"))
			if not item:
				continue
			doc.append(
				"action_items",
				{
					"item": item[:140],
					"responsible": clean_text(row.get("responsible"))[:140]
					if row.get("responsible")
					else None,
					"due_date": row.get("due_date") or None,
					"done": cint(row.get("done")),
				},
			)


def clean_text(value) -> str | None:
	text = str(value or "").strip()
	return text[:MAX_TEXT] or None


def validate_submit(doc, has_next_meeting: bool, closing: bool) -> None:
	"""A MOM is complete with what was discussed and a next step: a follow-up date, a next
	meeting, or the lead closing."""
	if doc.status in FINAL:
		frappe.throw(_("This meeting is {0}.").format(_(doc.status)))
	if doc.scheduled_at and frappe.utils.get_datetime(doc.scheduled_at) > now_datetime():
		frappe.throw(_("The meeting hasn't started yet. Submit its MOM after it."))
	if not doc.discussion_points:
		frappe.throw(_("Write the key discussion points."))
	if not (doc.next_follow_up_on or has_next_meeting or closing):
		frappe.throw(_("Set the next follow-up date or schedule the next meeting."))


def mark_submitted(doc) -> None:
	doc.status = MOM_SUBMITTED
	doc.prepared_by = frappe.session.user
	doc.submitted_on = now_datetime()


# ---------- the AI draft ----------


def draft_mom(doc, notes: str) -> dict:
	"""The AI's MOM from rough notes: {fields, action_items, warnings}. Nothing is written."""
	notes = (notes or "").strip()[:MAX_NOTES]
	if not notes:
		frappe.throw(_("Write your notes from the meeting first."))
	facts = LeadFacts([doc.lead])
	today = getdate()
	meeting = {
		"meeting": doc.stage_label,
		"on": str(getdate(doc.scheduled_at)),
		"type": doc.meeting_type,
		"participants": doc.participants,
		"agenda": doc.agenda,
	}
	answer = ai.call_ai(
		MOM_PROMPT,
		frappe.as_json(
			{"today": str(today), "rough_notes": notes, "meeting": meeting, "lead": facts.facts(doc.lead)}
		),
		"MOM draft",
		("TBO Lead Meeting", doc.name),
	)["response"]
	known = facts.known_numbers(doc.lead, notes, meeting, str(today))
	fields, dropped = {}, []
	for field in TEXT_FIELDS:
		raw = answer.get(field)
		if not raw:
			continue
		text = ai.guarded(raw, known)
		if text:
			fields[field] = text[:MAX_TEXT]
		else:
			dropped.append(field)
	if answer.get("client_interest") in INTERESTS:
		fields["client_interest"] = answer["client_interest"]
	follow_up = valid_date(answer.get("next_follow_up_on"), today)
	if follow_up:
		fields["next_follow_up_on"] = str(follow_up)
	actions = []
	for row in (answer.get("action_items") or [])[:MAX_ACTIONS]:
		if not isinstance(row, dict):
			continue
		item = ai.guarded(row.get("item"), known)
		if not item:
			dropped.append("action_items")
			continue
		due = valid_date(row.get("due_date"), today)
		actions.append(
			{
				"item": item[:140],
				"responsible": (ai.guarded(row.get("responsible"), known) or "")[:140] or None,
				"due_date": str(due) if due else None,
			}
		)
	warnings = []
	if dropped:
		warnings.append(
			_(
				"Some parts had numbers or dates that weren't in your notes, so they were left out: {0}."
			).format(", ".join(sorted({_(field.replace("_", " ")) for field in dropped})))
		)
	return {"fields": fields, "action_items": actions, "warnings": warnings}


def valid_date(value, today):
	"""A date from today to 180 days ahead, else None."""
	try:
		day = getdate(str(value or "")[:10]) if value else None
	except Exception:
		return None
	if day and getdate(today) <= day <= getdate(add_days(today, FOLLOW_UP_MAX_DAYS)):
		return day
	return None


def mom_summary(doc) -> str:
	"""One short line for the meeting card."""
	text = strip_html(doc.get("discussion_points") or doc.get("decisions") or "").strip()
	return " ".join(text.split())[:240]


MOM_PROMPT = """You turn a TBO salesperson's rough notes from a client meeting into minutes of
meeting (MOM). TBO (Team Back Office) builds software and runs back-office services in Kerala,
India. The notes may be English, Malayalam, or Malayalam typed in Latin letters. Write the MOM in
clear, short English, keeping every name, number, price and date exactly as written. Do not add
anything that is not in the notes or the facts. Leave a field empty ("") when the notes say
nothing about it.
Fields:
- "agenda": what the meeting was for;
- "discussion_points": the key points discussed, one per line;
- "client_requirements": what the client needs, one per line;
- "decisions": what was decided or agreed;
- "client_interest": "Positive", "Neutral" or "Negative";
- "objections": the client's concerns or objections;
- "quotation_feedback": what the client said about a quotation or price, if anything;
- "action_items": [{"item": "...", "responsible": "<a name from the notes, or TBO / Client>",
  "due_date": "YYYY-MM-DD or empty"}];
- "next_follow_up_on": the next follow-up date as YYYY-MM-DD (from today to 180 days ahead), or "";
- "next_meeting_details": what the next meeting is about and when, if the notes say.
Answer only JSON with exactly these keys. Never write a number, price, count or date that is not in
the notes or the facts, except the dates of next_follow_up_on and due_date.
"""
