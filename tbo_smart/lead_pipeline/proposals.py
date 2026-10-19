"""Proposals to a lead (docs/proposals.md).

A proposal (TBO Proposal) is a lead's document in ordered blocks (cover, about us, challenge,
solution, scope, timeline, pricing, terms, custom), started from a template (TBO Proposal
Template) and optionally drafted by the AI from the lead, its notes and its meetings' minutes.
Its pricing is the lead's own ERPNext Quotation (lead_quotation.save_quotation), so the price
lives in one place and the segment band and discount approval apply. The client reads it at
`/proposal/<token>` (www/proposal.py) and accepts or declines there; acceptance moves the
lead along the pipeline's won path (quotations.set_status, transitions.advance_lead).

Nothing goes to the client until the salesperson presses Send, and Send is refused while the
pricing waits for approval. Everything is behind TBO Smart Settings → Proposals (off by
default; needs Quotations on the lead); the AI draft has its own switch, also off.
"""

import re
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import add_days, cint, escape_html, get_url, getdate, now_datetime, today

from tbo_smart import ai
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.lead_pipeline import quotations_enabled, setting
from tbo_smart.lead_pipeline.statuses import CLOSED_WON, PROPOSAL_SENT
from tbo_smart.lead_pipeline.transitions import advance_lead
from tbo_smart.sales.activities import plain_text

DOCTYPE = "TBO Proposal"
TEMPLATE = "TBO Proposal Template"
DRAFT, SENT, VIEWED, ACCEPTED, DECLINED, EXPIRED = (
	"Draft",
	"Sent",
	"Viewed",
	"Accepted",
	"Declined",
	"Expired",
)
WITH_CLIENT = (SENT, VIEWED)
SECTION_TYPES = (
	"Cover",
	"About us",
	"Challenge",
	"Solution",
	"Scope",
	"Timeline",
	"Pricing",
	"Terms",
	"Custom",
)
PRICING = "Pricing"
THEMES = ("TBO Teal", "Graphite")
DEFAULT_VALIDITY = 30
MAX_SECTIONS = 30
MAX_HEADING = 140
MAX_BODY = 8000
MAX_REASON = 1000
TOKEN = re.compile(r"^[0-9a-f]{32}$")

TRACKING_FIELDS = (
	"public_token",
	"sent_on",
	"sent_to",
	"first_viewed_on",
	"last_viewed_on",
	"decided_on",
	"decided_by_name",
	"decided_by_email",
	"decline_reason",
)
# a new proposal's workflow fields, whatever a caller sent (Proposal.check_locked_fields)
NEW_PROPOSAL = {
	"status": DRAFT,
	"quotation": None,
	"proposal_family": None,
	"revision_no": 0,
	"is_latest": 1,
	"view_count": 0,
	**{field: None for field in TRACKING_FIELDS if field != "public_token"},
}
# set only by this module's workflow (Proposal.check_locked_fields)
LOCKED_FIELDS = (
	"status",
	"quotation",
	"proposal_family",
	"revision_no",
	"is_latest",
	"view_count",
	*TRACKING_FIELDS,
)


def proposals_enabled() -> bool:
	"""TBO Smart Settings → Proposals (needs Quotations on the lead)."""
	return quotations_enabled() and bool(cint(setting("enable_proposals")))


def ai_enabled() -> bool:
	"""TBO Smart Settings → AI proposal drafts, with a provider set up."""
	return proposals_enabled() and bool(cint(setting("enable_proposal_ai"))) and ai.ai_ready()


def require_proposals() -> None:
	if not proposals_enabled():
		frappe.throw(_("Proposals are not switched on."), frappe.ValidationError)


def validity_days() -> int:
	return cint(setting("proposal_validity_days")) or DEFAULT_VALIDITY


def clean(value, limit: int) -> str:
	return str(value or "").strip()[:limit]


# ---------- the documents ----------


