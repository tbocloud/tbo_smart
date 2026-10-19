"""Quotations on the lead (docs/lead-pipeline.md §9).

The deal's New quotation dialog is reused on the lead: the same values, validation and ERPNext
Quotation, made out to the Lead. Every method needs TBO Smart Settings → Lead pipeline and
Quotations on the lead, and checks the lead under the sales rules (tbo_smart.sales.permissions);
ERPNext's Quotation permissions and the discount/commission control apply on save and submit,
which run as the session user.
"""

import frappe
from erpnext.controllers.accounts_controller import get_default_taxes_and_charges
from frappe import _
from frappe.utils import add_days, cint, flt, now_datetime, today

from tbo_smart.api.finance.quotation import (
	TAX_TEMPLATE,
	apply_values,
	form_choices,
	parse_values,
	serialize_quotation,
)
from tbo_smart.finance import get_default_company
from tbo_smart.lead_pipeline import presets as catalogue
from tbo_smart.lead_pipeline import quotations as q
from tbo_smart.lead_pipeline import quotations_enabled, rate_card, require_quotations
from tbo_smart.quotation_control import QuotationControl
from tbo_smart.sales.opportunity import lead_opportunity
from tbo_smart.smart_leads.utils import normalise_phone, whatsapp_link

# "<quotation>--<time>.pdf": unique, so Frappe never appends its own suffix, and SAL-QTN-1's
# PDF can't be mistaken for SAL-QTN-10's
PDF_SEPARATOR = "--"
LIST_FIELDS = [
	"name",
	"docstatus",
	"tbo_quote_status",
	"transaction_date",
	"valid_till",
	"currency",
	"grand_total",
	"net_total",
	"tbo_revision_no",
	"tbo_quote_family",
	"tbo_is_latest",
	"tbo_sent_via",
	"tbo_sent_on",
	"tbo_lead_meeting",
	"tbo_package",
]


@frappe.whitelist()
def get_access() -> dict:
	"""{enabled, can_edit_presets, can_make_orders}."""
	if not quotations_enabled():
		return {"enabled": False}
	return {
		"enabled": bool(frappe.has_permission("Quotation", "read")),
		"can_edit_presets": catalogue.can_edit_presets(),
		"can_make_orders": bool(frappe.has_permission("Sales Order", "create")),
	}


@frappe.whitelist()
def get_lead_quotations(lead: str) -> dict:
	"""The lead's quotations, newest family first, latest revision flagged, with the deal once
	the lead is won."""
	require_lead(lead, "read")
	rows = q.lead_quotations(lead, LIST_FIELDS)
	states = q.draft_states([row.name for row in rows if row.docstatus == 0])
	pdfs = lead_pdfs(lead)
	for row in rows:
		if row.docstatus == 0:
			row.tbo_quote_status = states[row.name]
		row.pdf = next((url for name, url in pdfs if name.startswith(f"{row.name}{PDF_SEPARATOR}")), None)
	return {
		"lead": lead,
		"deal": lead_opportunity(lead),
		"can_write": bool(frappe.has_permission("Lead", "write", lead)),
		"quotations": rows,
	}


def pdf_file_name(quotation: str) -> str:
	return f"{quotation}{PDF_SEPARATOR}{now_datetime().strftime('%Y%m%d-%H%M%S')}.pdf"


def lead_pdfs(lead: str) -> list:
	"""(file name, url) of the PDFs saved on the lead, newest first. Each is named
	"<quotation>--<time>.pdf" (pdf_file_name), so it matches its own quotation only."""
	return frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Lead", "attached_to_name": lead, "file_name": ("like", "%.pdf")},
		fields=["file_name", "file_url"],
		order_by="creation desc",
		as_list=True,
	)


@frappe.whitelist()
def get_quotation_form(lead: str, quotation: str | None = None, meeting: str | None = None) -> dict:
	"""The builder's values: a new quotation for the lead (company, validity, default tax,
	payment terms and terms) or one of its drafts, plus the catalogue of packages and services
	priced for the lead's segment and its `pricing` (segment, price list, discount band)."""
	require_lead(lead, "read")
	pricing = rate_card.lead_pricing(lead)
	priced = {"catalogue": catalogue.get_catalogue(pricing["price_list"]), "pricing": pricing}
	if quotation:
		doc = get_lead_quotation(quotation, lead, "read")
		return {**form_choices(doc.company), **priced, **serialize(doc)}
	company = get_default_company() or frappe.defaults.get_user_default("Company")
	lead_doc = frappe.db.get_value("Lead", lead, ["lead_name", "company_name"], as_dict=True)
	return {
		**form_choices(company),
		**priced,
		"name": None,
		"company": company,
		"party": {
			"quotation_to": "Lead",
			"party_name": lead,
			"label": lead_doc.company_name or lead_doc.lead_name,
		},
		"currency": frappe.get_cached_value("Company", company, "default_currency") if company else None,
		**new_quotation_defaults(company),
		"additional_discount_percentage": 0,
		"commission": 0,
		"meeting": meeting
		if meeting and frappe.db.exists("TBO Lead Meeting", {"name": meeting, "lead": lead})
		else None,
		"items": [],
	}


