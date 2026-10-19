"""The family of client documents (docs/client-documents.md): one engine, one doctype.

Every document a client reads after the first call is a TBO Proposal of its own type: the
proposal, then the quotation, the project agreement (work order) and the documents after it. They
share the blocks, the libraries, the ivory page and PDF, the letter head, the link, sending, views,
accept / sign and revisions (proposals.py). What differs per type is here: its sections and
headings, how each section is built from data, whether its quotation is its price, and the words
the client reads. A type is made from the accepted document before it (`follows`), copying its
client profile, scoping and modules, so presales mostly reviews.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import add_days, flt, formatdate, getdate

from tbo_smart.lead_pipeline import proposal_builder as builder
from tbo_smart.lead_pipeline import setting

PROPOSAL, QUOTATION, WORK_ORDER, BRD = "Proposal", "Quotation", "Work Order", "BRD"
CLAUSE_LIBRARY = "Agreement Clause"

# The Universal IT Proposal (docs/proposals.md §2): 23 sections in three parts, each with its
# default heading. A template is a preset on top of it: which sections are on, the default modules
# and the scoping questions.
MASTER_SECTIONS = (
	("Cover", "Cover"),
	("Executive Summary", "Executive Summary"),
	("Company Overview", "Company Overview"),
	("Client Background", "Client Background & Business Understanding"),
	("Business Challenges", "Business Challenges"),
	("Proposed Solution", "Proposed Solution"),
	("Scope of Work", "Scope of Work"),
	("Modules & Features", "Functional Modules & Features"),
	("Proposed Workflow", "Proposed Workflow"),
	("Technical Architecture", "Technical Architecture"),
	("Deliverables", "Project Deliverables"),
	("Implementation Plan", "Implementation Plan"),
	("Project Timeline", "Project Timeline"),
	("Project Team", "Project Team & Responsibilities"),
	("Investment", "Investment"),
	("Payment Terms", "Payment Terms"),
	("Warranty & Support", "Warranty & Post-Implementation Support"),
	("AMC", "Annual Maintenance Contract (AMC)"),
	("Assumptions", "Assumptions & Dependencies"),
	("Out of Scope", "Out of Scope"),
	("Risks & Mitigation", "Risks & Mitigation"),
	("Terms & Conditions", "Terms & Conditions"),
	("Acceptance", "Acceptance & Approval"),
)
PART_STARTS = {
	"Executive Summary": "Part A — Business Proposal",
	"Scope of Work": "Part B — Technical & Implementation Proposal",
	"Investment": "Part C — Commercial Proposal",
}
# the quotation: the commercial offer only, priced by its own ERPNext Quotation
QUOTATION_SECTIONS = (
	("Cover", "Cover"),
	("Modules & Features", "What is included"),
	("Investment", "Quotation"),
	("Payment Terms", "Payment Terms"),
	("AMC", "Annual Maintenance Contract (AMC)"),
	("Terms & Conditions", "Terms & Conditions"),
	("Acceptance", "Acceptance"),
)
# the project agreement: what was agreed, signed by both sides
WORK_ORDER_SECTIONS = (
	("Cover", "Cover"),
	("Parties", "Parties"),
	("Purpose", "Purpose of this Agreement"),
	("Scope of Work", "Scope of Work"),
	("Modules & Features", "Modules & Customisations"),
	("Deliverables", "Deliverables"),
	("Project Timeline", "Project Timeline"),
	("Investment", "Price"),
	("Payment Terms", "Payment Milestones"),
	("Change Requests", "Change Request Process"),
	("Warranty & Support", "Warranty & Support"),
	("AMC", "Annual Maintenance Contract (AMC)"),
	("Intellectual Property", "Intellectual Property & Source Code"),
	("Confidentiality", "Confidentiality"),
	("Termination", "Termination"),
	("Terms & Conditions", "General Terms"),
	("Acceptance", "Signatures"),
)
# the business requirements: the AI drafts the narrative from the client's own words only
BRD_SECTIONS = (
	("Cover", "Cover"),
	("Business Objectives", "Business Objectives"),
	("Stakeholders", "Stakeholders"),
	("Current Process", "Current Process (As-Is)"),
	("Pain Points", "Pain Points"),
	("Desired Process", "Desired Process (To-Be)"),
	("Business Rules", "Business Rules"),
	("Reports Needed", "Reports Needed"),
	("Integrations", "Integrations"),
	("Data Migration", "Data Migration"),
	("Success Criteria", "Success Criteria"),
	("Constraints", "Constraints"),
	("Assumptions", "Assumptions"),
	("Open Questions", "Open Questions"),
	("Acceptance", "Approval"),
)
# the clause library's groups, one a section of the agreement
CLAUSE_GROUPS = {
	"Change Requests": "Change Requests",
	"Intellectual Property": "Intellectual Property",
	"Confidentiality": "Confidentiality",
	"Termination": "Termination",
	"Terms & Conditions": "General",
}


@dataclass(frozen=True)
class DocumentType:
	"""What one type of client document is (see the module's docstring)."""

	name: str
	prefix: str
	# TBO Smart Settings' switch for it (the proposal's is Proposals itself)
	flag: str | None
	# the accepted type it is made from
	follows: str | None
	sections: tuple
	parts: dict = field(default_factory=dict)
	builders: dict = field(default_factory=dict)
	ai_sections: tuple = ()
	# its quotation is its price: priced here and sent with it, and accepting it accepts the quotation
	priced: bool = False
	# it shows the price of the accepted quotation it was made from
	shows_price: bool = False
	contents: bool = True
	# the client signs it with their designation too
	signature: bool = False
	# what Accepted reads as for this type
	accepted_as: str = "Accepted"
	# the AI's writer for `ai_sections`: writer(doc, kinds, feature, whole) -> warnings
	writer: Callable | None = None
	# the usage log's feature names: a whole draft, one section
	draft_feature: str = "Proposal draft"
	section_feature: str = "Proposal section"
	# the client answers questions on their own page (the BRD)
	questionnaire: bool = False
	# called on a new document before its sections are built
	prepare: Callable | None = None

	@property
	def headings(self) -> dict:
		return dict(self.sections)

	@property
	def section_types(self) -> tuple:
		return (*(kind for kind, _heading in self.sections), "Image", "Custom")

	@property
	def part_of(self) -> dict:
		"""{section type: its part}: each section belongs to the part it falls in."""
		part, found = None, {}
		for kind, _heading in self.sections:
			part = self.parts.get(kind, part)
			found[kind] = part
		return found


# ---------- building the sections from data ----------


def build_section(doc, kind: str) -> str | None:
	"""A section's text from the data and the libraries, or None for one that isn't built from data
	(the cover, the acceptance, the AI's, a custom block)."""
	make = of(doc).builders.get(kind)
	return make(doc) if make else None


def build_all(doc) -> None:
	"""Every data section's text, for a new document."""
	for row in doc.sections:
		text = build_section(doc, row.section_type)
		if text is not None:
			row.body = text or None


def company_of(doc) -> frappe._dict:
	"""Our company on the document: its quotation's, else the default one."""
	name = (
		frappe.db.get_value("Quotation", doc.quotation, "company") if doc.get("quotation") else None
	) or frappe.defaults.get_global_default("company")
	return (
		frappe.db.get_value("Company", name, ["name", "company_name", "tax_id"], as_dict=True)
		or frappe._dict()
	)


def company_address(company: str | None) -> str:
	"""The company's first address on one line."""
	if not company:
		return ""
	link = frappe.qb.DocType("Dynamic Link")
	found = (
		frappe.qb.from_(link)
		.select(link.parent)
		.where(link.link_doctype == "Company")
		.where(link.link_name == company)
		.where(link.parenttype == "Address")
		.limit(1)
		.run()
	)
	if not found:
		return ""
	address = frappe.db.get_value(
		"Address", found[0][0], ["address_line1", "address_line2", "city", "state", "pincode"], as_dict=True
	)
	return ", ".join(part for part in (address or {}).values() if part)


def parties(doc) -> str:
	"""Who signs: us (with the address and GSTIN) and the client (with their contact)."""
	ours = company_of(doc)
	lead = frappe.db.get_value("Lead", doc.lead, ["company_name", "lead_name", "city", "state"], as_dict=True)
	client = lead.company_name or lead.lead_name
	provider = [ours.company_name or "", company_address(ours.name)]
	if ours.tax_id:
		provider.append(_("GSTIN: {0}").format(ours.tax_id))
	customer = [client, ", ".join(part for part in (lead.city, lead.state) if part)]
	if lead.company_name and lead.lead_name:
		customer.append(_("Represented by {0}").format(lead.lead_name))
	return "\n\n".join(
		[
			f"## {_('Service provider')}\n" + "\n".join(line for line in provider if line),
			f"## {_('Client')}\n" + "\n".join(line for line in customer if line),
			_('In this agreement, {0} is "TBO" and {1} is "the Client".').format(
				ours.company_name or "", client
			),
		]
	)


def lineage(doc) -> list:
	"""The documents this one was made from, nearest first."""
	found, name = [], doc.get("source_document")
	while name and len(found) < 10:
		row = frappe.db.get_value(
			"TBO Proposal",
			name,
			["name", "document_type", "decided_on", "source_document"],
			as_dict=True,
		)
		if not row:
			break
		found.append(row)
		name = row.source_document
	return found


def purpose(doc) -> str:
	"""What the agreement confirms, with the references of the documents the client accepted."""
	ours = company_of(doc)
	client = frappe.db.get_value("Lead", doc.lead, "company_name") or frappe.db.get_value(
		"Lead", doc.lead, "lead_name"
	)
	accepted = {row.document_type: row for row in lineage(doc)}
	refs = [
		_("the {0} {1}, accepted on {2}").format(
			_(kind).lower(), accepted[kind].name, formatdate(accepted[kind].decided_on, "d MMMM yyyy")
		)
		for kind in (PROPOSAL, QUOTATION)
		if kind in accepted and accepted[kind].decided_on
	]
	text = _("This agreement confirms that {0} engages {1} for {2}").format(
		client, ours.company_name or _("us"), doc.get("solution_type") or _("the project")
	)
	text += (", " + _("as set out in {0}").format((" " + _("and") + " ").join(refs)) if refs else "") + "."
	parts = [
		text,
		_(
			"It sets the scope, the timeline, the price, the payment milestones and the terms of the project. Where it differs from the documents before it, this agreement prevails."
		),
	]
	if doc.get("start_date"):
		parts.append(_("The project starts on {0}.").format(formatdate(doc.start_date, "d MMMM yyyy")))
	return "\n\n".join(parts)


def dated_timeline(doc) -> str:
	"""The proposal's timeline template with dates from the agreement's start: each phase starts
	the day after the one before ends (calendar days)."""
	chosen = builder.chosen_timeline(doc)
	if not chosen:
		return ""
	rows = builder.milestone_rows(chosen.body)
	total = f"{flt(chosen.weeks):g}"
	if not doc.get("start_date"):
		return (
			f"{_('Planned over {0} days from the start date, which is set before signing.').format(total)}\n\n"
			+ builder.table((_("Phase"), _("Days")), [(name, f"{days:g}") for name, days in rows])
		)
	start, dated = getdate(doc.start_date), []
	for name, days in rows:
		end = add_days(start, max(math.ceil(days) - 1, 0))
		dated.append((name, f"{days:g}", formatdate(start, "d MMM yyyy"), formatdate(end, "d MMM yyyy")))
		start = add_days(end, 1)
	finish = dated[-1][3] if dated else formatdate(doc.start_date, "d MMM yyyy")
	return (
		f"{_('Starts on {0} and is planned to finish by {1} ({2} days).').format(formatdate(doc.start_date, 'd MMMM yyyy'), finish, total)}\n\n"
		+ builder.table((_("Phase"), _("Days"), _("From"), _("To")), dated)
	)


def agreed_price(doc) -> str:
	"""The agreed price is the accepted quotation's; the notes on what it includes follow."""
	quote = next((row for row in lineage(doc) if row.document_type == QUOTATION), None)
	lead_in = (
		_("The price is the one in the quotation {0} the Client accepted.").format(quote.name)
		if quote
		else _("The price is the one the Client accepted.")
	)
	notes = builder.investment(doc)
	return "\n\n".join(part for part in (lead_in, notes) if part)


def clauses(doc, kind: str) -> str:
	"""The clause library's clauses for one section: a single clause as its text, several as
	numbered subsections."""
	group = CLAUSE_GROUPS[kind]
	rows = [row for row in builder.library(CLAUSE_LIBRARY, doc) if (row.group or "") == group]
	if len(rows) == 1:
		return (rows[0].body or "").strip()
	return builder.titled(rows)


def change_requests(doc) -> str:
	text = clauses(doc, "Change Requests")
	day_rate = flt(setting("proposal_day_rate"))
	if day_rate:
		rate = _("Changes are charged at {0} a person-day.").format(
			builder.money(day_rate, builder.default_currency())
		)
		text = "\n\n".join(part for part in (text, rate) if part)
	return text


def agreed_amc(doc) -> str:
	"""The AMC plan the client chose (the proposal's recommended one), its features and yearly fee;
	empty when there is none."""
	answers = builder.scoping_answers(doc.scoping, doc.solution_type)
	if not answers["amc"] or not answers["amc_plan"]:
		return ""
	plan = next((row for row in builder.library("AMC Plan", doc) if row.title == answers["amc_plan"]), None)
	if not plan:
		return ""
	rows = []
	for line in builder.lines(plan.body):
		label, _sep, value = line.partition("|")
		rows.append((label.strip(), value.strip()))
	rows.append((_("Yearly fee"), builder.money(plan.amount, builder.default_currency())))
	note = _(
		"The {0} plan starts when the warranty ends and is billed yearly, separate from the project price."
	).format(plan.title)
	return f"{note}\n\n" + builder.table((_("What you get"), plan.title), rows)


def quotation_terms(doc) -> str:
	"""Its validity, then the quotation's own terms (ERPNext's Terms and Conditions), else the
	terms library without the proposal's validity line."""
	from tbo_smart.sales.activities import plain_text

	validity = (
		_("This quotation is valid till {0}.").format(formatdate(doc.valid_till, "d MMMM yyyy"))
		if doc.get("valid_till")
		else ""
	)
	terms = (
		plain_text(frappe.db.get_value("Quotation", doc.quotation, "terms")) if doc.get("quotation") else ""
	)
	if not terms:
		terms = builder.titled(
			[row for row in builder.library("Terms", doc) if row.title.casefold() != "validity"]
		)
	return "\n\n".join(part for part in (validity, terms) if part)


PROPOSAL_TYPE = DocumentType(
	name=PROPOSAL,
	prefix="PROP",
	flag=None,
	follows=None,
	sections=MASTER_SECTIONS,
	parts=PART_STARTS,
	builders=builder.BUILDERS,
	ai_sections=builder.AI_SECTIONS,
	writer=builder.write_sections,
	priced=True,
)
QUOTATION_TYPE = DocumentType(
	name=QUOTATION,
	prefix="QUOT",
	flag="enable_quotation_documents",
	follows=PROPOSAL,
	sections=QUOTATION_SECTIONS,
	builders={
		"Modules & Features": builder.modules_and_features,
		"Investment": builder.investment,
		"Payment Terms": builder.payment_terms,
		"AMC": builder.amc,
		"Terms & Conditions": quotation_terms,
	},
	priced=True,
	contents=False,
)
WORK_ORDER_TYPE = DocumentType(
	name=WORK_ORDER,
	prefix="WO",
	flag="enable_work_orders",
	follows=QUOTATION,
	sections=WORK_ORDER_SECTIONS,
	builders={
		"Parties": parties,
		"Purpose": purpose,
		"Scope of Work": builder.scope_of_work,
		"Modules & Features": builder.modules_and_features,
		"Deliverables": builder.deliverables,
		"Project Timeline": dated_timeline,
		"Investment": agreed_price,
		"Payment Terms": builder.payment_terms,
		"Change Requests": change_requests,
		"Warranty & Support": builder.warranty,
		"AMC": agreed_amc,
		"Intellectual Property": lambda doc: clauses(doc, "Intellectual Property"),
		"Confidentiality": lambda doc: clauses(doc, "Confidentiality"),
		"Termination": lambda doc: clauses(doc, "Termination"),
		"Terms & Conditions": lambda doc: clauses(doc, "Terms & Conditions"),
	},
	shows_price=True,
	signature=True,
	accepted_as="Signed",
)


def brd_type() -> DocumentType:
	"""The BRD's type (its builders and writer live in brd.py)."""
	from tbo_smart.lead_pipeline import brd

	return DocumentType(
		name=BRD,
		prefix="BRD",
		flag="enable_brds",
		follows=WORK_ORDER,
		sections=BRD_SECTIONS,
		builders=brd.BUILDERS,
		ai_sections=brd.AI_SECTIONS,
		writer=brd.write_sections,
		draft_feature="BRD draft",
		section_feature="BRD section",
		questionnaire=True,
		prepare=lambda doc: brd.merge_questions(doc, brd.library_questions(doc)),
		accepted_as="Approved",
	)


# in the order of the chain
TYPES = {spec.name: spec for spec in (PROPOSAL_TYPE, QUOTATION_TYPE, WORK_ORDER_TYPE, brd_type())}
ALL_SECTION_TYPES = tuple(dict.fromkeys(kind for spec in TYPES.values() for kind in spec.section_types))


def of(doc_or_type) -> DocumentType:
	"""A document's type (or a type by name); an old proposal without one is a proposal."""
	name = (
		doc_or_type
		if isinstance(doc_or_type, str) or doc_or_type is None
		else doc_or_type.get("document_type")
	)
	spec = TYPES.get(name or PROPOSAL)
	if not spec:
		frappe.throw(_("{0} isn't a kind of document we make.").format(name))
	return spec


def label(name: str) -> str:
	"""The type's name for people: the work order reads as the project agreement."""
	return {WORK_ORDER: _("Project Agreement")}.get(name) or _(name)


def accepted_label(name: str) -> str:
	"""What Accepted reads as for this type."""
	return _(of(name).accepted_as)


def default_sections(spec: DocumentType) -> list[dict]:
	"""A new document's sections: every section of its type, on, with its heading (a proposal
	without a template gets the master structure)."""
	return [{"section_type": kind, "heading": heading} for kind, heading in spec.sections]


def words(name: str) -> dict:
	"""The words the client's page, the PDF and the email use for this type."""
	if name == BRD:
		return {
			"kicker": _("Business Requirements Document"),
			"noun": _("BRD"),
			"for": _("Business requirements of {0}, from {1}"),
			"decide_title": _("Approve your requirements"),
			"decide_text": _(
				"Approve this document when it says what your business needs. We'll write the functional specification from it."
			),
			"accept_button": _("Approve the BRD"),
			"decline_summary": _("Something missing or wrong? Ask for changes"),
			"decline_prompt": _("What should change? (optional)"),
			"decline_button": _("Ask for changes"),
			"accepted": _("Approved"),
			"accepted_note": _("by {0} on {1}. Thank you, we'll write the functional specification next."),
			"declined": _("Changes asked for"),
			"ask_new": _("Ask us for the updated BRD."),
			"agree_text": _(
				"By approving below, the client confirms these are its business requirements for the project."
			),
			"accepted_online": _("Approved online"),
			"email_intro": _(
				"Your business requirements are written down. Read them, and approve them on the same page or tell us what to change."
			),
			"read_link": _("Read the business requirements: {0}"),
			"subject": _("{0}: your business requirements"),
		}
	if name == WORK_ORDER:
		return {
			"kicker": _("Project Agreement / Work Order"),
			"noun": _("agreement"),
			"for": _("Agreement with {0} from {1}"),
			"decide_title": _("Sign the agreement"),
			"decide_text": _(
				"Type your name, designation and email to sign this agreement. We'll countersign and start the project."
			),
			"accept_button": _("Sign the agreement"),
			"decline_summary": _("Something to change? Ask for changes"),
			"decline_prompt": _("What should change? (optional)"),
			"decline_button": _("Ask for changes"),
			"accepted": _("Signed"),
			"accepted_note": _("by {0} on {1}. Thank you, we'll be in touch to start."),
			"declined": _("Changes asked for"),
			"ask_new": _("Ask us for the updated agreement."),
			"agree_text": _(
				"By signing below, both parties agree to this agreement: its scope, timeline, price, payment milestones and terms."
			),
			"accepted_online": _("Signed online"),
			"email_intro": _(
				"Thank you for choosing us. The project agreement is ready for you to read and sign on the same page."
			),
			"read_link": _("Read and sign the agreement: {0}"),
			"subject": _("{0}: the project agreement"),
		}
	if name == QUOTATION:
		return {
			"kicker": _("Quotation"),
			"noun": _("quotation"),
			"for": _("Quotation for {0} from {1}"),
			"decide_title": _("Ready to go ahead?"),
			"decide_text": _("Accept the quotation and we'll prepare the project agreement."),
			"accept_button": _("Accept quotation"),
			"decline_summary": _("Not right yet? Decline it"),
			"decline_prompt": _("What would make it work? (optional)"),
			"decline_button": _("Decline quotation"),
			"accepted": _("Accepted"),
			"accepted_note": _("by {0} on {1}. Thank you, we'll be in touch."),
			"declined": _("Declined"),
			"ask_new": _("Ask us for an updated quotation."),
			"agree_text": _("By signing below, the client accepts this quotation, its price and its terms."),
			"accepted_online": _("Accepted online"),
			"email_intro": _(
				"Thank you for accepting our proposal. Our quotation is ready for you to read and accept."
			),
			"read_link": _("Read the quotation: {0}"),
			"subject": _("{0}: our quotation"),
		}
	return {
		"kicker": _("Proposal"),
		"noun": _("proposal"),
		"for": _("Proposal for {0} from {1}"),
		"decide_title": _("Ready to go ahead?"),
		"decide_text": _("Accept the proposal and we'll start on the next steps with you."),
		"accept_button": _("Accept proposal"),
		"decline_summary": _("Not the right fit? Decline it"),
		"decline_prompt": _("What would make it work? (optional)"),
		"decline_button": _("Decline proposal"),
		"accepted": _("Accepted"),
		"accepted_note": _("by {0} on {1}. Thank you, we'll be in touch."),
		"declined": _("Declined"),
		"ask_new": _("Ask us for a new proposal."),
		"agree_text": _("By signing below, both parties accept this proposal, its scope, price and terms."),
		"accepted_online": _("Accepted online"),
		"email_intro": _(
			"Thank you for your time. Our proposal is ready for you to read, and you can accept it on the same page."
		),
		"read_link": _("Read the proposal: {0}"),
		"subject": _("{0}: our proposal"),
	}
