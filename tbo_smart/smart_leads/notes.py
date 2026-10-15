"""AI call notes, the lead brief and WhatsApp drafts (docs/smart-leads.md §6).

The AI only proposes. A note, a status change or a follow-up is written when the user confirms,
under the user's own permissions. Every text the AI writes passes the number guard: a number
that is not in the user's words or the lead's facts throws the AI text away.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime, strip_html

from tbo_smart import ai
from tbo_smart.api.mobile.crm import add_call, add_follow_up_task, add_note, close_due_follow_ups
from tbo_smart.smart_leads import FOLLOW_UP_TYPE
from tbo_smart.smart_leads.facts import LeadFacts
from tbo_smart.smart_leads.utils import normalise_phone, whatsapp_link

MAX_INPUT_CHARS = 4000
FOLLOW_UP_MAX_DAYS = 90
BRIEF_LINES = 3
WHATSAPP_CHARS = 700
LANGUAGES = {"en": "English", "ml": "Malayalam (in Malayalam script)"}
KINDS = ("call", "note")


def lead_statuses() -> list[dict]:
	return frappe.get_all("CRM Lead Status", fields=["name", "type"], order_by="position asc")


# ---------- the note box ----------


def draft_note(lead: str, text: str) -> dict:
	"""The AI's proposal for rough words about a lead: {note, kind, next_status, follow_up_on,
	follow_up_reason, warnings}. Nothing is written."""
	text = (text or "").strip()[:MAX_INPUT_CHARS]
	if not text:
		frappe.throw(_("Write what happened first."))
	lead_facts = LeadFacts([lead])
	facts = lead_facts.facts(lead)
	statuses = [status.name for status in lead_statuses()]
	today = getdate()
	answer = ai.call_ai(
		NOTE_PROMPT.format(statuses=", ".join(statuses)),
		frappe.as_json({"today": str(today), "rough_notes": text, "lead": facts}),
		"Lead note",
		("CRM Lead", lead),
	)["response"]
	known = lead_facts.known_numbers(lead, text, str(today))
	warnings = []
	note = ai.guarded(answer.get("note"), known)
	if not note:
		note = text
		warnings.append(
			_("The AI added numbers or dates that weren't in your words, so your own text is kept.")
		)
	follow_up_on = valid_follow_up(answer.get("follow_up_on"), today)
	reason = (
		ai.guarded(answer.get("follow_up_reason"), known | ai.fact_numbers(str(follow_up_on)))
		if follow_up_on
		else None
	)
	status = answer.get("next_status") if answer.get("next_status") in statuses else facts["status"]
	return {
		"note": note,
		"kind": answer.get("kind") if answer.get("kind") in KINDS else "note",
		"next_status": status,
		"follow_up_on": str(follow_up_on) if follow_up_on else None,
		"follow_up_reason": reason or "",
		"warnings": warnings,
	}


def valid_follow_up(value, today):
	"""A date from tomorrow to 90 days ahead, else None."""
	try:
		day = getdate(str(value or "")[:10]) if value else None
	except Exception:
		return None
	if day and getdate(add_days(today, 1)) <= day <= getdate(add_days(today, FOLLOW_UP_MAX_DAYS)):
		return day
	return None


def save_note(
	lead: str,
	text: str,
	kind: str = "note",
	status: str | None = None,
	follow_up_on: str | None = None,
	follow_up_reason: str | None = None,
) -> dict:
	"""Write what the user confirmed: an FCRM Note, or a CRM Call Log with its note (a call
	logged by someone with no mobile number on their profile is saved as a note "Call: …");
	a call closes my follow-ups on the lead that are due by now; the status change; and the next
	follow-up as a "Sales follow-up" CRM Task assigned to me."""
	text = (text or "").strip()[:MAX_INPUT_CHARS]
	if not text:
		frappe.throw(_("The note is empty."))
	if kind not in KINDS:
		frappe.throw(_("kind must be call or note."))
	frappe.has_permission("CRM Lead", "read", lead, throw=True)
	result = {"lead": lead, "kind": kind}
	if kind == "call":
		try:
			call = add_call("CRM Lead", lead, text)
			result.update({"call": call.name, "note": call.note})
		except frappe.ValidationError:
			# no mobile number on the profile or the lead: keep the words, say it was a call
			result.update(
				{"note": add_note("CRM Lead", lead, _("Call: {0}").format(text)).name, "kind": "note"}
			)
		close_due_follow_ups("CRM Lead", lead)
	else:
		result["note"] = add_note("CRM Lead", lead, text).name
	if status and status != frappe.db.get_value("CRM Lead", lead, "status"):
		result["status"] = set_lead_status(lead, status, text)
	if follow_up_on:
		day = getdate(follow_up_on)
		if day < getdate():
			frappe.throw(_("The next follow-up can't be in the past."))
		task = add_follow_up_task(
			"CRM Lead",
			lead,
			f"{day} 10:00:00",
			title=(follow_up_reason or "").strip()[:140] or None,
			task_type=FOLLOW_UP_TYPE,
		)
		result["task"] = task.name
	return result


def set_lead_status(lead: str, status: str, notes: str | None = None) -> str:
	"""Change a lead's status as the session user. A Lost-type status (Junk, Unqualified) needs
	CRM's lost reason: "Other" with the notes, unless the lead already has one."""
	if not frappe.db.exists("CRM Lead Status", status):
		frappe.throw(_("{0} is not a lead status.").format(status))
	doc = frappe.get_doc("CRM Lead", lead)
	doc.check_permission("write")
	doc.status = status
	if frappe.get_cached_value("CRM Lead Status", status, "type") == "Lost" and not doc.lost_reason:
		doc.lost_reason = "Other"
		doc.lost_notes = strip_html(notes or "").strip()[:500] or _("Closed from TBO smart leads")
	doc.save()
	return doc.status


