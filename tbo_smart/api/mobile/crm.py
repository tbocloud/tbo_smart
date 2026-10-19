"""TBO Smart CRM façade over ERPNext's own sales records: leads (Lead), deals (Opportunity),
organizations (Prospect), follow-ups (ToDo), activity and insights.

Every endpoint needs the `crm` capability. Lists use frappe.get_list, so the sales rules
(tbo_smart.sales.permissions: owner, assignment, the sales hierarchy) decide what each user
sees; `scope` narrows that further within the caller's crm_scopes. Writes are normal inserts
and saves under the session user. The JSON is the contract the app was built against when the
records were Frappe CRM's: docs/tbo-smart-api-ess-crm.md.
"""

import base64
import binascii
import re

import frappe
from frappe import _
from frappe.desk.form.assign_to import add as add_assignment
from frappe.query_builder import Case
from frappe.query_builder.functions import (
	Avg,
	Coalesce,
	Count,
	CustomFunction,
	Date,
	IfNull,
	Lower,
	Max,
	Sum,
	Trim,
)
from frappe.utils import add_days, cint, flt, getdate, now_datetime, today
from hrms.api import upload_base64_file
from pypika.terms import EmptyCriterion, LiteralValue

from tbo_smart.api.mobile.capabilities import (
	CRM_ROLES,
	get_sales_team,
	list_scope_users,
	require,
	resolve_crm_scope,
	scope_users,
)
from tbo_smart.api.mobile.utils import (
	cached_insights,
	get_full_names,
	in_window,
	page_envelope,
	page_window,
	percent,
	period_windows,
	trend_index,
)
from tbo_smart.lead_pipeline.statuses import STATUSES, WON_STATUSES
from tbo_smart.lead_pipeline.won import convert_lead as make_lead_deal
from tbo_smart.sales import default_currency
from tbo_smart.sales.activities import (
	CALL,
	DONE,
	OPEN,
	TimestampDiff,
	add_call,
	add_follow_up_task,
	add_log,
	add_note,
	close_due_follow_ups,
	get_timeline,
	last_calls,
	next_follow_ups,
	task_title,
)
from tbo_smart.sales.opportunity import (
	CLOSED_STAGE_TYPES,
	converted_leads_query,
	customer_of,
	customers_of,
	deal_stages,
	leads_with_opportunity,
	stage_type,
	stage_types,
)
from tbo_smart.sales.stages import lead_lost_reason, set_lead_stage, set_lost_reason
from tbo_smart.smart_leads import scoring_enabled
from tbo_smart.smart_leads.call_today import build_call_today

REF_DOCTYPES = {"lead": "Lead", "deal": "Opportunity"}
ACTIVITY_KINDS = ("call", "note", "meeting", "email", "whatsapp")
# the app shows the task statuses Frappe CRM had
TASK_STATUS_LABELS = {OPEN: "Todo", DONE: "Done", "Cancelled": "Canceled"}
STALLED_DAYS = 14
INSIGHT_LIST_LIMIT = 10

DUPLICATE_LIMIT = 10
# Phones match on their last 9 digits, so +971 50 418 2276, 050 418 2276 and 504182276 are one number.
PHONE_MATCH_DIGITS = 9
MIN_PHONE_DIGITS = 7
# How the app captured a lead: a label for the capture note, and Lead Source names it may map to.
SOURCE_CHANNELS = {
	"card_scan": ("Business card scan", ("Business Card", "Card Scan", "Visiting Card")),
	"contacts": ("Phone contacts", ("Phone Contacts", "Phone Contact", "Contacts")),
	"whatsapp_share": ("WhatsApp share", ("WhatsApp", "WhatsApp Share")),
	"manual": ("Typed in", ()),
}
CAPTURE_NOTE_PREFIX = "Captured in TBO Smart:"
CARD_IMAGE_MAX_BYTES = 2 * 1024 * 1024
JPEG_MAGIC = b"\xff\xd8\xff"
# Kinds the app has a screen for; contacts and customers are shown but not opened.
OPENABLE_KINDS = ("lead", "deal", "organization")

RegexpReplace = CustomFunction("REGEXP_REPLACE", ["value", "pattern", "replacement"])
Right = CustomFunction("RIGHT", ["value", "length"])


class DuplicateLead(frappe.ValidationError):
	"""create_lead found possible duplicates and was not forced; the response carries `matches`."""

	http_status_code = 409


LEAD_FIELDS = [
	"name",
	"lead_name",
	"company_name as organization",
	"job_title",
	"tbo_stage as status",
	"source",
	"lead_owner",
	"mobile_no",
	"phone",
	"email_id as email",
	"territory",
	"tbo_score as score",
	"tbo_temperature as temperature",
	"creation",
]
DEAL_FIELDS = [
	"name",
	"customer_name as organization",
	"contact_display as lead_name",
	"sales_stage as status",
	"opportunity_amount as deal_value",
	"currency",
	"probability",
	"expected_closing as expected_closure_date",
	"tbo_closed_on as closed_date",
	"opportunity_owner as deal_owner",
	"order_lost_reason as lost_notes",
	"tbo_next_step as next_step",
	"contact_mobile as mobile_no",
	"contact_email as email",
	"creation",
]


# ---------- Options ----------


@frappe.whitelist()
def get_options() -> dict:
	"""The site's lead sources, lead stages, deal stages and lost reasons, in pipeline order."""
	require_crm()
	return {
		"lead_sources": frappe.get_list("Lead Source", pluck="name", order_by="name asc"),
		"lead_statuses": [{"name": name, "type": kind} for name, kind, _color in STATUSES],
		"deal_stages": deal_stages(),
		"lost_reasons": frappe.get_list("Opportunity Lost Reason", pluck="name", order_by="name asc"),
	}


# ---------- Leads ----------


