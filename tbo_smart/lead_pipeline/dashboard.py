"""The pipeline dashboard (docs/lead-pipeline.md §11): the Pipeline page's figures and the cards
on the CRM dashboard, through the fork's `crm_dashboard_providers` / `crm_dashboard_layout`
hooks.

Everything is counted for a set of lead owners: a salesperson's own leads, a team lead's sales
team, everyone for a Sales Manager outside the hierarchy or a System Manager (the mobile API's
CRM scope rule, `capabilities.crm_scopes`). Filters: a date range, a BDE within that scope, a
lead source and a lead status. Each figure is one grouped query.
"""

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Count, IfNull, Sum
from frappe.utils import add_days, cint, flt, get_first_day, getdate, now_datetime, today

from tbo_smart.api.mobile.capabilities import get_capabilities, scope_users
from tbo_smart.api.mobile.utils import get_full_names, percent
from tbo_smart.lead_pipeline import pipeline_enabled, setting
from tbo_smart.lead_pipeline.meetings import CANCELLED, MOM_PENDING, MOM_SUBMITTED, UPCOMING
from tbo_smart.lead_pipeline.quotations import (
	ACCEPTED,
	APPROVED,
	PENDING,
	REJECTED,
	SENT,
	VIEWED,
	WITH_CLIENT,
)
from tbo_smart.lead_pipeline.statuses import (
	LOST_STATUSES,
	OPEN_STATUSES,
	POSITIVE,
	STATUS_NAMES,
	WON_STATUSES,
)
from tbo_smart.smart_leads import CLOSED_TASK_STATUSES

MAX_RANGE_DAYS = 731
LIST_SIZE = 10
CLIENT_WAITING = (SENT, VIEWED)


def dashboard_enabled() -> bool:
	"""TBO Smart Settings → Pipeline dashboard (needs the lead pipeline on)."""
	return pipeline_enabled() and bool(cint(setting("enable_pipeline_dashboard")))


def require_dashboard() -> None:
	if not dashboard_enabled():
		frappe.throw(_("The pipeline dashboard is not switched on."), frappe.ValidationError)


def scope_owners() -> list[str] | None:
	"""Lead owners the session user may count: None means everyone."""
	capabilities = get_capabilities()
	if not capabilities.crm:
		frappe.throw(_("Only CRM users can see the pipeline dashboard."), frappe.PermissionError)
	return scope_users(capabilities.crm_scopes[-1])


