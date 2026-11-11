"""Handing a department's leads to its people by hand (docs/call-batches.md §12): the hub's Assign
leads page lists a member's open leads and the unassigned ones, gives leads to a member (picked, or
the next N of a product) and takes them back to the unassigned pool.

The same people as the call plans may (`plans.planned_department`): Sales Managers and System
Managers, and a department's heads for it. A lead changes hands as the batches give one: the owner
is set and the person gets it as a ToDo assignment; the previous owner's assignment closes and the
lead leaves their running batch if they had not called it yet.
"""

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Coalesce, Count
from frappe.utils import cint

from tbo_smart.sales import products, stale
from tbo_smart.sales.call_batches import BATCH, ITEM, assign_undated
from tbo_smart.sales.call_batches.plans import planned_department

MAX_LEADS = 500
HANDED_NOTE = "Assigned to you on Assign leads"
MAX_PAGE = 100


def has_phone(Lead):
	return (
		(Coalesce(Lead.mobile_no, "") != "")
		| (Coalesce(Lead.phone, "") != "")
		| (Coalesce(Lead.whatsapp_no, "") != "")
	)


def department_leads(
	department: str,
	owner: str | None = None,
	product: str | None = None,
	search: str | None = None,
	start=0,
	page_length=50,
) -> dict:
	"""A page of the department's open leads: `owner` a member's (blank: the unassigned ones),
	optionally one `product` and a `search` on the name, company or phone. {rows: [{name,
	lead_name, company_name, product, stage, temperature, phone, modified}], total, products:
	{product ("" for none): leads}} (the counts ignore the product and search)."""
	doc = planned_department(department)
	owner = (owner or "").strip()
	if owner and owner not in {row.user for row in doc.members}:
		frappe.throw(_("{0} is not a member of {1}.").format(owner, department))
	Lead = frappe.qb.DocType("Lead")
	base = (
		frappe.qb.from_(Lead)
		.where(Lead.tbo_department == department)
		.where(Lead.tbo_stage.isin(list(stale.STALE_STAGES)))
		.where((Lead.lead_owner == owner) if owner else (Lead.lead_owner.isnull() | (Lead.lead_owner == "")))
	)
	has_product = frappe.db.has_column("Lead", "tbo_product")
	counts = {}
	if has_product:
		counts = {
			(name or ""): total
			for name, total in base.select(Lead.tbo_product, Count(Lead.name)).groupby(Lead.tbo_product).run()
		}
	query = base
	if product and has_product:
		query = query.where(Coalesce(Lead.tbo_product, "") == ("" if product == "-" else product))
	text = " ".join(str(search or "").split())
	if text:
		like = f"%{text}%"
		query = query.where(
			Lead.lead_name.like(like) | Lead.company_name.like(like) | Lead.mobile_no.like(like)
		)
	total = query.select(Count(Lead.name)).run()[0][0]
	fields = [
		Lead.name,
		Lead.lead_name,
		Lead.company_name,
		Lead.tbo_stage,
		Lead.tbo_temperature,
		Coalesce(Lead.mobile_no, Lead.phone, Lead.whatsapp_no).as_("phone"),
		Lead.modified,
	]
	if has_product:
		fields.append(Lead.tbo_product)
	rows = (
		query.select(*fields)
		.orderby(Lead.modified, order=frappe.qb.desc)
		.offset(max(cint(start), 0))
		.limit(min(max(cint(page_length) or 50, 1), MAX_PAGE))
	).run(as_dict=True)
	return {
		"rows": [
			{
				"name": row.name,
				"lead_name": row.lead_name,
				"company_name": row.company_name,
				"product": row.get("tbo_product") or None,
				"stage": row.tbo_stage,
				"temperature": row.tbo_temperature,
				"phone": row.phone,
				"modified": row.modified,
			}
			for row in rows
		],
		"total": total,
		"products": counts,
	}


def assign_leads(department: str, user: str, leads=None, count=0, product: str | None = None) -> dict:
	"""Give `user` (a member) the `leads` named, or else the next `count` unassigned open leads
	with a phone (of `product` when given), hot first, then newest. A lead owned by someone else
	moves to `user`. {assigned, skipped: [{name, reason}]}."""
	doc = planned_department(department)
	if user not in {row.user for row in doc.members}:
		frappe.throw(
			_("{0} is not a member of {1}. Add them to the department first.").format(user, department)
		)
	names = list(dict.fromkeys(frappe.parse_json(leads) if isinstance(leads, str) else leads or []))
	skipped = []
	if names:
		check_size(len(names))
		found = {
			row.name: row
			for row in frappe.get_all(
				"Lead",
				filters={"name": ("in", names)},
				fields=["name", "tbo_department", "lead_owner"],
			)
		}
		chosen = []
		for name in names:
			row = found.get(name)
			if not row or row.tbo_department != department:
				skipped.append({"name": name, "reason": _("not in {0}").format(department)})
			elif row.lead_owner == user:
				skipped.append({"name": name, "reason": _("already theirs")})
			else:
				chosen.append(name)
	else:
		count = cint(count)
		if count <= 0:
			frappe.throw(_("Pick the leads, or say how many to give."))
		check_size(count)
		chosen = next_unassigned(doc, count, clean_product(doc, product))
	for name in chosen:
		hand(name, user)
	return {"assigned": len(chosen), "skipped": skipped}