class Proposal:
	"""TBO Proposal's controller (tbo_smart.tbo_smart.doctype.tbo_proposal)."""

	def validate(self):
		self.check_sections()
		self.check_quotation()
		self.check_locked_fields()

	def before_insert(self):
		self.set_token()

	def after_insert(self):
		self.start_family()

	def check_sections(self):
		if len(self.sections) > MAX_SECTIONS:
			frappe.throw(_("A proposal has at most {0} blocks.").format(MAX_SECTIONS))
		for row in self.sections:
			row.heading = clean(row.heading, MAX_HEADING) or None
			row.body = clean(row.body, MAX_BODY) or None

	def check_quotation(self):
		"""The pricing is the lead's own quotation, never another lead's (the client's page shows it)."""
		if not self.quotation:
			return
		row = frappe.db.get_value(
			"Quotation",
			self.quotation,
			["quotation_to", "party_name", "tbo_lead", "opportunity"],
			as_dict=True,
		)
		if not row or q.lead_of(row) != self.lead:
			frappe.throw(_("The pricing must be a quotation of this lead."), frappe.PermissionError)

	def check_locked_fields(self):
		"""Status, link, revision and tracking fields change only through this module
		(`flags.proposal_workflow`), and a sent proposal's content only through a revision."""
		if self.flags.proposal_workflow:
			return
		if self.is_new():
			# a new proposal starts as a fresh draft whatever was sent (its link is set by before_insert)
			self.update(NEW_PROPOSAL)
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if any(self.get(field) != before.get(field) for field in LOCKED_FIELDS):
			frappe.throw(_("Those fields are set by the proposal's workflow."), frappe.PermissionError)
		if before.status != DRAFT:
			frappe.throw(
				_("A proposal that is {0} can't be changed. Create a revision instead.").format(
					_(before.status)
				)
			)

	def set_token(self):
		"""The client's link: unguessable, and new for every revision."""
		self.public_token = frappe.generate_hash(length=32)

	def start_family(self):
		"""A new proposal starts its own family, revision 0, the latest."""
		if not self.proposal_family:
			self.db_set({"proposal_family": self.name, "revision_no": 0, "is_latest": 1})


class ProposalTemplate:
	"""TBO Proposal Template's controller."""

	def validate(self):
		self.check_sections()
		self.check_packages()

	def check_sections(self):
		if not self.sections or len(self.sections) > MAX_SECTIONS:
			frappe.throw(_("A template has 1 to {0} blocks.").format(MAX_SECTIONS))

	def check_packages(self):
		seen = set()
		for row in self.packages:
			if row.package in seen:
				frappe.throw(_("{0} is in the template twice.").format(row.package))
			seen.add(row.package)


def has_permission(doc, ptype=None, user=None) -> bool:
	"""A proposal is open to whoever may read its lead (tbo_smart.sales.permissions); changing it
	needs write on the lead. Deleting stays with System Manager."""
	user = user or frappe.session.user
	if ptype == "delete":
		return "System Manager" in frappe.get_roles(user)
	lead_ptype = (
		"read" if ptype in ("read", "select", "print", "export", "report", "email", "share") else "write"
	)
	return bool(frappe.has_permission("Lead", lead_ptype, doc=doc.lead, user=user))


def get_permission_query_conditions(user=None) -> str:
	"""List queries: only proposals of the leads the user may read."""
	user = user or frappe.session.user
	if "System Manager" in frappe.get_roles(user):
		return ""
	leads = frappe.get_list("Lead", fields=["name"], user=user, limit_page_length=0, run=0)
	return f"`tab{DOCTYPE}`.`lead` in ({leads})"


# ---------- making one ----------


def template_payload(name: str) -> dict:
	return templates(include_disabled=True, names=[name])[0]


def templates(include_disabled: bool = False, names: list[str] | None = None) -> list[dict]:
	"""Templates with their blocks and packages, by name, in three queries however many."""
	filters = {} if include_disabled else {"enabled": 1}
	if names is not None:
		filters["name"] = ("in", names or [""])
	parents = frappe.get_all(
		TEMPLATE,
		filters=filters,
		fields=["name", "template_name", "solution_type", "description", "enabled"],
		order_by="template_name asc",
	)
	payloads = {
		row.name: {**row, "enabled": cint(row.enabled), "sections": [], "packages": []} for row in parents
	}
	children = {"parent": ("in", list(payloads) or [""]), "parenttype": TEMPLATE}
	for row in frappe.get_all(
		"TBO Proposal Template Section",
		filters={**children, "parentfield": "sections"},
		fields=["parent", "section_type", "heading", "guidance", "body"],
		order_by="idx asc",
	):
		payloads[row.pop("parent")]["sections"].append(row)
	for row in frappe.get_all(
		"TBO Proposal Template Package",
		filters={**children, "parentfield": "packages"},
		fields=["parent", "package"],
		order_by="idx asc",
	):
		payloads[row.parent]["packages"].append(row.package)
	return list(payloads.values())


