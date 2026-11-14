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

from tbo_smart.lead_pipeline import document_steps as steps
from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline import proposal_builder as builder
from tbo_smart.lead_pipeline import proposals as p
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.lead_pipeline import setting
from tbo_smart.lead_pipeline.implementation_plan import UAT_SIGNED

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
	if spec.name == kinds.QUOTATION and kinds.proposal_is_quotation():
		# the proposal carries the final price: no separate quotation step
		return False
	return bool(cint(setting(spec.flag))) and document_enabled(kinds.made_from(spec))


def require_document(name: str) -> None:
	if name == kinds.QUOTATION and kinds.proposal_is_quotation():
		frappe.throw(
			_("The proposal is the quotation: make the project agreement from the accepted proposal."),
			frappe.ValidationError,
		)
	if not document_enabled(name):
		frappe.throw(
			_("{0} isn't switched on in TBO Smart Settings.").format(kinds.label(name)),
			frappe.ValidationError,
		)


def enabled_types() -> list:
	return [spec for spec in kinds.TYPES.values() if document_enabled(spec.name)]


# ---------- making one ----------


def create_document(
	source_name: str | None, document_type: str, lead: str | None = None, announce: bool = True
) -> tuple:
	"""The step's document, as the session user. In the normal flow it is made from the accepted
	document before it; a step made after skipped ones, or out of order, from the latest accepted
	document before it, or from the lead itself when there is none (a chain started in the middle).
	Out of order needs the lead's owner, a head or a manager (document_steps.can_steer). Returns
	(document, warnings)."""
	spec = kinds.of(document_type)
	if not kinds.made_from(spec):
		frappe.throw(_("A proposal starts from the lead's requirements, not from another document."))
	require_document(spec.name)
	source = None
	if source_name:
		if not isinstance(source_name, str) or not frappe.db.exists(p.DOCTYPE, source_name):
			frappe.throw(_("Document {0} not found.").format(source_name), frappe.DoesNotExistError)
		source = frappe.get_doc(p.DOCTYPE, source_name)
		source.check_permission("write")
		lead = source.lead
	if not isinstance(lead, str) or not frappe.db.exists("Lead", lead):
		frappe.throw(_("Lead {0} not found.").format(lead), frappe.DoesNotExistError)
	frappe.has_permission("Lead", "write", lead, throw=True)
	if not kinds.chain_for(lead).includes(spec.name):
		frappe.throw(_("This lead's type doesn't have a {0}.").format(kinds.label(spec.name).lower()))
	steps.require_open(lead)
	# the lead's row lock: two requests at once wait here in turn, and the second sees the first's
	# document (or a revision made meanwhile)
	frappe.db.get_value("Lead", lead, "name", for_update=True)
	existing = in_play(lead, spec.name)
	if existing:
		frappe.throw(
			_("{0} {1} already exists for this lead. Open it, or create a revision of it.").format(
				kinds.label(spec.name), existing
			),
			frappe.DuplicateEntryError,
		)
	found = chain(lead)
	step = next(step for step in found["steps"] if step["document_type"] == spec.name)
	if step.get("create_refused"):
		frappe.throw(step["create_refused"])
	if step["source"] != (source.name if source else None):
		frappe.throw(
			_("Make it from {0}, the latest accepted document before it.").format(step["source"])
			if step["source"]
			else _("Make it from the lead: no document before it is accepted.")
		)
	if not step["in_order"]:
		steps.require_steer(lead)
	prefill = frappe.get_doc(p.DOCTYPE, step["prefill"]) if not source and step["prefill"] else None
	doc = new_document(spec, lead, source, prefill)
	switch_off_unchosen(doc)
	warnings = make_quotation(doc, source) if spec.priced else []
	if spec.prepare:
		spec.prepare(doc)
	kinds.build_all(doc)
	doc.flags.proposal_workflow = True
	doc.insert()
	if spec.priced:
		p.tag_quotation(doc)
	if spec.writer and p.ai_enabled():
		warnings += write_draft(doc, spec)
	# made after all: no longer skipped or not used
	steps.clear(lead, spec.name)
	if announce:
		p.tell_lead_owner(
			lead,
			_("{0} made the {1} {2} from {3}").format(
				frappe.utils.get_fullname(frappe.session.user),
				kinds.words(spec.name)["noun"],
				doc.name,
				source.name if source else _("the lead"),
			),
		)
	return doc, warnings