def new_quotation_defaults(company: str | None) -> dict:
	"""What a new lead quotation starts with: the validity, the company's default tax template, and
	the payment terms and terms set in TBO Smart Settings."""
	terms = q.setting("quotation_terms")
	return {
		"valid_till": add_days(today(), q.validity_days()),
		"taxes_and_charges": (get_default_taxes_and_charges(TAX_TEMPLATE, company=company) or {}).get(
			"taxes_and_charges"
		)
		if company
		else None,
		"payment_terms_template": q.setting("quotation_payment_terms"),
		"tc_name": terms,
		"terms": frappe.db.get_value("Terms and Conditions", terms, "terms") if terms else None,
	}


@frappe.whitelist(methods=["POST"])
def save_quotation(lead: str, values: dict | str, quotation: str | None = None) -> dict:
	"""Create a draft quotation for the lead, or update one of its drafts."""
	require_lead(lead, "write")
	values = parse_values(values)
	if quotation:
		doc = get_lead_quotation(quotation, lead, "write")
		if doc.docstatus != 0:
			frappe.throw(_("Only a draft quotation can be changed. Create a revision instead."))
	else:
		if not frappe.has_permission("Quotation", "create"):
			frappe.throw(_("You cannot create quotations."), frappe.PermissionError)
		doc = new_lead_quotation(lead, values)
	apply_values(doc, values)
	if values.get("package") and frappe.db.exists(catalogue.PACKAGE, values.package):
		doc.tbo_package = values.package
	if doc.is_new():
		doc.insert()
		q.start_family(doc)
	else:
		doc.save()
	q.sync_status(doc)
	return {
		**serialize(doc),
		"needs_approval": [
			{"kind": kind, "percent": percent, "limit": limit}
			for kind, percent, limit in QuotationControl(doc).uncovered()
		],
	}


def new_lead_quotation(lead: str, values) -> object:
	label = frappe.db.get_value("Lead", lead, ["company_name", "lead_name"], as_dict=True)
	doc = frappe.new_doc("Quotation")
	meeting = values.get("meeting")
	doc.update(
		{
			"quotation_to": "Lead",
			"party_name": lead,
			"customer_name": label.company_name or label.lead_name,
			"opportunity": lead_opportunity(lead),
			"tbo_lead_meeting": meeting
			if meeting and frappe.db.exists("TBO Lead Meeting", {"name": meeting, "lead": lead})
			else None,
			"tbo_quote_status": q.DRAFT,
			"selling_price_list": rate_card.price_list_for(rate_card.lead_segment(lead)),
			# the discount comes off the net total, as the approval limits read it
			"apply_discount_on": "Net Total",
			"company": values.get("company") or get_default_company(),
			"transaction_date": today(),
			"valid_till": values.get("valid_till") or add_days(today(), q.validity_days()),
		}
	)
	return doc


def serialize(doc) -> dict:
	return {
		**serialize_quotation(doc),
		"lead": q.lead_of(doc),
		"meeting": doc.get("tbo_lead_meeting"),
		"package": doc.get("tbo_package"),
		"quote_status": q.draft_status(doc) if doc.docstatus == 0 else doc.get("tbo_quote_status"),
		"revision": cint(doc.get("tbo_revision_no")),
		"family": q.family_of(doc),
		"is_latest": cint(doc.get("tbo_is_latest")),
	}


# ---------- with the client ----------


@frappe.whitelist(methods=["POST"])
def mark_sent(
	quotation: str,
	via: str,
	recipients: str | None = None,
	message: str | None = None,
	follow_up_days: int | None = None,
) -> dict:
	"""Record how the quotation went out and schedule the follow-up. Email sends it through
	ERPNext with the TBO PDF attached (logged on the quotation); WhatsApp returns the wa.me link
	with the message for the user to send; Manual only records it. A draft is submitted first
	and is refused while its approval is pending."""
	doc = get_lead_quotation(quotation, None, "write")
	lead = frappe.db.get_value(
		"Lead", q.lead_of(doc), ["email_id", "mobile_no", "phone", "lead_name"], as_dict=True
	)
	text = (message or "").strip() or _("Please find our quotation {0} attached.").format(doc.name)
	if via == "Email":
		to = (recipients or lead.email_id or "").strip()
		if not to:
			frappe.throw(_("Give the email address to send it to."))
	task = q.mark_sent(doc, via, cint(follow_up_days) if follow_up_days not in (None, "") else None)
	result = {"quotation": doc.name, "status": doc.tbo_quote_status, "task": task}
	if via == "Email":
		from frappe.core.doctype.communication.email import make

		make(
			doctype="Quotation",
			name=doc.name,
			recipients=to,
			subject=_("Quotation {0} from {1}").format(doc.name, doc.company),
			content=frappe.utils.escape_html(text).replace("\n", "<br>"),
			send_email=1,
			print_format=q.setting("quotation_print_format") or "TBO Quotation",
			attach_document_print=1,
		)
	elif via == "WhatsApp":
		phone = next((number for number in (lead.mobile_no, lead.phone) if normalise_phone(number)), None)
		result["whatsapp_url"] = whatsapp_link(phone, text) if phone else None
	return result