def save_template(values: dict) -> str:
	"""Create or update a template (preset editors only): name, solution type, description,
	enabled, blocks and default packages."""
	from tbo_smart.lead_pipeline.presets import require_preset_editor

	require_preset_editor()
	name = (values.get("name") or "").strip()
	doc = (
		frappe.get_doc(TEMPLATE, name)
		if name and frappe.db.exists(TEMPLATE, name)
		else frappe.new_doc(TEMPLATE)
	)
	template_name = clean(values.get("template_name") or name, MAX_HEADING)
	if not template_name:
		frappe.throw(_("Give the template a name."))
	if frappe.db.exists(TEMPLATE, {"template_name": template_name, "name": ("!=", doc.name or "")}):
		frappe.throw(
			_("A template called {0} already exists.").format(template_name), frappe.DuplicateEntryError
		)
	sections = values.get("sections")
	if not isinstance(sections, list):
		frappe.throw(_("A template needs its blocks."))
	doc.update(
		{
			"template_name": template_name,
			"solution_type": clean(values.get("solution_type"), MAX_HEADING) or None,
			"description": clean(values.get("description"), MAX_BODY) or None,
			"enabled": cint(values.get("enabled", 1)),
		}
	)
	doc.set("sections", [])
	for row in sections:
		doc.append("sections", section_row(row, with_guidance=True))
	doc.set("packages", [])
	for package in values.get("packages") or []:
		doc.append("packages", {"package": package})
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	return doc.name


def section_row(row, with_guidance: bool = False) -> dict:
	if not isinstance(row, dict) or row.get("section_type") not in SECTION_TYPES:
		frappe.throw(_("Each block is one of: {0}.").format(", ".join(SECTION_TYPES)))
	values = {
		"section_type": row["section_type"],
		"heading": clean(row.get("heading"), MAX_HEADING) or None,
		"body": clean(row.get("body"), MAX_BODY) or None,
	}
	if with_guidance:
		values["guidance"] = clean(row.get("guidance"), MAX_BODY) or None
	return values


def create_proposal(lead: str, template: str | None = None, ai_draft: bool = False) -> tuple:
	"""A draft proposal for the lead from `template` (its blocks with their default text), as the
	session user. With `ai_draft`, the AI writes each block (but pricing) first. Returns
	(proposal, warnings)."""
	if template and not frappe.db.exists(TEMPLATE, {"name": template, "enabled": 1}):
		frappe.throw(_("Template {0} not found.").format(template), frappe.DoesNotExistError)
	if ai_draft and not ai_enabled():
		frappe.throw(_("AI proposal drafts are not switched on."))
	tpl = template_payload(template) if template else None
	label = frappe.db.get_value("Lead", lead, ["company_name", "lead_name"], as_dict=True)
	client = label.company_name or label.lead_name
	kind = (tpl and (tpl["solution_type"] or tpl["template_name"])) or _("Proposal")
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"lead": lead,
			"template": template,
			"title": clean(_("{0} for {1}").format(kind, client), MAX_HEADING),
			"status": DRAFT,
			"theme": THEMES[0],
			"valid_till": add_days(today(), validity_days()),
			"sections": [section_row(row) for row in (tpl["sections"] if tpl else default_sections())],
		}
	)
	doc.insert()
	warnings = []
	if ai_draft:
		# after the insert, so the AI usage log names the proposal
		try:
			warnings = draft_sections(doc, tpl)
			doc.save()
		except ai.AIProviderError as e:
			# the proposal is still made, with the template's text, and the person told why
			warnings = [
				_("The AI couldn't draft it ({0}), so the template's text is in place.").format(e.summary)
			]
	return doc, warnings


def default_sections() -> list[dict]:
	return [{"section_type": kind} for kind in ("Cover", "Solution", PRICING, "Terms")]


# ---------- the AI draft ----------