def new_document(spec, lead: str, source=None, prefill=None):
	"""The new document, unsaved: copied from `source` (its profile, requirements, scoping and
	modules), else from `prefill` (a document before it the client hasn't accepted, such as a
	skipped draft: copied the same, but not named as its source), else from the lead (its profile,
	its notes as the requirements, the lead type's template and its modules)."""
	values = {
		"doctype": p.DOCTYPE,
		"lead": lead,
		"document_type": spec.name,
		"source_document": source.name if source else None,
		"prepared_by": frappe.session.user,
		"status": p.DRAFT,
		"theme": p.THEMES[0],
		"valid_till": frappe.utils.add_days(frappe.utils.today(), p.validity_days()),
		"chat": "[]",
		"sections": [p.section_row(row, allowed=spec.section_types) for row in kinds.default_sections(spec)],
	}
	copy = source or prefill
	if copy:
		values.update(
			{
				**{field: copy.get(field) for field in COPIED},
				"title": p.clean(f"{kinds.label(spec.name)}: {root_title(copy)}", MAX_TITLE),
				"quotation": source.quotation if source and spec.shows_price else None,
				"modules": [
					{field: row.get(field) for field in builder.MODULE_LINE_FIELDS} for row in copy.modules
				],
			}
		)
		return frappe.get_doc(values)
	from tbo_smart.lead_pipeline.proposal_profile import profile_defaults

	template = p.default_template(lead)
	tpl = p.template_payload(template) if template else None
	solution = (tpl and (tpl["solution_type"] or tpl["template_name"])) or None
	answers = builder.scoping_answers(None, solution)
	answers["modules"] = answers["modules"] or (tpl["default_modules"] if tpl else [])
	doc = frappe.get_doc(
		{
			**values,
			"template": template,
			"solution_type": solution,
			"title": p.clean(
				f"{kinds.label(spec.name)}: {_('{0} for {1}').format(solution or _('Project'), p.client_label(lead))}",
				MAX_TITLE,
			),
			"pricing_mode": p.PRICING_MODES[0],
			"client_profile": frappe.as_json(profile_defaults(lead).get("profile") or {}),
			"requirements": p.clean(builder.requirements_from_lead(lead), p.MAX_BODY) or None,
			"scoping": frappe.as_json(answers),
		}
	)
	doc.set("modules", builder.module_rows(doc, answers["modules"]))
	return doc


def after_decision(name: str, accepted: bool) -> None:
	"""Queued when the client answers (proposals.decide), run as the lead's owner: an accepted
	document's next step is made as a draft for presales to review and send (the Quotation, while
	the proposal isn't the quotation: the automatic quotation); "Ask for changes" on an agreement,
	BRD, SRS or plan makes its revision. A failure is said on the lead, and the step can still be
	made from the lead's Documents card."""
	if not frappe.db.exists(p.DOCTYPE, name):
		return
	doc = frappe.get_doc(p.DOCTYPE, name)
	owner = frappe.db.get_value("Lead", doc.lead, "lead_owner")
	if not owner or not frappe.db.get_value("User", owner, "enabled"):
		return
	user = frappe.session.user
	frappe.set_user(owner)  # the owner reviews and sends what is made for them - nosemgrep
	try:
		if accepted:
			prepare_next(doc)
		elif kinds.of(doc).name in kinds.REVISED_ON_CHANGES:
			revise_on_changes(doc)
	except frappe.DuplicateEntryError:
		# presales made it by hand meanwhile: nothing to do
		frappe.db.rollback()
		frappe.clear_last_message()
	except Exception as e:
		frappe.db.rollback()
		frappe.clear_last_message()
		frappe.log_error(
			title=_("Client document: the next step wasn't made"),
			reference_doctype=p.DOCTYPE,
			reference_name=name,
		)
		p.tell_lead_owner(
			doc.lead,
			_(
				"Couldn't prepare the next document after {0} ({1}). Make it from the lead's Documents card."
			).format(name, frappe.utils.strip_html(str(e)) or type(e).__name__),
			always=True,
		)
	finally:
		frappe.set_user(user)  # nosemgrep