@frappe.whitelist()
def get_leads(
	status: str | None = None,
	search: str | None = None,
	include_converted: bool = False,
	page: int = 1,
	page_length: int = 20,
	scope: str | None = None,
	source: str | None = None,
	temperature: str | None = None,
	metric: str | None = None,
	period: str | None = None,
) -> dict:
	"""Leads the session user may read in `scope` (mine / team / all; default: widest), newest first.

	`source` and `temperature` (Hot / Warm / Cold) filter; `metric` with `period` keeps the leads
	one number of the leads overview counts (`tbo_smart.api.mobile.leads.get_overview`), converted
	ones included, as the hub's list does."""
	capabilities = require_crm()
	filters = scope_filter(capabilities, scope, "lead_owner")
	if status:
		filters["tbo_stage"] = status
	filters.update(overview_filters(source, temperature, metric, period))
	if not metric and not cint(include_converted):
		converted = converted_leads_query().run(pluck=True)
		if converted:
			filters["name"] = ("not in", converted)
	or_filters = search_filters(search, ["lead_name", "company_name", "email_id", "mobile_no"])
	start, length = page_window(page, page_length)
	leads = frappe.get_list(
		"Lead",
		fields=LEAD_FIELDS,
		filters=filters,
		or_filters=or_filters,
		order_by="creation desc",
		start=start,
		page_length=length + 1,
	)
	return page_envelope(serialize_leads(leads), page, page_length)


def overview_filters(
	source: str | None, temperature: str | None, metric: str | None, period: str | None
) -> dict:
	"""get_list filters for the overview's lead lists, with the hub's own rules and figures."""
	# hub_leads imports this module, so it is imported when it is needed
	from tbo_smart.api.hub_leads import TEMPERATURES, overview_board, resolve_period

	filters = {}
	if source:
		filters["source"] = source
	if temperature:
		if temperature not in TEMPERATURES:
			frappe.throw(_("temperature must be one of {0}.").format(", ".join(TEMPERATURES)))
		filters["tbo_temperature"] = temperature
	if metric:
		board, _allowed = overview_board(resolve_period(period, None, None), None, None, None)
		filters["name"] = ("in", board.metric_leads(metric) or [""])
	return filters


@frappe.whitelist()
def get_lead(name: str) -> dict:
	"""One lead with its notes, tasks and activity."""
	require_crm()
	frappe.has_permission("Lead", "read", name, throw=True)
	lead = frappe.get_all("Lead", filters={"name": name}, fields=LEAD_FIELDS)[0]
	return {**serialize_leads([lead])[0], **app_timeline("Lead", name)}


@frappe.whitelist(methods=["POST"])
def create_lead(
	lead_name: str,
	organization: str | None = None,
	email: str | None = None,
	mobile_no: str | None = None,
	source: str | None = None,
	note: str | None = None,
	follow_up_on: str | None = None,
	follow_up_note: str | None = None,
	assign_to: str | None = None,
	source_channel: str | None = None,
	card_image: str | None = None,
	force: bool = False,
) -> dict:
	"""Create a lead owned by the session user.

	Everything is checked before the insert: the assignee, the source channel, the card image
	and possible duplicates. Duplicates without `force` raise DuplicateLead (HTTP 409) with the
	matches in the response.
	"""
	capabilities = require_crm()
	first_name, _space, last_name = (lead_name or "").strip().partition(" ")
	if not first_name:
		frappe.throw(_("Give the lead a name."))
	assignee = check_assignee(capabilities, assign_to)
	channel = check_source_channel(source_channel)
	card = check_card_image(card_image)
	matches = find_duplicates(mobile_no, email, organization)
	if matches and not cint(force):
		frappe.response["matches"] = matches
		frappe.throw(
			_("This looks like a lead the team already has. Check the matches or create it anyway."),
			DuplicateLead,
			title=_("Possible duplicate"),
		)

	lead = frappe.new_doc("Lead")
	lead.update(
		{
			"first_name": first_name,
			"last_name": last_name,
			"company_name": organization,
			"email_id": email,
			"mobile_no": mobile_no,
			"source": source or get_channel_lead_source(channel),
			"lead_owner": frappe.session.user,
		}
	)
	lead.insert()
	if assignee:
		add_assignment({"assign_to": [assignee], "doctype": "Lead", "name": lead.name})
	if note:
		add_note("Lead", lead.name, note)
	if follow_up_note:
		add_note("Lead", lead.name, follow_up_note)
	if follow_up_on:
		add_follow_up_task(
			"Lead",
			lead.name,
			follow_up_on,
			assigned_to=assignee or frappe.session.user,
			description=follow_up_note,
		)
	if channel:
		add_note("Lead", lead.name, f"{CAPTURE_NOTE_PREFIX} {SOURCE_CHANNELS[channel][0]} ({channel})")
	if matches:
		add_note("Lead", lead.name, describe_duplicates(matches))
	if card:
		upload_base64_file(card, f"{lead.name}-card.jpg", "Lead", lead.name)
	return get_lead(lead.name)


@frappe.whitelist()
def get_assignable_users() -> dict:
	"""Enabled sales users the session user may assign a new lead to: anyone for crm_scope all,
	their sales team for team, nobody for staff (own)."""
	capabilities = require_crm()
	return {"data": get_crm_users(assignable_team(capabilities))}


@frappe.whitelist()
def check_duplicates(
	phone: str | None = None, email: str | None = None, organization: str | None = None
) -> dict:
	"""Leads, deals, organizations, contacts and customers that look like this person.

	Records the caller cannot read come back without their name, title or status."""
	require_crm()
	return {"data": {"matches": find_duplicates(phone, email, organization)}}


# ---------- Lead capture: assignment, channel, card image ----------


def assignable_team(capabilities: frappe._dict) -> list[str] | None:
	"""Who the caller may assign to: None = any sales user (all), their sales team (team), else []."""
	if "all" in capabilities.crm_scopes:
		return None
	if "team" in capabilities.crm_scopes:
		return get_sales_team(frappe.session.user)
	return []


def get_crm_users(users: list[str] | None) -> list[dict]:
	"""Enabled system users with a sales role, limited to `users` unless None (one query)."""
	if users == []:
		return []
	User = frappe.qb.DocType("User")
	HasRole = frappe.qb.DocType("Has Role")
	query = (
		frappe.qb.from_(User)
		.join(HasRole)
		.on((HasRole.parent == User.name) & (HasRole.parenttype == "User"))
		.select(User.name.as_("user"), User.full_name, User.user_image.as_("image"))
		.distinct()
		.where(
			(User.enabled == 1)
			& (User.user_type == "System User")
			& User.name.notin(["Administrator", "Guest"])
			& HasRole.role.isin(list(CRM_ROLES))
		)
		.orderby(User.full_name)
	)
	if users is not None:
		query = query.where(User.name.isin(users))
	return query.run(as_dict=True)