def draft_sections(doc, tpl: dict | None) -> list[str]:
	"""The AI writes each block but pricing from the lead's facts, notes and meetings' minutes
	and the template's guidance. A block whose text has a number that isn't in those facts is
	left as it was, with a warning. Returns the warnings."""
	from tbo_smart.smart_leads.facts import LeadFacts

	facts = LeadFacts([doc.lead])
	minutes = meeting_minutes(doc.lead)
	guidance = {
		row["section_type"]: {
			"heading": row.get("heading"),
			"guidance": row.get("guidance"),
			"default": row.get("body"),
		}
		for row in (tpl["sections"] if tpl else [])
	}
	blocks = [
		{
			"key": str(index),
			"type": row.section_type,
			"heading": row.heading or (guidance.get(row.section_type) or {}).get("heading"),
			"guidance": (guidance.get(row.section_type) or {}).get("guidance"),
			"default_text": row.body,
		}
		for index, row in enumerate(doc.sections)
		if row.section_type != PRICING
	]
	if not blocks:
		return []
	context = {
		"today": today(),
		"solution": tpl and (tpl["solution_type"] or tpl["template_name"]),
		"lead": facts.facts(doc.lead),
		"meetings": minutes,
		"blocks": blocks,
	}
	answer = ai.call_ai(PROPOSAL_PROMPT, frappe.as_json(context), "Proposal draft", (DOCTYPE, doc.name))[
		"response"
	]
	known = facts.known_numbers(doc.lead, minutes, blocks, today())
	written = {
		str(row.get("key")): row
		for row in (answer.get("blocks") or [])
		if isinstance(row, dict) and row.get("key") is not None
	}
	dropped = []
	for index, row in enumerate(doc.sections):
		block = written.get(str(index))
		if row.section_type == PRICING or not block:
			continue
		body = clean(block.get("body"), MAX_BODY)
		heading = clean(block.get("heading"), MAX_HEADING)
		if body and not ai.invents_numbers(body, known):
			row.body = body
		elif body:
			dropped.append(row.heading or _(row.section_type))
		if heading and not ai.invents_numbers(heading, known):
			row.heading = heading
	if not dropped:
		return []
	return [
		_(
			"Some blocks had numbers or dates that aren't in the lead's notes, so they were left out: {0}."
		).format(", ".join(dropped))
	]


def meeting_minutes(lead: str) -> list[dict]:
	"""The submitted minutes of the lead's meetings, oldest first."""
	from tbo_smart.lead_pipeline.meetings import DOCTYPE as MEETING
	from tbo_smart.lead_pipeline.meetings import MOM_SUBMITTED

	rows = frappe.get_all(
		MEETING,
		filters={"lead": lead, "status": MOM_SUBMITTED},
		fields=[
			"stage_label",
			"scheduled_at",
			"discussion_points",
			"client_requirements",
			"decisions",
			"objections",
			"quotation_feedback",
		],
		order_by="scheduled_at asc",
	)
	return [
		{
			"meeting": row.stage_label,
			"on": str(getdate(row.scheduled_at)) if row.scheduled_at else None,
			**{key: row[key] for key in row if key not in ("stage_label", "scheduled_at") and row[key]},
		}
		for row in rows
	]


PROPOSAL_PROMPT = """You draft the blocks of a sales proposal from TBO (Team Back Office), which
builds software (ERPNext, helpdesks, custom solutions) and runs back-office and digital
marketing services in Kerala, India. You get the lead's facts and notes, the minutes of the
meetings held with them, and the blocks to write, each with its type, heading, guidance and
default text. Write each block in clear, warm, specific English for the client to read: what
they told us, what TBO will do about it, in their terms. Use the default text as the base for
blocks like "About us" and "Terms". Keep every name, number, price and date exactly as written
in the facts; never invent a number, price, count, percentage or date. Prices live in the
pricing block, which you do not write. Paragraphs are separated by a blank line; a list item
starts with "- ". Answer only JSON: {"blocks": [{"key": "<the block's key>", "heading": "...",
"body": "..."}]} with one entry per block you were given.
"""


# ---------- editing ----------


def require_draft(doc) -> None:
	if doc.status != DRAFT:
		frappe.throw(
			_("A proposal that is {0} can't be changed. Create a revision instead.").format(_(doc.status))
		)


def save_content(doc, data: dict) -> None:
	"""The title, theme, validity and blocks of a draft."""
	require_draft(doc)
	if "title" in data:
		doc.title = clean(data.get("title"), MAX_HEADING)
		if not doc.title:
			frappe.throw(_("Give the proposal a title."))
	if "theme" in data:
		if data.get("theme") not in THEMES:
			frappe.throw(_("The theme is one of: {0}.").format(", ".join(THEMES)))
		doc.theme = data["theme"]
	if "valid_till" in data:
		valid_till = data.get("valid_till") or None
		if valid_till and getdate(valid_till) < getdate(today()):
			frappe.throw(_("Valid till can't be in the past."))
		doc.valid_till = valid_till
	if "sections" in data:
		if not isinstance(data["sections"], list):
			frappe.throw(_("sections must be a list of blocks."))
		doc.set("sections", [section_row(row) for row in data["sections"]])
	doc.save()


