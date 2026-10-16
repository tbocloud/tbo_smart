"""The CRM's smart lead screens: Call today, the lead panel, follow-ups and the triage
(docs/smart-leads.md §7; the CRM side is tbocloud/crm docs/tbo-smart-leads.md).

Every method checks its feature's switch and Frappe CRM's own permissions: lists use
frappe.get_list, and a lead is read or written only when the session user may.
"""

import json

import frappe
from frappe import _

from tbo_smart import ai
from tbo_smart.api.mobile.capabilities import get_capabilities
from tbo_smart.api.mobile.crm import add_follow_up_task, get_crm_users
from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.finance import crm_installed
from tbo_smart.smart_leads import (
	FOLLOW_UP_TYPE,
	OPEN_STATUSES,
	call_today_size,
	follow_ups_enabled,
	is_sales_head,
	notes,
	notes_enabled,
	scoring_enabled,
	triage,
	triage_enabled,
)
from tbo_smart.smart_leads.call_today import build_call_today, viewable_people
from tbo_smart.smart_leads.facts import LeadFacts
from tbo_smart.smart_leads.follow_ups import FollowUpRun
from tbo_smart.smart_leads.scoring import conversion_rates, lead_score, status_positions
from tbo_smart.smart_leads.utils import enabled_users, normalise_phone, tel_link, whatsapp_link

TRIAGE_ROLES = {"System Manager", "Sales Manager"}


def require(enabled: bool, message: str) -> None:
	if not enabled:
		frappe.throw(message, title=_("Switched off"))


def require_lead(lead: str, ptype: str = "read") -> None:
	frappe.has_permission("CRM Lead", ptype, lead, throw=True)


def can_triage() -> bool:
	return frappe.session.user == "Administrator" or bool(set(frappe.get_roles()) & TRIAGE_ROLES)


def parse_list(value) -> list:
	return json.loads(value) if isinstance(value, str) else list(value or [])


# ---------- access ----------


@frappe.whitelist()
def get_access() -> dict:
	"""Which smart lead features are on and what the session user may do (nothing for a user
	without CRM access)."""
	if not crm_installed() or not get_capabilities().crm:
		return {"enabled": False}
	scoring, follow_ups, lead_notes, lead_triage = (
		scoring_enabled(),
		follow_ups_enabled(),
		notes_enabled(),
		triage_enabled(),
	)
	return {
		"enabled": scoring or follow_ups or lead_notes or lead_triage,
		"scoring": scoring,
		"follow_ups": follow_ups,
		"notes": lead_notes,
		"triage": lead_triage and can_triage(),
		"ai_ready": ai.ai_ready(),
		"is_sales_head": is_sales_head(),
		"can_manage": "System Manager" in frappe.get_roles(),
		"call_today_size": call_today_size(),
	}


# ---------- Call today ----------


@frappe.whitelist()
def get_call_today(user: str | None = None) -> dict:
	"""The person's Call today list (default: me); someone else's only within my team."""
	require(scoring_enabled(), _("Lead scoring is switched off in TBO Smart Settings."))
	return build_call_today(user)


@frappe.whitelist()
def get_people() -> list[dict]:
	"""Whose Call today list I may open: everyone in the CRM, or my sales team."""
	require(scoring_enabled(), _("Lead scoring is switched off in TBO Smart Settings."))
	return get_crm_users(viewable_people())


@frappe.whitelist()
def get_follow_up_summary(user: str | None = None) -> dict:
	"""Counts for the person's own open leads today: overdue, due today, re-engage, no follow-up."""
	require(follow_ups_enabled(), _("Lead follow-ups are switched off in TBO Smart Settings."))
	user = user or frappe.session.user
	people = viewable_people()
	if people is not None and user not in people:
		frappe.throw(_("You can only see your own or your team's follow-ups."), frappe.PermissionError)
	# only the leads I may read under CRM's rules
	visible = frappe.get_list(
		"CRM Lead",
		filters={"lead_owner": user, "status": ("in", OPEN_STATUSES), "converted": 0},
		pluck="name",
		limit_page_length=0,
	)
	run = FollowUpRun(leads=visible)
	run.evaluate()
	counts = {"overdue": 0, "due_today": 0, "re_engage": 0, "no_follow_up": 0, "escalated": 0}
	for item in run.items:
		counts[item.rule] += 1
		counts["escalated"] += item.level > 0
	return counts