class PipelineDashboard:
	"""The figures for one set of filters. `owners`: the lead owners counted (None: all)."""

	def __init__(self, from_date=None, to_date=None, owners=None, source=None, status=None):
		self.from_date = getdate(from_date or get_first_day(today()))
		self.to_date = getdate(to_date or today())
		if self.to_date < self.from_date:
			frappe.throw(_("To date must be on or after from date."))
		if (self.to_date - self.from_date).days > MAX_RANGE_DAYS:
			frappe.throw(_("Choose a range of at most two years."))
		if status and status not in STATUS_NAMES and not frappe.db.exists("CRM Lead Status", status):
			frappe.throw(_("{0} is not a lead status.").format(status))
		self.end = add_days(self.to_date, 1)
		self.owners = owners
		self.source = source
		self.status = status
		self.now = now_datetime()
		self.Lead = frappe.qb.DocType("CRM Lead")
		self.Meeting = frappe.qb.DocType("TBO Lead Meeting")
		self.Quotation = frappe.qb.DocType("Quotation")

	# ---------- the whole page ----------

	def build(self) -> dict:
		won, lost = self.closed("Won"), self.closed("Lost")
		created = self.leads_by_status()
		return {
			"from_date": self.from_date,
			"to_date": self.to_date,
			"leads_by_status": created,
			"leads_created": sum(row["count"] for row in created),
			"meetings_completed": self.meetings_completed(),
			"upcoming_meetings": self.upcoming_meetings(),
			"mom_pending": self.mom_pending(),
			"overdue_follow_ups": self.overdue_follow_ups(),
			"high_potential": self.high_potential(),
			"won": won,
			"lost": lost,
			"lost_reasons": self.lost_reasons(),
			"win_rate": percent(won, won + lost),
			"conversion_rate": percent(self.cohort_won(), sum(row["count"] for row in created)),
			"by_bde": self.by_bde(),
			"quotations": self.quotations(),
		}

	# ---------- filters ----------

	def lead_criterion(self, Lead=None):
		Lead = Lead or self.Lead
		criterion = Lead.name.isnotnull()
		if self.owners is not None:
			criterion &= Lead.lead_owner.isin(self.owners or [""])
		if self.source:
			criterion &= Lead.source == self.source
		if self.status:
			criterion &= Lead.status == self.status
		return criterion

	def in_range(self, field):
		return (field >= self.from_date) & (field < self.end)

	# ---------- leads ----------

	def leads_by_status(self) -> list[dict]:
		"""Leads created in the range by their status now, in pipeline order (empty ones too)."""
		Lead = self.Lead
		counts = dict(
			(
				frappe.qb.from_(Lead)
				.select(Lead.status, Count(Lead.name))
				.where(self.lead_criterion() & self.in_range(Lead.creation))
				.groupby(Lead.status)
			).run()
		)
		order = list(STATUS_NAMES) + sorted(set(counts) - set(STATUS_NAMES))
		return [{"status": status, "count": cint(counts.get(status))} for status in order]

	def closed_rows(self, kind: str):
		"""(lead, lost_reason) of the leads that entered a Won or Lost status in the range, from
		CRM's Status Change Log (a row is a stretch in one status, from `from_date`)."""
		Log = frappe.qb.DocType("CRM Status Change Log")
		Lead = self.Lead
		statuses = WON_STATUSES if kind == "Won" else LOST_STATUSES
		return (
			frappe.qb.from_(Log)
			.join(Lead)
			.on(Lead.name == Log.parent)
			.select(Lead.name, Lead.lost_reason, Lead.lead_owner)
			.distinct()
			.where(
				(Log.parenttype == "CRM Lead")
				& Log["from"].isin(statuses)
				& self.in_range(Log.from_date)
				& self.lead_criterion()
			)
		).run(as_dict=True)

	def closed(self, kind: str) -> int:
		return len(self.closed_rows(kind))

	def lost_reasons(self) -> list[dict]:
		counts: dict[str, int] = {}
		for row in self.closed_rows("Lost"):
			reason = row.lost_reason or _("Not given")
			counts[reason] = counts.get(reason, 0) + 1
		return [
			{"reason": reason, "count": count}
			for reason, count in sorted(counts.items(), key=lambda r: -r[1])
		]

	def cohort_won(self) -> int:
		"""Leads created in the range that are won now (the conversion rate's numerator)."""
		Lead = self.Lead
		return cint(
			(
				frappe.qb.from_(Lead)
				.select(Count(Lead.name))
				.where(
					self.lead_criterion()
					& self.in_range(Lead.creation)
					& (Lead.status.isin(WON_STATUSES) | (Lead.converted == 1))
				)
			).run()[0][0]
		)

	def high_potential(self) -> dict:
		"""Open leads that are Hot by their smart-leads score or Positive / Interested, best score
		first."""
		Lead = self.Lead
		criterion = (
			self.lead_criterion()
			& Lead.status.isin(OPEN_STATUSES)
			& (Lead.converted == 0)
			& ((Lead.lead_temperature == "Hot") | (Lead.status == POSITIVE))
		)
		count = (frappe.qb.from_(Lead).select(Count(Lead.name)).where(criterion)).run()[0][0]
		rows = (
			frappe.qb.from_(Lead)
			.select(
				Lead.name, Lead.lead_name, Lead.organization, Lead.status, Lead.lead_score, Lead.lead_owner
			)
			.where(criterion)
			.orderby(IfNull(Lead.lead_score, 0), order=frappe.qb.desc)
			.limit(LIST_SIZE)
		).run(as_dict=True)
		return {"count": cint(count), "rows": rows}

	def overdue_follow_ups(self) -> int:
		Task = frappe.qb.DocType("CRM Task")
		Lead = self.Lead
		return cint(
			(
				frappe.qb.from_(Task)
				.join(Lead)
				.on((Task.reference_doctype == "CRM Lead") & (Task.reference_docname == Lead.name))
				.select(Count(Task.name))
				.where(
					Task.status.notin(CLOSED_TASK_STATUSES)
					& (Task.due_date < getdate())
					& Lead.status.isin(OPEN_STATUSES)
					& self.lead_criterion()
				)
			).run()[0][0]
		)

	# ---------- meetings ----------

	def meetings_query(self):
		return frappe.qb.from_(self.Meeting).join(self.Lead).on(self.Lead.name == self.Meeting.lead)

	def meetings_completed(self) -> dict:
		"""Meetings whose MOM was submitted in the range: 1st, 2nd, 3rd and additional."""
		Meeting = self.Meeting
		rows = (
			self.meetings_query()
			.select(Meeting.stage, Count(Meeting.name))
			.where(
				(Meeting.status == MOM_SUBMITTED)
				& self.in_range(Meeting.submitted_on)
				& self.lead_criterion()
			)
			.groupby(Meeting.stage)
		).run()
		result = {"first": 0, "second": 0, "third": 0, "additional": 0}
		keys = {1: "first", 2: "second", 3: "third"}
		for stage, count in rows:
			result[keys.get(cint(stage), "additional")] += cint(count)
		result["total"] = sum(result.values())
		return result

	def meeting_list(self, criterion, order_field, ascending=True) -> dict:
		Meeting, Lead = self.Meeting, self.Lead
		count = (self.meetings_query().select(Count(Meeting.name)).where(criterion)).run()[0][0]
		rows = (
			self.meetings_query()
			.select(
				Meeting.name,
				Meeting.lead,
				Lead.lead_name,
				Lead.organization,
				Meeting.stage_label,
				Meeting.scheduled_at,
				Meeting.meeting_type,
				Meeting.bde,
				Meeting.mom_pending_since,
			)
			.where(criterion)
			.orderby(order_field, order=frappe.qb.asc if ascending else frappe.qb.desc)
			.limit(LIST_SIZE)
		).run(as_dict=True)
		return {"count": cint(count), "rows": rows}

	def upcoming_meetings(self) -> dict:
		Meeting = self.Meeting
		return self.meeting_list(
			Meeting.status.isin(UPCOMING) & (Meeting.scheduled_at >= self.now) & self.lead_criterion(),
			Meeting.scheduled_at,
		)

	def mom_pending(self) -> dict:
		Meeting = self.Meeting
		return self.meeting_list(
			(Meeting.status == MOM_PENDING) & self.lead_criterion(), Meeting.scheduled_at
		)

	# ---------- per BDE ----------

	def by_bde(self) -> list[dict]:
		"""Per lead owner: meetings held (MOM submitted in the range), meetings due in the range
		that have ended and their MOM submission rate, MOM pending now, quotations made and sent
		in the range, and leads won."""
		Meeting, Lead, Quotation = self.Meeting, self.Lead, self.Quotation
		ended = (
			(Meeting.scheduled_at < self.now)
			& self.in_range(Meeting.scheduled_at)
			& (Meeting.status != CANCELLED)
		)
		meetings = (
			self.meetings_query()
			.select(
				Lead.lead_owner,
				Sum(
					Case()
					.when((Meeting.status == MOM_SUBMITTED) & self.in_range(Meeting.submitted_on), 1)
					.else_(0)
				).as_("held"),
				Sum(Case().when(ended, 1).else_(0)).as_("ended"),
				Sum(Case().when(ended & (Meeting.status == MOM_SUBMITTED), 1).else_(0)).as_("ended_with_mom"),
				Sum(Case().when(Meeting.status == MOM_PENDING, 1).else_(0)).as_("mom_pending"),
			)
			.where(self.lead_criterion())
			.groupby(Lead.lead_owner)
		).run(as_dict=True)
		quotes = (
			frappe.qb.from_(Quotation)
			.join(Lead)
			.on(Lead.name == Quotation.tbo_crm_lead)
			.select(
				Lead.lead_owner,
				Sum(Case().when(self.in_range(Quotation.transaction_date), 1).else_(0)).as_("quotations"),
				Sum(Case().when(self.in_range(Quotation.tbo_sent_on), 1).else_(0)).as_("sent"),
			)
			.where((Quotation.docstatus != 2) & self.lead_criterion())
			.groupby(Lead.lead_owner)
		).run(as_dict=True)
		won: dict[str, int] = {}
		for row in self.closed_rows("Won"):
			won[row.lead_owner] = won.get(row.lead_owner, 0) + 1
		rows: dict[str, dict] = {}
		for row in meetings:
			rows.setdefault(row.lead_owner, {}).update(row)
		for row in quotes:
			rows.setdefault(row.lead_owner, {}).update(row)
		for owner, count in won.items():
			rows.setdefault(owner, {})["won"] = count
		names = get_full_names(set(rows))
		result = []
		for owner, row in rows.items():
			if not owner:
				continue
			ended, with_mom = cint(row.get("ended")), cint(row.get("ended_with_mom"))
			result.append(
				{
					"user": owner,
					"full_name": names.get(owner, owner),
					"meetings_held": cint(row.get("held")),
					"mom_pending": cint(row.get("mom_pending")),
					"mom_rate": percent(with_mom, ended),
					"quotations": cint(row.get("quotations")),
					"quotations_sent": cint(row.get("sent")),
					"won": cint(row.get("won")),
				}
			)
		return sorted(result, key=lambda row: (-row["won"], -row["meetings_held"], row["full_name"]))

	# ---------- quotations ----------

	def quotations(self) -> dict:
		"""Lead quotations made in the range, by status, with their value; pending approval and
		waiting for the client now; and the value of the active ones (the latest revision of
		each, approved or with the client and still valid): the revenue potential."""
		Quotation, Lead = self.Quotation, self.Lead
		base = frappe.qb.from_(Quotation).join(Lead).on(Lead.name == Quotation.tbo_crm_lead)
		rows = (
			base.select(
				Quotation.tbo_quote_status,
				Count(Quotation.name).as_("count"),
				Sum(Quotation.base_grand_total).as_("value"),
			)
			.where(
				(Quotation.docstatus != 2) & self.in_range(Quotation.transaction_date) & self.lead_criterion()
			)
			.groupby(Quotation.tbo_quote_status)
		).run(as_dict=True)
		by_status = {row.tbo_quote_status or "Draft": row for row in rows}

		def now_count(statuses):
			return cint(
				(
					base.select(Count(Quotation.name)).where(
						(Quotation.docstatus != 2)
						& Quotation.tbo_quote_status.isin(statuses)
						& self.lead_criterion()
					)
				).run()[0][0]
			)

		active = (
			base.select(Count(Quotation.name), Sum(Quotation.base_grand_total)).where(
				(Quotation.docstatus == 1)
				& (Quotation.tbo_is_latest == 1)
				& Quotation.tbo_quote_status.isin((APPROVED, *WITH_CLIENT))
				& (IfNull(Quotation.valid_till, "2999-12-31") >= getdate())
				& self.lead_criterion()
			)
		).run()[0]
		return {
			"currency": frappe.get_cached_value(
				"Company", frappe.defaults.get_global_default("company"), "default_currency"
			),
			"by_status": [
				{"status": status, "count": cint(row["count"]), "value": flt(row["value"], 2)}
				for status, row in by_status.items()
			],
			"count": sum(cint(row["count"]) for row in by_status.values()),
			"value": flt(sum(flt(row["value"]) for row in by_status.values()), 2),
			"pending_approval": now_count((PENDING,)),
			"awaiting_client": now_count(CLIENT_WAITING),
			"accepted": cint(by_status.get(ACCEPTED, {}).get("count")),
			"rejected": cint(by_status.get(REJECTED, {}).get("count")),
			"active": {"count": cint(active[0]), "value": flt(active[1], 2)},
		}


