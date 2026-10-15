"""The daily Call today list per salesperson (docs/smart-leads.md §4.3).

Built live from the same facts as the score, so it is always current: open leads owned by the
person that the session user may read under Frappe CRM's own rules, in this order:

1. follow-ups overdue, oldest first;
2. follow-ups due today;
3. leads with no follow-up and no touch today, best score first.

Leads with a follow-up on a later day are left out (they already have a plan). The list holds
`call_today_size` leads (12 by default, 5-30); `more` counts the ones that didn't fit.
"""

import frappe
from frappe import _
from frappe.utils import get_datetime, getdate

from tbo_smart.api.mobile.capabilities import get_capabilities, get_sales_team
from tbo_smart.api.mobile.utils import get_full_names
from tbo_smart.smart_leads import OPEN_STATUSES, call_today_size, is_sales_head
from tbo_smart.smart_leads.facts import LeadFacts, first_due
from tbo_smart.smart_leads.scoring import conversion_rates, lead_score, status_positions
from tbo_smart.smart_leads.utils import normalise_phone, tel_link, whatsapp_link

OVERDUE, DUE_TODAY, BEST = "overdue", "due_today", "best_score"
GROUP_ORDER = {OVERDUE: 0, DUE_TODAY: 1, BEST: 2}
# leads with no follow-up whose facts are loaded, best stored score first: the live score
# reorders them, and a lead whose score jumped today is re-scored on change anyway
POOL_FACTOR, MIN_POOL = 5, 60


def viewable_people() -> list[str] | None:
	"""Whose list the session user may open: None = anyone (crm_scope all or a sales head),
	else their sales team (themselves at least)."""
	capabilities = get_capabilities()
	if not capabilities.crm:
		frappe.throw(_("Your role has no access to CRM."), frappe.PermissionError)
	if capabilities.crm_scope == "all" or is_sales_head():
		return None
	return get_sales_team(frappe.session.user)


def check_person(user: str | None) -> str:
	user = user or frappe.session.user
	people = viewable_people()
	if people is not None and user not in people:
		frappe.throw(_("You can only open your own or your team's Call today list."), frappe.PermissionError)
	return user


def build_call_today(user: str | None = None, size: int | None = None, today=None) -> dict:
	"""{user, user_name, date, size, more, data: [row]} for `user` (default: me)."""
	user = check_person(user)
	today = getdate(today)
	size = size or call_today_size()
	# cheap first: the person's open leads by last night's score and each one's earliest
	# follow-up; the full facts are loaded only for the ones that can make the list
	names = frappe.get_list(
		"CRM Lead",
		filters={"lead_owner": user, "status": ("in", OPEN_STATUSES), "converted": 0},
		pluck="name",
		order_by="lead_score desc, creation desc",
		limit_page_length=0,
	)
	due_by = first_due(names)
	with_due = [name for name in names if name in due_by and getdate(due_by[name]) <= today]
	unplanned = [name for name in names if name not in due_by]
	facts = LeadFacts(with_due + unplanned[: max(size * POOL_FACTOR, MIN_POOL)], today)
	rates, positions = conversion_rates(), status_positions()
	candidates = []
	touched_today = 0
	for name in facts.names:
		follow_up = facts.next_follow_up(name)
		due = getdate(follow_up.due_date) if follow_up else None
		if due and due > today:
			continue
		if not due and facts.days_since_touch(name) == 0:
			touched_today += 1
			continue
		group = OVERDUE if due and due < today else DUE_TODAY if due else BEST
		score, temperature, reason = lead_score(facts, name, rates, positions)
		candidates.append((group, due, score, name, temperature, reason, follow_up))
	candidates.sort(
		key=lambda c: (
			GROUP_ORDER[c[0]],
			get_datetime(c[6].due_date) if c[6] else get_datetime(today),
			-c[2],
			c[3],
		)
	)
	rows = [
		serialize(facts, group, score, name, temperature, reason, follow_up)
		for group, _due, score, name, temperature, reason, follow_up in candidates[:size]
	]
	return {
		"user": user,
		"user_name": get_full_names([user]).get(user, user),
		"date": str(today),
		"size": size,
		"more": max(len(with_due) + len(unplanned) - touched_today - size, 0),
		"data": rows,
	}


def serialize(
	facts: LeadFacts, group: str, score: int, name: str, temperature: str, reason: str, follow_up
) -> dict:
	lead = facts.lead(name)
	phone = lead.mobile_no or lead.phone
	at, kind = facts.last_touch(name)
	return {
		"id": name,
		"lead_name": lead.lead_name,
		"organization": lead.organization,
		"status": lead.status,
		"source": lead.source,
		"score": score,
		"band": temperature,
		"reason": reason,
		"why": group,
		"phone": phone,
		"phone_e164": f"+{normalise_phone(phone)}" if normalise_phone(phone) else None,
		"tel": tel_link(phone),
		"whatsapp": whatsapp_link(phone),
		"email": lead.email,
		"last_touch": {"at": at, "kind": kind, "days": facts.days_since_touch(name)} if at else None,
		"follow_up": {
			"task": follow_up.name,
			"title": follow_up.title,
			"due": follow_up.due_date,
			"overdue": group == OVERDUE,
		}
		if follow_up
		else None,
	}