def prepare_next(doc) -> None:
	"""The step after an accepted document, made from it as a draft, when that step is next (not
	skipped, not made already) and is a client document (the project is the hub's)."""
	if steps.closed_reason(doc.lead):
		return
	action = chain(doc.lead)["next"]
	if not action or action["action"] != "create" or action.get("source") != doc.name:
		return
	made, warnings = create_document(doc.name, action["document_type"], announce=False)
	kind = kinds.of(made).name
	subject = (
		_("Quotation {0} created from the accepted proposal {1}: review and send it").format(
			made.quotation or made.name, doc.name
		)
		if kind == kinds.QUOTATION
		else _("{0} {1} prepared from {2}: review and send it").format(kinds.label(kind), made.name, doc.name)
	)
	if warnings:
		subject += " (" + "; ".join(warnings) + ")"
	p.tell_lead_owner(doc.lead, subject, always=True)


def revise_on_changes(doc) -> None:
	"""The client asked for changes: the next version as a draft, once (a revision made meanwhile
	by hand is enough)."""
	locked = frappe.db.get_value(p.DOCTYPE, doc.name, ["status", "is_latest"], as_dict=True, for_update=True)
	if locked.status != p.DECLINED or not cint(locked.is_latest):
		return
	revision = p.create_revision(doc)
	noun = kinds.words(kinds.of(doc).name)["noun"]
	p.tell_lead_owner(
		doc.lead,
		_("Revision {0} of the {1} is ready for the changes asked: {2}").format(
			revision.name, noun, doc.decline_reason or _("no reason given")
		),
		always=True,
	)


def write_draft(doc, spec) -> list[str]:
	"""The AI's narrative on a new document, in one call (after the insert, so the usage log
	names it). When every provider fails it stays as the data built it, with a warning."""
	from tbo_smart import ai

	try:
		warnings = spec.writer(doc, None, spec.draft_feature)
	except frappe.ValidationError as e:
		# a provider failure or the day's AI limit: the document is still made
		frappe.clear_last_message()
		reason = e.summary if isinstance(e, ai.AIProviderError) else str(e)
		return [
			_("The AI couldn't write the draft ({0}). It is built from the data; try Regenerate.").format(
				reason
			)
		]
	doc.save()
	return warnings


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
		if source and source.quotation and frappe.db.exists("Quotation", source.quotation)
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
		fields=[
			"name",
			"title",
			"status",
			"quotation",
			"sent_on",
			"view_count",
			"decided_on",
			"modified",
			"hub_project",
			"hub_project_url",
			"uat_status",
		],
		order_by="creation desc",
	)
	return next((row for row in rows if row.status == p.ACCEPTED), rows[0] if rows else None)


def chain(lead: str) -> dict:
	"""The lead's document chain for the session user (docs/client-documents.md §9):

	{steps, next, blocked, can_steer, note}. `steps` has each switched-on type in order
	(Proposal → Quotation → Project Agreement → … or Proposal → Project Agreement → … while the
	proposal is the quotation), then the Project and the UAT sign-off, each with its document's
	status, or Skipped / Not used (who, when, why), and what the person may do with it
	(`actions`: open, create, skip, bring_back). `next` is the one primary action: the first step
	still to do, after any steps skipped or not used. `blocked` says why nothing moves on a lost
	lead."""
	rules = kinds.chain_for(lead)
	recorded = steps.rows(lead)
	found = []
	for spec in enabled_types():
		if not rules.includes(spec.name):
			continue
		row = latest(lead, spec.name)
		found.append(document_step(spec, row, recorded.get(spec.name)))
		if spec.name == rules.project_after:
			found += delivery_steps(row, rules.uat, recorded)
	mark_not_used(found)
	blocked = steps.closed_reason(lead)
	may_steer = steps.can_steer(lead)
	may_write = bool(frappe.has_permission("Lead", "write", lead))  # a flag for the actions - nosemgrep
	action = None if blocked else next_action(found)
	for index in range(len(found)):
		add_actions(found, index, action, may_write and not blocked, may_steer)
	return {
		"steps": found,
		"next": action,
		"blocked": blocked,
		"can_steer": bool(may_steer and not blocked),
		"note": None if blocked else waiting_note(found, action),
	}


