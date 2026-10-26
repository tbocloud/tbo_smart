"""The Daily Sales Performance Report's figures (docs/hub-leads-api.md §2.9).

One salesperson's day as the Sales Manager's template asks for it: the calls, the fresh and the
follow-up leads, the positive leads, the quotations sent, the new leads (with their names), and
the week's pace so far. Counts and the leads behind them; the hub holds the targets and compares.

Who each thing counts for: calls and their leads for the person who took the call; a completed
follow-up for whom it is allocated to; a stage move, a new lead, a quotation and a meeting for the
lead's owner (as the Sales scoreboard, `SalesActivity`). Every figure is read in one grouped query
for everybody in scope, never per lead.
"""

import frappe
from frappe.query_builder.functions import IfNull, Min
from frappe.utils import add_days, cint, flt, getdate

from tbo_smart.lead_pipeline import document_types as kinds
from tbo_smart.lead_pipeline.overview import SalesActivity
from tbo_smart.lead_pipeline.proposals import DOCTYPE as PROPOSAL
from tbo_smart.lead_pipeline.statuses import NEW_LEAD, POSITIVE
from tbo_smart.sales.activities import DONE
from tbo_smart.telephony import ANSWERED
from tbo_smart.telephony.calls import DOCTYPE as CALL

QUOTATION_KIND, PROPOSAL_KIND = "quotation", "proposal"


def week_start(day):
	"""The Monday of the week `day` is in."""
	day = getdate(day)
	return add_days(day, -day.weekday())