def save_pricing(doc, values: dict) -> None:
	"""The pricing block: the lead's quotation, made or updated through the quotation builder
	(segment rates, band and approval apply). A quotation already sent is revised first, so the
	one the client had stays as it was."""
	from tbo_smart.api import lead_quotation

	require_draft(doc)
	quotation = doc.quotation if doc.quotation and frappe.db.exists("Quotation", doc.quotation) else None
	if quotation and frappe.db.get_value("Quotation", quotation, "docstatus") != 0:
		quotation = q.create_revision(frappe.get_doc("Quotation", quotation)).name
	saved = lead_quotation.save_quotation(doc.lead, values, quotation)
	if doc.quotation != saved["name"]:
		doc.quotation = saved["name"]
		doc.flags.proposal_workflow = True
		doc.save()


def create_revision(doc):
	"""A new draft copied from this proposal with the next revision number in its family; it
	becomes the latest and gets its own link. The earlier ones stay as they were."""
	family = doc.proposal_family or doc.name
	# the family's first proposal's row lock: two revisions at once take the next numbers in turn
	frappe.db.get_value(DOCTYPE, family, "name", for_update=True)
	members = frappe.get_all(DOCTYPE, filters={"proposal_family": family}, fields=["name", "revision_no"])
	revision = frappe.copy_doc(doc)
	revision.update(
		{
			"proposal_family": family,
			"revision_no": max([cint(row.revision_no) for row in members] + [cint(doc.revision_no)]) + 1,
			"is_latest": 1,
			"status": DRAFT,
			"valid_till": add_days(today(), validity_days()),
			**{field: None for field in TRACKING_FIELDS},
			"view_count": 0,
		}
	)
	revision.flags.proposal_workflow = True
	revision.insert()
	for row in members:
		if row.name != revision.name:
			frappe.db.set_value(DOCTYPE, row.name, "is_latest", 0, update_modified=False)
	return revision


def revisions(doc) -> list[dict]:
	return frappe.get_all(
		DOCTYPE,
		filters={"proposal_family": doc.proposal_family or doc.name},
		fields=["name", "revision_no", "status", "is_latest", "sent_on", "modified"],
		order_by="revision_no asc",
	)


# ---------- sending ----------


def public_url(doc, preview: bool = False) -> str:
	return get_url(f"/proposal/{doc.public_token}" + ("?preview=1" if preview else ""))


def send_blockers(doc) -> list[str]:
	"""Why the proposal can't be sent now, in words for the salesperson; empty when it can."""
	from tbo_smart.quotation_control import QuotationControl
	from tbo_smart.tbo_smart.doctype.tbo_quotation_approval_settings.tbo_quotation_approval_settings import (
		format_limit,
	)

	reasons = []
	if not cint(doc.is_latest):
		reasons.append(_("Only the latest revision can be sent."))
	if doc.status not in (DRAFT, *WITH_CLIENT):
		reasons.append(_("A proposal that is {0} can't be sent.").format(_(doc.status)))
	if not doc.sections:
		reasons.append(_("Add at least one block."))
	if doc.valid_till and getdate(doc.valid_till) < getdate(today()):
		reasons.append(_("Its validity has passed. Set a new valid till date."))
	if not has_outgoing_email():
		reasons.append(_("The ERP site has no outgoing email account yet. Ask an admin to set one up."))
	if not doc.quotation:
		return reasons
	quotation = frappe.get_doc("Quotation", doc.quotation)
	if quotation.docstatus == 2 or quotation.get("tbo_quote_status") in q.CLOSED:
		reasons.append(
			_("Its quotation {0} is {1}. Price it again.").format(
				quotation.name, _(quotation.get("tbo_quote_status") or "Cancelled")
			)
		)
	elif price_expired(quotation):
		reasons.append(
			_("Its quotation {0} was valid till {1}. Price it again.").format(
				quotation.name, frappe.utils.formatdate(quotation.valid_till)
			)
		)
	elif quotation.docstatus == 0:
		for kind, percent, limit in QuotationControl(quotation).uncovered():
			waiting = q.draft_status(quotation) == q.PENDING
			reasons.append(
				(
					_("The pricing's {0} of {1}% is waiting for approval (your limit is {2}).")
					if waiting
					else _(
						"The pricing's {0} of {1}% is above your limit ({2}). Request approval, or bring it within the limit."
					)
				).format(_(kind).lower(), percent, format_limit(limit))
			)
	return reasons


def has_outgoing_email() -> bool:
	"""An email account the ERP site sends a lead's email from."""
	from frappe.email.doctype.email_account.email_account import EmailAccount

	return bool(EmailAccount.find_outgoing(match_by_doctype="Lead"))


