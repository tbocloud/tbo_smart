"""Copy Frappe CRM's records into ERPNext's own, once, before the CRM app is uninstalled
(docs/erpnext-lead-cutover.md §4).

    bench --site <site> execute tbo_smart.cutover.crm_to_erpnext.run --kwargs "{'dry_run': True}"
    bench --site <site> execute tbo_smart.cutover.crm_to_erpnext.run --kwargs "{'dry_run': False}"
    bench --site <site> execute tbo_smart.cutover.crm_to_erpnext.verify
    bench --site <site> execute tbo_smart.cutover.crm_to_erpnext.finalize

The CRM tables are read by their names through frappe.qb, never through the CRM app's code, so
this runs while the app is installed and imports nothing from it. Record names are kept (a CRM
Lead CRM-LEAD-2026-00042 becomes the Lead CRM-LEAD-2026-00042, a CRM Deal the Opportunity of the
same name), so every link to them stays valid; each copy also records its source
(`crm_lead_ref`, `crm_deal_ref`, or the kept name for notes, calls and tasks), so a second run
skips what is done and a run cut off halfway continues. It commits every `batch_size` records.
"""

import json
import os
import random

import frappe
from frappe import _
from frappe.query_builder.functions import Count
from frappe.utils import cint, escape_html, flt, get_fullname, getdate, now_datetime
from frappe.utils.nestedset import rebuild_tree

from tbo_smart import lead_source
from tbo_smart.lead_pipeline.statuses import CLOSED_LOST, CLOSED_WON, NEW_LEAD, STATUS_NAMES, STATUSES
from tbo_smart.sales.hierarchy import DOCTYPE as HIERARCHY
from tbo_smart.sales.stages import OTHER_REASON
from tbo_smart.smart_leads.migration import note_html

BATCH_SIZE = 200
SAMPLE_SIZE = 25
REPORT_FOLDER = "crm_to_erpnext"
TASK_STATUS = {
	"Backlog": "Open",
	"Todo": "Open",
	"In Progress": "Open",
	"Done": "Closed",
	"Canceled": "Cancelled",
}
# the CRM doctypes and their ERPNext replacements, for the links other records hold
DOCTYPE_MAP = {"CRM Lead": "Lead", "CRM Deal": "Opportunity", "CRM Organization": "Prospect"}
NATIVE_QUOTATION_PARTIES = ["Customer", "Lead", "Prospect"]
# tbo_smart's own custom fields on ERPNext doctypes that pointed at CRM doctypes, removed once
# their values are copied (those on the CRM's own doctypes go with the app)
OLD_FIELDS = (
	("Sales Order", "crm_deal"),
	("Sales Invoice", "crm_deal"),
	("Payment Entry", "crm_deal"),
	("Quotation", "tbo_crm_lead"),
)
COPIED_NOTE = "Copied from Frappe CRM"


def crm_tables_exist() -> bool:
	return bool(frappe.db.table_exists("CRM Lead"))


def table(doctype: str):
	return frappe.qb.DocType(doctype)


def rows_of(doctype: str, where=None, order_by: str = "name") -> list:
	"""Every row of a CRM table, read by its name (no CRM code involved)."""
	if not frappe.db.table_exists(doctype):
		return []
	Table = table(doctype)
	query = frappe.qb.from_(Table).select("*").orderby(Table[order_by])
	if where is not None:
		query = query.where(where)
	return query.run(as_dict=True)


def single_value(doctype: str, field: str):
	"""A Single's stored value, read from tabSingles (the doctype may be an uninstalled app's)."""
	Singles = table("Singles")
	rows = (
		frappe.qb.from_(Singles)
		.select(Singles.value)
		.where((Singles.doctype == doctype) & (Singles.field == field))
	).run()
	return rows[0][0] if rows else None


