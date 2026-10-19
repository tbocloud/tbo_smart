"""Proposals move to the Universal IT Proposal (docs/proposals.md §2): the v1.0 block types become
their master sections, and every proposal takes the one theme, Ivory."""

import frappe

from tbo_smart.lead_pipeline.proposals import OLD_TYPES, THEMES


def execute():
	for doctype in ("TBO Proposal Section", "TBO Proposal Template Section"):
		table = frappe.qb.DocType(doctype)
		for old, new in OLD_TYPES.items():
			frappe.qb.update(table).set(table.section_type, new).where(table.section_type == old).run()
	proposal = frappe.qb.DocType("TBO Proposal")
	frappe.qb.update(proposal).set(proposal.theme, THEMES[0]).where(proposal.theme != THEMES[0]).run()