def document_step(spec, row, recorded) -> dict:
	"""One client document's step: its latest document, unless the step is skipped or not used
	(a draft left behind when it was skipped stays openable)."""
	skipped = steps.payload(recorded)
	decided = bool(row and row.status in steps.DECIDED)
	step = {
		"document_type": spec.name,
		"label": kinds.label(spec.name),
		"name": row.name if row else None,
		"title": row.title if row else None,
		# the document's own status, whatever the step shows
		"document_status": row.status if row else None,
		"status": row.status if row else None,
		"status_label": status_label(spec.name, row.status) if row else None,
		"done": bool(row and row.status == p.ACCEPTED),
		"view_count": cint(row.view_count) if row else 0,
		"quotation_status": quotation_status(row.quotation) if row and spec.priced else None,
		"skipped": None,
		# brought back after a skip: wanted, so it isn't taken as not used
		"wanted": bool(recorded and not recorded.status),
	}
	if skipped and not decided:
		step.update(skipped_values(skipped))
	return step


def skipped_values(skipped: dict) -> dict:
	return {
		"status": skipped["status"],
		"status_label": _(skipped["status"]),
		"skipped": skipped,
		"done": False,
	}


def delivery_steps(plan, with_uat: bool, recorded: dict) -> list[dict]:
	"""After the last document: the hub's project, made from it (kept on the plan) or straight from
	the lead (kept on the lead's Project step), and (with_uat) the UAT sign-off on that project."""
	own = recorded.get(steps.PROJECT)
	holder = plan if plan and plan.hub_project else (own if own and own.hub_project else None)
	project = holder.hub_project if holder else None
	uat = holder.uat_status if holder else None
	project_step = {
		"document_type": steps.PROJECT,
		"label": _("Project"),
		"name": project,
		"title": project,
		"document_status": None,
		"status": "Created" if project else None,
		"status_label": _("Created") if project else None,
		"done": bool(project),
		"url": holder.hub_project_url if holder else None,
		"view_count": 0,
		"quotation_status": None,
		"skipped": None,
		"wanted": bool(own and not own.status),
		# made straight from the lead (no plan): its UAT has no SRS to test against
		"from_lead": bool(holder is not None and holder is own),
	}
	skipped = None if project else steps.payload(own)
	if skipped:
		project_step.update(skipped_values(skipped))
	if not with_uat:
		return [project_step]
	own_uat = recorded.get(steps.UAT)
	uat_step = {
		"document_type": steps.UAT,
		"label": _("UAT sign-off"),
		"name": project,
		"title": None,
		"document_status": None,
		"status": uat,
		"status_label": _(uat) if uat else None,
		"done": uat == UAT_SIGNED,
		"view_count": 0,
		"quotation_status": None,
		"skipped": None,
		"wanted": bool(own_uat and not own_uat.status),
		"from_lead": project_step["from_lead"],
	}
	skipped = None if uat_step["done"] else steps.payload(own_uat)
	if skipped:
		uat_step.update(skipped_values(skipped))
	return [project_step, uat_step]


def started(step: dict) -> bool:
	"""The step has a document (or the project / UAT is under way), skipped or not."""
	return bool(step["name"] or step["done"])


def settled(step: dict) -> bool:
	return bool(step["done"] or step["skipped"] or step.get("implicit"))


def mark_not_used(found: list[dict]) -> None:
	"""A chain started further on: the steps before the furthest one under way that have nothing
	(and weren't brought back) show as Not used, without a record, until someone marks them."""
	last = max(
		(index for index, step in enumerate(found) if started(step) and not step["skipped"]), default=0
	)
	# nothing under way (last 0): nothing before it
	for step in found[:last]:
		if not started(step) and not step["skipped"] and not step["wanted"]:
			step.update({"status": steps.NOT_USED, "status_label": _(steps.NOT_USED), "implicit": True})


def next_action(found: list[dict]) -> dict | None:
	"""The first step still to do: carry on with its document, or make it."""
	for index, step in enumerate(found):
		if settled(step):
			continue
		return step_action(step, found[:index])
	return None