class Cutover:
	"""One run: what it found, what it copied, what failed."""

	def __init__(self, dry_run: bool = True, batch_size: int = BATCH_SIZE):
		self.dry_run = bool(cint(dry_run))
		self.batch_size = max(cint(batch_size) or BATCH_SIZE, 1)
		self.lead_names: dict[str, str] = {}
		self.deal_names: dict[str, str] = {}
		self.report = frappe._dict(
			dry_run=self.dry_run, started=str(now_datetime()), counts={}, problems={}, renamed={}, failed=[]
		)
		self.users = set(frappe.get_all("User", pluck="name"))

	# ---------- the run ----------

	def run(self) -> dict:
		if not crm_tables_exist():
			frappe.throw(_("There are no Frappe CRM tables on this site: nothing to copy."))
		self.survey()
		if self.dry_run:
			return self.finish()
		settings = self.prepare_settings()
		flags = {key: frappe.flags.get(key) for key in ("mute_emails", "in_import", "tbo_cutover")}
		# no emails, no assignment or mention notices, no scoring jobs or deals from the hooks
		frappe.flags.update({"mute_emails": True, "in_import": True, "tbo_cutover": True})
		try:
			self.copy_masters()
			self.copy_hierarchy()
			self.copy_organizations()
			self.copy_leads()
			self.copy_deals()
			self.copy_notes()
			self.copy_calls()
			self.copy_tasks()
			self.relink()
			frappe.db.commit()  # nosemgrep
		finally:
			frappe.flags.update(flags)
			self.restore_settings(settings)
		return self.finish()

	def survey(self) -> None:
		"""Counts and problems, whatever the mode; nothing is written."""
		Lead, Deal, Copy = table("CRM Lead"), table("CRM Deal"), table("Lead")
		counts = self.report.counts
		for kind, doctype in (
			("leads", "CRM Lead"),
			("deals", "CRM Deal"),
			("organizations", "CRM Organization"),
			("notes", "FCRM Note"),
			("calls", "CRM Call Log"),
			("tasks", "CRM Task"),
			("hierarchy", "CRM Sales Hierarchy"),
		):
			counts[kind] = {"crm": frappe.db.count(doctype) if frappe.db.table_exists(doctype) else 0}
		# copied by an earlier run; "copied" below counts this run's
		counts["leads"]["already"] = frappe.db.count("Lead", {"crm_lead_ref": ("is", "set")})
		counts["deals"]["already"] = frappe.db.count("Opportunity", {"crm_deal_ref": ("is", "set")})
		emails = (
			frappe.qb.from_(Lead)
			.select(Lead.email)
			.where(Lead.email.isnotnull() & (Lead.email != ""))
			.groupby(Lead.email)
			.having(Count("*") > 1)
		).run(pluck=True)
		# a Lead of the same name that is not this lead's copy: the copy gets a new name
		taken = (
			frappe.qb.from_(Lead)
			.join(Copy)
			.on(Copy.name == Lead.name)
			.select(Lead.name)
			.where(is_blank(Copy.crm_lead_ref) | (Copy.crm_lead_ref != Lead.name))
		).run(pluck=True)
		statuses = (
			frappe.qb.from_(Lead).select(Lead.status).distinct().where(Lead.status.notin(list(STATUS_NAMES)))
		).run(pluck=True)
		orphans = (
			frappe.qb.from_(Deal).select(Deal.name).where(is_blank(Deal.lead) & is_blank(Deal.organization))
		).run(pluck=True)
		self.report.problems = {
			"duplicate_emails": emails,
			"lead_names_taken": taken,
			"statuses_outside_the_pipeline": statuses,
			"deals_without_lead_or_organization": orphans,
		}

	def finish(self) -> dict:
		self.report.finished = str(now_datetime())
		if not self.dry_run:
			self.report.path = write_report(self.report)
		print_report(self.report)
		return self.report

	# ---------- settings around the run ----------

	def prepare_settings(self) -> dict:
		"""ERPNext would make a Contact for every lead and refuse leads sharing an email: the
		CRM did neither. Contacts stay off for the run; duplicate emails stay allowed when the
		CRM has them (ERPNext validates every later save of those leads too)."""
		saved = {
			"auto_creation_of_contact": frappe.db.get_single_value("CRM Settings", "auto_creation_of_contact")
		}
		frappe.db.set_single_value("CRM Settings", "auto_creation_of_contact", 0)
		if self.report.problems["duplicate_emails"]:
			frappe.db.set_single_value("CRM Settings", "allow_lead_duplication_based_on_emails", 1)
		frappe.db.commit()  # nosemgrep
		return saved

	def restore_settings(self, saved: dict) -> None:
		for field, value in saved.items():
			frappe.db.set_single_value("CRM Settings", field, value)
		frappe.db.commit()  # nosemgrep

	# ---------- masters ----------

	def copy_masters(self) -> None:
		for row in rows_of("CRM Lead Source"):
			lead_source.ensure(row.name)
		for row in rows_of("CRM Lost Reason"):
			ensure("Opportunity Lost Reason", row.name, {"lost_reason": row.name})
		ensure("Opportunity Lost Reason", OTHER_REASON, {"lost_reason": OTHER_REASON})
		for row in rows_of("CRM Industry"):
			ensure("Industry Type", row.name, {"industry": row.name})
		root = frappe.db.get_value(
			"Territory", {"is_group": 1, "parent_territory": ("in", ("", None))}, "name"
		)
		for row in rows_of("CRM Territory"):
			ensure(
				"Territory", row.name, {"territory_name": row.name, "parent_territory": root, "is_group": 0}
			)
		for row in rows_of("CRM Deal Status", order_by="position"):
			values = {
				"tbo_stage_type": row.type or "Ongoing",
				"tbo_probability": flt(row.probability),
				"tbo_position": cint(row.position),
			}
			if frappe.db.exists("Sales Stage", row.name):
				frappe.db.set_value("Sales Stage", row.name, values)
			else:
				frappe.get_doc({"doctype": "Sales Stage", "stage_name": row.name, **values}).insert(
					ignore_permissions=True
				)
		hierarchy = single_value("FCRM Settings", "enable_sales_hierarchy")
		if hierarchy is not None:
			frappe.db.set_single_value("TBO Smart Settings", "enable_sales_hierarchy", cint(hierarchy))
		company = single_value("ERPNext CRM Settings", "erpnext_company")
		if (
			company
			and frappe.db.exists("Company", company)
			and not frappe.db.get_single_value("TBO Smart Settings", "sales_company")
		):
			frappe.db.set_single_value("TBO Smart Settings", "sales_company", company)
		frappe.db.commit()  # nosemgrep

	def copy_hierarchy(self) -> None:
		rows = rows_of("CRM Sales Hierarchy")
		copied = 0
		for row in rows:
			if frappe.db.exists(HIERARCHY, row.name) or row.user not in self.users:
				continue
			doc = frappe.get_doc(
				{
					"doctype": HIERARCHY,
					"name": row.name,
					"user": row.user,
					"full_name": row.full_name,
					"reports_to": row.reports_to,
					"is_group": cint(row.is_group),
				}
			)
			doc.db_insert()
			copied += 1
		if copied:
			rebuild_tree(HIERARCHY)
		self.report.counts["hierarchy"]["copied"] = copied
		frappe.db.commit()  # nosemgrep

	def copy_organizations(self) -> None:
		copied = 0
		for row in rows_of("CRM Organization"):
			if frappe.db.exists("Prospect", row.name):
				continue
			copied += self.attempt(
				"organization",
				row.name,
				lambda row=row: self.insert(
					"Prospect",
					{
						"company_name": row.organization_name or row.name,
						"website": row.website,
						"no_of_employees": row.no_of_employees,
						"annual_revenue": flt(row.annual_revenue),
						"industry": row.industry
						if frappe.db.exists("Industry Type", row.industry or "")
						else None,
						"territory": row.territory
						if frappe.db.exists("Territory", row.territory or "")
						else None,
						"prospect_owner": row.owner if row.owner in self.users else None,
					},
					row,
					set_name=row.name,
				),
			)
		self.report.counts["organizations"]["copied"] = copied
		frappe.db.commit()  # nosemgrep

	# ---------- leads ----------

	def copy_leads(self) -> None:
		done = dict(
			frappe.get_all(
				"Lead", filters={"crm_lead_ref": ("is", "set")}, fields=["crm_lead_ref", "name"], as_list=True
			)
		)
		self.lead_names.update(done)
		logs = self.stage_logs("CRM Lead")
		copied = 0
		for index, row in enumerate(rows_of("CRM Lead"), start=1):
			if row.name in done:
				continue
			copied += self.attempt(
				"lead", row.name, lambda row=row: self.copy_lead(row, logs.get(row.name, []))
			)
			if index % self.batch_size == 0:
				frappe.db.commit()  # nosemgrep
		self.report.counts["leads"]["copied"] = copied
		frappe.db.commit()  # nosemgrep

	def copy_lead(self, row, logs: list) -> None:
		stage = self.lead_stage(row)
		reason, notes = row.lost_reason, row.lost_notes
		if stage == CLOSED_LOST:
			if not reason or not frappe.db.exists("Opportunity Lost Reason", reason):
				reason, notes = OTHER_REASON, notes or row.lost_reason or COPIED_NOTE
			elif reason == OTHER_REASON and not notes:
				notes = COPIED_NOTE
		values = {
			"salutation": row.salutation if frappe.db.exists("Salutation", row.salutation or "") else None,
			"first_name": row.first_name,
			"middle_name": row.middle_name,
			"last_name": row.last_name,
			"lead_name": row.lead_name,
			"company_name": row.organization,
			"email_id": row.email,
			"mobile_no": row.mobile_no,
			"phone": row.phone,
			"website": row.website,
			"job_title": row.job_title,
			"no_of_employees": row.no_of_employees,
			"annual_revenue": flt(row.annual_revenue),
			lead_source.field(): row.source if lead_source.exists(row.source) else None,
			"industry": row.industry if frappe.db.exists("Industry Type", row.industry or "") else None,
			"territory": row.territory if frappe.db.exists("Territory", row.territory or "") else None,
			"lead_owner": row.lead_owner if row.lead_owner in self.users else None,
			"tbo_stage": stage,
			"tbo_lost_reason": reason if stage == CLOSED_LOST else row.lost_reason or None,
			"tbo_lost_notes": notes,
			"tbo_score": cint(row.lead_score),
			"tbo_temperature": row.lead_temperature,
			"crm_lead_ref": row.name,
		}
		for field in (
			"tbo_score_reason",
			"tbo_scored_at",
			"tbo_ai_adjust",
			"tbo_ai_reason",
			"tbo_ai_hash",
			"tbo_ai_brief",
			"tbo_ai_brief_hash",
			"tbo_ai_brief_at",
		):
			values[field] = row.get(field)
		if values["tbo_lost_reason"] and not frappe.db.exists(
			"Opportunity Lost Reason", values["tbo_lost_reason"]
		):
			values["tbo_lost_reason"] = OTHER_REASON
		name = self.free_name("Lead", row.name, "crm_lead_ref")
		lead = self.insert("Lead", values, row, set_name=name, email_check=False)
		self.lead_names[row.name] = lead
		if lead != row.name:
			self.report.renamed[row.name] = lead
		self.write_stage_log("Lead", lead, logs)
		if row.get("_assign"):
			frappe.db.set_value("Lead", lead, "_assign", row._assign, update_modified=False)

	def lead_stage(self, row) -> str:
		"""The CRM status when it is a pipeline stage, else the stage of its type."""
		if row.status in STATUS_NAMES:
			return row.status
		kind = frappe.db.get_value("CRM Lead Status", row.status, "type") if row.status else None
		if cint(row.converted) or kind == "Won":
			return CLOSED_WON
		if kind == "Lost":
			return CLOSED_LOST
		return next((name for name, stage_kind, _color in STATUSES if stage_kind == kind), NEW_LEAD)

	# ---------- deals ----------

	def copy_deals(self) -> None:
		done = dict(
			frappe.get_all(
				"Opportunity",
				filters={"crm_deal_ref": ("is", "set")},
				fields=["crm_deal_ref", "name"],
				as_list=True,
			)
		)
		self.deal_names.update(done)
		if not self.lead_names:
			self.lead_names.update(
				dict(
					frappe.get_all(
						"Lead",
						filters={"crm_lead_ref": ("is", "set")},
						fields=["crm_lead_ref", "name"],
						as_list=True,
					)
				)
			)
		logs = self.stage_logs("CRM Deal")
		products = self.deal_products()
		contacts = self.deal_contacts()
		company = frappe.db.get_single_value(
			"TBO Smart Settings", "sales_company"
		) or frappe.defaults.get_global_default("company")
		copied = 0
		for index, row in enumerate(rows_of("CRM Deal"), start=1):
			if row.name in done:
				continue
			copied += self.attempt(
				"deal",
				row.name,
				lambda row=row: self.copy_deal(
					row, logs.get(row.name, []), products.get(row.name, []), contacts.get(row.name), company
				),
			)
			if index % self.batch_size == 0:
				frappe.db.commit()  # nosemgrep
		self.report.counts["deals"]["copied"] = copied
		frappe.db.commit()  # nosemgrep

	def copy_deal(self, row, logs: list, products: list, contact: str | None, company: str | None) -> None:
		customer = (
			row.get("erpnext_customer")
			if frappe.db.exists("Customer", row.get("erpnext_customer") or "")
			else None
		)
		party = self.deal_party(row, customer)
		kind = frappe.db.get_value("Sales Stage", row.status, "tbo_stage_type") if row.status else None
		values = {
			**party,
			"opportunity_owner": row.deal_owner if row.deal_owner in self.users else None,
			"sales_stage": row.status if frappe.db.exists("Sales Stage", row.status or "") else None,
			"opportunity_amount": flt(row.deal_value or row.expected_deal_value),
			"currency": row.currency,
			"conversion_rate": flt(row.exchange_rate) or 1,
			"probability": flt(row.probability),
			"expected_closing": row.expected_closure_date,
			"tbo_next_step": row.next_step,
			"tbo_customer": customer,
			"customer_name": row.organization or row.organization_name or row.lead_name,
			"contact_person": contact,
			"contact_email": row.email,
			"contact_mobile": row.mobile_no,
			"phone": row.phone,
			"job_title": row.job_title,
			"website": row.website,
			"no_of_employees": row.no_of_employees,
			"annual_revenue": flt(row.annual_revenue),
			lead_source.field(): row.source if lead_source.exists(row.source) else None,
			"industry": row.industry if frappe.db.exists("Industry Type", row.industry or "") else None,
			"territory": row.territory if frappe.db.exists("Territory", row.territory or "") else None,
			"company": row.get("tbo_company") or company,
			"tbo_finance_stage": row.get("tbo_stage"),
			"tbo_onboarding_complete": cint(row.get("tbo_onboarding_complete")),
			"tbo_closed": cint(row.get("tbo_closed")),
			"tbo_accounts_owner": row.get("tbo_accounts_owner")
			if row.get("tbo_accounts_owner") in self.users
			else None,
			"crm_deal_ref": row.name,
			"items": products,
		}
		if kind == "Lost":
			reason = (
				row.lost_reason
				if frappe.db.exists("Opportunity Lost Reason", row.lost_reason or "")
				else OTHER_REASON
			)
			values.update({"lost_reasons": [{"lost_reason": reason}], "order_lost_reason": row.lost_notes})
		name = self.free_name("Opportunity", row.name, "crm_deal_ref")
		deal = self.insert("Opportunity", values, row, set_name=name)
		self.deal_names[row.name] = deal
		if deal != row.name:
			self.report.renamed[row.name] = deal
		# the CRM's own day it was won or lost
		frappe.db.set_value("Opportunity", deal, "tbo_closed_on", row.closed_date, update_modified=False)
		self.write_stage_log("Opportunity", deal, logs)
		if row.get("_assign"):
			frappe.db.set_value("Opportunity", deal, "_assign", row._assign, update_modified=False)

	def deal_party(self, row, customer: str | None) -> dict:
		"""The deal's lead when it came from one, else its Customer, else its organization (a
		Prospect, made when missing), else a new lead for its contact."""
		if row.lead and self.lead_names.get(row.lead):
			return {"opportunity_from": "Lead", "party_name": self.lead_names[row.lead]}
		if customer:
			return {"opportunity_from": "Customer", "party_name": customer}
		organization = row.organization or row.organization_name
		if organization:
			if not frappe.db.exists("Prospect", organization):
				frappe.get_doc({"doctype": "Prospect", "company_name": organization}).insert(
					ignore_permissions=True
				)
			return {"opportunity_from": "Prospect", "party_name": organization}
		lead = frappe.get_doc(
			{
				"doctype": "Lead",
				"first_name": row.first_name or row.lead_name or row.name,
				"last_name": row.last_name,
				"email_id": row.email,
				"mobile_no": row.mobile_no,
				"lead_owner": row.deal_owner if row.deal_owner in self.users else None,
				"tbo_stage": CLOSED_WON,
			}
		)
		lead.flags.update(
			{"ignore_permissions": True, "ignore_mandatory": True, "ignore_email_validation": True}
		)
		lead.insert()
		self.report.counts.setdefault("leads_made_for_deals", 0)
		self.report.counts["leads_made_for_deals"] += 1
		return {"opportunity_from": "Lead", "party_name": lead.name}

	def deal_products(self) -> dict:
		"""{deal: [Opportunity Item rows]} from the deals' CRM Products with an ERPNext item."""
		items = {row.name: row.get("erpnext_item_code") for row in rows_of("CRM Product")}
		result: dict[str, list] = {}
		missing = 0
		for row in rows_of("CRM Products", table("CRM Products").parenttype == "CRM Deal", order_by="idx"):
			item = items.get(row.product_code) or (
				row.product_code if frappe.db.exists("Item", row.product_code or "") else None
			)
			if not item or not frappe.db.exists("Item", item):
				missing += 1
				continue
			result.setdefault(row.parent, []).append(
				{"item_code": item, "qty": flt(row.qty) or 1, "rate": flt(row.rate)}
			)
		self.report.problems["deal_products_without_item"] = missing
		return result

	def deal_contacts(self) -> dict:
		"""{deal: its primary Contact}."""
		contacts = {}
		for row in rows_of("CRM Contacts", table("CRM Contacts").parenttype == "CRM Deal", order_by="idx"):
			if (
				row.contact
				and frappe.db.exists("Contact", row.contact)
				and (row.is_primary or row.parent not in contacts)
			):
				contacts[row.parent] = row.contact
		return contacts

	# ---------- notes, calls, tasks ----------

	def target(self, reference_doctype: str | None, name: str | None) -> tuple[str, str] | None:
		if reference_doctype == "CRM Organization":
			# a Prospect keeps its organization's name
			return ("Prospect", name) if name and frappe.db.exists("Prospect", name) else None
		names = {"CRM Lead": self.lead_names, "CRM Deal": self.deal_names}.get(reference_doctype)
		if names is None or not names.get(name):
			return None
		return DOCTYPE_MAP[reference_doctype], names[name]

	def load_names(self) -> None:
		"""The copies made by an earlier run, when this one resumes past the leads and deals."""
		for doctype, field, names in (
			("Lead", "crm_lead_ref", self.lead_names),
			("Opportunity", "crm_deal_ref", self.deal_names),
		):
			if not names:
				names.update(
					dict(
						frappe.get_all(
							doctype, filters={field: ("is", "set")}, fields=[field, "name"], as_list=True
						)
					)
				)

	def copy_notes(self) -> None:
		self.load_names()
		# a call's note becomes the call Event's description, not a note of its own
		call_notes = {row.note for row in rows_of("CRM Call Log") if row.note}
		copied = 0
		for index, row in enumerate(rows_of("FCRM Note"), start=1):
			target = self.target(row.reference_doctype, row.reference_docname)
			if row.name in call_notes or not target or frappe.db.exists("Comment", row.name):
				continue
			doc = frappe.get_doc(
				{
					"doctype": "Comment",
					"name": row.name,
					"comment_type": "Comment",
					"reference_doctype": target[0],
					"reference_name": target[1],
					"content": note_html(row.title, row.content),
					"comment_email": row.owner,
					"comment_by": get_fullname(row.owner),
				}
			)
			self.keep_times(doc, row)
			doc.db_insert()
			copied += 1
			if index % self.batch_size == 0:
				frappe.db.commit()  # nosemgrep
		self.report.counts["notes"]["copied"] = copied
		frappe.db.commit()  # nosemgrep

	def copy_calls(self) -> None:
		notes = {row.name: row.content for row in rows_of("FCRM Note")}
		copied = 0
		for index, row in enumerate(rows_of("CRM Call Log"), start=1):
			target = self.target(row.reference_doctype, row.reference_docname)
			if not target or frappe.db.exists("Event", row.name):
				continue
			starts = row.start_time or row.creation
			event = frappe.get_doc(
				{
					"doctype": "Event",
					"name": row.name,
					"subject": _("Call to {0}").format(row.get("to") or target[1]),
					"event_category": "Call",
					"event_type": "Private",
					"status": "Completed",
					"send_reminder": 0,
					"starts_on": starts,
					"ends_on": row.end_time if row.end_time and row.end_time != starts else None,
					"description": notes.get(row.note)
					or _("{0} call, {1}").format(row.type or "", row.status or "").strip(),
				}
			)
			event.owner = row.caller or row.receiver or row.owner
			self.keep_times(event, row, owner=event.owner)
			event.db_insert()
			frappe.get_doc(
				{
					"doctype": "Event Participants",
					"parent": event.name,
					"parenttype": "Event",
					"parentfield": "event_participants",
					"idx": 1,
					"reference_doctype": target[0],
					"reference_docname": target[1],
				}
			).db_insert()
			copied += 1
			if index % self.batch_size == 0:
				frappe.db.commit()  # nosemgrep
		self.report.counts["calls"]["copied"] = copied
		frappe.db.commit()  # nosemgrep

	def copy_tasks(self) -> None:
		copied = 0
		for index, row in enumerate(rows_of("CRM Task"), start=1):
			name = str(row.name)
			target = self.target(row.reference_doctype, row.reference_docname)
			if frappe.db.exists("ToDo", name):
				continue
			body = escape_html(row.title or _("Follow up"))
			if (row.description or "").strip():
				body += "<br><br>" + row.description
			todo = frappe.get_doc(
				{
					"doctype": "ToDo",
					"name": name,
					"status": TASK_STATUS.get(row.status, "Open"),
					"priority": row.priority if row.priority in ("High", "Medium", "Low") else "Medium",
					"date": getdate(row.due_date) if row.due_date else None,
					"allocated_to": row.assigned_to if row.assigned_to in self.users else None,
					"assigned_by": row.owner if row.owner in self.users else None,
					"description": body,
					"reference_type": target[0] if target else None,
					"reference_name": target[1] if target else None,
					**{
						field: row.get(field)
						for field in (
							"tbo_task_type",
							"tbo_sales_invoice",
							"tbo_promised_date",
							"tbo_promised_amount",
							"tbo_escalation_level",
						)
					},
				}
			)
			self.keep_times(todo, row)
			todo.db_insert()
			if todo.status == "Open" and target and todo.allocated_to:
				# the assignee can open the lead or deal, as with any open ToDo
				todo.update_in_reference()
			copied += 1
			if index % self.batch_size == 0:
				frappe.db.commit()  # nosemgrep
		self.report.counts["tasks"]["copied"] = copied
		frappe.db.commit()  # nosemgrep

	# ---------- the links other records hold ----------

	def relink(self) -> None:
		self.load_names()
		self.relink_quotations()
		self.relink_deal_fields()
		for old, new in (("CRM Lead", self.lead_names), ("CRM Deal", self.deal_names)):
			self.relink_references(old, DOCTYPE_MAP[old], new)
		self.relink_references("CRM Organization", "Prospect", {})
		self.link_customers_to_organizations()
		frappe.db.commit()  # nosemgrep

	def relink_quotations(self) -> None:
		"""Quotation To CRM Lead → Lead; CRM Deal → the deal's Customer, else its party; the deal
		(Frappe CRM's `crm_deal`, or the deal as party) → ERPNext's `opportunity`; the lead →
		`tbo_lead`."""
		Quotation = table("Quotation")
		columns = set(frappe.db.get_table_columns("Quotation"))
		fields = [
			Quotation.name,
			Quotation.quotation_to,
			Quotation.party_name,
			Quotation.opportunity,
			Quotation.tbo_lead,
		]
		if "crm_deal" in columns:
			fields.append(Quotation.crm_deal)
		if "tbo_crm_lead" in columns:
			fields.append(Quotation.tbo_crm_lead)
		rows = frappe.qb.from_(Quotation).select(*fields).run(as_dict=True)
		moved = 0
		for row in rows:
			values = {}
			deal_ref = row.get("crm_deal") or (row.party_name if row.quotation_to == "CRM Deal" else None)
			deal = self.deal_names.get(deal_ref) if deal_ref else None
			if deal and not row.opportunity:
				values["opportunity"] = deal
			if row.quotation_to == "CRM Lead" and self.lead_names.get(row.party_name):
				lead = self.lead_names[row.party_name]
				values.update(quotation_to="Lead", party_name=lead, tbo_lead=lead)
			elif row.quotation_to == "CRM Deal" and deal:
				opportunity = frappe.db.get_value(
					"Opportunity",
					deal,
					["tbo_customer", "opportunity_from", "party_name", "tbo_lead"],
					as_dict=True,
				)
				values["tbo_lead"] = opportunity.tbo_lead
				if opportunity.tbo_customer:
					values.update(quotation_to="Customer", party_name=opportunity.tbo_customer)
				else:
					values.update(
						quotation_to=opportunity.opportunity_from, party_name=opportunity.party_name
					)
			# a quotation already moved to a Customer keeps its CRM lead only in tbo_crm_lead,
			# which finalize() drops
			crm_lead = self.lead_names.get(row.get("tbo_crm_lead"))
			if crm_lead and not row.tbo_lead and not values.get("tbo_lead"):
				values["tbo_lead"] = crm_lead
			if values:
				frappe.db.set_value("Quotation", row.name, values, update_modified=False)
				moved += 1
		self.report.counts["quotations_relinked"] = moved

	def relink_deal_fields(self) -> None:
		"""Sales Order, Sales Invoice and Payment Entry `crm_deal` → `opportunity`; TBO records'
		deal and lead links when a name changed."""
		moved = 0
		for doctype in ("Sales Order", "Sales Invoice", "Payment Entry"):
			if "crm_deal" not in frappe.db.get_table_columns(doctype):
				continue
			Table = table(doctype)
			for name, deal in (
				frappe.qb.from_(Table)
				.select(Table.name, Table.crm_deal)
				.where(Table.crm_deal.isnotnull() & (Table.crm_deal != "") & is_blank(Table.opportunity))
			).run():
				if self.deal_names.get(deal):
					frappe.db.set_value(
						doctype, name, "opportunity", self.deal_names[deal], update_modified=False
					)
					moved += 1
		self.report.counts["finance_documents_relinked"] = moved
		# from every copy, not only this run's renames, so a resumed run still relinks
		for names, links in (
			(
				self.deal_names,
				(
					("TBO Finance Exception", "opportunity"),
					("TBO Fund Allocation", "opportunity"),
					("TBO Fund Allocation Line", "opportunity"),
					("TBO Quotation Adjustment", "deal"),
				),
			),
			(self.lead_names, (("TBO Lead Meeting", "lead"), ("TBO Lead Triage Row", "lead"))),
		):
			for old, new in names.items():
				if old == new:
					continue
				for doctype, field in links:
					frappe.db.set_value(doctype, {field: old}, field, new, update_modified=False)

	def relink_references(self, old: str, new: str, names: dict) -> None:
		"""Comments, ToDos (assignments), Files, Communications and Notification Logs on a CRM
		record now point at its copy."""
		for old_name, new_name in ((key, value) for key, value in names.items() if key != value):
			for doctype, type_field, name_field in REFERENCE_FIELDS:
				frappe.db.set_value(
					doctype,
					{type_field: old, name_field: old_name},
					name_field,
					new_name,
					update_modified=False,
				)
		for doctype, type_field, _name_field in REFERENCE_FIELDS:
			Table = table(doctype)
			frappe.qb.update(Table).set(Table[type_field], new).where(Table[type_field] == old).run()

	def link_customers_to_organizations(self) -> None:
		"""A deal's Customer belongs to its organization (`Customer.prospect_name`), when blank."""
		for row in rows_of("CRM Deal"):
			customer = row.get("erpnext_customer")
			if not (customer and row.organization and frappe.db.exists("Prospect", row.organization)):
				continue
			if frappe.db.exists("Customer", customer) and not frappe.db.get_value(
				"Customer", customer, "prospect_name"
			):
				frappe.db.set_value(
					"Customer", customer, "prospect_name", row.organization, update_modified=False
				)

	# ---------- helpers ----------

	def stage_logs(self, parenttype: str) -> dict[str, list]:
		rows = rows_of(
			"CRM Status Change Log", table("CRM Status Change Log").parenttype == parenttype, order_by="idx"
		)
		logs: dict[str, list] = {}
		for row in sorted(rows, key=lambda row: (row.parent, row.idx)):
			logs.setdefault(row.parent, []).append(row)
		return logs

	def write_stage_log(self, doctype: str, name: str, logs: list) -> None:
		"""The CRM's status history replaces the row the insert logged."""
		if not logs:
			return
		frappe.db.delete("TBO Stage Change", {"parenttype": doctype, "parent": name})
		previous = None
		for idx, log in enumerate(logs, start=1):
			frappe.get_doc(
				{
					"doctype": "TBO Stage Change",
					"parent": name,
					"parenttype": doctype,
					"parentfield": "tbo_stage_log",
					"idx": idx,
					"stage": log.get("from"),
					"previous_stage": previous,
					"changed_on": log.from_date,
					"changed_by": log.log_owner if log.log_owner in self.users else None,
				}
			).db_insert()
			previous = log.get("from")

	def free_name(self, doctype: str, name: str, ref_field: str) -> str | None:
		"""The source's name when it is free here, else None (a new name from the series)."""
		existing = frappe.db.get_value(doctype, name, ref_field)
		return None if existing is not None or frappe.db.exists(doctype, name) else name

	def insert(
		self, doctype: str, values: dict, source, set_name: str | None = None, email_check: bool = True
	) -> str:
		doc = frappe.new_doc(doctype)
		doc.update(values)
		doc.flags.update(
			{
				"ignore_permissions": True,
				"ignore_mandatory": True,
				"ignore_links": True,
				"ignore_email_validation": not email_check,
			}
		)
		doc.insert(set_name=set_name)
		frappe.db.set_value(
			doctype,
			doc.name,
			{
				"creation": source.creation,
				"modified": source.modified,
				"owner": source.owner if source.owner in self.users else "Administrator",
				"modified_by": source.modified_by if source.modified_by in self.users else "Administrator",
			},
			update_modified=False,
		)
		return doc.name

	def keep_times(self, doc, source, owner: str | None = None) -> None:
		doc.creation, doc.modified = source.creation, source.modified
		doc.owner = owner or (source.owner if source.owner in self.users else "Administrator")
		doc.modified_by = source.modified_by if source.modified_by in self.users else doc.owner

	def attempt(self, kind: str, name: str, step) -> bool:
		"""Copy one record; a failure is recorded and undone, and the run goes on. Returns
		whether it was copied."""
		savepoint = f"tbo_cutover_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(savepoint)
		try:
			step()
			return True
		except Exception as error:
			frappe.db.rollback(save_point=savepoint)
			self.report.failed.append({"kind": kind, "name": name, "error": str(error)[:300]})
			return False