class DailyReport(SalesActivity):
	"""The figures for the lead owners `owners` (None: everyone) from `from_date` to `to_date`:
	one day for the day's report, Monday to the day for the week's pace."""

	def __init__(self, from_date=None, to_date=None, owners=None):
		super().__init__(from_date, to_date, owners)
		self.StageLog = frappe.qb.DocType("TBO Stage Change")

	# ---------- the day ----------

	def build_day(self, people: list[str]) -> dict:
		"""{user: the day's figures} for `people`; see `get_daily_report` for what each is."""
		calls = self.call_figures(people)
		moved = self.moved_leads(people)
		done = self.done_leads(people)
		contacted = {
			user: calls.get(user, {}).get("leads", set()) | moved.get(user, set()) | done.get(user, set())
			for user in people
		}
		first_at = self.first_contacts(set().union(*contacted.values()))
		positive = self.positive_leads()
		quotations = self.sent_documents()
		new = self.new_leads()
		follow_ups = self.follow_ups_by_person()
		meetings = {row["user"]: cint(row.get("meetings_ended")) for row in self.by_bde()}
		result = {}
		for user in people:
			fresh = {lead for lead in contacted[user] if self.on_range(first_at.get(lead))}
			items = quotations.get(user, [])
			result[user] = {
				"calls_total": calls.get(user, {}).get("total", 0),
				"calls_connected": calls.get(user, {}).get("connected", 0),
				"fresh_leads": len(fresh),
				"follow_up_leads": len(contacted[user] - fresh),
				"positive_leads": positive.get(user, []),
				"new_quotations": {
					"count": len(items),
					"value": flt(sum(item["value"] for item in items), 2),
					"items": items,
				},
				"meetings_held": meetings.get(user, 0),
				"follow_ups_done": cint(follow_ups.get(user, {}).get("done")),
				"follow_ups_overdue": cint(follow_ups.get(user, {}).get("overdue")),
				"new_leads": new.get(user, []),
			}
		return result

	def on_range(self, when) -> bool:
		return bool(when) and self.from_date <= getdate(when) <= self.to_date

	def call_figures(self, people: list[str]) -> dict:
		"""{agent: {total, connected, leads}}: TBO Calls started in the range, in or out, by the
		person who took them; connected ones are answered."""
		Call = frappe.qb.DocType(CALL)
		figures: dict = {}
		for agent, status, lead in (
			frappe.qb.from_(Call)
			.select(Call.agent, Call.status, Call.lead)
			.where(Call.agent.isin(people or [""]) & self.in_range(Call.started_at))
		).run():
			row = figures.setdefault(agent, {"total": 0, "connected": 0, "leads": set()})
			row["total"] += 1
			row["connected"] += int(status == ANSWERED)
			if lead:
				row["leads"].add(lead)
		return figures

	def moved_leads(self, people: list[str]) -> dict:
		"""{owner: leads} whose stage changed in the range (the first row of a stage log is the lead
		coming in, not a move)."""
		Log, Lead = self.StageLog, self.Lead
		moved: dict = {}
		for owner, lead in (
			frappe.qb.from_(Log)
			.join(Lead)
			.on((Log.parenttype == "Lead") & (Log.parent == Lead.name))
			.select(Lead.lead_owner, Lead.name)
			.distinct()
			.where((Log.idx > 1) & self.in_range(Log.changed_on) & self.lead_criterion())
		).run():
			moved.setdefault(owner, set()).add(lead)
		return {user: leads for user, leads in moved.items() if user in people}

	def done_leads(self, people: list[str]) -> dict:
		"""{person: leads} whose follow-up the person completed in the range."""
		ToDo = frappe.qb.DocType("ToDo")
		done: dict = {}
		for user, lead in (
			frappe.qb.from_(ToDo)
			.select(ToDo.allocated_to, ToDo.reference_name)
			.distinct()
			.where(
				(ToDo.reference_type == "Lead")
				& (ToDo.status == DONE)
				& ToDo.allocated_to.isin(people or [""])
				& self.in_range(ToDo.modified)
			)
		).run():
			done.setdefault(user, set()).add(lead)
		return done

	def first_contacts(self, leads: set) -> dict:
		"""{lead: when it was first contacted}: its first call (anyone's) or its first move out of
		New Lead, whichever came first. A lead with neither isn't in it."""
		if not leads:
			return {}
		Call, Log = frappe.qb.DocType(CALL), self.StageLog
		first: dict = {}
		by_call = (
			frappe.qb.from_(Call)
			.select(Call.lead, Min(Call.started_at))
			.where(Call.lead.isin(list(leads)))
			.groupby(Call.lead)
		)
		by_move = (
			frappe.qb.from_(Log)
			.select(Log.parent, Min(Log.changed_on))
			.where(
				(Log.parenttype == "Lead")
				& Log.parent.isin(list(leads))
				& (Log.previous_stage == NEW_LEAD)
				& (Log.stage != NEW_LEAD)
			)
			.groupby(Log.parent)
		)
		for lead, when in (*by_call.run(), *by_move.run()):
			if when and (lead not in first or when < first[lead]):
				first[lead] = when
		return first

	def positive_leads(self) -> dict:
		"""{owner: [{name, lead_name, company_name}]}: leads that entered Positive / Interested in
		the range, once each."""
		Log, Lead = self.StageLog, self.Lead
		rows = (
			frappe.qb.from_(Log)
			.join(Lead)
			.on((Log.parenttype == "Lead") & (Log.parent == Lead.name))
			.select(Lead.lead_owner, Lead.name, Lead.lead_name, Lead.company_name)
			.distinct()
			.where((Log.stage == POSITIVE) & self.in_range(Log.changed_on) & self.lead_criterion())
			.orderby(Lead.name)
		).run(as_dict=True)
		return self.by_owner_rows(rows)

	def new_leads(self) -> dict:
		"""{owner: [{name, lead_name, company_name}]}: leads created in the range, imports from the
		old CRM (`crm_lead_ref` set) left out."""
		Lead = self.Lead
		rows = (
			frappe.qb.from_(Lead)
			.select(Lead.lead_owner, Lead.name, Lead.lead_name, Lead.company_name)
			.where(self.lead_criterion() & self.in_range(Lead.creation) & self.not_imported())
			.orderby(Lead.creation)
		).run(as_dict=True)
		return self.by_owner_rows(rows)

	@staticmethod
	def by_owner_rows(rows: list) -> dict:
		result: dict = {}
		for row in rows:
			result.setdefault(row.lead_owner, []).append(
				{"name": row.name, "lead_name": row.lead_name, "company_name": row.company_name}
			)
		return result

	def sent_documents(self) -> dict:
		"""{owner: [{kind, name, lead, lead_name, value}]}: what went to a client in the range, the
		hub's "Proposals sent" figure: each quotation sent (every revision; not cancelled) and each
		proposal sent with no quotation behind it (value 0). A proposal with a quotation is that
		quotation."""
		Quotation, Proposal, Lead = self.Quotation, frappe.qb.DocType(PROPOSAL), self.Lead
		sent: dict = {}
		for row in (
			self.quotation_query()
			.select(
				Lead.lead_owner,
				Quotation.name,
				Quotation.tbo_lead,
				Lead.lead_name,
				Quotation.base_grand_total,
				Quotation.tbo_sent_on,
			)
			.where((Quotation.docstatus != 2) & self.in_range(Quotation.tbo_sent_on) & self.lead_criterion())
		).run(as_dict=True):
			sent.setdefault(row.lead_owner, []).append(
				(
					row.tbo_sent_on,
					{
						"kind": QUOTATION_KIND,
						"name": row.name,
						"lead": row.tbo_lead,
						"lead_name": row.lead_name,
						"value": flt(row.base_grand_total, 2),
					},
				)
			)
		for row in (
			frappe.qb.from_(Proposal)
			.join(Lead)
			.on(Lead.name == Proposal.lead)
			.select(Lead.lead_owner, Proposal.name, Proposal.lead, Lead.lead_name, Proposal.sent_on)
			.where(
				(Proposal.document_type == kinds.PROPOSAL)
				& (IfNull(Proposal.quotation, "") == "")
				& self.in_range(Proposal.sent_on)
				& self.lead_criterion()
			)
		).run(as_dict=True):
			sent.setdefault(row.lead_owner, []).append(
				(
					row.sent_on,
					{
						"kind": PROPOSAL_KIND,
						"name": row.name,
						"lead": row.lead,
						"lead_name": row.lead_name,
						"value": 0.0,
					},
				)
			)
		return {
			owner: [item for _when, item in sorted(pairs, key=lambda pair: pair[0])]
			for owner, pairs in sent.items()
		}

	# ---------- the week so far ----------

	def build_week(self, people: list[str]) -> dict:
		"""{user: {leads, meetings, proposals, follow_ups}} over the range (Monday to the day)."""
		new = self.new_leads()
		sent = self.sent_documents()
		meetings = {row["user"]: cint(row.get("meetings_ended")) for row in self.by_bde()}
		follow_ups = self.follow_ups_by_person()
		return {
			user: {
				"leads": len(new.get(user, [])),
				"meetings": meetings.get(user, 0),
				"proposals": len(sent.get(user, [])),
				"follow_ups": cint(follow_ups.get(user, {}).get("done")),
			}
			for user in people
		}