def send(doc, recipients: str | None = None, message: str | None = None, attach_pdf: bool = False) -> None:
	"""Email the client the link (and with `attach_pdf` the PDF), recorded as a Communication on
	the lead. The quotation goes out with it (quotations.mark_sent: submitted, Sent to Client,
	the lead to Proposal Sent and a follow-up); without one the lead moves to Proposal Sent."""
	reasons = send_blockers(doc)
	if reasons:
		frappe.throw("<br>".join(escape_html(reason) for reason in reasons), title=_("Not sent"))
	lead = frappe.db.get_value("Lead", doc.lead, ["email_id", "lead_name", "company_name"], as_dict=True)
	to = (recipients or lead.email_id or "").strip()
	if not to:
		frappe.throw(_("The lead has no email. Give the address to send it to."))
	if doc.quotation:
		quotation = frappe.get_doc("Quotation", doc.quotation)
		if quotation.get("tbo_quote_status") not in q.WITH_CLIENT:
			q.mark_sent(quotation, "Email")
	else:
		advance_lead(doc.lead, PROPOSAL_SENT, _("proposal {0} sent").format(doc.name))
	if doc.status == DRAFT:
		doc.status = SENT
	doc.sent_on = now_datetime()
	doc.sent_to = to
	doc.valid_till = doc.valid_till or add_days(today(), validity_days())
	doc.flags.proposal_workflow = True
	doc.save()
	email(doc, lead, to, message, attach_pdf)


def email(doc, lead, to: str, message: str | None, attach_pdf: bool) -> None:
	from frappe.core.doctype.communication.email import make

	company = frappe.db.get_value("Company", frappe.defaults.get_global_default("company"), "company_name")
	greeting = _("Dear {0},").format(lead.lead_name or lead.company_name)
	text = (message or "").strip() or "\n\n".join(
		[
			greeting,
			_(
				"Thank you for your time. Our proposal is ready for you to read, and you can accept it on the same page."
			),
		]
	)
	paragraphs = "".join(
		f"<p>{escape_html(part).replace(chr(10), '<br>')}</p>" for part in text.split("\n\n")
	)
	link = public_url(doc)
	content = (
		f"{paragraphs}<p><a href=\"{link}\">{escape_html(_('Read the proposal: {0}').format(doc.title))}</a></p>"
		f"<p>{escape_html(link)}</p>"
	)
	attachments = [{"fname": f"{doc.name}.pdf", "fcontent": render_pdf(doc)}] if attach_pdf else None
	make(
		doctype="Lead",
		name=doc.lead,
		recipients=to,
		subject=_("{0}: our proposal").format(company or doc.title) if company else doc.title,
		content=content,
		send_email=1,
		attachments=attachments,
	)


# ---------- the client's page ----------


def by_token(token: str | None):
	"""The proposal behind a link, or None (a malformed or unknown token)."""
	if not isinstance(token, str) or not TOKEN.match(token):
		return None
	name = frappe.db.get_value(DOCTYPE, {"public_token": token}, "name")
	return frappe.get_doc(DOCTYPE, name) if name else None


def public_doc(token: str | None, preview: bool = False):
	"""The proposal a client may read: sent (or decided), or any revision in preview."""
	doc = by_token(token)
	if not doc or not proposals_enabled() or (doc.status == DRAFT and not preview):
		return None
	return doc


@contextmanager
def as_system():
	"""The client is Guest; their accept or decline moves the lead as a system action."""
	user = frappe.session.user
	frappe.set_user("Administrator")  # the token was checked; the pipeline moves - nosemgrep
	try:
		yield
	finally:
		frappe.set_user(user)  # back to the guest - nosemgrep


def record_view(doc) -> None:
	"""One more view; the first moves Sent to Viewed (and its quotation to Viewed /
	Acknowledged) and tells the lead's owner."""
	# under the row lock, so two views at once both count and neither undoes an answer
	locked = frappe.db.get_value(DOCTYPE, doc.name, ["status", "view_count"], as_dict=True, for_update=True)
	if locked.status not in WITH_CLIENT:
		return
	values = {"view_count": cint(locked.view_count) + 1, "last_viewed_on": now_datetime()}
	first = locked.status == SENT
	if first:
		values.update({"status": VIEWED, "first_viewed_on": values["last_viewed_on"]})
	frappe.db.set_value(DOCTYPE, doc.name, values, update_modified=False)
	doc.update(values)
	if not first:
		return
	with as_system():
		quotation = quotation_with_client(doc)
		if quotation and quotation.tbo_quote_status == q.SENT:
			q.set_status(quotation, q.VIEWED, _("opened proposal {0}").format(doc.name))
		tell_owner(doc, _("{0} opened the proposal {1}").format(client_name(doc), doc.title))