def step_action(step: dict, before: list[dict]) -> dict | None:
	kind = step["document_type"]
	if kind == steps.UAT:
		# no project yet, or one made straight from the lead (no SRS for the tests): waits (`note`)
		if not step["name"] or step.get("from_lead"):
			return None
		return {"action": "uat", "document_type": kind, "name": step["name"], "label": _("UAT sign-off")}
	if kind == steps.PROJECT:
		plan = before[-1] if before and before[-1]["done"] and not before[-1]["skipped"] else None
		return {
			"action": "create_project",
			"document_type": kind,
			"source_type": plan["document_type"] if plan else "Lead",
			"source": plan["name"] if plan else None,
			"label": _("Create project"),
		}
	if step["name"]:
		status = step["document_status"]
		label = (
			_("Review and send")
			if status == p.DRAFT
			else _("Open the {0}").format(phrase(kind))
			if status in p.WITH_CLIENT
			else _("Revise the {0}").format(phrase(kind))
		)
		return {"action": "open", "document_type": kind, "name": step["name"], "label": label}
	if kind == kinds.PROPOSAL:
		return {"action": "create_proposal", "document_type": kind, "label": _("New proposal")}
	return {
		"action": "create",
		"document_type": kind,
		"source": source_for(before),
		"label": _("Create {0}").format(phrase(kind)),
	}


def source_for(before: list[dict]) -> str | None:
	"""The latest accepted document before a step (skipped ones aside), else None: the lead."""
	for step in reversed(before):
		if step["document_type"] in kinds.TYPES and step["done"] and not step["skipped"]:
			return step["name"]
	return None


def prefill_for(before: list[dict]) -> str | None:
	"""Without an accepted document before a step: the latest document before it of any status (a
	skipped draft), to copy the client's details from, else None: the lead."""
	return next(
		(step["name"] for step in reversed(before) if step["document_type"] in kinds.TYPES and step["name"]),
		None,
	)


def add_actions(found: list[dict], index: int, action: dict | None, may_write: bool, may_steer: bool) -> None:
	"""What the person may do with the step, and for making it: from what (`source`), whether it
	is the next step (`in_order`) and, when it can't be made, why (`create_refused`)."""
	step = found[index]
	kind = step["document_type"]
	is_next = bool(action and action["document_type"] == kind)
	before = found[:index]
	actions = ["open"] if step["name"] else []
	source = source_for(before)
	step.update(
		{
			"source": source,
			"prefill": None if source else prefill_for(before),
			"in_order": is_next,
			"create_refused": None,
		}
	)
	if may_write and kind != steps.UAT and not started(step):
		refused = create_refusal(before) or (
			None
			if is_next or may_steer
			else _("Only the lead's owner, a head of its department or a manager can work out of order.")
		)
		step["create_refused"] = refused
		if not refused:
			actions.append("create")
	elif not may_write:
		step["create_refused"] = _("You can't change this lead's documents.")
	if may_write and may_steer:
		if step["skipped"]:
			actions.append("bring_back")
		elif not steps.skip_refused(step):
			actions.append("skip")
	step["actions"] = actions


def create_refusal(before: list[dict]) -> str | None:
	"""A step is made once every document before it is accepted, skipped or not used."""
	for step in before:
		if step["document_type"] in kinds.TYPES and step["name"] and not settled(step):
			return _("{0} {1} is {2}. Make this once it is accepted, or skip that step first.").format(
				step["label"], step["name"], (step["status_label"] or "").lower()
			)
	return None


def waiting_note(found: list[dict], action: dict | None) -> str | None:
	"""Why nothing is next while a step is still to do: the UAT has no project, or its project was
	made straight from the lead, with no SRS to test against."""
	waiting = next((step for step in found if not settled(step)), None)
	if action or not waiting:
		return None
	if waiting.get("from_lead"):
		return _(
			"The UAT sign-off tests the SRS, and this project was made straight from the lead. Skip the UAT, or send a sign-off from the project."
		)
	return _("The UAT sign-off needs a project. Skip it, or bring the project back.")


def phrase(name: str) -> str:
	"""The type's name inside a sentence: "quotation", but "BRD" and "SRS" as they are."""
	label = kinds.label(name)
	return label if label.isupper() else label.lower()


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
