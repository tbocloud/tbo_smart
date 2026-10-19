"""The client documents after the proposal: making each from the accepted one before it, and the
chain on the lead (docs/client-documents.md).

A document is made from the accepted, latest document of the type before it (`follows`): it copies
the client profile, the requirements, the scoping answers and the modules, so its data sections are
built at no AI cost and presales mostly reviews. The quotation gets its own ERPNext Quotation (a
revision of the accepted proposal's, else one priced from the modules) with the taxes, terms,
payment terms and validity filled in, under the discount approval as always. Every type has its
own switch in TBO Smart Settings, off by default.
"""

import frappe
from frappe import _
from frappe.utils import cint

from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline import proposal_builder as builder
from tbo_smart.lead_pipeline import proposals as p
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.lead_pipeline import setting

# what a document copies from the one it is made from
COPIED = (
	"lead",
	"template",
	"solution_type",
	"client_profile",
	"requirements",
	"scoping",
	"pricing_mode",
	"letter_head_override",
)
# a document still in play: a new one of its type isn't made beside it (a revision is)
IN_PLAY = (p.DRAFT, *p.WITH_CLIENT, p.ACCEPTED)
MAX_TITLE = 140


def document_enabled(name: str) -> bool:
	"""Proposals on, this type's own switch on, and the switches of the types before it."""
	spec = kinds.of(name)
	if not p.proposals_enabled():
		return False
	if spec.flag is None:
		return True
	return bool(cint(setting(spec.flag))) and document_enabled(spec.follows)


def require_document(name: str) -> None:
	if not document_enabled(name):
		frappe.throw(
			_("{0} isn't switched on in TBO Smart Settings.").format(kinds.label(name)),
			frappe.ValidationError,
		)


def enabled_types() -> list:
	return [spec for spec in kinds.TYPES.values() if document_enabled(spec.name)]


# ---------- making one ----------


def create_document(source_name: str, document_type: str) -> tuple:
	"""The next document, as the session user, from the accepted document before it. Returns
	(document, warnings)."""
	spec = kinds.of(document_type)
	if not spec.follows:
		frappe.throw(_("A proposal starts from the lead's requirements, not from another document."))
	require_document(spec.name)
	if not isinstance(source_name, str) or not frappe.db.exists(p.DOCTYPE, source_name):
		frappe.throw(_("Document {0} not found.").format(source_name), frappe.DoesNotExistError)
	source = frappe.get_doc(p.DOCTYPE, source_name)
	source.check_permission("write")
	if kinds.of(source).name != spec.follows:
		frappe.throw(
			_("A {0} is made from an accepted {1}.").format(
				kinds.label(spec.name).lower(), kinds.label(spec.follows).lower()
			)
		)
	# read under the source's row lock: two requests at once wait here in turn (the second sees
	# the first's document), and a revision made meanwhile is seen
	locked = frappe.db.get_value(
		p.DOCTYPE, source.name, ["status", "is_latest"], as_dict=True, for_update=True
	)
	if locked.status != p.ACCEPTED or not cint(locked.is_latest):
		frappe.throw(_("Make it once the client has accepted {0}.").format(source.title))
	existing = in_play(source.lead, spec.name)
	if existing:
		frappe.throw(
			_("{0} {1} already exists for this lead. Open it, or create a revision of it.").format(
				kinds.label(spec.name), existing
			),
			frappe.DuplicateEntryError,
		)
	doc = frappe.get_doc(
		{
			"doctype": p.DOCTYPE,
			**{field: source.get(field) for field in COPIED},
			"document_type": spec.name,
			"source_document": source.name,
			"prepared_by": frappe.session.user,
			"title": p.clean(f"{kinds.label(spec.name)}: {root_title(source)}", MAX_TITLE),
			"status": p.DRAFT,
			"theme": p.THEMES[0],
			"valid_till": frappe.utils.add_days(frappe.utils.today(), p.validity_days()),
			"quotation": source.quotation if spec.shows_price else None,
			"chat": "[]",
			"sections": [
				p.section_row(row, allowed=spec.section_types) for row in kinds.default_sections(spec)
			],
			"modules": [
				{field: row.get(field) for field in builder.MODULE_LINE_FIELDS} for row in source.modules
			],
		}
	)
	switch_off_unchosen(doc)
	warnings = make_quotation(doc, source) if spec.priced else []
	kinds.build_all(doc)
	doc.flags.proposal_workflow = True
	doc.insert()
	return doc, warnings


def root_title(doc) -> str:
	"""The proposal's title at the start of the chain ("ERPNext Implementation for …")."""
	for row in reversed([doc, *kinds.lineage(doc)]):
		if row.get("document_type") in (None, kinds.PROPOSAL):
			return frappe.db.get_value(p.DOCTYPE, row.name, "title") or doc.title
	return doc.title


def switch_off_unchosen(doc) -> None:
	"""The AMC section is on only when the client chose AMC on the proposal."""
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	for row in doc.sections:
		if row.section_type == "AMC" and not answers["amc"]:
			row.enabled = 0