def unassign_leads(department: str, leads) -> dict:
	"""Take the `leads` back to the department's unassigned pool: {unassigned, skipped}."""
	planned_department(department)
	names = list(dict.fromkeys(frappe.parse_json(leads) if isinstance(leads, str) else leads or []))
	if not names:
		frappe.throw(_("Pick the leads first."))
	check_size(len(names))
	found = {
		row.name: row
		for row in frappe.get_all(
			"Lead", filters={"name": ("in", names)}, fields=["name", "tbo_department", "lead_owner"]
		)
	}
	done, skipped = 0, []
	for name in names:
		row = found.get(name)
		if not row or row.tbo_department != department:
			skipped.append({"name": name, "reason": _("not in {0}").format(department)})
		elif not row.lead_owner:
			skipped.append({"name": name, "reason": _("not assigned")})
		else:
			hand(name, None)
			done += 1
	return {"unassigned": done, "skipped": skipped}


def check_size(size: int) -> None:
	if size > MAX_LEADS:
		frappe.throw(_("At most {0} leads at a time.").format(MAX_LEADS))


def clean_product(department, product: str | None) -> str:
	wanted = " ".join(str(product or "").split())
	if not wanted:
		return ""
	chosen = products.find(department.name, wanted)
	if not chosen:
		frappe.throw(_("{0} is not an active product of {1}.").format(wanted, department.name))
	return chosen


def next_unassigned(department, count: int, product: str) -> list[str]:
	"""The next `count` unassigned open leads with a phone, rows locked (SKIP LOCKED) so the
	batches pulling at the same moment never take the same one."""
	Lead = frappe.qb.DocType("Lead")
	query = (
		frappe.qb.from_(Lead)
		.select(Lead.name)
		.where(
			(Lead.tbo_department == department.name)
			& (Lead.lead_owner.isnull() | (Lead.lead_owner == ""))
			& Lead.tbo_stage.isin(list(stale.STALE_STAGES))
			& has_phone(Lead)
		)
		.orderby(Case().when(Lead.tbo_temperature == "Hot", 0).else_(1))
		.orderby(Lead.creation, order=frappe.qb.desc)
		.limit(count)
	)
	if product:
		query = query.where(Lead.tbo_product == product)
	return [name for (name,) in query.for_update(skip_locked=True).run()]


def hand(lead: str, user: str | None, note: str = HANDED_NOTE) -> None:
	"""`lead` to `user` (None: nobody). The previous owner's open assignment closes and the lead
	leaves their running batch where it is still waiting to be called. `note` is the assignment's
	text: by hand on Assign leads, or the daily auto-assign (auto_assign.AUTO_NOTE)."""
	doc = frappe.get_doc("Lead", lead)
	previous = doc.lead_owner
	doc.lead_owner = user
	doc.save(ignore_permissions=True)
	if previous and previous != user:
		close_assignments(lead, previous)
		drop_from_batches(lead, previous)
	if user:
		assign_undated(lead, user, note)


def close_assignments(lead: str, user: str) -> None:
	"""`user`'s open ToDos on `lead` are cancelled. Saving each ToDo also takes `user` off the
	lead's assigned list. Not `assign_to.remove`: its arguments differ between Frappe versions
	(the live site's has no `ignore_permissions`)."""
	for name in frappe.get_all(
		"ToDo",
		filters={"reference_type": "Lead", "reference_name": lead, "allocated_to": user, "status": "Open"},
		pluck="name",
	):
		todo = frappe.get_doc("ToDo", name)
		todo.status = "Cancelled"
		todo.save(ignore_permissions=True)


def drop_from_batches(lead: str, user: str) -> None:
	"""`user`'s calls of `lead` that were never made go: those waiting in a running batch and the
	overdue ones of ended batches, so the lead leaves their Today's calls and Overdue lists. Calls
	already made stay (the day's report counts them)."""
	Batch, Item = frappe.qb.DocType(BATCH), frappe.qb.DocType(ITEM)
	items = (
		frappe.qb.from_(Item)
		.join(Batch)
		.on(Batch.name == Item.parent)
		.select(Item.name)
		.where((Batch.user == user) & (Item.lead == lead) & (Coalesce(Item.outcome, "") == ""))
	).run(pluck=True)
	if items:
		frappe.db.delete(ITEM, {"name": ("in", items)})
