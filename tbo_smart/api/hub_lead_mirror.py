"""What the hub needs to keep a local copy of the leads (docs/hub-leads-api.md §2.32).

Only the hub's integration user may call these (the role in TBO Smart Settings → Hub
integration role); they read, never write, and each answers for every lead whatever the
caller's own scope, because the copy is filtered per person on the hub afterwards with
`get_mirror_scopes`. A lead's facts that decide who sees it (owner, assignees, department)
travel with it; the people's scopes travel apart, so a change of team or of department head
reaches every lead at once without touching it.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from tbo_smart.api.hub_leads import (
	ROW_FIELDS,
	hub_user_problem,
	integration_role,
	lead_payload,
	serialize_rows,
)
from tbo_smart.sales import LEAD
from tbo_smart.sales.change_log import changes_since
from tbo_smart.sales.departments import headed_departments
from tbo_smart.sales.hierarchy import get_sales_team
from tbo_smart.sales.permissions import sees_everything

MAX_BULK = 100
MAX_CHANGES = 1000
MAX_INDEX = 2000
# what a payload says about the person who asked; the integration user's answer would mislead
PERSON_FLAGS = ("can_write", "can_call", "can_remove_numbers", "can_merge")


def require_integration_user() -> None:
	if integration_role() not in frappe.get_roles():
		frappe.throw(_("Only the hub's integration user may read the lead feed."), frappe.PermissionError)


@frappe.whitelist()
def get_lead_changes(since: str | None = None, limit: int = 500) -> dict:
	"""{changes: [{lead, at, deleted}], next_since, more, now}: each lead changed after `since`
	once, with the time of its latest change, oldest first (at most `limit`, up to 1000).
	Ask again from `next_since` while `more`."""
	require_integration_user()
	return changes_since(since, min(max(cint(limit) or 500, 1), MAX_CHANGES))


@frappe.whitelist()
def get_lead_index(after: str | None = None, limit: int = 1000) -> dict:
	"""{leads: [{name, modified}], next_after, more, total, now}: every lead by name, a page at
	a time after `after` (the last name of the page before), with its `modified`; `total` (all
	leads) on the first page. The hub's backfill and nightly comparison read it."""
	require_integration_user()
	limit = min(max(cint(limit) or 1000, 1), MAX_INDEX)
	Lead = frappe.qb.DocType(LEAD)
	query = frappe.qb.from_(Lead).select(Lead.name, Lead.modified).orderby(Lead.name).limit(limit + 1)
	if after:
		query = query.where(Lead.name > after)
	rows = query.run(as_dict=True)
	more = len(rows) > limit
	rows = rows[:limit]
	return {
		"leads": rows,
		"next_after": rows[-1].name if rows else after,
		"more": more,
		"total": frappe.db.count(LEAD) if not after else None,
		"now": now_datetime(),
	}


@frappe.whitelist()
def get_leads_bulk(names: list | str) -> dict:
	"""{leads, missing, errors}: for up to 100 lead names, each lead's list row (`row`), its
	`get_lead` answer (`payload`, without the flags that are about the person asking) and what
	decides who sees it: `owner`, `assignees` (open and closed follow-up assignments) and
	`department`. `missing` are names that are not leads (deleted or merged); `errors` maps a
	lead that could not be read to why."""
	require_integration_user()
	names = list(dict.fromkeys(frappe.parse_json(names) if isinstance(names, str) else names or []))
	if len(names) > MAX_BULK:
		frappe.throw(_("Ask for at most {0} leads at a time.").format(MAX_BULK))
	rows = {
		row["name"]: row
		for row in serialize_rows(frappe.get_all(LEAD, filters={"name": ("in", names)}, fields=ROW_FIELDS))
	}
	assignees = assignees_of(list(rows))
	leads, errors = [], {}
	for name in names:
		if name not in rows:
			continue
		try:
			payload = lead_payload(name)
		except Exception as error:
			errors[name] = str(error)[:200]
			continue
		for flag in PERSON_FLAGS:
			payload["lead"].pop(flag, None)
		payload.pop("allowed_stages", None)
		row = rows[name]
		leads.append(
			{
				"name": name,
				"row": row,
				"payload": payload,
				"owner": row["lead_owner"],
				"assignees": assignees.get(name, []),
				"department": row["tbo_department"],
				"modified": row["modified"],
			}
		)
	return {"leads": leads, "missing": [name for name in names if name not in rows], "errors": errors}


def assignees_of(names: list[str]) -> dict[str, list[str]]:
	"""{lead: the users it is assigned to}: any ToDo not cancelled, as the ERP's rule counts them."""
	if not names:
		return {}
	ToDo = frappe.qb.DocType("ToDo")
	found: dict[str, set] = {}
	for lead, user in (
		frappe.qb.from_(ToDo)
		.select(ToDo.reference_name, ToDo.allocated_to)
		.where(
			(ToDo.reference_type == LEAD)
			& (ToDo.status != "Cancelled")
			& ToDo.allocated_to.isnotnull()
			& ToDo.reference_name.isin(names)
		)
	).run():
		found.setdefault(lead, set()).add(user)
	return {lead: sorted(users) for lead, users in found.items()}


@frappe.whitelist()
def get_mirror_scopes() -> dict:
	"""{scopes: {user: {all, team, departments}}, now}: for every person the hub may name
	(enabled sales users): `all` when they see every lead; else the users whose leads they see
	(themselves and their team in the sales hierarchy) and the departments they head. A lead is
	theirs when `all`, or its owner or an assignee is in `team`, or its department is one of
	`departments`: exactly `tbo_smart.sales.permissions`."""
	require_integration_user()
	scopes = {}
	for user in frappe.get_all(
		"User", filters={"enabled": 1, "user_type": "System User"}, pluck="name", order_by="name"
	):
		if user in ("Administrator", "Guest") or hub_user_problem(user):
			continue
		everything = sees_everything(user, LEAD, "read")
		scopes[user] = {
			"all": everything,
			"team": [] if everything else get_sales_team(user),
			"departments": [] if everything else sorted(headed_departments(user).run(pluck=True)),
		}
	return {"scopes": scopes, "now": now_datetime()}