def build_dashboard(from_date=None, to_date=None, bde=None, source=None, status=None) -> dict:
	"""The Pipeline page for the session user, within their CRM scope."""
	require_dashboard()
	allowed = scope_owners()
	owners = allowed
	if bde:
		if allowed is not None and bde not in allowed:
			frappe.throw(_("{0} is not in your team.").format(bde), frappe.PermissionError)
		owners = [bde]
	result = PipelineDashboard(from_date, to_date, owners, source, status).build()
	# the caller's own scope, whatever the BDE filter narrowed it to
	result["scope"] = "all" if allowed is None else ("own" if allowed == [frappe.session.user] else "team")
	result["people"] = people(allowed)
	return result


def people(owners: list[str] | None) -> list[dict]:
	"""Whom the BDE filter offers: the scope's people, or every lead owner for an all scope."""
	if owners is None:
		owners = frappe.get_all(
			"CRM Lead", filters={"lead_owner": ("is", "set")}, pluck="lead_owner", distinct=True
		)
	names = get_full_names(set(owners))
	return sorted(
		({"user": user, "full_name": names.get(user, user)} for user in owners),
		key=lambda row: row["full_name"],
	)


# ---------- CRM dashboard cards (crm_dashboard_providers / crm_dashboard_layout) ----------