# (doctype, the doctype field, the name field) of records that point at a CRM record
REFERENCE_FIELDS = (
	("Comment", "reference_doctype", "reference_name"),
	("ToDo", "reference_type", "reference_name"),
	("File", "attached_to_doctype", "attached_to_name"),
	("Communication", "reference_doctype", "reference_name"),
	("Communication Link", "link_doctype", "link_name"),
	("Notification Log", "document_type", "document_name"),
	("Event Participants", "reference_doctype", "reference_docname"),
)


def is_blank(field):
	"""The field is NULL or ""."""
	return field.isnull() | (field == "")


def ensure(doctype: str, name: str, values: dict) -> None:
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)


def write_report(report: dict) -> str:
	folder = frappe.get_site_path("private", "files", REPORT_FOLDER)
	os.makedirs(folder, exist_ok=True)
	path = os.path.join(folder, f"report-{now_datetime().strftime('%Y%m%d-%H%M%S')}.json")
	with open(path, "w") as file:  # the site's own private folder and a timestamp name - nosemgrep
		json.dump(report, file, indent=1, default=str)
	return path


def print_report(report: dict) -> None:
	print(("DRY RUN: nothing written" if report.get("dry_run") else "Copied") + f" ({report.get('started')})")
	for kind, counts in report.get("counts", {}).items():
		print(f"  {kind}: {counts}")
	for problem, value in report.get("problems", {}).items():
		print(f"  {problem}: {len(value) if isinstance(value, list) else value}")
	if report.get("renamed"):
		print(f"  renamed: {len(report['renamed'])}")
	if report.get("failed"):
		print(f"  FAILED: {len(report['failed'])} (first: {report['failed'][0]})")
	if report.get("path"):
		print(f"  report: {report['path']}")


