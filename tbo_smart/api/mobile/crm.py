"""TBO Smart CRM façade (v1) over Frappe CRM: leads, deals, organizations, follow-ups, activity.

Lists use frappe.get_list, so Frappe CRM's own rules (roles, the sales hierarchy and
shares) decide what each user sees. Writes are normal inserts and saves under the
session user. Contract: docs/tbo-smart-api-ess-crm.md.
"""

import frappe
from crm.api.activities import get_linked_notes, get_linked_tasks
from crm.api.comment import add_comment
from crm.fcrm.doctype.crm_lead.crm_lead import convert_to_deal
from frappe import _
from frappe.query_builder.functions import Coalesce, Max, Min
from frappe.utils import cint, flt, get_datetime, now_datetime, strip_html

from tbo_smart.api.mobile.utils import get_full_names, page_envelope, page_window

REF_DOCTYPES = {"lead": "CRM Lead", "deal": "CRM Deal"}
CLOSED_TASK_STATUSES = ("Done", "Canceled")
ACTIVITY_KINDS = ("call", "note", "meeting", "email", "whatsapp")
COMMENT_LABELS = {"meeting": "Meeting", "email": "Email", "whatsapp": "WhatsApp"}

LEAD_FIELDS = [
	"name",
	"lead_name",
	"organization",
	"job_title",
	"status",
	"source",
	"lead_owner",
	"mobile_no",
	"phone",
	"email",
	"territory",
	"converted",
	"creation",
]
DEAL_FIELDS = [
	"name",
	"organization",
	"lead_name",
	"first_name",
	"last_name",
	"status",
	"deal_value",
	"expected_deal_value",
	"currency",
	"probability",
	"expected_closure_date",
	"closed_date",
	"deal_owner",
	"lost_reason",
	"lost_notes",
	"next_step",
	"mobile_no",
	"email",
	"creation",
]


# ---------- Options ----------


@frappe.whitelist()
def get_options() -> dict:
	"""The site's lead sources, lead statuses, deal stages and lost reasons, in CRM order."""
	return {
		"lead_sources": frappe.get_list("CRM Lead Source", pluck="name", order_by="name asc"),
		"lead_statuses": frappe.get_list("CRM Lead Status", fields=["name", "type"], order_by="position asc"),
		"deal_stages": frappe.get_list(
			"CRM Deal Status", fields=["name", "type", "probability"], order_by="position asc"
		),
		"lost_reasons": frappe.get_list("CRM Lost Reason", pluck="name", order_by="name asc"),
	}


# ---------- Leads ----------


@frappe.whitelist()
def get_leads(
	status: str | None = None,
	search: str | None = None,
	include_converted: bool = False,
	page: int = 1,
	page_length: int = 20,
) -> dict:
	"""Leads the session user may read, newest first."""
	filters = {}
	if status:
		filters["status"] = status
	if not cint(include_converted):
		filters["converted"] = 0
	or_filters = search_filters(search, ["lead_name", "organization", "email", "mobile_no"])
	start, length = page_window(page, page_length)
	leads = frappe.get_list(
		"CRM Lead",
		fields=LEAD_FIELDS,
		filters=filters,
		or_filters=or_filters,
		order_by="creation desc",
		start=start,
		page_length=length + 1,
	)
	return page_envelope(serialize_leads(leads), page, page_length)


@frappe.whitelist()
def get_lead(name: str) -> dict:
	"""One lead with its notes, tasks and activity."""
	frappe.has_permission("CRM Lead", "read", name, throw=True)
	lead = frappe.db.get_value("CRM Lead", name, LEAD_FIELDS, as_dict=True)
	return {**serialize_leads([lead])[0], **get_timeline("CRM Lead", name)}


@frappe.whitelist(methods=["POST"])
def create_lead(
	lead_name: str,
	organization: str | None = None,
	email: str | None = None,
	mobile_no: str | None = None,
	source: str | None = None,
	note: str | None = None,
) -> dict:
	"""Create a lead owned by the session user, with an optional first note."""
	first_name, _space, last_name = (lead_name or "").strip().partition(" ")
	if not first_name:
		frappe.throw(_("Give the lead a name."))
	lead = frappe.new_doc("CRM Lead")
	lead.update(
		{
			"first_name": first_name,
			"last_name": last_name,
			"organization": organization,
			"email": email,
			"mobile_no": mobile_no,
			"source": source,
			"lead_owner": frappe.session.user,
		}
	)
	lead.insert()
	if note:
		add_note("CRM Lead", lead.name, note)
	return get_lead(lead.name)