@frappe.whitelist()
def get_needs_owner() -> list[dict]:
	"""Open leads whose owner is disabled or missing (sales heads only)."""
	if not is_sales_head():
		frappe.throw(_("Only the sales head sees the leads that need an owner."), frappe.PermissionError)
	leads = frappe.get_list(
		"CRM Lead",
		filters={"status": ("in", OPEN_STATUSES), "converted": 0},
		fields=[
			"name",
			"lead_name",
			"organization",
			"status",
			"lead_owner",
			"lead_score",
			"lead_temperature",
		],
		order_by="lead_score desc, creation desc",
		limit_page_length=0,
	)
	active = enabled_users({lead.lead_owner for lead in leads})
	orphans = [lead for lead in leads if lead.lead_owner not in active]
	names = get_full_names({lead.lead_owner for lead in orphans})
	return [
		{
			"id": lead.name,
			"lead_name": lead.lead_name,
			"organization": lead.organization,
			"status": lead.status,
			"owner": lead.lead_owner,
			"owner_name": names.get(lead.lead_owner),
			"score": lead.lead_score,
			"band": lead.lead_temperature,
		}
		for lead in orphans
	]


@frappe.whitelist(methods=["POST"])
def reassign_leads(leads: str | list, user: str) -> dict:
	"""Give leads to an active CRM user (sales heads only); CRM shares and assigns them."""
	if not is_sales_head():
		frappe.throw(_("Only the sales head can reassign leads."), frappe.PermissionError)
	if not get_crm_users([user]):
		frappe.throw(_("{0} is not an active CRM user.").format(user))
	moved = []
	for lead in parse_list(leads):
		doc = frappe.get_doc("CRM Lead", lead)
		doc.check_permission("write")
		doc.lead_owner = user
		doc.save()
		moved.append(lead)
	return {"moved": moved}


# ---------- one lead ----------


@frappe.whitelist()
def get_lead_insight(lead: str) -> dict:
	"""What the lead panel shows: score, band and reason, the next follow-up, phone links and
	the cached brief."""
	require_lead(lead)
	facts = LeadFacts([lead])
	row = facts.lead(lead)
	phone = row.mobile_no or row.phone
	result = {
		"lead": lead,
		"scoring": scoring_enabled(),
		"notes": notes_enabled(),
		"phone_e164": f"+{normalise_phone(phone)}" if normalise_phone(phone) else None,
		"tel": tel_link(phone),
		"whatsapp": whatsapp_link(phone),
		"next_follow_up": None,
	}
	follow_up = facts.next_follow_up(lead)
	if follow_up:
		result["next_follow_up"] = {
			"task": follow_up.name,
			"title": follow_up.title,
			"due": follow_up.due_date,
		}
	if result["scoring"]:
		score, band, reason = lead_score(facts, lead, conversion_rates(), status_positions())
		result.update({"score": score, "band": band, "reason": reason})
	if result["notes"]:
		result["brief"] = notes.get_brief(lead)
	return result


@frappe.whitelist(methods=["POST"])
def refresh_brief(lead: str) -> dict:
	require(notes_enabled(), _("AI notes are switched off in TBO Smart Settings."))
	# it writes the cached brief on the lead
	require_lead(lead, "write")
	return notes.refresh_brief(lead)


@frappe.whitelist(methods=["POST"])
def draft_note(lead: str, text: str) -> dict:
	"""The AI's proposal for rough words; nothing is written."""
	require(notes_enabled(), _("AI notes are switched off in TBO Smart Settings."))
	require_lead(lead)
	return notes.draft_note(lead, text)