def check_assignee(capabilities: frappe._dict, assign_to: str | None) -> str | None:
	"""The user to assign the new lead to, or None. Assigning to yourself is the default (you
	own the lead), so it is always allowed and adds nothing."""
	assign_to = (assign_to or "").strip()
	if not assign_to or assign_to == frappe.session.user:
		return None
	team = assignable_team(capabilities)
	if team is not None and assign_to not in team:
		frappe.throw(_("You can only assign leads to people in your sales team."), frappe.PermissionError)
	if not get_crm_users([assign_to]):
		frappe.throw(_("{0} is not an active sales user.").format(assign_to))
	return assign_to


def check_source_channel(source_channel: str | None) -> str | None:
	if not source_channel:
		return None
	if source_channel not in SOURCE_CHANNELS:
		frappe.throw(_("source_channel must be one of {0}.").format(", ".join(SOURCE_CHANNELS)))
	return source_channel


def get_channel_lead_source(channel: str | None) -> str | None:
	"""An existing Lead Source that names the channel; never creates one."""
	names = SOURCE_CHANNELS[channel][1] if channel else ()
	if not names:
		return None
	return frappe.db.get_value("Lead Source", {"name": ("in", names)}, "name")


def check_card_image(card_image: str | None) -> str | None:
	"""The base64 of a JPEG of at most 2 MB (a data: URL prefix is allowed), else ValidationError."""
	if not card_image:
		return None
	content = card_image.split(",", 1)[1] if card_image.startswith("data:") else card_image
	content = "".join(content.split())
	try:
		decoded = base64.b64decode(content, validate=True)
	except (binascii.Error, ValueError):
		frappe.throw(_("The card image is not valid base64."))
	if len(decoded) > CARD_IMAGE_MAX_BYTES:
		frappe.throw(_("The card image is larger than 2 MB."))
	if not decoded.startswith(JPEG_MAGIC):
		frappe.throw(_("The card image must be a JPEG."))
	return content


# ---------- Duplicates ----------


def duplicate_keys(phone: str | None, email: str | None, organization: str | None) -> frappe._dict:
	"""Normalised search keys: the phone's last 9 digits (7 at least), the lowercase email, and
	the lowercase organization with outer whitespace trimmed."""
	digits = re.sub(r"\D", "", phone or "")
	return frappe._dict(
		phone=digits[-PHONE_MATCH_DIGITS:] if len(digits) >= MIN_PHONE_DIGITS else None,
		email=(email or "").strip().lower() or None,
		organization=(organization or "").strip().lower() or None,
	)


def any_of(criteria: list):
	combined = EmptyCriterion()
	for criterion in criteria:
		combined |= criterion
	return combined


def phone_criterion(fields: list, key: str):
	return any_of([Right(RegexpReplace(field, "[^0-9]", ""), len(key)) == key for field in fields])


def text_criterion(fields: list, key: str):
	return any_of([Lower(Trim(field)) == key for field in fields])


def duplicate_sources() -> list[frappe._dict]:
	"""Per kind: the query, the fields each key is compared with, and the columns to return."""
	Lead = frappe.qb.DocType("Lead")
	Deal = frappe.qb.DocType("Opportunity")
	Organization = frappe.qb.DocType("Prospect")
	Contact = frappe.qb.DocType("Contact")
	ContactPhone = frappe.qb.DocType("Contact Phone")
	ContactEmail = frappe.qb.DocType("Contact Email")
	Link = frappe.qb.DocType("Dynamic Link")
	Customer = frappe.qb.DocType("Customer")
	# ERPNext makes a Contact for each new lead: the lead is the match, and its contact would show
	# a hidden lead's person to someone who may not see the lead
	lead_contacts = (
		frappe.qb.from_(Link)
		.select(Link.parent)
		.where((Link.parenttype == "Contact") & (Link.link_doctype == "Lead"))
	)
	return [
		frappe._dict(
			kind="lead",
			doctype="Lead",
			table=Lead,
			# A converted lead is found through its deal.
			query=frappe.qb.from_(Lead).where(Lead.name.notin(converted_leads_query())),
			phone=[Lead.mobile_no, Lead.phone],
			email=[Lead.email_id],
			organization=[Lead.company_name],
			title=Coalesce(Lead.lead_name, Lead.company_name, Lead.name),
			owner=Lead.lead_owner,
			status=Lead.tbo_stage,
		),
		frappe._dict(
			kind="deal",
			doctype="Opportunity",
			table=Deal,
			query=frappe.qb.from_(Deal),
			phone=[Deal.contact_mobile, Deal.phone, Deal.whatsapp],
			email=[Deal.contact_email],
			organization=[Deal.customer_name],
			title=Coalesce(Deal.customer_name, Deal.contact_display, Deal.name),
			owner=Deal.opportunity_owner,
			status=Deal.sales_stage,
		),
		frappe._dict(
			kind="organization",
			doctype="Prospect",
			table=Organization,
			query=frappe.qb.from_(Organization),
			phone=[],
			email=[],
			organization=[Organization.company_name],
			title=Organization.company_name,
			owner=Coalesce(Organization.prospect_owner, Organization.owner),
			status=None,
		),
		frappe._dict(
			kind="contact",
			doctype="Contact",
			table=Contact,
			query=frappe.qb.from_(Contact)
			.left_join(ContactPhone)
			.on((ContactPhone.parent == Contact.name) & (ContactPhone.parenttype == "Contact"))
			.left_join(ContactEmail)
			.on((ContactEmail.parent == Contact.name) & (ContactEmail.parenttype == "Contact"))
			.where(Contact.name.notin(lead_contacts)),
			phone=[ContactPhone.phone, Contact.mobile_no, Contact.phone],
			email=[ContactEmail.email_id, Contact.email_id],
			organization=[],
			title=Coalesce(Contact.full_name, Contact.name),
			owner=Contact.owner,
			status=Contact.status,
		),
		frappe._dict(
			kind="customer",
			doctype="Customer",
			table=Customer,
			query=frappe.qb.from_(Customer),
			phone=[],
			email=[],
			organization=[Customer.customer_name],
			title=Customer.customer_name,
			owner=Coalesce(Customer.account_manager, Customer.owner),
			status=Case().when(Customer.disabled == 1, "Disabled").else_("Active"),
		),
	]


def find_duplicates(phone: str | None, email: str | None, organization: str | None) -> list[dict]:
	"""Up to 10 records matching the phone, email or organization, leads first: one grouped
	query per doctype, then one frappe.get_list per doctype for what the caller may read."""
	keys = duplicate_keys(phone, email, organization)
	matches = []
	for source in duplicate_sources():
		remaining = DUPLICATE_LIMIT - len(matches)
		if remaining <= 0:
			break
		matches += search_duplicates(source, keys, remaining)
	return serialize_duplicates(matches)