@frappe.whitelist(methods=["POST"])
def set_lead_status(name: str, status: str) -> dict:
	lead = frappe.get_doc("CRM Lead", name)
	lead.check_permission("write")
	lead.status = status
	lead.save()
	return {"name": lead.name, "status": lead.status}


@frappe.whitelist(methods=["POST"])
def convert_lead(name: str) -> dict:
	"""Convert a lead to a deal with Frappe CRM's own conversion; a converted lead returns its deal.

	The existing-deal check is done here rather than with convert_to_deal(if_converted=...),
	which older Frappe CRM releases do not have.
	"""
	frappe.has_permission("CRM Lead", "write", name, throw=True)
	deal = frappe.db.get_value("CRM Deal", {"lead": name}, "name") or convert_to_deal(lead=name)
	return get_deal(deal)


def serialize_leads(leads: list) -> list[dict]:
	names = [lead.name for lead in leads]
	next_follow_up = get_next_follow_ups("CRM Lead", names)
	last_contact = get_last_calls("CRM Lead", names)
	owners = get_full_names({lead.lead_owner for lead in leads})
	return [
		{
			"name": lead.name,
			"lead_name": lead.lead_name,
			"organization": lead.organization,
			"designation": lead.job_title,
			"status": lead.status,
			"source": lead.source,
			"owner": owners.get(lead.lead_owner, lead.lead_owner),
			"owner_id": lead.lead_owner,
			"phone": lead.mobile_no or lead.phone,
			"email": lead.email,
			"territory": lead.territory,
			"converted": bool(lead.converted),
			"last_contact": last_contact.get(lead.name),
			"next_follow_up": next_follow_up.get(lead.name),
			"created": lead.creation,
		}
		for lead in leads
	]


# ---------- Deals ----------


@frappe.whitelist()
def get_deals(
	stage: str | None = None,
	mine: bool = False,
	search: str | None = None,
	page: int = 1,
	page_length: int = 20,
) -> dict:
	"""Deals the session user may read, optionally one stage or only theirs."""
	filters = {}
	if stage:
		filters["status"] = stage
	if cint(mine):
		filters["deal_owner"] = frappe.session.user
	or_filters = search_filters(search, ["organization", "lead_name", "email", "mobile_no"])
	start, length = page_window(page, page_length)
	deals = frappe.get_list(
		"CRM Deal",
		fields=DEAL_FIELDS,
		filters=filters,
		or_filters=or_filters,
		order_by="modified desc",
		start=start,
		page_length=length + 1,
	)
	return page_envelope(serialize_deals(deals), page, page_length)


@frappe.whitelist()
def get_deal(name: str) -> dict:
	"""One deal with its stage history, tasks, notes, activity and ERPNext customer."""
	frappe.has_permission("CRM Deal", "read", name, throw=True)
	deal = frappe.get_doc("CRM Deal", name)
	row = frappe._dict({field: deal.get(field) for field in DEAL_FIELDS})
	return {
		**serialize_deals([row])[0],
		"erpnext_customer": deal.get("erpnext_customer"),
		"stage_history": [
			{"stage": log.get("from"), "at": log.from_date}
			for log in deal.status_change_log
			if log.get("from")
		],
		**get_timeline("CRM Deal", name),
	}


@frappe.whitelist(methods=["POST"])
def move_deal_stage(name: str, stage: str, lost_reason: str | None = None) -> dict:
	"""Move a deal to another CRM Deal Status. Lost needs a reason.

	A reason that matches a CRM Lost Reason is used as is; free text is saved as
	lost reason "Other" with the text in lost notes, which is what CRM asks for.
	"""
	deal = frappe.get_doc("CRM Deal", name)
	deal.check_permission("write")
	stage_type = frappe.db.get_value("CRM Deal Status", stage, "type")
	if not stage_type:
		frappe.throw(_("{0} is not a deal stage.").format(stage))
	deal.status = stage
	if stage_type == "Lost":
		reason = (lost_reason or "").strip()
		if not reason:
			frappe.throw(_("Give a reason for losing the deal."))
		if frappe.db.exists("CRM Lost Reason", reason):
			deal.lost_reason, deal.lost_notes = reason, None
		else:
			deal.lost_reason, deal.lost_notes = "Other", reason
	deal.save()
	return get_deal(deal.name)


