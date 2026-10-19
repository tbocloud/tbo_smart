"""Stage history and the lead's stage rules (docs/erpnext-lead-cutover.md §2.1).

Every stage a lead (`tbo_stage`) or a deal (`sales_stage`) enters is kept in its `tbo_stage_log`
table (TBO Stage Change: the stage, the one before, when and by whom), as Frappe CRM's Status
Change Log did. The first row is the record's own insert. Scores, dashboards and the app's stage
history read it.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

from tbo_smart.lead_pipeline.statuses import CLOSED_LOST, NEW_LEAD, STATUS_NAMES, status_type
from tbo_smart.sales import DEAL, LEAD

LOG_FIELD = "tbo_stage_log"
STAGE_FIELD = {LEAD: "tbo_stage", DEAL: "sales_stage"}
OTHER_REASON = "Other"


def record_change(doc) -> None:
	"""Append the stage the record enters to its log (on insert, and when the stage changes)."""
	field = STAGE_FIELD[doc.doctype]
	stage = doc.get(field)
	if not stage or not doc.meta.has_field(LOG_FIELD):
		return
	before = None if doc.is_new() else doc.get_doc_before_save()
	previous = before.get(field) if before else None
	if not doc.is_new() and stage == previous:
		return
	doc.append(
		LOG_FIELD,
		{
			"stage": stage,
			"previous_stage": previous,
			"changed_on": now_datetime(),
			"changed_by": frappe.session.user,
		},
	)


class LeadStage:
	"""doc_events: Lead validate."""

	def __init__(self, doc):
		self.doc = doc

	def validate(self):
		self.set_default()
		self.require_lost_reason()
		record_change(self.doc)

	def set_default(self):
		if not self.doc.get("tbo_stage"):
			self.doc.tbo_stage = NEW_LEAD

	def require_lost_reason(self):
		"""A lost lead says why, as Frappe CRM asked: a lost reason, and notes for "Other"."""
		if self.doc.tbo_stage != CLOSED_LOST:
			return
		if not self.doc.get("tbo_lost_reason"):
			frappe.throw(_("Give a reason for losing the lead."), title=_("Lost reason"))
		if self.doc.tbo_lost_reason == OTHER_REASON and not (self.doc.get("tbo_lost_notes") or "").strip():
			frappe.throw(_("Describe why the lead was lost."), title=_("Lost reason"))


def validate_lead(doc, method=None):
	LeadStage(doc).validate()


def set_lead_stage(name: str, stage: str, lost_reason: str | None = None):
	"""Save a lead's new stage under the session user's permissions; a Lost-type stage (Closed –
	Lost) needs a reason (`set_lost_reason`). Returns the lead."""
	if stage not in STATUS_NAMES:
		frappe.throw(_("{0} is not a lead stage.").format(stage))
	lead = frappe.get_doc(LEAD, name)
	lead.check_permission("write")
	lead.tbo_stage = stage
	if status_type(stage) == "Lost":
		set_lost_reason(lead, lost_reason, _("Give a reason for losing the lead."))
	system_save(lead)
	return lead


def system_save(lead, ignore_permissions: bool = False) -> None:
	"""Save (or insert) a lead for a change the system makes, not a person editing its fields: a
	stage move, an owner change, a status the pipeline sets, a lead made from a call. Only the
	fields it set change, so a mandatory field left empty on an old or imported lead (e.g. Source
	made required on the site) mustn't block it. A person editing the lead's own fields
	(hub_leads.update_lead) goes through Lead.save as usual and is still asked for them."""
	lead.flags.ignore_mandatory = True
	if lead.is_new():
		lead.insert(ignore_permissions=ignore_permissions)
	else:
		lead.save(ignore_permissions=ignore_permissions)


def set_lost_reason(doc, lost_reason: str | None, missing: str) -> None:
	"""A reason that is an Opportunity Lost Reason is used as it is; free text is saved as
	"Other" with the text as the notes. On a Lead it fills `tbo_lost_reason` / `tbo_lost_notes`,
	on an Opportunity its `lost_reasons` and `order_lost_reason`."""
	reason = (lost_reason or "").strip()
	if not reason:
		frappe.throw(missing)
	known = frappe.db.exists("Opportunity Lost Reason", reason)
	name, notes = (reason, None) if known else (OTHER_REASON, reason)
	if doc.doctype == LEAD:
		doc.tbo_lost_reason, doc.tbo_lost_notes = name, notes
	else:
		doc.set("lost_reasons", [{"lost_reason": name}])
		doc.order_lost_reason = notes


def lead_lost_reason(lead) -> str | None:
	"""A lead's reason as people read it: the notes when there are any, else the reason."""
	return lead.get("tbo_lost_notes") or lead.get("tbo_lost_reason")