def card_dashboard(from_date, to_date, user) -> PipelineDashboard | None:
	"""The cards count the dashboard's user (a salesperson always gets themselves), when the
	session user may see that person; else their own scope."""
	if not dashboard_enabled():
		return None
	try:
		owners = scope_owners()
	except frappe.PermissionError:
		return None
	if user:
		if owners is not None and user not in owners:
			return None
		owners = [user]
	return PipelineDashboard(from_date, to_date, owners)


def get_tbo_pipeline_meetings_held(from_date=None, to_date=None, user=None) -> dict | None:
	board = card_dashboard(from_date, to_date, user)
	if not board:
		return None
	return {
		"title": _("Meetings held"),
		"tooltip": _("Meetings whose minutes were submitted in the range"),
		"value": board.meetings_completed()["total"],
	}


def get_tbo_pipeline_mom_pending(from_date=None, to_date=None, user=None) -> dict | None:
	board = card_dashboard(from_date, to_date, user)
	if not board:
		return None
	return {
		"title": _("MOM pending"),
		"tooltip": _("Meetings that have ended without their minutes"),
		"value": board.mom_pending()["count"],
		"negativeIsBetter": True,
	}


def get_tbo_pipeline_win_rate(from_date=None, to_date=None, user=None) -> dict | None:
	board = card_dashboard(from_date, to_date, user)
	if not board:
		return None
	won, lost = board.closed("Won"), board.closed("Lost")
	return {
		"title": _("Lead win rate"),
		"tooltip": _("Leads Closed – Won out of those closed in the range"),
		"value": percent(won, won + lost),
		"suffix": "%",
	}


def get_tbo_pipeline_active_quotes(from_date=None, to_date=None, user=None) -> dict | None:
	board = card_dashboard(from_date, to_date, user)
	if not board or not cint(setting("enable_lead_quotations")):
		return None
	quotations = board.quotations()
	return {
		"title": _("Active quotations"),
		"tooltip": _("Value of the latest quotations approved or with clients and still valid"),
		"value": quotations["active"]["value"],
		"prefix": frappe.db.get_value("Currency", quotations["currency"], "symbol")
		or quotations["currency"]
		or "",
	}


CARDS = (
	"tbo_pipeline_meetings_held",
	"tbo_pipeline_mom_pending",
	"tbo_pipeline_win_rate",
	"tbo_pipeline_active_quotes",
)


def get_cards(from_date, to_date, user=None) -> list[dict]:
	"""crm_dashboard_layout hook: the pipeline cards that are switched on."""
	cards = []
	for name in CARDS:
		data = globals()[f"get_{name}"](from_date, to_date, user)
		if data is not None:
			cards.append({"name": name, "type": "number_chart", "w": 4, "h": 3, "data": data})
	return cards