def serialize_deals(deals: list) -> list[dict]:
	names = [deal.name for deal in deals]
	stage_types = dict(frappe.get_all("CRM Deal Status", fields=["name", "type"], as_list=True))
	owners = get_full_names({deal.deal_owner for deal in deals})
	next_follow_up = get_next_follow_ups("CRM Deal", names)
	result = []
	for deal in deals:
		contact = deal.lead_name or " ".join(part for part in (deal.first_name, deal.last_name) if part)
		result.append(
			{
				"name": deal.name,
				"title": deal.organization or contact or deal.name,
				"organization": deal.organization,
				"contact": contact,
				"stage": deal.status,
				"stage_type": stage_types.get(deal.status),
				"amount": flt(deal.deal_value or deal.expected_deal_value),
				"currency": deal.currency,
				"probability": flt(deal.probability),
				"expected_close": deal.expected_closure_date,
				"closed_on": deal.closed_date,
				"owner": owners.get(deal.deal_owner, deal.deal_owner),
				"owner_id": deal.deal_owner,
				"lost_reason": deal.lost_notes or deal.lost_reason,
				"next_step": deal.next_step,
				"phone": deal.mobile_no,
				"email": deal.email,
				"next_follow_up": next_follow_up.get(deal.name),
				"created": deal.creation,
			}
		)
	return result


# ---------- Organizations ----------


@frappe.whitelist()
def get_organizations(search: str | None = None, page: int = 1, page_length: int = 20) -> dict:
	"""CRM Organizations with open deal counts and the ERPNext Customers CRM linked to their deals."""
	or_filters = search_filters(search, ["organization_name", "website"])
	start, length = page_window(page, page_length)
	organizations = frappe.get_list(
		"CRM Organization",
		fields=["name", "organization_name", "territory", "industry", "website", "no_of_employees"],
		or_filters=or_filters,
		order_by="organization_name asc",
		start=start,
		page_length=length + 1,
	)
	return page_envelope(serialize_organizations(organizations), page, page_length)


@frappe.whitelist()
def get_organization(name: str) -> dict:
	"""One organization with its contacts and deals."""
	frappe.has_permission("CRM Organization", "read", name, throw=True)
	organization = frappe.db.get_value(
		"CRM Organization",
		name,
		["name", "organization_name", "territory", "industry", "website", "no_of_employees"],
		as_dict=True,
	)
	contacts = frappe.get_list(
		"Contact",
		filters={"company_name": name},
		fields=["name", "full_name", "designation", "mobile_no", "phone", "email_id"],
		order_by="full_name asc",
	)
	deals = frappe.get_list(
		"CRM Deal", filters={"organization": name}, fields=DEAL_FIELDS, order_by="modified desc"
	)
	return {
		**serialize_organizations([organization])[0],
		"contacts": [
			{
				"name": contact.full_name or contact.name,
				"contact": contact.name,
				"designation": contact.designation,
				"phone": contact.mobile_no or contact.phone,
				"email": contact.email_id,
			}
			for contact in contacts
		],
		"deals": serialize_deals(deals),
	}


def serialize_organizations(organizations: list) -> list[dict]:
	names = [organization.name for organization in organizations]
	open_deals, customers = get_organization_deal_links(names)
	return [
		{
			"name": organization.name,
			"organization_name": organization.organization_name,
			"territory": organization.territory,
			"industry": organization.industry,
			"website": organization.website,
			"no_of_employees": organization.no_of_employees,
			"open_deals": open_deals.get(organization.name, 0),
			"erpnext_customers": sorted(customers.get(organization.name, [])),
		}
		for organization in organizations
	]


