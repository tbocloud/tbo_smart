"""What telephony needs on install and every migrate (docs/telephony-myoperator.md §2, §3)."""

import frappe

from tbo_smart import lead_source
from tbo_smart.sales import erpnext_installed
from tbo_smart.telephony import LEAD_SOURCE, SETTINGS

# a Single's field defaults apply only to a new document: an existing TBO Smart Settings gets
# the telephony defaults here, once, while the field has never been saved
DEFAULT_FIELDS = (
	"telephony_create_leads",
	"telephony_recording_days",
	"telephony_recording_notice",
	"call_summary_min_seconds",
	"transcription_model",
	"transcription_price_per_minute",
)


def setup_telephony() -> None:
	"""The lead source new caller leads get, and the settings' defaults."""
	if not erpnext_installed():
		return
	lead_source.ensure(LEAD_SOURCE)
	set_default_settings()


def set_default_settings() -> None:
	Singles = frappe.qb.DocType("Singles")
	saved = set(
		frappe.qb.from_(Singles)
		.select(Singles.field)
		.where((Singles.doctype == SETTINGS) & Singles.field.isin(DEFAULT_FIELDS))
		.run(pluck=True)
	)
	meta = frappe.get_meta(SETTINGS)
	for fieldname in DEFAULT_FIELDS:
		default = meta.get_field(fieldname).default
		if fieldname not in saved and default is not None:
			frappe.db.set_single_value(SETTINGS, fieldname, default)