# ---------- the commands ----------


def run(dry_run: bool = True, batch_size: int = BATCH_SIZE) -> dict:
	"""Copy (or, with dry_run, only count) the CRM's records into ERPNext's."""
	return Cutover(dry_run=dry_run, batch_size=batch_size).run()


def verify(sample: int = SAMPLE_SIZE) -> dict:
	"""Compare the counts and `sample` random leads and deals field by field, and look for links
	still pointing at CRM doctypes. Prints PASS or the differences."""
	if not crm_tables_exist():
		frappe.throw(_("There are no Frappe CRM tables on this site to compare with."))
	differences = []
	leads = dict(
		frappe.get_all(
			"Lead", filters={"crm_lead_ref": ("is", "set")}, fields=["crm_lead_ref", "name"], as_list=True
		)
	)
	deals = dict(
		frappe.get_all(
			"Opportunity",
			filters={"crm_deal_ref": ("is", "set")},
			fields=["crm_deal_ref", "name"],
			as_list=True,
		)
	)
	call_notes = {row.note for row in rows_of("CRM Call Log") if row.note}
	for label, source, copied in (
		("leads", frappe.db.count("CRM Lead"), len(leads)),
		("deals", frappe.db.count("CRM Deal"), len(deals)),
		(
			"notes",
			len(
				[
					row
					for row in rows_of("FCRM Note")
					if row.name not in call_notes and row.reference_doctype in DOCTYPE_MAP
				]
			),
			frappe.db.count("Comment", {"name": ("in", [row.name for row in rows_of("FCRM Note")] or [""])}),
		),
		(
			"calls",
			len(
				[row for row in rows_of("CRM Call Log") if row.reference_doctype in ("CRM Lead", "CRM Deal")]
			),
			frappe.db.count("Event", {"name": ("in", [row.name for row in rows_of("CRM Call Log")] or [""])}),
		),
		(
			"tasks",
			frappe.db.count("CRM Task"),
			frappe.db.count("ToDo", {"name": ("in", [str(row.name) for row in rows_of("CRM Task")] or [""])}),
		),
	):
		if source != copied:
			differences.append(f"{label}: {source} in the CRM, {copied} copied")
	differences += sample_differences("CRM Lead", "Lead", leads, LEAD_CHECKS, sample)
	differences += sample_differences("CRM Deal", "Opportunity", deals, DEAL_CHECKS, sample)
	failures, orphans = dangling_links()
	differences += failures
	result = {"pass": not differences, "differences": differences, "orphans": orphans}
	print("PASS" if result["pass"] else "DIFFERENCES:\n  " + "\n  ".join(differences))
	if orphans:
		print("Already broken before the cutover (not counted):\n  " + "\n  ".join(orphans))
	return result