def get_organization_deal_links(names: list[str]) -> tuple[dict, dict]:
	"""Open deal count and ERPNext customers per organization, from the deals the user can read.

	Frappe CRM links a Customer to a Deal (CRM Deal.erpnext_customer, set when the deal
	is won with the ERPNext integration on); there is no direct Organization -> Customer link.
	"""
	if not names:
		return {}, {}
	fields = ["name", "organization", "status"]
	has_customer = frappe.get_meta("CRM Deal").has_field("erpnext_customer")
	if has_customer:
		fields.append("erpnext_customer")
	deals = frappe.get_list("CRM Deal", filters={"organization": ("in", names)}, fields=fields)
	stage_types = dict(frappe.get_all("CRM Deal Status", fields=["name", "type"], as_list=True))
	open_deals, customers = {}, {}
	for deal in deals:
		if stage_types.get(deal.status) not in ("Won", "Lost"):
			open_deals[deal.organization] = open_deals.get(deal.organization, 0) + 1
		if has_customer and deal.erpnext_customer:
			customers.setdefault(deal.organization, set()).add(deal.erpnext_customer)
	return open_deals, customers


# ---------- Follow-ups and activity ----------


@frappe.whitelist()
def get_my_follow_ups() -> list[dict]:
	"""Open CRM Tasks assigned to me that are due today or overdue, oldest first."""
	end_of_today = get_datetime(f"{frappe.utils.nowdate()} 23:59:59")
	tasks = frappe.get_list(
		"CRM Task",
		filters={
			"assigned_to": frappe.session.user,
			"status": ("not in", CLOSED_TASK_STATUSES),
			"due_date": ("<=", end_of_today),
		},
		fields=["name", "title", "status", "priority", "due_date", "reference_doctype", "reference_docname"],
		order_by="due_date asc",
		limit=100,
	)
	refs = get_ref_labels(tasks)
	return [
		{
			"id": task.name,
			"kind": "task",
			"subject": task.title,
			"status": task.status,
			"priority": task.priority,
			"due": task.due_date,
			"overdue": get_datetime(task.due_date) < now_datetime(),
			"with": refs.get(task.reference_docname, {}).get("with"),
			"organization": refs.get(task.reference_docname, {}).get("organization"),
			"ref": ref_of(task.reference_doctype, task.reference_docname),
		}
		for task in tasks
	]


@frappe.whitelist(methods=["POST"])
def log_activity(
	doctype: str,
	name: str,
	kind: str,
	text: str,
	next_follow_up: str | None = None,
	phone: str | None = None,
) -> dict:
	"""Log a call, note, meeting, email or WhatsApp on a lead or deal.

	call -> CRM Call Log (Manual, Outgoing, Completed) to `phone` (default: the record's
	mobile) from the caller's own mobile, with the text as its note;
	note -> FCRM Note; meeting / email / whatsapp -> a CRM comment, as Frappe CRM has no
	doctype for them. A call or meeting closes my open follow-ups on the record that are
	due by now. next_follow_up (date or datetime) adds a CRM Task assigned to me.
	"""
	reference_doctype = REF_DOCTYPES.get(doctype)
	if not reference_doctype:
		frappe.throw(_("doctype must be lead or deal."))
	if kind not in ACTIVITY_KINDS:
		frappe.throw(_("kind must be one of {0}.").format(", ".join(ACTIVITY_KINDS)))
	if not (text or "").strip():
		frappe.throw(_("Write what happened."))
	frappe.has_permission(reference_doctype, "read", name, throw=True)

	if kind == "note":
		add_note(reference_doctype, name, text)
	elif kind == "call":
		add_call(reference_doctype, name, text, phone)
	else:
		add_comment(reference_doctype, name, f"{COMMENT_LABELS[kind]}: {frappe.utils.escape_html(text)}")

	if kind in ("call", "meeting"):
		close_due_follow_ups(reference_doctype, name)
	if next_follow_up:
		add_follow_up_task(reference_doctype, name, next_follow_up)
	return get_timeline(reference_doctype, name)


