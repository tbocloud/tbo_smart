"""The segment rate card (docs/lead-pipeline.md §9.8): a price list and a discount band per lead
segment (`Lead.tbo_segment`), kept in TBO Smart Settings → Segment rate card (TBO Segment Rate).

- **Price:** a lead's quotation lines are priced from its segment's price list (ERPNext's own
  Price List and Item Price), else from Selling Settings' selling price list, line by line.
- **Band:** anyone may give up to the segment's discount on a lead's quotation without approval.
  Above it, the quotation approval levels apply as before (tbo_smart.quotation_control reads the
  band through `quotation_band`); a level whose own limit is higher keeps its own.

Only the preset editors (presets.can_edit_presets) change it. Everything reads as no rate card
while Quotations on the lead is off.
"""

import frappe
from frappe import _
from frappe.utils import flt

from tbo_smart.lead_pipeline import SETTINGS, presets, quotations_enabled
from tbo_smart.lead_pipeline.setup import SEGMENTS

ROW = "TBO Segment Rate"


def segment_rows() -> dict[str, frappe._dict]:
	"""{segment: its row (price_list, max_discount)} for the segments on the rate card."""
	rows = frappe.get_all(
		ROW,
		filters={"parenttype": SETTINGS, "parent": SETTINGS, "parentfield": "segment_rates"},
		fields=["segment", "price_list", "max_discount"],
		order_by="idx asc",
	)
	return {row.segment: row for row in rows}


def own_price_list(segment: str | None) -> str | None:
	"""The segment's own price list, or None when it uses the selling price list."""
	row = segment_rows().get(segment) if segment and quotations_enabled() else None
	return row.price_list if row and row.price_list else None


def price_list_for(segment: str | None) -> str | None:
	"""Where a segment's rates come from: its own list, else the selling price list."""
	return own_price_list(segment) or presets.price_list()


def band_for(segment: str | None) -> float | None:
	"""The segment's discount band, or None when it has none (blank or 0)."""
	row = segment_rows().get(segment) if segment and quotations_enabled() else None
	return flt(row.max_discount, 2) if row and flt(row.max_discount) > 0 else None


def lead_segment(lead: str | None) -> str | None:
	return frappe.db.get_value("Lead", lead, "tbo_segment") if lead else None


def lead_pricing(lead: str) -> dict:
	"""{segment, price_list, discount_band} for the lead's quotations."""
	segment = lead_segment(lead)
	return {
		"segment": segment,
		"price_list": price_list_for(segment),
		"discount_band": band_for(segment),
	}


def quotation_band(doc) -> float | None:
	"""The discount band of the segment of the lead a quotation belongs to; None for any other
	quotation."""
	from tbo_smart.lead_pipeline.quotations import lead_of

	return band_for(lead_segment(lead_of(doc)))


# ---------- the editors' screen ----------


def get_rate_card() -> dict:
	"""{segments: [{segment, price_list, max_discount}], default_price_list, price_lists,
	services: [{item_code, item_name, item_group, uom, rate, rates: {segment: rate}}], can_edit}.
	`rate` is the selling price list's; `rates` holds each segment's own list rate (None where
	that list has no price for the item, or the segment has no list of its own)."""
	rows = segment_rows()
	segments = [
		{
			"segment": segment,
			"price_list": rows[segment].price_list if segment in rows else None,
			"max_discount": flt(rows[segment].max_discount, 2) if segment in rows else 0,
		}
		for segment in SEGMENTS
	]
	services = presets.get_services()
	own_lists = {row["segment"]: row["price_list"] for row in segments if row["price_list"]}
	prices = item_prices(list(set(own_lists.values())), [row["item_code"] for row in services])
	for service in services:
		service["rates"] = {
			segment: prices.get((price_list, service["item_code"]))
			for segment, price_list in own_lists.items()
		}
	return {
		"segments": segments,
		"default_price_list": presets.price_list(),
		"price_lists": frappe.get_all(
			"Price List", filters={"selling": 1, "enabled": 1}, pluck="name", order_by="name asc"
		),
		"services": services,
		"can_edit": presets.can_edit_presets(),
	}


def item_prices(price_lists: list[str], item_codes: list[str]) -> dict[tuple, float]:
	"""{(price list, item): selling rate}."""
	if not price_lists or not item_codes:
		return {}
	Price = frappe.qb.DocType("Item Price")
	rows = (
		frappe.qb.from_(Price)
		.select(Price.price_list, Price.item_code, Price.price_list_rate)
		.where(Price.price_list.isin(price_lists) & Price.item_code.isin(item_codes) & (Price.selling == 1))
	).run(as_dict=True)
	return {(row.price_list, row.item_code): flt(row.price_list_rate) for row in rows}


def save_rate_card(rows: list) -> dict:
	"""Replace the rate card with `rows` ([{segment, price_list, max_discount}]); a segment with
	neither a list nor a band is left off. TBO Smart Settings checks the rows."""
	presets.require_preset_editor()
	if not isinstance(rows, list):
		frappe.throw(_("rows must be a list of segments."))
	settings = frappe.get_single(SETTINGS)
	settings.set("segment_rates", [])
	for row in rows:
		if not isinstance(row, dict) or row.get("segment") not in SEGMENTS:
			frappe.throw(_("Each row needs a segment: {0}.").format(", ".join(SEGMENTS)))
		price_list = row.get("price_list") or None
		band = flt(row.get("max_discount"), 2)
		if price_list or band:
			settings.append(
				"segment_rates", {"segment": row["segment"], "price_list": price_list, "max_discount": band}
			)
	settings.save(ignore_permissions=True)
	return get_rate_card()


def set_segment_rate(item_code: str, rate: float, segment: str | None = None) -> float:
	"""The item's rate on the segment's own price list, or with no segment on the selling price
	list. A segment without its own list has no rates of its own to set."""
	presets.require_preset_editor()
	if not segment:
		return presets.set_service_rate(item_code, rate)
	if segment not in SEGMENTS:
		frappe.throw(_("{0} is not a segment.").format(segment))
	price_list = own_price_list(segment)
	if not price_list:
		frappe.throw(
			_("{0} uses the selling price list. Give it a price list of its own first.").format(_(segment))
		)
	return presets.set_service_rate(item_code, rate, price_list)
