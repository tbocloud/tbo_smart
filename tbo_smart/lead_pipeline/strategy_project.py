"""What the hub makes a Content / Digital Marketing lead's project from: the accepted strategy
proposal (docs/lead-pipeline.md §13). The chain of that lead type ends at the proposal, so the
project follows it directly, the same way an ERP lead's follows the implementation plan
(implementation_plan.project_plan)."""

import frappe
from frappe.utils import add_days, add_months, getdate

from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline.implementation_plan import require_approved
from tbo_smart.lead_pipeline.proposals import client_name

MONTHS = 6


def section_body(doc, kind: str) -> str | None:
	"""The text of an enabled section of the proposal, as the client accepted it."""
	row = next((row for row in doc.sections if row.section_type == kind and row.enabled), None)
	return (row.body or None) if row else None


def project_brief(doc) -> dict:
	"""The accepted strategy proposal as the hub makes its project from it: the client, the lead's
	type and department, the six months (the proposal's start date, else the day the client
	accepted, and six months from it) and the roadmap and deliverables the client accepted."""
	require_approved(doc)
	lead = frappe.db.get_value(
		"Lead", doc.lead, ["lead_name", "tbo_lead_type", "tbo_department"], as_dict=True
	)
	start = getdate(doc.start_date or doc.decided_on)
	end = add_days(add_months(start, MONTHS), -1)
	return {
		"proposal": doc.name,
		"lead": doc.lead,
		"lead_name": lead.lead_name,
		"client": client_name(doc),
		"customer": frappe.db.get_value("Customer", {"lead_name": doc.lead}, "name"),
		"tbo_lead_type": lead.tbo_lead_type,
		"tbo_department": lead.tbo_department,
		"project_name": f"{client_name(doc)} - {doc.solution_type or kinds.CONTENT_LEAD}"[:140],
		"solution_type": doc.solution_type,
		"start": str(start),
		"end": str(end),
		"months": [
			{
				"month": month + 1,
				"start": str(add_months(start, month)),
				"end": str(add_days(add_months(start, month + 1), -1)),
			}
			for month in range(MONTHS)
		],
		"roadmap": section_body(doc, "Implementation Plan"),
		"monthly_deliverables": section_body(doc, "Deliverables"),
		"hub_project": doc.hub_project,
	}