def search_duplicates(source: frappe._dict, keys: frappe._dict, limit: int) -> list[frappe._dict]:
	checks = {}
	if keys.phone and source.phone:
		checks["phone"] = phone_criterion(source.phone, keys.phone)
	if keys.email and source.email:
		checks["email"] = text_criterion(source.email, keys.email)
	if keys.organization and source.organization:
		checks["organization"] = text_criterion(source.organization, keys.organization)
	if not checks:
		return []
	table = source.table
	status = Max(source.status) if source.status is not None else LiteralValue("NULL")
	rows = (
		source.query.select(
			table.name,
			Max(source.title).as_("title"),
			Max(source.owner).as_("owner"),
			status.as_("status"),
			*(Max(Case().when(check, 1).else_(0)).as_(f"on_{key}") for key, check in checks.items()),
		)
		.where(any_of(list(checks.values())))
		.groupby(table.name)
		.orderby(Max(table.modified), order=frappe.qb.desc)
		.limit(limit)
	).run(as_dict=True)
	for row in rows:
		row.kind, row.doctype = source.kind, source.doctype
		row.match_on = [key for key in checks if row.get(f"on_{key}")]
	return rows


def serialize_duplicates(matches: list) -> list[dict]:
	"""Details only for records the caller can read; for the rest, that a match exists and whose."""
	readable = set()
	for doctype in {match.doctype for match in matches}:
		names = [match.name for match in matches if match.doctype == doctype]
		readable |= {
			(doctype, name)
			for name in frappe.get_list(doctype, filters={"name": ("in", names)}, pluck="name")
		}
	# Hidden matches may show the owner's name, never their user id (email).
	owners = get_full_names({match.owner for match in matches}, fallback_to_id=False)
	result = []
	for match in matches:
		visible = (match.doctype, match.name) in readable
		result.append(
			{
				"kind": match.kind,
				"name": match.name if visible else None,
				"title": match.title
				if visible
				else _("Someone on the team already has this {0}").format(match.kind),
				"owner_name": owners.get(match.owner, match.owner if visible else None),
				"status": match.status if visible else None,
				"match_on": match.match_on,
				"can_open": visible and match.kind in OPENABLE_KINDS,
			}
		)
	return result


def describe_duplicates(matches: list[dict]) -> str:
	"""The note on a lead created despite matches. Hidden records are named by kind and owner only."""
	parts = [
		f"{match['kind']} {match['name']} ({match['title']})"
		if match["name"]
		else _("a {0} owned by {1}").format(match["kind"], match["owner_name"] or _("someone on the team"))
		for match in matches
	]
	return _("Created despite possible duplicate of {0}").format("; ".join(parts))


@frappe.whitelist(methods=["POST"])
def set_lead_status(name: str, status: str, lost_reason: str | None = None) -> dict:
	"""Change a lead's stage. A Lost-type stage (Closed – Lost) needs a reason, as for deals."""
	require_crm()
	lead = set_lead_stage(name, status, lost_reason)
	return {"name": lead.name, "status": lead.tbo_stage, "lost_reason": lead_lost_reason(lead)}


@frappe.whitelist(methods=["POST"])
def convert_lead(name: str) -> dict:
	"""The lead's deal; a converted lead returns its existing deal. With the lead pipeline on,
	only a Closed – Won lead gets its deal (docs/lead-pipeline.md §6)."""
	require_crm()
	return get_deal(make_lead_deal(name))