# (CRM field, copy field) compared on the samples
LEAD_CHECKS = (
	("lead_name", "lead_name"),
	("lead_owner", "lead_owner"),
	("organization", "company_name"),
	("mobile_no", "mobile_no"),
	("lead_score", "tbo_score"),
)
DEAL_CHECKS = (("deal_owner", "opportunity_owner"), ("status", "sales_stage"), ("next_step", "tbo_next_step"))


def sample_differences(
	source_doctype: str, doctype: str, names: dict, checks: tuple, sample: int
) -> list[str]:
	"""Field differences on `sample` random copies, and their open tasks against the CRM's."""
	picked = random.sample(sorted(names), min(sample, len(names)))
	found = []
	for source in picked:
		row = (
			frappe.qb.from_(table(source_doctype))
			.select("*")
			.where(table(source_doctype).name == source)
			.run(as_dict=True)[0]
		)
		name = names[source]
		copy = frappe.db.get_value(doctype, name, ["*"], as_dict=True)
		for source_field, field in checks:
			wanted = row.get(source_field) or None
			if source_field.endswith("owner") and wanted and not frappe.db.exists("User", wanted):
				# an owner who is no user here was left blank on purpose
				wanted = None
			if wanted != (copy.get(field) or None):
				found.append(f"{doctype} {name}: {field} {copy.get(field)!r}, CRM {row.get(source_field)!r}")
		if doctype == "Lead" and row.status in STATUS_NAMES and copy.tbo_stage != row.status:
			found.append(f"Lead {name}: stage {copy.tbo_stage!r}, CRM {row.status!r}")
		tasks = frappe.get_all(
			"CRM Task",
			filters={"reference_docname": source, "status": ("not in", ("Done", "Canceled"))},
			pluck="name",
		)
		copied = frappe.db.count(
			"ToDo", {"name": ("in", [str(task) for task in tasks] or [""]), "status": "Open"}
		)
		if copied != len(tasks):
			found.append(f"{doctype} {name}: {copied} open tasks, CRM {len(tasks)}")
	return found