@frappe.whitelist(methods=["POST"])
def set_quote_status(
	quotation: str,
	status: str,
	note: str | None = None,
	close_lead: bool = False,
	lost_reason: str | None = None,
) -> dict:
	"""Viewed / Acknowledged, Under Negotiation, Revision Required, Accepted or Rejected. Accepted
	makes the lead Closed – Won (and its deal); Rejected needs a reason, and with `close_lead`
	the lead becomes Closed – Lost with `lost_reason`."""
	doc = get_lead_quotation(quotation, None, "write")
	if status == q.REJECTED and not (note or "").strip():
		frappe.throw(_("Say why the client rejected it."))
	if cint(close_lead) and not (lost_reason or "").strip():
		frappe.throw(_("Give a reason for losing the lead."))
	q.set_status(doc, status, note)
	if status == q.REJECTED and cint(close_lead):
		q.close_lead_lost(doc, lost_reason)
	doc.reload()
	return serialize(doc)


@frappe.whitelist(methods=["POST"])
def create_revision(quotation: str) -> dict:
	doc = get_lead_quotation(quotation, None, "write")
	if not frappe.has_permission("Quotation", "create"):
		frappe.throw(_("You cannot create quotations."), frappe.PermissionError)
	return serialize(q.create_revision(doc))


@frappe.whitelist()
def get_revisions(quotation: str) -> list[dict]:
	return q.get_revisions(get_lead_quotation(quotation, None, "read"))


@frappe.whitelist(methods=["POST"])
def save_pdf(quotation: str) -> dict:
	"""The quotation's TBO PDF, saved as a private file on the lead (the lead's Attachments)."""
	doc = get_lead_quotation(quotation, None, "write")
	pdf = frappe.get_print(
		"Quotation",
		doc.name,
		print_format=q.setting("quotation_print_format") or "TBO Quotation",
		as_pdf=True,
	)
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": pdf_file_name(doc.name),
			"attached_to_doctype": "Lead",
			"attached_to_name": q.lead_of(doc),
			"is_private": 1,
			"content": pdf,
		}
	).insert(ignore_permissions=True)
	return {"file_url": file.file_url, "file_name": file.file_name}


@frappe.whitelist(methods=["POST"])
def make_sales_order(quotation: str) -> dict:
	"""Convert an accepted quotation of a won lead into a draft Sales Order (people who may
	create Sales Orders)."""
	doc = get_lead_quotation(quotation, None, "read")
	if not frappe.has_permission("Sales Order", "create"):
		frappe.throw(_("You cannot create Sales Orders."), frappe.PermissionError)
	return {"sales_order": q.make_sales_order(doc)}


# ---------- the catalogue ----------


@frappe.whitelist()
def get_catalogue(segment: str | None = None) -> dict:
	"""The catalogue, priced for `segment` (its own price list where it has one)."""
	require_quotations()
	return catalogue.get_catalogue(rate_card.price_list_for(segment))


@frappe.whitelist(methods=["POST"])
def save_package(values: dict | str) -> dict:
	require_quotations()
	return {"name": catalogue.save_package(dict(parse_object(values)))}


@frappe.whitelist(methods=["POST"])
def set_service_rate(item_code: str, rate: float | str) -> dict:
	require_quotations()
	return {"item_code": item_code, "rate": catalogue.set_service_rate(item_code, flt(rate))}


# ---------- helpers ----------


def require_lead(lead: str, ptype: str) -> None:
	require_quotations()
	if not isinstance(lead, str) or not frappe.db.exists("Lead", lead):
		frappe.throw(_("Lead {0} not found.").format(lead), frappe.DoesNotExistError)
	frappe.has_permission("Lead", ptype, lead, throw=True)


def get_lead_quotation(quotation: str, lead: str | None, ptype: str):
	"""A quotation of a lead the user may see (read) or work on (write)."""
	require_quotations()
	if not isinstance(quotation, str) or not frappe.db.exists("Quotation", quotation):
		frappe.throw(_("Quotation {0} not found.").format(quotation), frappe.DoesNotExistError)
	doc = frappe.get_doc("Quotation", quotation)
	owner_lead = q.lead_of(doc)
	if not owner_lead or (lead and owner_lead != lead):
		frappe.throw(
			_("Quotation {0} does not belong to this lead.").format(quotation), frappe.PermissionError
		)
	frappe.has_permission("Lead", ptype, owner_lead, throw=True)
	return doc


def parse_object(values) -> frappe._dict:
	if isinstance(values, str):
		try:
			values = frappe.parse_json(values)
		except ValueError:
			values = None
	if not isinstance(values, dict):
		frappe.throw(_("values must be an object."))
	return frappe._dict(values)