def in_play(lead: str, document_type: str) -> str | None:
	"""The lead's latest document of this type that is still a draft, with the client or accepted
	(a locking read: it sees what another request committed while this one waited)."""
	return frappe.db.get_value(
		p.DOCTYPE,
		{"lead": lead, "document_type": document_type, "is_latest": 1, "status": ("in", IN_PLAY)},
		"name",
		for_update=True,
	)


def make_quotation(doc, source) -> list[str]:
	"""The quotation's own ERPNext Quotation, through the quotation builder (segment rates, the
	band and the discount approval apply): a revision of the accepted proposal's quotation, else one
	priced from the modules; with the company's default taxes, the terms, the payment terms and the
	validity wherever the quotation had none. Returns the warnings (nothing priced: the quotation is
	still made, and Send says why)."""
	from tbo_smart.api import lead_quotation
	from tbo_smart.api.finance.quotation import serialize_quotation
	from tbo_smart.finance import get_default_company

	if not frappe.has_permission("Quotation", "create"):
		frappe.throw(_("You cannot create quotations."), frappe.PermissionError)
	accepted = (
		frappe.get_doc("Quotation", source.quotation)
		if source.quotation and frappe.db.exists("Quotation", source.quotation)
		else None
	)
	company = (accepted and accepted.company) or get_default_company()
	defaults = lead_quotation.new_quotation_defaults(company)
	if accepted and accepted.docstatus == 1:
		draft = q.create_revision(accepted)
		current = serialize_quotation(draft)
		values = {
			"items": current["items"],
			"additional_discount_percentage": current["additional_discount_percentage"],
			"commission": current["commission"],
			**{key: current.get(key) or value for key, value in defaults.items()},
		}
		saved = lead_quotation.save_quotation(doc.lead, values, draft.name)
	else:
		try:
			values = {**defaults, **builder.pricing_values(doc)}
		except frappe.ValidationError as e:
			frappe.clear_last_message()
			return [_("Not priced yet: {0}").format(str(e))]
		saved = lead_quotation.save_quotation(doc.lead, values)
	doc.quotation = saved["name"]
	doc.valid_till = saved.get("valid_till") or doc.valid_till
	return []


# ---------- the chain on the lead ----------


def latest(lead: str, document_type: str) -> frappe._dict | None:
	"""The lead's document of this type that the chain shows: an accepted one, else the newest
	latest revision."""
	rows = frappe.get_all(
		p.DOCTYPE,
		filters={"lead": lead, "document_type": document_type, "is_latest": 1},
		fields=["name", "title", "status", "quotation", "sent_on", "view_count", "decided_on", "modified"],
		order_by="creation desc",
	)
	return next((row for row in rows if row.status == p.ACCEPTED), rows[0] if rows else None)


def chain(lead: str) -> dict:
	"""{steps, next}: each switched-on type in order (Proposal → Quotation → Project Agreement →
	…) with its document's status, and the one next action (make the proposal, make the next
	document from the accepted one, or carry on with the one in progress)."""
	steps, action, before = [], None, None
	for spec in enabled_types():
		row = latest(lead, spec.name)
		step = {
			"document_type": spec.name,
			"label": kinds.label(spec.name),
			"name": row.name if row else None,
			"title": row.title if row else None,
			"status": row.status if row else None,
			"status_label": status_label(spec.name, row.status) if row else None,
			"done": bool(row and row.status == p.ACCEPTED),
			"view_count": cint(row.view_count) if row else 0,
			"quotation_status": quotation_status(row.quotation) if row and spec.priced else None,
		}
		if action is None:
			action = next_action(spec, step, before)
		steps.append(step)
		before = step
	return {"steps": steps, "next": action}


def next_action(spec, step: dict, before: dict | None) -> dict | None:
	if step["name"] and not step["done"]:
		return {
			"action": "open",
			"document_type": spec.name,
			"name": step["name"],
			"label": _("Continue the {0}").format(kinds.label(spec.name).lower()),
		}
	if step["name"]:
		return None
	if not spec.follows:
		return {"action": "create_proposal", "document_type": spec.name, "label": _("New proposal")}
	return {
		"action": "create",
		"document_type": spec.name,
		"source": before["name"],
		"label": _("Create {0}").format(kinds.label(spec.name).lower()),
	}


def status_label(document_type: str, status: str) -> str:
	return kinds.accepted_label(document_type) if status == p.ACCEPTED else _(status)


def quotation_status(quotation: str | None) -> str | None:
	"""Where the document's ERPNext Quotation is (a draft's from its approval record)."""
	if not quotation or not frappe.db.exists("Quotation", quotation):
		return None
	row = frappe.db.get_value("Quotation", quotation, ["docstatus", "tbo_quote_status"], as_dict=True)
	if row.docstatus == 0:
		return q.draft_status(frappe.get_doc("Quotation", quotation))
	return row.tbo_quote_status
