"""AI first messages as drafts in the outbox (docs/prospecting.md §10).

The text comes from the existing `autopilot.draft_message` (for a lead Find prospects created it
writes from the website signals and the "Why call" line, with the same number guard), so there is
one drafting function. A draft is only a row in the outbox: a person reads, edits and approves it.
"""

import uuid

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart import ai
from tbo_smart.outreach import AI, CHANNELS, DRAFT, EMAIL, FAILED, MESSAGE, WHATSAPP, batch_limit
from tbo_smart.outreach.guards import blocked_reason, live_message
from tbo_smart.smart_leads import autopilot

PROGRESS_KEY = "tbo_smart:outreach:batch:{}"
LAST_BATCH_KEY = "tbo_smart:outreach:last_batch:{}"
PROGRESS_SECONDS = 60 * 60
JOB_SECONDS = 30 * 60


def channel_of(channel: str) -> str:
	"""`Email` or `WhatsApp` from either spelling."""
	found = next((name for name in CHANNELS if name.lower() == (channel or "").strip().lower()), None)
	if not found:
		frappe.throw(_("channel must be Email or WhatsApp."))
	return found


def lead_names(leads, run: str | None) -> list[str]:
	"""The leads a request names: a list (or JSON list) of leads, or a prospect run's leads."""
	leads = frappe.parse_json(leads) if isinstance(leads, str) and leads.strip().startswith("[") else leads
	if isinstance(leads, str):
		leads = [leads]
	if leads:
		return list(dict.fromkeys(str(lead) for lead in leads if lead))
	if run:
		if not frappe.db.exists("TBO Prospect Run", run):
			frappe.throw(_("{0} is not a prospect run.").format(run), frappe.DoesNotExistError)
		return frappe.get_all(
			"Lead", filters={"tbo_prospect_run": run}, order_by="creation asc", pluck="name"
		)
	frappe.throw(_("Name the leads or a prospect run."))


def queue_drafts(leads, run: str | None, channel: str) -> dict:
	"""Check the leads the acting user may write to, then draft the eligible ones in the
	background, at most the batch limit. Returns {batch, queued, skipped: [{lead, lead_name,
	reason}]}; nothing is sent."""
	channel = channel_of(channel)
	if not ai.ai_ready():
		frappe.throw(_("AI isn't set up. Add an API key in TBO Smart Settings."))
	limit = batch_limit()
	eligible, skipped = [], []
	for lead in lead_names(leads, run):
		reason = skip_reason(lead, channel)
		if not reason and len(eligible) >= limit:
			reason = _("Over the limit of {0} drafts per request.").format(limit)
		if reason:
			skipped.append(
				{"lead": lead, "lead_name": frappe.db.get_value("Lead", lead, "lead_name"), "reason": reason}
			)
		else:
			eligible.append(lead)
	batch = uuid.uuid4().hex[:10]
	if eligible:
		set_progress(batch, total=len(eligible), done=0, failed=0)
		frappe.cache.set_value(
			LAST_BATCH_KEY.format(frappe.session.user), batch, expires_in_sec=PROGRESS_SECONDS
		)
		frappe.enqueue(
			"tbo_smart.outreach.drafting.run_batch",
			queue="long",
			timeout=JOB_SECONDS,
			enqueue_after_commit=True,
			user=frappe.session.user,
			leads=eligible,
			channel=channel,
			batch=batch,
			run=run,
		)
	return {"batch": batch, "queued": len(eligible), "skipped": skipped}


def skip_reason(lead: str, channel: str) -> str | None:
	if not frappe.db.exists("Lead", lead):
		return _("The lead does not exist.")
	if not frappe.has_permission("Lead", "write", lead):
		return _("You cannot edit this lead.")
	if existing := live_message(lead, channel):
		return _("{0} is already in the outbox.").format(existing)
	return blocked_reason(lead, channel)


def run_batch(user: str, leads: list[str], channel: str, batch: str, run: str | None = None) -> None:
	"""Background job: draft each lead as `user`, carrying on past a failing one."""
	previous = frappe.session.user
	frappe.set_user(user)  # the drafts are theirs, and the AI signs with their name - nosemgrep
	try:
		for lead in leads:
			failed = False
			try:
				draft_one(lead, channel, batch=batch, run=run)
				frappe.db.commit()  # nosemgrep
			except Exception:
				failed = True
				frappe.log_error(title=f"Outreach: drafting for {lead}")
			bump_progress(batch, failed)
	finally:
		frappe.set_user(previous)  # nosemgrep


def draft_one(lead: str, channel: str, batch: str | None = None, run: str | None = None) -> str:
	"""Draft `lead`'s first message on `channel` as the session user and put it in the outbox as a
	Draft. Returns the message."""
	channel = channel_of(channel)
	if reason := skip_reason(lead, channel):
		frappe.throw(reason)
	draft = autopilot.draft_message(lead, channel.lower())
	return (
		frappe.get_doc(
			{
				"doctype": MESSAGE,
				"lead": lead,
				"channel": channel,
				"subject": draft.get("subject") if channel == EMAIL else None,
				"body": draft["text"],
				"recipient": draft.get("to"),
				"status": DRAFT,
				"drafted_by": AI,
				"batch": batch,
				"prospect_run": run or frappe.db.get_value("Lead", lead, "tbo_prospect_run"),
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def regenerate(doc) -> None:
	"""Replace a draft's text with a new AI draft, in memory; the caller saves."""
	draft = autopilot.draft_message(doc.lead, doc.channel.lower())
	doc.body = draft["text"]
	doc.subject = draft.get("subject") if doc.channel == EMAIL else None
	doc.recipient = draft.get("to") or doc.recipient
	doc.drafted_by = AI
	if doc.status == FAILED:
		doc.status = DRAFT
		doc.error = None


# ---------- progress of a batch, for the outbox ----------


def set_progress(batch: str, **values) -> None:
	frappe.cache.set_value(PROGRESS_KEY.format(batch), values, expires_in_sec=PROGRESS_SECONDS)


def bump_progress(batch: str, failed: bool) -> None:
	progress = frappe.cache.get_value(PROGRESS_KEY.format(batch)) or {"total": 0, "done": 0, "failed": 0}
	progress["failed" if failed else "done"] = cint(progress["failed" if failed else "done"]) + 1
	set_progress(batch, **progress)


def current_progress(user: str) -> dict | None:
	"""{batch, total, done, failed} for the user's latest request while it is still drafting."""
	batch = frappe.cache.get_value(LAST_BATCH_KEY.format(user))
	progress = frappe.cache.get_value(PROGRESS_KEY.format(batch)) if batch else None
	if not progress or cint(progress["done"]) + cint(progress["failed"]) >= cint(progress["total"]):
		return None
	return {"batch": batch, **progress}