@frappe.whitelist(methods=["POST"])
def add_follow_up(doctype: str, name: str, due: str, title: str | None = None) -> dict:
	"""Schedule a follow-up (a CRM Task assigned to me) on a lead or deal."""
	reference_doctype = REF_DOCTYPES.get(doctype)
	if not reference_doctype:
		frappe.throw(_("doctype must be lead or deal."))
	frappe.has_permission(reference_doctype, "read", name, throw=True)
	task = add_follow_up_task(reference_doctype, name, due, title)
	return {"id": task.name, "subject": task.title, "due": task.due_date, "status": task.status}


@frappe.whitelist(methods=["POST"])
def set_task_done(name: str, done: bool = True) -> dict:
	"""Tick or untick a CRM Task."""
	task = frappe.get_doc("CRM Task", name)
	task.check_permission("write")
	task.status = "Done" if cint(done) else "Todo"
	task.save()
	return {"id": task.name, "status": task.status, "done": task.status == "Done"}


def add_note(reference_doctype: str, name: str, text: str):
	note = frappe.new_doc("FCRM Note")
	note.update(
		{
			"title": text.strip().splitlines()[0][:140],
			"content": frappe.utils.escape_html(text),
			"reference_doctype": reference_doctype,
			"reference_docname": name,
		}
	)
	note.insert()
	return note


def add_call(reference_doctype: str, name: str, text: str, phone: str | None = None):
	"""CRM Call Log needs both numbers: the one called and the caller's own."""
	now = now_datetime()
	called = phone or frappe.db.get_value(reference_doctype, name, "mobile_no")
	caller = frappe.db.get_value("User", frappe.session.user, "mobile_no") or frappe.db.get_value(
		"Employee", {"user_id": frappe.session.user, "status": "Active"}, "cell_number"
	)
	if not called:
		frappe.throw(_("{0} has no mobile number. Send the number you called.").format(name))
	if not caller:
		frappe.throw(_("Add your mobile number to your user profile to log calls."))
	call = frappe.new_doc("CRM Call Log")
	call.update(
		{
			"type": "Outgoing",
			"status": "Completed",
			"telephony_medium": "Manual",
			"caller": frappe.session.user,
			"from": caller,
			"to": called,
			"start_time": now,
			"end_time": now,
			"note": add_note(reference_doctype, name, text).name,
			"reference_doctype": reference_doctype,
			"reference_docname": name,
		}
	)
	call.insert()
	return call


def add_follow_up_task(reference_doctype: str, name: str, due: str, title: str | None = None):
	label = frappe.db.get_value(
		reference_doctype, name, "lead_name" if reference_doctype == "CRM Lead" else "organization"
	)
	task = frappe.new_doc("CRM Task")
	task.update(
		{
			"title": title or _("Follow up with {0}").format(label or name),
			"status": "Todo",
			"priority": "Medium",
			"assigned_to": frappe.session.user,
			"due_date": get_datetime(due),
			"reference_doctype": reference_doctype,
			"reference_docname": name,
		}
	)
	task.insert()
	return task


def close_due_follow_ups(reference_doctype: str, name: str) -> None:
	due = frappe.get_list(
		"CRM Task",
		filters={
			"reference_doctype": reference_doctype,
			"reference_docname": name,
			"assigned_to": frappe.session.user,
			"status": ("not in", CLOSED_TASK_STATUSES),
			"due_date": ("<=", now_datetime()),
		},
		pluck="name",
	)
	for task_name in due:
		task = frappe.get_doc("CRM Task", task_name)
		task.status = "Done"
		task.save()


