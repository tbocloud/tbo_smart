"""ERPNext v16 moved a document's source and campaign to Frappe's UTM fields (`source` → `utm_source`,
`campaign_name`/`campaign` → `utm_campaign`). Its patch `v15_0.migrate_to_utm_analytics` moves the Lead
Sources and Campaigns themselves but leaves each document's value in the old column, so every lead would
lose its source. Copy the values across where the new field is still empty. Does nothing on v15."""

import frappe
from frappe.query_builder.functions import Coalesce

from tbo_smart import lead_source

SOURCE = ("source", "utm_source", "UTM Source")
FIELDS = {
	"Lead": (SOURCE, ("campaign_name", "utm_campaign", "UTM Campaign")),
	"Opportunity": (SOURCE, ("campaign", "utm_campaign", "UTM Campaign")),
	"Quotation": (SOURCE, ("campaign", "utm_campaign", "UTM Campaign")),
	"Sales Order": (SOURCE, ("campaign", "utm_campaign", "UTM Campaign")),
}


def execute():
	if not lead_source.on_utm():
		return
	for doctype, pairs in FIELDS.items():
		columns = set(frappe.db.get_table_columns(doctype))
		table = frappe.qb.DocType(doctype)
		for old, new, link in pairs:
			if old not in columns or new not in columns:
				continue
			to_copy = (Coalesce(table[old], "") != "") & (Coalesce(table[new], "") == "")
			for value in frappe.qb.from_(table).select(table[old]).distinct().where(to_copy).run(pluck=True):
				if not frappe.db.exists(link, value):
					frappe.get_doc({"doctype": link, "name": value}).insert(ignore_permissions=True)
			frappe.qb.update(table).set(table[new], table[old]).where(to_copy).run()