def decide(doc, accept: bool, name: str | None, email_address: str | None, reason: str | None = None) -> None:
	"""The client accepts or declines. Once decided it stays so: the same answer again changes
	nothing, the other one is refused. Accepting moves the quotation to Accepted (the lead to
	Closed – Won, which makes its deal) or, without a quotation, the lead itself."""
	target = ACCEPTED if accept else DECLINED
	status = frappe.db.get_value(DOCTYPE, doc.name, "status", for_update=True)
	if status == target:
		return
	if status in (ACCEPTED, DECLINED):
		frappe.throw(_("This proposal was already {0}.").format(_(status).lower()))
	if status not in WITH_CLIENT or not cint(doc.is_latest):
		frappe.throw(_("This proposal can't be answered any more. Ask us for the latest one."))
	if doc.valid_till and getdate(doc.valid_till) < getdate(today()):
		frappe.throw(_("This proposal has expired. Ask us for a new one."))
	if accept and doc.quotation and not quotation_with_client(doc):
		# its price has expired or was withdrawn; accepting would win the lead without it
		frappe.throw(_("The price in this proposal is no longer valid. Ask us for a new one."))
	name = clean(name, MAX_HEADING)
	email_address = clean(email_address, MAX_HEADING)
	if accept and not (name and email_address):
		frappe.throw(_("Type your name and email to accept."))
	if email_address:
		frappe.utils.validate_email_address(email_address, throw=True)
	values = {
		"status": target,
		"decided_on": now_datetime(),
		"decided_by_name": name or None,
		"decided_by_email": email_address or None,
		"decline_reason": None if accept else (clean(reason, MAX_REASON) or None),
	}
	frappe.db.set_value(DOCTYPE, doc.name, values)
	doc.update(values)
	with as_system():
		move_pipeline(doc, accept)
		who = name or email_address or client_name(doc)
		tell_owner(
			doc,
			(_("{0} accepted the proposal {1}") if accept else _("{0} declined the proposal {1}")).format(
				who, doc.title
			),
		)


def move_pipeline(doc, accept: bool) -> None:
	quotation = quotation_with_client(doc)
	note = _("on proposal {0}").format(doc.name)
	if accept:
		if quotation:
			q.set_status(quotation, q.ACCEPTED, note)
		else:
			advance_lead(doc.lead, CLOSED_WON, _("proposal {0} accepted").format(doc.name))
	elif quotation:
		q.set_status(quotation, q.REJECTED, doc.decline_reason or _("declined proposal {0}").format(doc.name))


def quotation_with_client(doc):
	"""The proposal's quotation while it is with the client and its price still valid (the
	nightly expiry may not have run yet)."""
	if not doc.quotation or not frappe.db.exists("Quotation", doc.quotation):
		return None
	quotation = frappe.get_doc("Quotation", doc.quotation)
	if quotation.get("tbo_quote_status") not in q.WITH_CLIENT or price_expired(quotation):
		return None
	return quotation


def price_expired(quotation) -> bool:
	return bool(quotation.valid_till and getdate(quotation.valid_till) < getdate(today()))


def client_name(doc) -> str:
	label = frappe.db.get_value("Lead", doc.lead, ["company_name", "lead_name"], as_dict=True)
	return label.company_name or label.lead_name


def tell_owner(doc, subject: str) -> None:
	"""A timeline comment on the lead and an in-app note (and push) to its owner."""
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	frappe.get_doc("Lead", doc.lead).add_comment("Info", subject)
	owner = frappe.db.get_value("Lead", doc.lead, "lead_owner")
	if owner:
		enqueue_create_notification(
			[owner],
			{
				"type": "Alert",
				"document_type": "Lead",
				"document_name": doc.lead,
				"subject": subject,
				"from_user": "Administrator",
			},
		)