@frappe.whitelist(methods=["POST"])
def save_note(
	lead: str,
	text: str,
	kind: str = "note",
	status: str | None = None,
	follow_up_on: str | None = None,
	follow_up_reason: str | None = None,
) -> dict:
	"""What the user confirmed: the note or call, the status and the next follow-up. Works with
	the AI off too (the box then saves the words as written)."""
	require_lead(lead)
	return notes.save_note(lead, text, kind, status, follow_up_on, follow_up_reason)


@frappe.whitelist(methods=["POST"])
def draft_whatsapp(lead: str, language: str = "en") -> dict:
	require(notes_enabled(), _("AI notes are switched off in TBO Smart Settings."))
	require_lead(lead)
	return notes.draft_whatsapp(lead, language)


@frappe.whitelist(methods=["POST"])
def set_follow_up(lead: str, due: str, title: str | None = None) -> dict:
	"""A "Sales follow-up" CRM Task on the lead, assigned to me."""
	require_lead(lead)
	if frappe.utils.getdate(due) < frappe.utils.getdate():
		frappe.throw(_("The next follow-up can't be in the past."))
	task = add_follow_up_task("CRM Lead", lead, due, title=title, task_type=FOLLOW_UP_TYPE)
	return {"task": task.name, "due": task.due_date, "title": task.title}


@frappe.whitelist(methods=["POST"])
def set_status(lead: str, status: str, notes_text: str | None = None) -> dict:
	"""Change the status under my permissions; Closed – Lost gets CRM's lost reason."""
	require_lead(lead, "write")
	return {"status": notes.set_lead_status(lead, status, notes_text)}


# ---------- triage ----------


def require_triage() -> None:
	require(triage_enabled(), _("The lead triage is switched off in TBO Smart Settings."))
	if not can_triage():
		frappe.throw(_("Only a Sales Manager or System Manager runs the triage."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def start_triage() -> dict:
	"""Queue a preview; nothing on any lead changes."""
	require_triage()
	if not ai.ai_ready():
		frappe.throw(_("Add an AI provider key in CRM Settings → TBO smart leads first."))
	return get_triage(triage.start())


@frappe.whitelist()
def get_triage(run: str | None = None) -> dict | None:
	"""A run with its rows (default: the latest)."""
	require_triage()
	run = run or frappe.db.get_value(triage.DOCTYPE, {}, "name", order_by="creation desc")
	if not run:
		return None
	doc = frappe.get_doc(triage.DOCTYPE, run)
	owners = get_full_names({row.lead_owner for row in doc.rows} | {row.assign_to for row in doc.rows})
	return {
		"name": doc.name,
		"status": doc.status,
		"creation": doc.creation,
		"lead_count": doc.lead_count,
		"call_again": doc.call_again,
		"suggest_close": doc.suggest_close,
		"skipped": doc.skipped,
		"estimated_cost_usd": doc.estimated_cost_usd,
		"applied_on": doc.applied_on,
		"error": doc.error,
		"rows": [
			{
				"lead": row.lead,
				"lead_name": row.lead_name,
				"owner": row.lead_owner,
				"owner_name": owners.get(row.lead_owner),
				"assign_to": row.assign_to,
				"assign_to_name": owners.get(row.assign_to),
				"status": row.status,
				"score": row.score,
				"bucket": row.bucket,
				"reason": row.reason,
				"follow_up_on": row.follow_up_on,
				"task": row.task,
				"closed_as": row.closed_as,
			}
			for row in doc.rows
		],
	}


@frappe.whitelist(methods=["POST"])
def apply_triage(run: str) -> dict:
	"""Create the Call again follow-ups. Never closes a lead."""
	require_triage()
	return {**triage.apply(run), "run": get_triage(run)}


@frappe.whitelist(methods=["POST"])
def close_triage_leads(run: str, leads: str | list, status: str) -> dict:
	"""A person closes the Suggest close leads they agree with, under their own permissions."""
	require_triage()
	return {**triage.close_leads(run, parse_list(leads), status), "run": get_triage(run)}
