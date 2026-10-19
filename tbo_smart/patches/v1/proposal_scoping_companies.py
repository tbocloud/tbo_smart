"""Proposals' scoping answers gain the number of companies (0: not said); users and companies can
be "Unlimited" (docs/proposals.md §3.1)."""

import frappe

from tbo_smart.lead_pipeline.proposal_builder import scoping_answers


def execute():
	proposal = frappe.qb.DocType("TBO Proposal")
	rows = (
		frappe.qb.from_(proposal)
		.select(proposal.name, proposal.scoping, proposal.solution_type)
		.run(as_dict=True)
	)
	for row in rows:
		if not row.scoping:
			continue
		answers = scoping_answers(row.scoping, row.solution_type)
		if frappe.parse_json(row.scoping) != answers:
			frappe.db.set_value(
				"TBO Proposal", row.name, "scoping", frappe.as_json(answers), update_modified=False
			)
