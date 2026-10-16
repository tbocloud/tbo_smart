# Copyright (c) 2026, sammish and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from tbo_smart.lead_pipeline.meetings import LeadMeeting


class TBOLeadMeeting(LeadMeeting, Document):
	"""A meeting with a lead and its minutes (docs/lead-pipeline.md §5). The logic lives in
	tbo_smart.lead_pipeline.meetings.LeadMeeting."""