def get_timeline(reference_doctype: str, name: str) -> dict:
	"""Notes, tasks and a merged newest-first activity list (calls, notes, comments)."""
	notes = get_linked_notes(name)
	tasks = get_linked_tasks(name)
	CallLog = frappe.qb.DocType("CRM Call Log")
	calls = (
		frappe.qb.from_(CallLog)
		.select(
			CallLog.name,
			CallLog.type,
			CallLog.status,
			CallLog.duration,
			CallLog.caller,
			CallLog.receiver,
			CallLog.note,
			Coalesce(CallLog.start_time, CallLog.creation).as_("at"),
		)
		.where((CallLog.reference_doctype == reference_doctype) & (CallLog.reference_docname == name))
	).run(as_dict=True)
	Comment = frappe.qb.DocType("Comment")
	comments = (
		frappe.qb.from_(Comment)
		.select(Comment.name, Comment.content, Comment.comment_email, Comment.creation)
		.where(
			(Comment.reference_doctype == reference_doctype)
			& (Comment.reference_name == name)
			& (Comment.comment_type == "Comment")
		)
	).run(as_dict=True)

	people = get_full_names(
		{note.owner for note in notes}
		| {call.caller or call.receiver for call in calls}
		| {comment.comment_email for comment in comments}
	)
	note_text = {note.name: strip_html(note.content or note.title or "").strip() for note in notes}
	call_notes = {call.note for call in calls if call.note}

	activities = [
		{
			"id": call.name,
			"at": call.at,
			"who": people.get(call.caller or call.receiver),
			"kind": "call",
			"text": note_text.get(call.note) or _("{0} call, {1}").format(call.type, call.status),
		}
		for call in calls
	]
	activities += [
		{
			"id": note.name,
			"at": note.creation,
			"who": people.get(note.owner),
			"kind": "note",
			"text": note_text[note.name],
		}
		for note in notes
		if note.name not in call_notes
	]
	activities += [
		{
			"id": comment.name,
			"at": comment.creation,
			"who": people.get(comment.comment_email, comment.comment_email),
			"kind": "comment",
			"text": strip_html(comment.content or "").strip(),
		}
		for comment in comments
	]
	activities.sort(key=lambda row: get_datetime(row["at"]), reverse=True)
	return {
		"notes": [
			{
				"id": note.name,
				"at": note.creation,
				"who": people.get(note.owner),
				"text": note_text[note.name],
			}
			for note in sorted(notes, key=lambda note: note.creation, reverse=True)
		],
		"tasks": [
			{
				"id": task.name,
				"title": task.title,
				"due": task.due_date,
				"status": task.status,
				"done": task.status == "Done",
				"assigned_to": task.assigned_to,
			}
			for task in sorted(tasks, key=lambda task: str(task.due_date or ""))
		],
		"activities": activities,
	}


def get_next_follow_ups(reference_doctype: str, names: list[str]) -> dict:
	if not names:
		return {}
	Task = frappe.qb.DocType("CRM Task")
	return dict(
		(
			frappe.qb.from_(Task)
			.select(Task.reference_docname, Min(Task.due_date))
			.where(
				(Task.reference_doctype == reference_doctype)
				& (Task.reference_docname.isin(names))
				& (Task.status.notin(CLOSED_TASK_STATUSES))
				& (Task.due_date.isnotnull())
			)
			.groupby(Task.reference_docname)
		).run()
	)


def get_last_calls(reference_doctype: str, names: list[str]) -> dict:
	if not names:
		return {}
	CallLog = frappe.qb.DocType("CRM Call Log")
	return dict(
		(
			frappe.qb.from_(CallLog)
			.select(CallLog.reference_docname, Max(Coalesce(CallLog.start_time, CallLog.creation)))
			.where((CallLog.reference_doctype == reference_doctype) & (CallLog.reference_docname.isin(names)))
			.groupby(CallLog.reference_docname)
		).run()
	)


def get_ref_labels(tasks: list) -> dict:
	"""Contact name and organization for each lead or deal the tasks point at (two queries)."""
	labels = {}
	for doctype, fields in (
		("CRM Lead", ["name", "lead_name", "organization"]),
		("CRM Deal", ["name", "lead_name", "organization"]),
	):
		names = [task.reference_docname for task in tasks if task.reference_doctype == doctype]
		if names:
			for row in frappe.get_list(doctype, filters={"name": ("in", names)}, fields=fields):
				labels[row.name] = {"with": row.lead_name, "organization": row.organization}
	return labels


def ref_of(reference_doctype: str | None, name: str | None) -> dict | None:
	kind = {doctype: key for key, doctype in REF_DOCTYPES.items()}.get(reference_doctype)
	return {"doctype": kind, "name": name} if kind else None


def search_filters(search: str | None, fields: list[str]) -> dict | None:
	search = (search or "").strip()
	if not search:
		return None
	return {field: ("like", f"%{search}%") for field in fields}