def dangling_links() -> tuple[list[str], list[str]]:
	"""(failures, orphans): records still pointing at a CRM record that has a copy to point at,
	which the cutover should have moved; and records pointing at a CRM record that no longer
	exists, broken before the cutover (listed, not failed)."""
	sources = {
		"CRM Lead": set(rows_names("CRM Lead")),
		"CRM Deal": set(rows_names("CRM Deal")),
		"CRM Organization": set(rows_names("CRM Organization")),
	}
	failures, orphans = [], []

	def tally(label: str, pairs) -> None:
		moved = sum(1 for doctype, name in pairs if name in sources.get(doctype, ()))
		broken = len(pairs) - moved
		if moved:
			failures.append(f"{label}: {moved} still pointing at a CRM record")
		if broken:
			orphans.append(f"{label}: {broken} pointing at a CRM record that no longer exists")

	for doctype, type_field, name_field in (("Quotation", "quotation_to", "party_name"), *REFERENCE_FIELDS):
		rows = frappe.get_all(
			doctype,
			filters={type_field: ("in", list(DOCTYPE_MAP))},
			fields=[type_field, name_field],
			as_list=True,
		)
		tally(doctype, rows)
	for doctype in ("Sales Order", "Sales Invoice", "Payment Entry"):
		if "crm_deal" not in frappe.db.get_table_columns(doctype):
			continue
		Table = table(doctype)
		deals = (
			frappe.qb.from_(Table)
			.select(Table.crm_deal)
			.where(Table.crm_deal.isnotnull() & (Table.crm_deal != "") & is_blank(Table.opportunity))
		).run(pluck=True)
		tally(f"{doctype} deal", [("CRM Deal", deal) for deal in deals])
	for doctype, field, target, source in (
		("TBO Lead Meeting", "lead", "Lead", "CRM Lead"),
		("TBO Lead Triage Row", "lead", "Lead", "CRM Lead"),
		("TBO Quotation Adjustment", "deal", "Opportunity", "CRM Deal"),
		("TBO Finance Exception", "opportunity", "Opportunity", "CRM Deal"),
		("TBO Fund Allocation", "opportunity", "Opportunity", "CRM Deal"),
	):
		Table, Target = table(doctype), table(target)
		missing = (
			frappe.qb.from_(Table)
			.left_join(Target)
			.on(Target.name == Table[field])
			.select(Table[field])
			.where(Table[field].isnotnull() & (Table[field] != "") & Target.name.isnull())
		).run(pluck=True)
		tally(f"{doctype} {field}", [(source, name) for name in missing])
	return failures, orphans


