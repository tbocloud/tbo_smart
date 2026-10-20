"""Switch "The proposal is the quotation" on for existing sites (a Single's default only reaches a
new site): the proposal's own ERPNext Quotation is the final price, and the chain goes Proposal →
Project Agreement (docs/client-documents.md §1)."""

import frappe


def execute():
	frappe.db.set_single_value("TBO Smart Settings", "proposal_is_quotation", 1)
