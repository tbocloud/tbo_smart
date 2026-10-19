"""Quotation discount and commission approvals for the CRM and the Quotation form.

Every method acts as the session user. Who may request, approve, forward or override is
decided here from TBO Quotation Approval Settings (the user's level comes from their roles),
never from anything the caller sends.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.lead_pipeline.rate_card import quotation_band, quotation_bands
from tbo_smart.quotation_control import (
	COMMISSION_FIELD,
	covers,
	effective_limit,
	get_covering,
	get_list_value,
	get_percent,
	new_adjustment,
)
from tbo_smart.sales import LEAD_LINK
from tbo_smart.tbo_smart.doctype.tbo_quotation_adjustment.tbo_quotation_adjustment import (
	GRANTED_STATUSES,
	OPEN_STATUSES,
)
from tbo_smart.tbo_smart.doctype.tbo_quotation_approval_settings.tbo_quotation_approval_settings import (
	format_limit,
	get_settings,
)

PENDING_LIMIT = 200
STATUS_BATCH_LIMIT = 100
REQUEST_SCOPES = ("mine", "all")
REDACTED_LATEST_FIELDS = (
	"kind",
	"status",
	"voided",
	"within_limit",
	"requested_percent",
	"approved_percent",
	"current_level",
)
LIST_FIELDS = [
	"name",
	"quotation",
	"customer",
	"deal",
	"kind",
	"status",
	"voided",
	"within_limit",
	"previous_percent",
	"requested_percent",
	"approved_percent",
	"quotation_value",
	"requested_by",
	"approved_by",
	"current_level",
	"current_rank",
	"reason",
	"remarks",
	"creation",
	"modified",
]


@frappe.whitelist()
def get_adjustment_limits() -> dict:
	"""The session user's level and limits. A limit of None means no limit; 0 means not set,
	so every non-zero adjustment needs approval."""
	settings = get_settings()
	level = settings.get_user_level()
	kinds = settings.controlled_kinds()
	levels = settings.sorted_levels()
	return {
		"enabled": bool(kinds),
		"applies_to": settings.applies_to,
		"kinds": kinds,
		"level": serialize_level(level),
		"limits": {kind: settings.get_limit(level, kind) for kind in kinds},
		"limit_labels": {kind: format_limit(settings.get_limit(level, kind)) for kind in kinds},
		# a request can wait at my level only when some level ranks below it
		"is_approver": bool(level and levels and cint(levels[0].rank) < cint(level.rank)),
		"can_view_all": can_view_all(level),
		"can_override": bool(level and cint(level.unlimited)),
		"can_forward": bool(level and settings.get_next_level(level.rank)),
		"open_requests": frappe.db.count(
			"TBO Quotation Adjustment",
			{
				"requested_by": frappe.session.user,
				"status": ("in", OPEN_STATUSES),
				"voided": 0,
			},
		),
	}


@frappe.whitelist()
def get_quotation_adjustment_status(quotation: str) -> dict:
	"""For each limited kind on a quotation: its percentage, my limit, whether it is covered and
	the newest adjustment (request or decision)."""
	frappe.get_doc("Quotation", quotation).check_permission("read")
	return collect_statuses([quotation])[quotation]


@frappe.whitelist()
def get_quotations_adjustment_status(quotations: list[str] | str) -> dict:
	"""get_quotation_adjustment_status for several quotations at once (a CRM deal's Quotations
	tab), keyed by quotation. Quotations I cannot read, or that do not exist, are left out."""
	names = quotations
	if isinstance(quotations, str):
		try:
			names = frappe.parse_json(quotations)
		except ValueError:
			names = None
	if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
		frappe.throw(_("quotations must be a list of Quotation names."))
	if len(names) > STATUS_BATCH_LIMIT:
		frappe.throw(_("Ask for at most {0} quotations at a time.").format(STATUS_BATCH_LIMIT))
	if not names:
		return {}
	# get_list applies Quotation permissions, so unreadable names drop out here
	readable = frappe.get_list(
		"Quotation", filters={"name": ("in", list(set(names)))}, pluck="name", limit_page_length=0
	)
	return collect_statuses(readable)


def collect_statuses(names: list[str]) -> dict:
	"""Adjustment status for quotations the caller may read, in a fixed number of queries: the
	quotations, their items, and their adjustments."""
	settings = get_settings()
	level = settings.get_user_level()
	kinds = settings.controlled_kinds()
	docs = load_quotations(names)
	adjustments = (
		frappe.get_all(
			"TBO Quotation Adjustment",
			filters={"quotation": ("in", list(docs)), "kind": ("in", kinds)},
			fields=LIST_FIELDS,
			order_by="creation desc",
		)
		if kinds and docs
		else []
	)
	latest = {}
	granted = {}
	for row in adjustments:
		key = (row.quotation, row.kind)
		latest.setdefault(key, row)
		if row.status in GRANTED_STATUSES and not cint(row.voided):
			granted.setdefault(key, []).append(row)
	readable = set(
		frappe.get_list(
			"TBO Quotation Adjustment",
			filters={"name": ("in", [row.name for row in latest.values()])},
			pluck="name",
			limit_page_length=0,
		)
		if latest
		else []
	)

	bands = dict(
		zip(
			docs,
			quotation_bands(list(docs.values())) if "Discount" in kinds else [None] * len(docs),
			strict=True,
		)
	)
	statuses = {}
	for name, doc in docs.items():
		value = get_list_value(doc)
		rows = []
		for kind in kinds:
			percent = get_percent(doc, kind)
			limit = effective_limit(settings, level, kind, bands[name])
			newest = latest.get((name, kind))
			rows.append(
				{
					"kind": kind,
					"percent": percent,
					"limit": limit,
					"limit_label": format_limit(limit),
					"within_limit": settings.is_within(limit, percent),
					"approved": percent <= 0
					or any(covers(row, percent, value) for row in granted.get((name, kind), [])),
					"latest": serialize_latest(newest, newest.name in readable) if newest else None,
				}
			)
		statuses[name] = {
			"quotation": name,
			"docstatus": cint(doc.docstatus),
			"level": serialize_level(level),
			"kinds": rows,
		}
	return statuses


def load_quotations(names: list[str]) -> dict:
	"""Quotations with just what the discount and commission maths need, items included."""
	if not names:
		return {}
	docs = {
		row.name: row
		for row in frappe.get_all(
			"Quotation",
			filters={"name": ("in", names)},
			# the party and lead, for a lead segment's discount band
			fields=[
				"name",
				"docstatus",
				"net_total",
				COMMISSION_FIELD,
				"quotation_to",
				"party_name",
				LEAD_LINK,
				"opportunity",
			],
		)
	}
	for doc in docs.values():
		doc["items"] = []
	for item in frappe.get_all(
		"Quotation Item",
		filters={"parent": ("in", list(docs)), "parenttype": "Quotation"},
		fields=["parent", "qty", "price_list_rate", "rate_with_margin", "rate", "is_alternative"],
		order_by="idx",
	):
		docs[item.parent]["items"].append(item)
	return docs


def serialize_latest(row, readable: bool) -> dict:
	"""The newest adjustment for a quotation and kind. Without read access to it (a BDE looking
	at another BDE's request) only its state is shared, not who asked, why, or its name."""
	data = serialize_adjustment(row)
	if readable:
		return data
	return {field: data[field] for field in REDACTED_LATEST_FIELDS} | {"name": None}


@frappe.whitelist(methods=["POST"])
def request_adjustment(quotation: str, kind: str, percent: float | str, reason: str) -> dict:
	"""Ask the next level up to approve `percent` for `kind` on a draft quotation I can edit."""
	settings = get_settings()
	if kind not in settings.controlled_kinds():
		frappe.throw(_("{0} adjustments are not limited, so no approval is needed.").format(_(kind)))
	doc = frappe.get_doc("Quotation", quotation)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(_("Only a draft quotation can get an adjustment approved."))
	percent = flt(percent, 2)
	if not 0 < percent <= 100:
		frappe.throw(_("Percent must be more than 0 and at most 100."))
	if not (reason or "").strip():
		frappe.throw(_("Give a reason for the request."))

	level = settings.get_user_level()
	if not level:
		frappe.throw(
			_("You have no quotation approval level. Ask HR to give you your designation's role."),
			frappe.PermissionError,
		)
	limit = effective_limit(settings, level, kind, quotation_band(doc))
	if settings.is_within(limit, percent):
		frappe.throw(
			_("{0}% is within your limit ({1}); set it on the quotation.").format(
				percent, format_limit(limit)
			)
		)
	open_request = frappe.db.get_value(
		"TBO Quotation Adjustment",
		{"quotation": doc.name, "kind": kind, "status": ("in", OPEN_STATUSES), "voided": 0},
		"name",
	)
	if open_request:
		frappe.throw(_("{0} is already waiting for a decision on this quotation.").format(open_request))
	next_level = settings.get_next_level(level.rank)
	if not next_level:
		frappe.throw(_("There is no higher level to approve this."))

	adjustment = new_adjustment(doc, kind, percent, get_list_value(doc))
	adjustment.update(
		{
			"status": "Pending",
			"reason": reason.strip(),
			"current_level": next_level.level,
			"current_rank": cint(next_level.rank),
		}
	)
	adjustment.add_step("Requested", level.level, percent, reason.strip())
	adjustment.insert(ignore_permissions=True)
	adjustment.notify_level(next_level)
	return serialize_adjustment(adjustment)


@frappe.whitelist(methods=["POST"])
def decide_adjustment(
	name: str, action: str, percent: float | str | None = None, remarks: str | None = None
) -> dict:
	"""approve (optionally a lower percent), reject (with remarks), forward to the next level, or
	override (no-limit level only, with a reason). The requester can never decide their own."""
	adjustment = frappe.get_doc("TBO Quotation Adjustment", name)
	settings = get_settings()
	level = settings.get_user_level()
	adjustment.check_can_decide(level, action)
	remarks = (remarks or "").strip() or None
	percent = None if percent in (None, "") else flt(percent, 2)
	value = get_list_value(frappe.get_doc("Quotation", adjustment.quotation))

	if action == "approve":
		adjustment.approve(level, percent, remarks, value)
	elif action == "reject":
		adjustment.reject(level, remarks)
	elif action == "forward":
		adjustment.forward(level, remarks)
	else:
		adjustment.override(level, percent, remarks, value)
	adjustment.save(ignore_permissions=True)

	if action == "forward":
		adjustment.notify_level(settings.get_level(adjustment.current_level))
	else:
		adjustment.notify_requester()
	return serialize_adjustment(adjustment)


@frappe.whitelist()
def get_pending_approvals() -> list[dict]:
	"""Open requests I may decide: waiting at my level, or below it when my level approves
	directly. My own requests are left out."""
	level = get_settings().get_user_level()
	if not level:
		return []
	Adjustment = frappe.qb.DocType("TBO Quotation Adjustment")
	waiting = Adjustment.current_level == level.level
	if cint(level.approves_directly) or cint(level.unlimited):
		waiting = waiting | (Adjustment.current_rank < cint(level.rank))
	rows = (
		frappe.qb.from_(Adjustment)
		.select(*[Adjustment[field] for field in LIST_FIELDS])
		.where(
			Adjustment.status.isin(OPEN_STATUSES)
			& (Adjustment.voided == 0)
			& (Adjustment.requested_by != frappe.session.user)
			& waiting
		)
		.orderby(Adjustment.creation)
		.limit(PENDING_LIMIT)
		.run(as_dict=True)
	)
	names = get_full_names({row.requested_by for row in rows})
	currencies = get_currencies({row.quotation for row in rows})
	settings = get_settings()
	pending = []
	for row in rows:
		item = serialize_adjustment(row, names)
		item["currency"] = currencies.get(row.quotation)
		item["can_approve_in_full"] = settings.is_within(
			settings.get_limit(level, row.kind), row.requested_percent
		)
		pending.append(item)
	return pending


@frappe.whitelist()
def get_adjustment_requests(scope: str = "mine") -> list[dict]:
	"""Approval requests, newest first: `mine` are the ones I made, `all` is every request and
	needs a level that approves directly (Director, CEO). Within-limit Applied rows are left
	out, since nobody had to approve them."""
	if scope not in REQUEST_SCOPES:
		frappe.throw(_("Scope must be one of {0}.").format(", ".join(REQUEST_SCOPES)))
	Adjustment = frappe.qb.DocType("TBO Quotation Adjustment")
	condition = Adjustment.within_limit == 0
	if scope == "mine":
		condition &= Adjustment.requested_by == frappe.session.user
	elif not can_view_all(get_settings().get_user_level()):
		frappe.throw(_("Only a level that approves directly can see every request."), frappe.PermissionError)
	rows = (
		frappe.qb.from_(Adjustment)
		.select(*[Adjustment[field] for field in LIST_FIELDS])
		.where(condition)
		.orderby(Adjustment.creation, order=frappe.qb.desc)
		.limit(PENDING_LIMIT)
		.run(as_dict=True)
	)
	names = get_full_names({row.requested_by for row in rows})
	currencies = get_currencies({row.quotation for row in rows})
	return [{**serialize_adjustment(row, names), "currency": currencies.get(row.quotation)} for row in rows]


@frappe.whitelist()
def get_adjustment_trail(name: str) -> dict:
	"""One adjustment with its append-only steps, oldest first, for anyone who can read it."""
	adjustment = frappe.get_doc("TBO Quotation Adjustment", name)
	adjustment.check_permission("read")
	names = get_full_names({adjustment.requested_by, *(step.by_user for step in adjustment.steps)})
	data = serialize_adjustment(adjustment, names)
	data["steps"] = [
		{
			"action": step.action,
			"by_user": step.by_user,
			"by_name": names.get(step.by_user, step.by_user),
			"level": step.level,
			"percent": flt(step.percent, 2),
			"at": step.at,
			"remarks": step.remarks,
		}
		for step in adjustment.steps
	]
	return data


def get_currencies(quotations: set[str]) -> dict[str, str]:
	"""Each quotation's currency, so a list can show its value with the right symbol."""
	if not quotations:
		return {}
	Quotation = frappe.qb.DocType("Quotation")
	rows = (
		frappe.qb.from_(Quotation)
		.select(Quotation.name, Quotation.currency)
		.where(Quotation.name.isin(list(quotations)))
		.run()
	)
	return dict(rows)


def can_view_all(level) -> bool:
	return bool(level and (cint(level.approves_directly) or cint(level.unlimited)))


def serialize_level(level) -> dict | None:
	if not level:
		return None
	return {
		"level": level.level,
		"role": level.role,
		"rank": cint(level.rank),
		"approves_directly": bool(cint(level.approves_directly)),
		"unlimited": bool(cint(level.unlimited)),
	}


def serialize_adjustment(row, names: dict | None = None) -> dict:
	data = {field: row.get(field) for field in LIST_FIELDS}
	for field in ("previous_percent", "requested_percent", "approved_percent", "quotation_value"):
		data[field] = flt(data[field], 2)
	data["voided"] = bool(cint(data["voided"]))
	data["within_limit"] = bool(cint(data["within_limit"]))
	data["current_rank"] = cint(data["current_rank"])
	if names is not None:
		data["requested_by_name"] = names.get(row.requested_by, row.requested_by)
	return data
