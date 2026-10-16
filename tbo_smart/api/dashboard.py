"""The sales dashboard's cards (docs/lead-pipeline.md §11, docs/smart-leads.md §4.4,
docs/crm-finance-workflow.md §8).

The finance, smart lead and pipeline cards that Frappe CRM's dashboard showed through hooks,
for whichever screen shows them now (the hub). Each module decides which of its cards are
switched on and which the user may see; a salesperson only ever gets their own figures.
"""

import frappe
from frappe.utils import get_first_day, getdate, today

from tbo_smart.api.mobile.capabilities import get_capabilities
from tbo_smart.finance import dashboard as finance_cards
from tbo_smart.finance.access import is_finance_user
from tbo_smart.lead_pipeline import dashboard as pipeline_cards
from tbo_smart.sales.hierarchy import get_sales_team
from tbo_smart.smart_leads import dashboard as smart_lead_cards


@frappe.whitelist()
def get_cards(
	from_date: str | None = None, to_date: str | None = None, user: str | None = None
) -> list[dict]:
	"""[{name, type, w, h, data}] for the date range (this month by default) and `user` (a sales
	user whose figures to show; a salesperson always gets their own, a team lead their team's)."""
	from_date = getdate(from_date or get_first_day(today()))
	to_date = getdate(to_date or today())
	scope = get_capabilities().crm_scope
	if scope == "own":
		user = frappe.session.user
	elif scope == "team" and not is_finance_user():
		# a team lead sees one of their team, never everyone's
		if not user or user not in get_sales_team(frappe.session.user):
			user = frappe.session.user
	return [
		*finance_cards.get_cards(from_date, to_date, user),
		*smart_lead_cards.get_cards(from_date, to_date, user),
		*pipeline_cards.get_cards(from_date, to_date, user),
	]