def rows_names(doctype: str) -> list[str]:
	return frappe.qb.from_(table(doctype)).select(table(doctype).name).run(pluck=True)


def finalize(force: bool = False) -> dict:
	"""Before the CRM app is uninstalled: verify, then delete tbo_smart's own custom fields that
	still link to CRM doctypes, put Quotation To back to ERPNext's own parties, and list any other
	custom field or property setter that names a CRM doctype."""
	if not cint(force) and not verify()["pass"]:
		frappe.throw(_("verify() found differences: fix them (or pass force=1) before finalize."))
	removed = []
	for doctype, fieldname in OLD_FIELDS:
		name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
			removed.append(f"{doctype}.{fieldname}")
	setter = "Quotation-quotation_to-link_filters"
	if frappe.db.exists("Property Setter", setter):
		frappe.db.set_value(
			"Property Setter",
			setter,
			"value",
			json.dumps([["DocType", "name", "in", NATIVE_QUOTATION_PARTIES]]),
		)
		frappe.clear_cache(doctype="Quotation")
	# the CRM app's doctypes are in its FCRM module
	crm_doctypes = frappe.get_all("DocType", filters={"module": "FCRM"}, pluck="name") or list(DOCTYPE_MAP)
	remaining = [
		f"Custom Field {row.dt}.{row.fieldname} → {row.options}"
		for row in frappe.get_all(
			"Custom Field", filters={"options": ("in", crm_doctypes)}, fields=["dt", "fieldname", "options"]
		)
	]
	frappe.db.commit()  # nosemgrep
	result = {"removed": removed, "still_naming_crm": remaining}
	print(f"Removed: {removed or 'nothing'}")
	print(
		"Still naming CRM doctypes:\n  " + "\n  ".join(remaining)
		if remaining
		else "Nothing else names a CRM doctype."
	)
	return result