NOTE_PROMPT = """You turn a salesperson's rough notes about a lead into a clean CRM note for TBO
(Team Back Office, a software and back-office services company in Kerala, India).
The rough notes may be English, Malayalam, or Malayalam typed in Latin letters. Write the note in
clear, short English, in the first person plural where natural ("We called…"), keeping every
name, number and date exactly as written. Do not add anything that is not in the rough notes.
Also suggest:
- "kind": "call" if they talked to the lead on the phone, else "note";
- "next_status": one of [{statuses}], the current status when nothing changed;
- "follow_up_on": the next follow-up date as YYYY-MM-DD, from tomorrow to 90 days after today
  (use the date the notes ask for; else a sensible one), or null when the lead is closing;
- "follow_up_reason": a few words on what to do then.
Answer only JSON: {{"note": "...", "kind": "...", "next_status": "...", "follow_up_on": "...",
"follow_up_reason": "..."}}. Never write a number, count or date that is not in the rough notes
or the facts, except follow_up_on.
"""


# ---------- the lead brief ----------


def get_brief(lead: str) -> dict:
	"""The cached brief: {lines, at, stale}. `stale` when the lead's notes or status changed
	since it was written, or there is none yet."""
	facts = LeadFacts([lead])
	row = facts.lead(lead)
	lines = [line for line in (row.tbo_ai_brief or "").split("\n") if line]
	return {
		"lines": lines,
		"at": row.tbo_ai_brief_at,
		"stale": row.tbo_ai_brief_hash != facts.content_hash(lead),
	}


def refresh_brief(lead: str) -> dict:
	"""Ask the AI for a three-line summary of the lead's history and cache it on the lead."""
	facts = LeadFacts([lead])
	answer = ai.call_ai(
		BRIEF_PROMPT,
		frappe.as_json({"today": str(getdate()), "lead": facts.facts(lead)}),
		"Lead brief",
		("CRM Lead", lead),
	)["response"]
	known = facts.known_numbers(lead, str(getdate()))
	raw = answer.get("lines") if isinstance(answer.get("lines"), list) else []
	lines = [line for line in (ai.guarded(text, known) for text in raw[:BRIEF_LINES]) if line]
	if not lines:
		frappe.throw(_("The AI couldn't write a brief from this lead's history. Try again later."))
	frappe.db.set_value(
		"CRM Lead",
		lead,
		{
			"tbo_ai_brief": "\n".join(line[:220] for line in lines),
			"tbo_ai_brief_hash": facts.content_hash(lead),
			"tbo_ai_brief_at": now_datetime(),
		},
		update_modified=False,
	)
	return get_brief(lead)


BRIEF_PROMPT = """You brief a TBO salesperson about a lead before they call.
You get JSON facts: status, source, age, last touch, status changes, notes and calls, and what
the old CRM recorded before the migration.
Write exactly three short lines (at most 25 words each):
1. who this is and what they want;
2. what has happened so far;
3. what to do next.
Plain English. Answer only JSON: {"lines": ["...", "...", "..."]}.
Never write a number, count or date that is not in the facts. If the facts are thin, say so.
"""


# ---------- WhatsApp ----------


def draft_whatsapp(lead: str, language: str = "en") -> dict:
	"""A short WhatsApp message from the lead's context, and the wa.me link that opens it."""
	if language not in LANGUAGES:
		frappe.throw(_("Language must be en or ml."))
	lead_facts = LeadFacts([lead])
	facts = lead_facts.facts(lead)
	doc = frappe.db.get_value("CRM Lead", lead, ["mobile_no", "phone"], as_dict=True)
	phone = doc.mobile_no or doc.phone
	if not normalise_phone(phone):
		frappe.throw(_("This lead has no usable mobile number."))
	sender = frappe.utils.get_fullname(frappe.session.user)
	answer = ai.call_ai(
		WHATSAPP_PROMPT.format(language=LANGUAGES[language]),
		frappe.as_json({"today": str(getdate()), "from": sender, "lead": facts}),
		"WhatsApp draft",
		("CRM Lead", lead),
	)["response"]
	text = ai.guarded(answer.get("message"), lead_facts.known_numbers(lead, sender, str(getdate())))
	if not text:
		frappe.throw(_("The AI's draft had details that aren't on the lead. Try again."))
	text = text[:WHATSAPP_CHARS]
	return {"message": text, "phone": f"+{normalise_phone(phone)}", "url": whatsapp_link(phone, text)}


WHATSAPP_PROMPT = """You write a short, warm WhatsApp message from a TBO salesperson to a lead.
TBO (Team Back Office) builds software and runs back-office services in Kerala, India.
Use the lead's facts to pick up where things were left (a demo, a quote, a question). Keep it to
2-4 sentences, no emojis, no links, sign off with the salesperson's first name.
Write it in {language}.
Answer only JSON: {{"message": "..."}}.
Never write a number, price, count or date that is not in the facts.
"""
