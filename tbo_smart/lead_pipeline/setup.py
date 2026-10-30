"""Custom fields, masters and the print format of the lead pipeline on ERPNext's Lead and
Opportunity, created on install and every migrate (docs/lead-pipeline.md §4,
docs/erpnext-lead-cutover.md §2.1). Nothing on a site without ERPNext."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from tbo_smart.lead_pipeline import lead_category
from tbo_smart.lead_pipeline.statuses import NEW_LEAD, STATUS_NAMES
from tbo_smart.sales import DEAL, LEAD, LEAD_LINK, erpnext_installed
from tbo_smart.sales.departments import DEPARTMENT, LEAD_TYPES
from tbo_smart.sales.opportunity import STAGE_TYPES, ensure_default_stages
from tbo_smart.sales.stages import OTHER_REASON

QUOTE_STATUSES = (
	"Draft",
	"Pending Approval",
	"Approved",
	"Sent to Client",
	"Viewed / Acknowledged",
	"Under Negotiation",
	"Revision Required",
	"Accepted",
	"Rejected",
	"Expired",
)
PRINT_FORMAT = "TBO Quotation"
SEGMENTS = ("Retail", "Small", "Medium", "Large", "Premium")
HUB_ROLE = "Hub Integration"


def get_pipeline_fields() -> dict:
	tracked = {"read_only": 1, "no_copy": 1, "allow_on_submit": 1}
	hidden = {"hidden": 1, "read_only": 1, "no_copy": 1}
	stage_log = {
		"fieldname": "tbo_stage_log",
		"fieldtype": "Table",
		"options": "TBO Stage Change",
		"label": "Stage history",
		"read_only": 1,
		"no_copy": 1,
	}
	# the client document a quotation prices (docs/client-documents.md §3): not no_copy, so ERPNext's
	# mapper carries it from the quotation to the Sales Order and on to the Sales Invoice
	proposal_link = {
		"fieldname": "tbo_proposal",
		"fieldtype": "Link",
		"options": "TBO Proposal",
		"label": "Proposal",
		"read_only": 1,
		"description": "The proposal (or quotation document) the client accepted this price on; the client's reference.",
	}
	return {
		"Lead": [
			{
				# with ERPNext's industry, state and no_of_employees: lead_category.py
				"fieldname": "tbo_district",
				"fieldtype": "Data",
				"label": "District",
				"insert_after": "state",
				"in_standard_filter": 1,
				"description": "Free text; Kerala's districts are suggested in the hub.",
			},
			{
				"fieldname": "tbo_stage",
				"fieldtype": "Select",
				"options": "\n".join(STATUS_NAMES),
				"default": NEW_LEAD,
				"label": "Stage",
				"insert_after": "status",
				"in_list_view": 1,
				"in_standard_filter": 1,
				"search_index": 1,
				"description": "TBO's pipeline stage. ERPNext sets Status itself.",
			},
			{
				"fieldname": "tbo_segment",
				"fieldtype": "Select",
				"options": "\n" + "\n".join(SEGMENTS),
				"label": "Segment",
				"insert_after": "tbo_stage",
				"in_standard_filter": 1,
			},
			{
				"fieldname": "tbo_department",
				"fieldtype": "Link",
				"options": DEPARTMENT,
				"label": "Department",
				"insert_after": "tbo_segment",
				"in_list_view": 1,
				"in_standard_filter": 1,
				"search_index": 1,
				"description": "Its heads see every lead of the department (docs/lead-departments.md).",
			},
			{
				"fieldname": "tbo_lead_type",
				"fieldtype": "Select",
				"options": "\n" + "\n".join(LEAD_TYPES),
				"label": "Lead type",
				"insert_after": "tbo_department",
				"in_standard_filter": 1,
				"fetch_from": "tbo_department.lead_type",
				"fetch_if_empty": 1,
			},
			{
				"fieldname": "tbo_lost_reason",
				"fieldtype": "Link",
				"options": "Opportunity Lost Reason",
				"label": "Lost reason",
				"insert_after": "tbo_lead_type",
				"depends_on": "eval:doc.tbo_stage == 'Closed – Lost'",
			},
			{
				"fieldname": "tbo_lost_notes",
				"fieldtype": "Small Text",
				"label": "Lost notes",
				"insert_after": "tbo_lost_reason",
				"depends_on": "eval:doc.tbo_stage == 'Closed – Lost'",
			},
			{
				# what the client's website says, read once for its proposals (proposal_profile)
				"fieldname": "tbo_web_profile",
				"fieldtype": "Long Text",
				"label": "Website profile",
				"insert_after": "website",
				"read_only": 1,
				"no_copy": 1,
				"description": "Read from the client's website for proposals (JSON).",
			},
			{
				"fieldname": "tbo_web_profile_on",
				"fieldtype": "Datetime",
				"label": "Website read on",
				"insert_after": "tbo_web_profile",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "tbo_stage_section",
				"fieldtype": "Section Break",
				"label": "Stage history",
				"insert_after": "notes",
				"collapsible": 1,
			},
			{**stage_log, "insert_after": "tbo_stage_section"},
			{
				"fieldname": "crm_lead_ref",
				"fieldtype": "Data",
				"label": "Frappe CRM lead",
				"insert_after": "tbo_stage_log",
				"search_index": 1,
				**hidden,
			},
		],
		"Opportunity": [
			{
				"fieldname": "tbo_lead",
				"fieldtype": "Link",
				"options": "Lead",
				"label": "Lead",
				"insert_after": "party_name",
				"read_only": 1,
				"search_index": 1,
				"description": "The lead it was made from, also once it is made out to the lead's Customer.",
			},
			{
				"fieldname": "tbo_customer",
				"fieldtype": "Link",
				"options": "Customer",
				"label": "ERPNext customer",
				"insert_after": "tbo_lead",
				"read_only": 1,
				"no_copy": 1,
				"description": "The client's Customer, once there is one.",
			},
			{
				"fieldname": "tbo_next_step",
				"fieldtype": "Data",
				"label": "Next step",
				"insert_after": "sales_stage",
			},
			{
				"fieldname": "tbo_closed_on",
				"fieldtype": "Date",
				"label": "Won or lost on",
				"insert_after": "tbo_next_step",
				"read_only": 1,
				"no_copy": 1,
			},
			{
				"fieldname": "tbo_stage_section",
				"fieldtype": "Section Break",
				"label": "Stage history",
				"insert_after": "notes",
				"collapsible": 1,
			},
			{**stage_log, "insert_after": "tbo_stage_section"},
			{
				"fieldname": "crm_deal_ref",
				"fieldtype": "Data",
				"label": "Frappe CRM deal",
				"insert_after": "tbo_stage_log",
				"search_index": 1,
				**hidden,
			},
		],
		"Sales Stage": [
			{
				"fieldname": "tbo_stage_type",
				"fieldtype": "Select",
				"options": "\n" + "\n".join(STAGE_TYPES),
				"label": "Type",
				"insert_after": "stage_name",
				"in_list_view": 1,
				"description": "Won and Lost close the deal; blank reads as Ongoing.",
			},
			{
				"fieldname": "tbo_probability",
				"fieldtype": "Percent",
				"label": "Probability",
				"insert_after": "tbo_stage_type",
			},
			{
				"fieldname": "tbo_position",
				"fieldtype": "Int",
				"label": "Position",
				"insert_after": "tbo_probability",
				"description": "The order of the stages in the pipeline.",
			},
		],
		"Quotation": [
			{
				"fieldname": "tbo_lead_section",
				"fieldtype": "Section Break",
				"label": "TBO lead pipeline",
				"insert_after": "party_name",
				"collapsible": 1,
			},
			{
				"fieldname": "tbo_lead",
				"fieldtype": "Link",
				"options": "Lead",
				"label": "Lead",
				"insert_after": "tbo_lead_section",
				"read_only": 1,
				"search_index": 1,
				"description": "The lead it was made for, also once it is made out to the lead's Customer.",
			},
			{
				"fieldname": "tbo_lead_meeting",
				"fieldtype": "Link",
				"options": "TBO Lead Meeting",
				"label": "From meeting",
				"insert_after": "tbo_lead",
				"read_only": 1,
			},
			{
				"fieldname": "tbo_package",
				"fieldtype": "Link",
				"options": "TBO Quotation Package",
				"label": "Package",
				"insert_after": "tbo_lead_meeting",
				"read_only": 1,
			},
			{
				"fieldname": "tbo_quote_status",
				"fieldtype": "Select",
				"options": "\n".join(QUOTE_STATUSES),
				"label": "Quote status",
				"insert_after": "tbo_package",
				"in_list_view": 1,
				"in_standard_filter": 1,
				**tracked,
			},
			{"fieldname": "tbo_lead_column", "fieldtype": "Column Break", "insert_after": "tbo_quote_status"},
			{
				"fieldname": "tbo_quote_family",
				"fieldtype": "Data",
				"label": "Quotation family",
				"insert_after": "tbo_lead_column",
				"search_index": 1,
				"read_only": 1,
				"description": "The first quotation of the chain; every revision carries it.",
			},
			{
				"fieldname": "tbo_revision_no",
				"fieldtype": "Int",
				"label": "Revision",
				"insert_after": "tbo_quote_family",
				"read_only": 1,
			},
			{
				"fieldname": "tbo_is_latest",
				"fieldtype": "Check",
				"label": "Latest revision",
				"insert_after": "tbo_revision_no",
				**tracked,
			},
			{
				"fieldname": "tbo_sent_via",
				"fieldtype": "Select",
				"options": "\nEmail\nWhatsApp\nManual",
				"label": "Sent via",
				"insert_after": "tbo_is_latest",
				**tracked,
			},
			{
				"fieldname": "tbo_sent_on",
				"fieldtype": "Datetime",
				"label": "Sent on",
				"insert_after": "tbo_sent_via",
				**tracked,
			},
			{**proposal_link, "insert_after": "tbo_sent_on", "allow_on_submit": 1},
		],
		# carried from the quotation by ERPNext's mapper, so accounts trace an order or an invoice
		# to the proposal the client accepted
		"Sales Order": [{**proposal_link, "insert_after": "po_date"}],
		"Sales Invoice": [{**proposal_link, "insert_after": "po_date"}],
	}


def setup_lead_pipeline() -> None:
	if not erpnext_installed():
		return
	create_custom_fields(get_pipeline_fields(), ignore_validate=True)
	link_lead_records()
	ensure_default_stages()
	ensure_other_reason()
	ensure_hub_role()
	lead_category.seed_industries()
	lead_category.ensure_blank_org_size()
	sync_print_format()
	seed_proposal_templates()
	seed_agreement_clauses()
	seed_srs_library()
	seed_plan_library()


def link_lead_records() -> None:
	"""Deals and quotations still made out to a lead that predate `tbo_lead` get it."""
	for doctype, party_type in ((DEAL, "opportunity_from"), ("Quotation", "quotation_to")):
		table = frappe.qb.DocType(doctype)
		(
			frappe.qb.update(table)
			.set(table[LEAD_LINK], table.party_name)
			.where((table[party_type] == LEAD) & table[LEAD_LINK].isnull())
		).run()


def ensure_hub_role() -> None:
	"""The role of the TBO Support hub's API user (docs/hub-leads-api.md)."""
	if not frappe.db.exists("Role", HUB_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": HUB_ROLE, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def ensure_other_reason() -> None:
	"""Free-text lost reasons are saved as "Other" with the text as the notes."""
	if not frappe.db.exists("Opportunity Lost Reason", OTHER_REASON):
		frappe.get_doc({"doctype": "Opportunity Lost Reason", "lost_reason": OTHER_REASON}).insert(
			ignore_permissions=True
		)


def sync_print_format() -> None:
	"""The TBO Quotation print format (Jinja, from quotation_print.html), kept in step with the
	code on every migrate. Copy it under another name to customise it."""
	html = frappe.read_file(frappe.get_app_path("tbo_smart", "lead_pipeline", "quotation_print.html"))
	if frappe.db.exists("Print Format", PRINT_FORMAT):
		if frappe.db.get_value("Print Format", PRINT_FORMAT, "html") != html:
			frappe.db.set_value("Print Format", PRINT_FORMAT, "html", html)
		return
	frappe.get_doc(
		{
			"doctype": "Print Format",
			"name": PRINT_FORMAT,
			"doc_type": "Quotation",
			"module": "Tbo Smart",
			"standard": "No",
			"custom_format": 1,
			"print_format_type": "Jinja",
			"html": html,
		}
	).insert(ignore_permissions=True)


TEMPLATES_SEEDED = "tbo_proposal_templates_seeded"
MASTER_SEEDED = "tbo_proposal_master_seeded"


def seed_proposal_templates() -> None:
	"""The Universal IT Proposal's presets, the module catalogue and the section libraries, once
	per site (a marker in the defaults): after that they are the editors' own, and a migrate never
	brings back one they deleted. On a site that had the first templates (v1.0), the presets of
	the same name are rewritten to the master structure and the other first ones disabled."""
	from tbo_smart.lead_pipeline import proposal_templates as seed

	if frappe.db.get_default(MASTER_SEEDED):
		return
	for module in seed.MODULES:
		if not frappe.db.exists("TBO Proposal Module", module["module_name"]):
			frappe.get_doc({"doctype": "TBO Proposal Module", **module}).insert(ignore_permissions=True)
	if not frappe.db.count("TBO Proposal Library Item"):
		for item in seed.LIBRARY:
			frappe.get_doc({"doctype": "TBO Proposal Library Item", **item}).insert(ignore_permissions=True)
	for template in seed.templates():
		if frappe.db.exists("TBO Proposal Template", template["template_name"]):
			doc = frappe.get_doc("TBO Proposal Template", template["template_name"])
			doc.update({key: value for key, value in template.items() if key != "sections"})
			doc.set("sections", template["sections"])
			doc.save(ignore_permissions=True)
		else:
			frappe.get_doc({"doctype": "TBO Proposal Template", **template}).insert(ignore_permissions=True)
	for name in seed.OLD_TEMPLATES:
		if frappe.db.exists("TBO Proposal Template", name):
			frappe.db.set_value("TBO Proposal Template", name, "enabled", 0)
	frappe.db.set_default(TEMPLATES_SEEDED, 1)
	frappe.db.set_default(MASTER_SEEDED, 1)


def add_strategy_preset() -> None:
	"""The Digital Marketing 6-month strategy preset on a site that was seeded before it existed
	(a patch; a new site gets it with the first seed): its template, modules and library rows are
	added where missing, and the general software rows are left out of it. Nothing the editors
	changed is overwritten."""
	from tbo_smart.lead_pipeline import proposal_templates as seed

	module = "TBO Proposal Module"
	library = "TBO Proposal Library Item"
	for row in seed.MODULES:
		if seed.STRATEGY in row["solution_types"] and not frappe.db.exists(module, row["module_name"]):
			frappe.get_doc({"doctype": module, **row}).insert(ignore_permissions=True)
	for row in seed.LIBRARY:
		match = {"library": row["library"], "title": row["title"]}
		if row.get("solution_types") == seed.STRATEGY:
			if not frappe.db.exists(library, {**match, "solution_types": seed.STRATEGY}):
				frappe.get_doc({"doctype": library, **row}).insert(ignore_permissions=True)
		elif row.get("solution_types") == f"!{seed.STRATEGY}":
			for name in frappe.get_all(
				library, filters={**match, "solution_types": ("is", "not set")}, pluck="name"
			):
				frappe.db.set_value(library, name, "solution_types", row["solution_types"])
	template = next(row for row in seed.templates() if row["template_name"] == seed.STRATEGY)
	if not frappe.db.exists("TBO Proposal Template", template["template_name"]):
		frappe.get_doc({"doctype": "TBO Proposal Template", **template}).insert(ignore_permissions=True)


CLAUSES_SEEDED = "tbo_agreement_clauses_seeded"


def seed_agreement_clauses() -> None:
	"""The project agreement's clauses (the Agreement Clause library), once per site and only when
	the library has none: after that they are the editors' own (docs/client-documents.md §4)."""
	from tbo_smart.lead_pipeline import proposal_templates as seed

	if frappe.db.get_default(CLAUSES_SEEDED):
		return
	if not frappe.db.exists("TBO Proposal Library Item", {"library": seed.CLAUSE}):
		for clause in seed.AGREEMENT_CLAUSES:
			frappe.get_doc({"doctype": "TBO Proposal Library Item", **clause}).insert(ignore_permissions=True)
	frappe.db.set_default(CLAUSES_SEEDED, 1)


SRS_SEEDED = "tbo_srs_library_seeded"


def seed_srs_library() -> None:
	"""What each catalogue module adds to an SRS, and the non-functional requirements, once per site
	(docs/client-documents.md §6): a module's SRS fields are filled only where they are blank, and
	the requirements only when the library has none, so the editors' own text is never replaced."""
	from tbo_smart.lead_pipeline import proposal_templates as seed

	if frappe.db.get_default(SRS_SEEDED):
		return
	for name, text in seed.SRS_TEXT.items():
		current = frappe.db.get_value("TBO Proposal Module", name, list(text), as_dict=True)
		if current is None:
			continue
		blank = {field: value for field, value in text.items() if value and not current.get(field)}
		if blank:
			frappe.db.set_value("TBO Proposal Module", name, blank)
	if not frappe.db.exists("TBO Proposal Library Item", {"library": seed.NFR}):
		for row in seed.NON_FUNCTIONAL:
			frappe.get_doc({"doctype": "TBO Proposal Library Item", **row}).insert(ignore_permissions=True)
	frappe.db.set_default(SRS_SEEDED, 1)


PLAN_SEEDED = "tbo_plan_library_seeded"


def seed_plan_library() -> None:
	"""The client's responsibilities for implementation plans, once per site and only when the
	library has none (docs/client-documents.md §7)."""
	from tbo_smart.lead_pipeline import proposal_templates as seed

	if frappe.db.get_default(PLAN_SEEDED):
		return
	if not frappe.db.exists("TBO Proposal Library Item", {"library": seed.RESPONSIBILITY}):
		for row in seed.CLIENT_RESPONSIBILITIES:
			frappe.get_doc({"doctype": "TBO Proposal Library Item", **row}).insert(ignore_permissions=True)
	frappe.db.set_default(PLAN_SEEDED, 1)


def after_migrate() -> None:
	setup_lead_pipeline()