def expire_proposals() -> int:
	"""Daily: proposals still with the client after their valid till date expire."""
	if not proposals_enabled():
		return 0
	names = frappe.get_all(
		DOCTYPE,
		filters={"status": ("in", WITH_CLIENT), "valid_till": ("<", today())},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value(DOCTYPE, name, "status", EXPIRED)
	return len(names)


# ---------- what the client sees ----------


def page_context(doc, preview: bool = False) -> dict:
	"""Everything the page and the PDF show, and nothing else: the blocks, the quotation's lines
	and totals, the company and the client's name. No emails, notes, owners or internal names."""
	company = (
		frappe.db.get_value(
			"Company",
			frappe.db.get_value("Quotation", doc.quotation, "company")
			if doc.quotation
			else frappe.defaults.get_global_default("company"),
			["company_name", "company_logo"],
			as_dict=True,
		)
		or frappe._dict()
	)
	expired = bool(doc.valid_till and getdate(doc.valid_till) < getdate(today()))
	pricing = pricing_table(doc.quotation) if doc.quotation else None
	return {
		"title": doc.title,
		"theme": "graphite" if doc.theme == "Graphite" else "teal",
		"client": client_name(doc),
		"company": company.company_name,
		"logo": get_url(company.company_logo) if (company.company_logo or "").startswith("/files/") else None,
		"valid_till": frappe.utils.formatdate(doc.valid_till, "d MMMM yyyy") if doc.valid_till else None,
		"revision": cint(doc.revision_no),
		"status": doc.status,
		"preview": preview,
		"superseded": not cint(doc.is_latest),
		"can_decide": not preview and doc.status in WITH_CLIENT and cint(doc.is_latest) and not expired,
		"expired": expired or doc.status == EXPIRED,
		"decided_by": doc.decided_by_name,
		"decided_on": frappe.utils.formatdate(doc.decided_on, "d MMMM yyyy") if doc.decided_on else None,
		"blocks": [
			{
				"type": row.section_type,
				"heading": row.heading or _(row.section_type),
				"parts": text_parts(row.body),
			}
			for row in doc.sections
			# an empty block shows nothing; the pricing block shows the quotation
			if (row.body or "").strip() or (row.section_type == PRICING and pricing)
		],
		"pricing": pricing,
		"token": doc.public_token,
	}


def text_parts(body: str | None) -> list[dict]:
	"""Plain text as paragraphs and bullet lists, for the template to escape and lay out."""
	parts = []
	for chunk in re.split(r"\n\s*\n", (body or "").strip()):
		lines = [line.strip() for line in chunk.splitlines() if line.strip()]
		if not lines:
			continue
		if all(line.startswith(("- ", "• ")) for line in lines):
			parts.append({"kind": "list", "items": [line[2:].strip() for line in lines]})
		else:
			parts.append({"kind": "text", "lines": lines})
	return parts


def pricing_table(quotation: str) -> dict | None:
	if not frappe.db.exists("Quotation", quotation):
		return None
	doc = frappe.get_doc("Quotation", quotation)

	def money(value):
		return frappe.utils.fmt_money(value, currency=doc.currency)

	return {
		"lines": [pricing_line(row, money) for row in doc.items],
		"total": money(doc.total),
		"discount": money(doc.discount_amount) if doc.discount_amount else None,
		"net_total": money(doc.net_total),
		"taxes": [{"label": row.description, "amount": money(row.tax_amount)} for row in doc.taxes],
		"grand_total": money(doc.grand_total),
		"in_words": doc.in_words,
		"payment_terms": [
			{
				"label": row.description or row.payment_term or _("Payment"),
				"due": frappe.utils.formatdate(row.due_date, "d MMM yyyy") if row.due_date else None,
				"amount": money(row.payment_amount),
			}
			for row in doc.get("payment_schedule") or []
		],
	}


def pricing_line(row, money) -> dict:
	"""One quotation line as the page shows it: the description as plain text, left out when it
	only repeats the item's name."""
	description = plain_text(row.description)
	return {
		"item": row.item_name or row.item_code,
		"description": description if description and description != (row.item_name or "").strip() else None,
		"qty": frappe.utils.fmt_money(row.qty, precision=0) if row.qty == int(row.qty) else row.qty,
		"rate": money(row.rate),
		"amount": money(row.amount),
	}


def render_html(doc, preview: bool = False, pdf: bool = False) -> str:
	return frappe.render_template(  # our own template; the values are escaped in it - nosemgrep
		"tbo_smart/www/proposal.html", {"proposal": page_context(doc, preview), "pdf": pdf}
	)


def render_pdf(doc) -> bytes:
	from frappe.utils.pdf import get_pdf

	return get_pdf(render_html(doc, pdf=True), {"margin-top": "12mm", "margin-bottom": "12mm"})


def lead_proposals(lead: str) -> list[dict]:
	"""The lead's proposals, newest first, for the lead page."""
	return frappe.get_all(
		DOCTYPE,
		filters={"lead": lead},
		fields=[
			"name",
			"title",
			"status",
			"revision_no",
			"is_latest",
			"proposal_family",
			"quotation",
			"sent_on",
			"view_count",
			"last_viewed_on",
			"decided_on",
			"modified",
		],
		order_by="creation desc",
	)