def serialize_leads(leads: list) -> list[dict]:
	names = [lead.name for lead in leads]
	next_follow_up = next_follow_ups("Lead", names)
	last_contact = last_calls("Lead", names)
	converted = leads_with_opportunity(names)
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
			"score": cint(lead.score),
			"temperature": lead.temperature,
			"converted": lead.name in converted,
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
	scope: str | None = None,
) -> dict:
	"""Deals the session user may read in `scope`, optionally one stage. `mine=1` is scope=mine."""
	capabilities = require_crm()
	filters = scope_filter(capabilities, "mine" if cint(mine) else scope, "opportunity_owner")
	if stage:
		filters["sales_stage"] = stage
	or_filters = search_filters(
		search, ["customer_name", "contact_display", "contact_email", "contact_mobile"]
	)
	start, length = page_window(page, page_length)
	deals = frappe.get_list(
		"Opportunity",
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
	require_crm()
	frappe.has_permission("Opportunity", "read", name, throw=True)
	deal = frappe.get_doc("Opportunity", name)
	row = frappe.get_all("Opportunity", filters={"name": name}, fields=DEAL_FIELDS)[0]
	return {
		**serialize_deals([row])[0],
		"erpnext_customer": customer_of(deal),
		"stage_history": [
			{"stage": log.stage, "at": log.changed_on} for log in deal.get("tbo_stage_log") or []
		],
		**app_timeline("Opportunity", name),
	}


@frappe.whitelist(methods=["POST"])
def move_deal_stage(name: str, stage: str, lost_reason: str | None = None) -> dict:
	"""Move a deal to another Sales Stage. A Lost-type stage needs a reason.

	A reason that is an Opportunity Lost Reason is used as it is; free text is saved as lost
	reason "Other" with the text as the detailed reason.
	"""
	require_crm()
	deal = frappe.get_doc("Opportunity", name)
	deal.check_permission("write")
	if not frappe.db.exists("Sales Stage", stage):
		frappe.throw(_("{0} is not a deal stage.").format(stage))
	deal.sales_stage = stage
	# a Percent column reads 0 when unset: keep the deal's own probability unless the stage sets
	# one, or closes the deal (Won 100, Lost 0)
	probability = flt(frappe.db.get_value("Sales Stage", stage, "tbo_probability"))
	if probability or stage_type(stage) in CLOSED_STAGE_TYPES:
		deal.probability = probability
	if stage_type(stage) == "Lost":
		set_lost_reason(deal, lost_reason, _("Give a reason for losing the deal."))
	deal.save()
	return get_deal(deal.name)


def deal_lost_reasons(names: list[str]) -> dict[str, str]:
	"""{deal: its first lost reason} (one query)."""
	if not names:
		return {}
	Row = frappe.qb.DocType("Opportunity Lost Reason Detail")
	return dict(
		(
			frappe.qb.from_(Row)
			.select(Row.parent, Max(Row.lost_reason))
			.where((Row.parenttype == "Opportunity") & Row.parent.isin(names))
			.groupby(Row.parent)
		).run()
	)


def serialize_deals(deals: list) -> list[dict]:
	names = [deal.name for deal in deals]
	types = stage_types()
	owners = get_full_names({deal.deal_owner for deal in deals})
	next_follow_up = next_follow_ups("Opportunity", names)
	reasons = deal_lost_reasons(names)
	result = []
	for deal in deals:
		contact = deal.lead_name
		result.append(
			{
				"name": deal.name,
				"title": deal.organization or contact or deal.name,
				"organization": deal.organization,
				"contact": contact,
				"stage": deal.status,
				"stage_type": types.get(deal.status) if deal.status else None,
				"amount": flt(deal.deal_value),
				"currency": deal.currency,
				"probability": flt(deal.probability),
				"expected_close": deal.expected_closure_date,
				"closed_on": deal.closed_date,
				"owner": owners.get(deal.deal_owner, deal.deal_owner),
				"owner_id": deal.deal_owner,
				"lost_reason": deal.lost_notes or reasons.get(deal.name),
				"next_step": deal.next_step,
				"phone": deal.mobile_no,
				"email": deal.email,
				"next_follow_up": next_follow_up.get(deal.name),
				"created": deal.creation,
			}
		)
	return result


# ---------- Organizations ----------

ORGANIZATION_FIELDS = [
	"name",
	"company_name as organization_name",
	"territory",
	"industry",
	"website",
	"no_of_employees",
]


@frappe.whitelist()
def get_organizations(search: str | None = None, page: int = 1, page_length: int = 20) -> dict:
	"""Organizations (Prospects) with open deal counts and their ERPNext Customers."""
	require_crm()
	or_filters = search_filters(search, ["company_name", "website"])
	start, length = page_window(page, page_length)
	organizations = frappe.get_list(
		"Prospect",
		fields=ORGANIZATION_FIELDS,
		or_filters=or_filters,
		order_by="company_name asc",
		start=start,
		page_length=length + 1,
	)
	return page_envelope(serialize_organizations(organizations), page, page_length)


@frappe.whitelist()
def get_organization(name: str) -> dict:
	"""One organization with its contacts and deals."""
	require_crm()
	frappe.has_permission("Prospect", "read", name, throw=True)
	organization = frappe.get_all("Prospect", filters={"name": name}, fields=ORGANIZATION_FIELDS)[0]
	contacts = frappe.get_list(
		"Contact",
		filters={"company_name": name},
		fields=["name", "full_name", "designation", "mobile_no", "phone", "email_id"],
		order_by="full_name asc",
	)
	deals = frappe.get_list(
		"Opportunity",
		or_filters=organization_deal_filters([name]),
		fields=DEAL_FIELDS,
		order_by="modified desc",
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


def organization_deal_filters(names: list[str]) -> dict:
	"""A deal belongs to an organization when it is made out to it or names it as the client."""
	return {"customer_name": ("in", names), "party_name": ("in", names)}


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
	"""Open deal count and ERPNext customers per organization: the Customers made from it
	(`prospect_name`) and those of the deals the user can read."""
	if not names:
		return {}, {}
	deals = frappe.get_list(
		"Opportunity",
		or_filters=organization_deal_filters(names),
		fields=[
			"name",
			"customer_name",
			"party_name",
			"opportunity_from",
			"sales_stage",
			"tbo_customer",
		],
	)
	types = stage_types()
	open_deals, customers = {}, {}
	for name, customer in frappe.get_all(
		"Customer", filters={"prospect_name": ("in", names)}, fields=["prospect_name", "name"], as_list=True
	):
		customers.setdefault(name, set()).add(customer)
	deal_customers = customers_of(deals)
	for deal in deals:
		organization = deal.party_name if deal.party_name in names else deal.customer_name
		if types.get(deal.sales_stage) not in CLOSED_STAGE_TYPES:
			open_deals[organization] = open_deals.get(organization, 0) + 1
		customer = deal_customers.get(deal.name)
		if customer:
			customers.setdefault(organization, set()).add(customer)
	return open_deals, customers


# ---------- Sales insights ----------


@frappe.whitelist()
def get_sales_insights(
	period: str = "month", scope: str | None = None, stalled_days: int = STALLED_DAYS
) -> dict:
	"""Read-only pipeline, win/loss, funnel, activity and trend aggregates, cached for a few minutes.

	`scope` (mine / team / all, default the widest in crm_scopes) filters on the deal and lead
	owner. Values are the deals' amounts in the company currency (`base_opportunity_amount`).
	"""
	capabilities = require("sales_insights", _("Only Sales Managers can see sales insights."))
	scope = resolve_crm_scope(capabilities, scope)
	stalled_days = min(max(cint(stalled_days) or STALLED_DAYS, 1), 365)
	windows = period_windows(period)
	return cached_insights(
		"sales", (period, scope, stalled_days), lambda: build_sales_insights(windows, scope, stalled_days)
	)


def build_sales_insights(windows: frappe._dict, scope: str, stalled_days: int) -> dict:
	owners = scope_users(scope)
	closed = get_closed_deals(windows.trend[0].from_date, owners)
	won = summarize_closed(closed, "Won", windows)
	lost = summarize_closed(closed, "Lost", windows)
	win_rate = percent(won["count"], won["count"] + lost["count"])
	previous_win_rate = percent(won["previous_count"], won["previous_count"] + lost["previous_count"])
	return {
		"period": windows.period,
		"from_date": windows.from_date,
		"to_date": windows.to_date,
		"previous": {"from_date": windows.previous_from, "to_date": windows.previous_to},
		"scope": scope,
		"currency": default_currency(),
		"pipeline": get_pipeline(owners),
		"won": won,
		"lost": lost,
		"win_rate": win_rate,
		"win_rate_delta": round(win_rate - previous_win_rate, 1),
		"stalled": get_stalled_deals(owners, stalled_days),
		"leads_funnel": get_leads_funnel(windows, owners),
		"activity": get_activity_by_owner(windows, owners),
		"trend": get_sales_trend(windows, owners, closed),
		"closing": get_closing_deals(windows, owners),
		"generated_at": now_datetime(),
	}


def deal_value(Deal):
	"""A deal's value in the company currency."""
	return Coalesce(Deal.base_opportunity_amount, 0)


def owned_by(field, owners: list[str] | None):
	"""Owner criterion for a scope; no filter for scope=all."""
	return field.isin(owners) if owners else EmptyCriterion()


def open_stage(Stage):
	"""A Sales Stage that is neither Won nor Lost (a blank type reads as Ongoing)."""
	return IfNull(Stage.tbo_stage_type, "").notin(CLOSED_STAGE_TYPES)


def get_pipeline(owners: list[str] | None) -> dict:
	"""Open stages in pipeline order: count, value, weighted value, average age and days in stage."""
	Deal = frappe.qb.DocType("Opportunity")
	Stage = frappe.qb.DocType("Sales Stage")
	Log = frappe.qb.DocType("TBO Stage Change")
	now = now_datetime()
	value = deal_value(Deal)
	stages = (
		frappe.qb.from_(Stage)
		.left_join(Deal)
		.on((Deal.sales_stage == Stage.name) & owned_by(Deal.opportunity_owner, owners))
		.select(
			Stage.name.as_("stage"),
			Stage.tbo_stage_type.as_("type"),
			Count(Deal.name).as_("count"),
			Sum(value).as_("value"),
			Sum(value * IfNull(Deal.probability, 0) / 100).as_("weighted"),
			Avg(TimestampDiff(LiteralValue("DAY"), Deal.creation, now)).as_("avg_age_days"),
		)
		.where(open_stage(Stage))
		.groupby(Stage.name, Stage.tbo_stage_type, Stage.tbo_position)
		.orderby(IfNull(Stage.tbo_position, 10_000))
		.orderby(Stage.name)
	).run(as_dict=True)
	# when each deal entered its current stage: its newest log row for that stage
	entered = (
		frappe.qb.from_(Deal)
		.join(Log)
		.on((Log.parent == Deal.name) & (Log.parenttype == "Opportunity") & (Log.stage == Deal.sales_stage))
		.select(Deal.name, Deal.sales_stage, Max(Log.changed_on).as_("since"))
		.where(
			Deal.sales_stage.isin([row.stage for row in stages] or [""])
			& owned_by(Deal.opportunity_owner, owners)
		)
		.groupby(Deal.name, Deal.sales_stage)
	).run(as_dict=True)
	days_in: dict[str, list[float]] = {}
	for row in entered:
		days_in.setdefault(row.sales_stage, []).append((now - row.since).total_seconds() / 86400)
	by_stage = [
		{
			"stage": row.stage,
			"type": row.type or "Ongoing",
			"count": row["count"],
			"value": flt(row.value),
			"weighted": flt(row.weighted),
			"avg_age_days": round(flt(row.avg_age_days), 1) if row["count"] else None,
			"avg_days_in_stage": round(sum(days_in[row.stage]) / len(days_in[row.stage]), 1)
			if days_in.get(row.stage)
			else None,
		}
		for row in stages
	]
	return {
		"count": sum(row["count"] for row in by_stage),
		"value": sum(row["value"] for row in by_stage),
		"weighted": sum(row["weighted"] for row in by_stage),
		"by_stage": by_stage,
	}


def get_closed_deals(from_date, owners: list[str] | None) -> list:
	"""Won and lost deals per closing day since from_date, in one grouped query. A deal closes on
	the day it entered its Won or Lost stage (`tbo_closed_on`), else on its last edit."""
	Deal = frappe.qb.DocType("Opportunity")
	Stage = frappe.qb.DocType("Sales Stage")
	closed_on = Coalesce(Deal.tbo_closed_on, Date(Deal.modified))
	return (
		frappe.qb.from_(Deal)
		.join(Stage)
		.on(Deal.sales_stage == Stage.name)
		.select(
			Stage.tbo_stage_type.as_("type"),
			closed_on.as_("closed_on"),
			Count(Deal.name).distinct().as_("count"),
			Sum(deal_value(Deal)).as_("value"),
		)
		.where(
			Stage.tbo_stage_type.isin(CLOSED_STAGE_TYPES)
			& owned_by(Deal.opportunity_owner, owners)
			& (closed_on >= from_date)
		)
		.groupby(Stage.tbo_stage_type, closed_on)
	).run(as_dict=True)


def summarize_closed(closed: list, kind: str, windows: frappe._dict) -> dict:
	totals = {"count": 0, "value": 0.0, "previous_count": 0, "previous_value": 0.0}
	for row in closed:
		if row.type != kind:
			continue
		if in_window(row.closed_on, windows.from_date, windows.to_date):
			totals["count"] += row["count"]
			totals["value"] += flt(row.value)
		elif in_window(row.closed_on, windows.previous_from, windows.previous_to):
			totals["previous_count"] += row["count"]
			totals["previous_value"] += flt(row.value)
	return {
		"count": totals["count"],
		"value": totals["value"],
		"count_delta": totals["count"] - totals["previous_count"],
		"value_delta": totals["value"] - totals["previous_value"],
		"previous_count": totals["previous_count"],
		"previous_value": totals["previous_value"],
	}


def get_stalled_deals(owners: list[str] | None, stalled_days: int) -> dict:
	"""Open deals with no edit, call, note, task change or comment in the last N days."""
	Deal = frappe.qb.DocType("Opportunity")
	Stage = frappe.qb.DocType("Sales Stage")
	cutoff = add_days(now_datetime(), -stalled_days)
	stalled = open_stage(Stage) & owned_by(Deal.opportunity_owner, owners) & (Deal.modified < cutoff)
	for activity in recent_deal_activity(cutoff):
		stalled &= Deal.name.notin(activity)
	value = deal_value(Deal)
	base = frappe.qb.from_(Deal).join(Stage).on(Deal.sales_stage == Stage.name).where(stalled)
	summary = base.select(Count(Deal.name).as_("count"), Sum(value).as_("value")).run(as_dict=True)[0]
	deals = (
		base.select(
			Deal.name,
			Deal.customer_name.as_("organization"),
			Deal.contact_display.as_("lead_name"),
			Deal.sales_stage.as_("stage"),
			value.as_("value"),
			Deal.opportunity_owner.as_("deal_owner"),
			Deal.modified.as_("idle_since"),
		)
		.orderby(value, order=frappe.qb.desc)
		.limit(INSIGHT_LIST_LIMIT)
	).run(as_dict=True)
	names = get_full_names({deal.deal_owner for deal in deals})
	return {
		"days": stalled_days,
		"count": summary["count"] or 0,
		"value": flt(summary.value),
		"deals": [
			{
				"name": deal.name,
				"title": deal.organization or deal.lead_name or deal.name,
				"stage": deal.stage,
				"value": flt(deal.value),
				"owner": names.get(deal.deal_owner, deal.deal_owner),
				"owner_id": deal.deal_owner,
				"idle_since": deal.idle_since,
			}
			for deal in deals
		],
	}


def recent_deal_activity(cutoff) -> list:
	"""Subqueries of deals with a call or logged activity, a note, or a task change since cutoff."""
	Event = frappe.qb.DocType("Event")
	Participant = frappe.qb.DocType("Event Participants")
	Comment = frappe.qb.DocType("Comment")
	ToDo = frappe.qb.DocType("ToDo")
	return [
		frappe.qb.from_(Participant)
		.join(Event)
		.on(Event.name == Participant.parent)
		.select(Participant.reference_docname)
		.where((Participant.reference_doctype == "Opportunity") & (Event.creation >= cutoff)),
		frappe.qb.from_(Comment)
		.select(Comment.reference_name)
		.where(
			(Comment.reference_doctype == "Opportunity")
			& Comment.reference_name.isnotnull()
			& (Comment.comment_type == "Comment")
			& (Comment.creation >= cutoff)
		),
		frappe.qb.from_(ToDo)
		.select(ToDo.reference_name)
		.where(
			(ToDo.reference_type == "Opportunity")
			& ToDo.reference_name.isnotnull()
			& (ToDo.modified >= cutoff)
		),
	]


def get_leads_funnel(windows: frappe._dict, owners: list[str] | None) -> dict:
	"""Leads created in the period, how many are qualified (a Won-type stage, or converted) and
	how many became deals, with the change against the previous span."""
	Lead = frappe.qb.DocType("Lead")
	converted = Lead.name.isin(converted_leads_query())
	qualified = converted | Lead.tbo_stage.isin(WON_STATUSES)

	def created_in(from_date, to_date):
		return (Lead.creation >= from_date) & (Lead.creation < add_days(to_date, 1))

	def count_if(criterion):
		return Sum(Case().when(criterion, 1).else_(0))

	current = created_in(windows.from_date, windows.to_date)
	previous = created_in(windows.previous_from, windows.previous_to)
	row = (
		frappe.qb.from_(Lead)
		.select(
			count_if(current).as_("new"),
			count_if(current & qualified).as_("qualified"),
			count_if(current & converted).as_("converted"),
			count_if(previous).as_("previous_new"),
			count_if(previous & converted).as_("previous_converted"),
		)
		.where(
			owned_by(Lead.lead_owner, owners)
			& (Lead.creation >= windows.previous_from)
			& (Lead.creation < add_days(windows.to_date, 1))
		)
	).run(as_dict=True)[0]
	new, converted_count = cint(row.new), cint(row.converted)
	return {
		"new": new,
		"qualified": cint(row.qualified),
		"converted": converted_count,
		"qualified_pct": percent(row.qualified, new),
		"converted_pct": percent(converted_count, new),
		"new_delta": new - cint(row.previous_new),
		"converted_delta": converted_count - cint(row.previous_converted),
	}


def get_activity_by_owner(windows: frappe._dict, owners: list[str] | None) -> list[dict]:
	"""Calls logged, notes written and sales tasks done in the period, per person (three grouped
	queries)."""
	start, end = windows.from_date, add_days(windows.to_date, 1)
	Event = frappe.qb.DocType("Event")
	Comment = frappe.qb.DocType("Comment")
	ToDo = frappe.qb.DocType("ToDo")
	on_sales = ("Lead", "Opportunity")
	counts = {}
	for key, table, user, criterion in (
		(
			"calls",
			Event,
			Event.owner,
			(Event.event_category == CALL) & (Event.creation >= start) & (Event.creation < end),
		),
		(
			"notes",
			Comment,
			Comment.owner,
			(Comment.comment_type == "Comment")
			& Comment.reference_doctype.isin(on_sales)
			& (Comment.creation >= start)
			& (Comment.creation < end),
		),
		(
			"tasks_done",
			ToDo,
			ToDo.allocated_to,
			(ToDo.status == DONE)
			& ToDo.reference_type.isin(on_sales)
			& (ToDo.modified >= start)
			& (ToDo.modified < end),
		),
	):
		rows = (
			frappe.qb.from_(table)
			.select(user.as_("user"), Count(table.name))
			.where(criterion & user.isnotnull() & owned_by(user, owners))
			.groupby(user)
		).run()
		for person, count in rows:
			counts.setdefault(person, {"calls": 0, "notes": 0, "tasks_done": 0})[key] = count
	names = get_full_names(set(counts))
	activity = [
		{"user": person, "name": names.get(person, person), **row, "total": sum(row.values())}
		for person, row in counts.items()
	]
	return sorted(activity, key=lambda row: (-row["total"], row["name"]))


def get_sales_trend(windows: frappe._dict, owners: list[str] | None, closed: list) -> list[dict]:
	"""Won value and count, and new leads, for each of the 8 trend periods."""
	trend = [
		{
			"label": window.label,
			"from_date": window.from_date,
			"to_date": window.to_date,
			"won_value": 0.0,
			"won_count": 0,
			"new_leads": 0,
		}
		for window in windows.trend
	]
	for row in closed:
		index = trend_index(windows, row.closed_on) if row.type == "Won" else None
		if index is not None:
			trend[index]["won_value"] += flt(row.value)
			trend[index]["won_count"] += row["count"]
	Lead = frappe.qb.DocType("Lead")
	created = Date(Lead.creation)
	leads = (
		frappe.qb.from_(Lead)
		.select(created, Count(Lead.name))
		.where(owned_by(Lead.lead_owner, owners) & (Lead.creation >= windows.trend[0].from_date))
		.groupby(created)
	).run()
	for day, count in leads:
		index = trend_index(windows, day)
		if index is not None:
			trend[index]["new_leads"] += count
	return trend


def get_closing_deals(windows: frappe._dict, owners: list[str] | None) -> list[dict]:
	"""The biggest open deals expected to close in the current period."""
	Deal = frappe.qb.DocType("Opportunity")
	Stage = frappe.qb.DocType("Sales Stage")
	value = deal_value(Deal)
	deals = (
		frappe.qb.from_(Deal)
		.join(Stage)
		.on(Deal.sales_stage == Stage.name)
		.select(
			Deal.name,
			Deal.customer_name.as_("organization"),
			Deal.contact_display.as_("lead_name"),
			Deal.sales_stage.as_("stage"),
			value.as_("value"),
			Deal.probability,
			Deal.expected_closing,
			Deal.opportunity_owner.as_("deal_owner"),
		)
		.where(
			open_stage(Stage)
			& owned_by(Deal.opportunity_owner, owners)
			& Deal.expected_closing.between(windows.from_date, windows.end_date)
		)
		.orderby(value, order=frappe.qb.desc)
		.limit(INSIGHT_LIST_LIMIT)
	).run(as_dict=True)
	names = get_full_names({deal.deal_owner for deal in deals})
	return [
		{
			"name": deal.name,
			"title": deal.organization or deal.lead_name or deal.name,
			"stage": deal.stage,
			"value": flt(deal.value),
			"probability": flt(deal.probability),
			"expected_close": deal.expected_closing,
			"owner": names.get(deal.deal_owner, deal.deal_owner),
			"owner_id": deal.deal_owner,
		}
		for deal in deals
	]


# ---------- Follow-ups and activity ----------


@frappe.whitelist()
def get_my_follow_ups(scope: str | None = None) -> list[dict]:
	"""Open tasks on leads and deals due today or overdue, oldest first: assigned to me
	(scope=mine, the default), to my sales team (team) or to anyone (all)."""
	capabilities = require_crm()
	users = scope_users(resolve_crm_scope(capabilities, scope or "mine"))
	filters = {
		"status": OPEN,
		"date": ("<=", today()),
		"reference_type": ("in", list(REF_DOCTYPES.values())),
	}
	if users:
		filters["allocated_to"] = ("in", users)
	# the scope is the permission: the caller may see the follow-ups of these people
	tasks = frappe.get_all(
		"ToDo",
		filters=filters,
		fields=[
			"name",
			"description",
			"status",
			"priority",
			"date",
			"allocated_to",
			"reference_type",
			"reference_name",
		],
		order_by="date asc",
		limit=100,
	)
	refs = get_ref_labels(tasks)
	assignees = get_full_names({task.allocated_to for task in tasks})
	now = getdate()
	return [
		{
			"id": task.name,
			"kind": "task",
			"subject": task_title(task.description),
			"status": TASK_STATUS_LABELS.get(task.status, task.status),
			"priority": task.priority,
			"due": task.date,
			"overdue": getdate(task.date) < now,
			"with": refs.get(task.reference_name, {}).get("with"),
			"organization": refs.get(task.reference_name, {}).get("organization"),
			"ref": ref_of(task.reference_type, task.reference_name),
			"assigned_to": task.allocated_to,
			"assigned_to_name": assignees.get(task.allocated_to, task.allocated_to),
		}
		for task in tasks
	]


@frappe.whitelist()
def get_call_today(user: str | None = None) -> dict:
	"""Today's Call today list (docs/smart-leads.md §4.3): overdue and due follow-ups first, then
	the best-scored leads with no plan, with the score's reason, last touch and phone links.
	`user` opens a teammate's list (team or all scope only). Needs Lead scoring switched on."""
	require_crm()
	if not scoring_enabled():
		frappe.throw(_("Lead scoring is switched off in TBO Smart Settings."), title=_("Switched off"))
	return build_call_today(user)


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

	call -> a completed call Event (to `phone`, default the record's mobile) with the text as
	its description; note -> a Comment; meeting / email / whatsapp -> an Event "Meeting: …".
	A call or meeting closes my open follow-ups on the record that are due by today.
	next_follow_up (a date) adds a ToDo assigned to me.
	"""
	require_crm()
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
		add_log(reference_doctype, name, kind, text)

	if kind in ("call", "meeting"):
		close_due_follow_ups(reference_doctype, name)
	if next_follow_up:
		add_follow_up_task(reference_doctype, name, next_follow_up)
	return app_timeline(reference_doctype, name)


@frappe.whitelist(methods=["POST"])
def add_follow_up(doctype: str, name: str, due: str, title: str | None = None) -> dict:
	"""Schedule a follow-up (a ToDo assigned to me) on a lead or deal."""
	require_crm()
	reference_doctype = REF_DOCTYPES.get(doctype)
	if not reference_doctype:
		frappe.throw(_("doctype must be lead or deal."))
	frappe.has_permission(reference_doctype, "read", name, throw=True)
	task = add_follow_up_task(reference_doctype, name, due, title)
	return {
		"id": task.name,
		"subject": task_title(task.description),
		"due": task.date,
		"status": TASK_STATUS_LABELS[task.status],
	}


@frappe.whitelist(methods=["POST"])
def set_task_done(name: str, done: bool = True) -> dict:
	"""Tick or untick a task."""
	require_crm()
	task = frappe.get_doc("ToDo", name)
	task.check_permission("write")
	task.status = DONE if cint(done) else OPEN
	task.save()
	return {"id": task.name, "status": TASK_STATUS_LABELS[task.status], "done": task.status == DONE}


def app_timeline(reference_doctype: str, name: str) -> dict:
	"""The record's timeline with the task statuses the app knows."""
	timeline = get_timeline(reference_doctype, name)
	for task in timeline["tasks"]:
		task["status"] = TASK_STATUS_LABELS.get(task["status"], task["status"])
	return timeline


def get_ref_labels(tasks: list) -> dict:
	"""Contact name and organization for each lead or deal the tasks point at (two queries)."""
	labels = {}
	for doctype, fields in (
		("Lead", ["name", "lead_name as contact", "company_name as organization"]),
		("Opportunity", ["name", "contact_display as contact", "customer_name as organization"]),
	):
		names = [task.reference_name for task in tasks if task.reference_type == doctype]
		if names:
			for row in frappe.get_all(doctype, filters={"name": ("in", names)}, fields=fields):
				labels[row.name] = {"with": row.contact, "organization": row.organization}
	return labels


def ref_of(reference_doctype: str | None, name: str | None) -> dict | None:
	kind = {doctype: key for key, doctype in REF_DOCTYPES.items()}.get(reference_doctype)
	return {"doctype": kind, "name": name} if kind else None


def require_crm() -> frappe._dict:
	return require("crm", _("Your role has no access to CRM."))


def scope_filter(capabilities: frappe._dict, scope: str | None, owner_field: str) -> dict:
	"""get_list filters for a lead or deal list scope (checked against crm_scopes)."""
	users = list_scope_users(capabilities, resolve_crm_scope(capabilities, scope))
	return {owner_field: ("in", users)} if users else {}


def search_filters(search: str | None, fields: list[str]) -> dict | None:
	search = (search or "").strip()
	if not search:
		return None
	return {field: ("like", f"%{search}%") for field in fields}
