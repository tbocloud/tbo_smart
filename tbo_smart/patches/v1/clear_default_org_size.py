"""Leads made before the organisation size could be empty all hold ERPNext's forced first band
("1-10"); it is cleared while no lead holds another band (docs/hub-leads-api.md §2.11)."""

from tbo_smart.lead_pipeline import lead_category


def execute():
	lead_category.clear_default_org_size()
