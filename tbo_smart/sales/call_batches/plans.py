"""Call plans (docs/call-batches.md §11): a manager or a department head decides, per member, which
product they are handed and how many calls each slot (Morning 50, Afternoon 50).

The plan sits on the member's row of the department (`call_product`, `slot_sizes`). The clock
and the next batch read it: a slot's size becomes the member's own, the product narrows the new
leads like a focus that the person cannot change, and the day's cap grows to the plan's total.
A member without a plan is handed batches as before.
"""

import json

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce, Count
from frappe.utils import cint

from tbo_smart.sales import departments as lead_departments
from tbo_smart.sales import products
from tbo_smart.sales.call_batches import BATCH, ITEM, batch_departments, slots_of

MAX_SLOT_CALLS = 500


def member_row(department, user: str):
	return next((row for row in department.get("members") or [] if row.user == user), None)


def sizes_of(row) -> dict[str, int]:
	"""{slot label: calls} the member's plan sets (empty: no plan for the slots)."""
	value = row.get("slot_sizes") if row else None
	if isinstance(value, str):
		try:
			value = json.loads(value or "{}")
		except ValueError:
			value = {}
	if not isinstance(value, dict):
		return {}
	return {str(label): max(cint(calls), 0) for label, calls in value.items()}


def has_plan(department, user: str) -> bool:
	row = member_row(department, user)
	return bool(row and (sizes_of(row) or (row.get("call_product") or "").strip()))


def slot_size(department, user: str, slot) -> int:
	"""The calls `user` is handed in `slot`: their plan's number for it, else the slot's size."""
	sizes = sizes_of(member_row(department, user))
	return sizes.get(slot.label, slot.size)


def plan_total(department, user: str) -> int:
	"""The calls a day the plan hands `user` (0: no plan for the slots)."""
	sizes = sizes_of(member_row(department, user))
	if not sizes:
		return 0
	return sum(sizes.get(slot.label, slot.size) for slot in slots_of(department))


def plan_product(department, user: str) -> str:
	"""The product the member's plan hands them, if it is still an active product of the
	department; else ""."""
	row = member_row(department, user)
	wanted = (row.get("call_product") or "").strip() if row else ""
	return products.find(department.name, wanted) or ""


# ---------- the managers' page ----------


def managed_departments() -> list:
	"""Departments with batches on that the session user plans: every one for a Sales Manager or
	System Manager, else those they head. PermissionError for anyone else."""
	departments = batch_departments()
	if lead_departments.can_manage():
		return departments
	headed = set(
		frappe.get_all(
			lead_departments.DEPARTMENT_USER,
			filters={
				"user": frappe.session.user,
				"parentfield": "heads",
				"parenttype": lead_departments.DEPARTMENT,
			},
			pluck="parent",
		)
	)
	if not headed:
		frappe.throw(_("Only a department head or a Sales Manager may assign leads."), frappe.PermissionError)
	return [department for department in departments if department.name in headed]


def planned_department(department: str):
	"""The department, when the session user plans it (`managed_departments`); else
	PermissionError."""
	if department not in {d.name for d in managed_departments()}:
		frappe.throw(
			_("You cannot assign leads in {0}: it is not one of your departments with daily calls.").format(
				department
			),
			frappe.PermissionError,
		)
	return frappe.get_doc(lead_departments.DEPARTMENT, department)


def owned_leads(department: str) -> dict[str, int]:
	"""{user: open leads they own} in the department."""
	from tbo_smart.sales import stale

	Lead = frappe.qb.DocType("Lead")
	rows = (
		frappe.qb.from_(Lead)
		.select(Lead.lead_owner, Count(Lead.name))
		.where(
			(Lead.tbo_department == department)
			& Lead.tbo_stage.isin(list(stale.STALE_STAGES))
			& Lead.lead_owner.isnotnull()
			& (Lead.lead_owner != "")
		)
		.groupby(Lead.lead_owner)
	).run()
	return dict(rows)


def handed_today(department: str, day) -> dict[str, dict]:
	"""{user: {size, called}} of the department's batches on `day`, retries not counted."""
	Batch, Item = frappe.qb.DocType(BATCH), frappe.qb.DocType(ITEM)
	rows = (
		frappe.qb.from_(Item)
		.join(Batch)
		.on(Batch.name == Item.parent)
		.select(Batch.user, Item.outcome)
		.where((Batch.department == department) & (Batch.batch_date == day) & (Item.is_repeat == 0))
	).run(as_dict=True)
	counts: dict[str, dict] = {}
	for row in rows:
		person = counts.setdefault(row.user, {"size": 0, "called": 0})
		person["size"] += 1
		person["called"] += 1 if row.outcome else 0
	return counts


def unassigned_by_product(department: str) -> dict[str, int]:
	"""{product ("" for none): open unassigned leads with a phone} in the department: what the
	batches can still pull."""
	from tbo_smart.sales import stale

	if not frappe.db.has_column("Lead", "tbo_product"):
		return {}
	Lead = frappe.qb.DocType("Lead")
	rows = (
		frappe.qb.from_(Lead)
		.select(Lead.tbo_product)
		.where(
			(Lead.tbo_department == department)
			& (Lead.lead_owner.isnull() | (Lead.lead_owner == ""))
			& Lead.tbo_stage.isin(list(stale.STALE_STAGES))
			& (
				(Coalesce(Lead.mobile_no, "") != "")
				| (Coalesce(Lead.phone, "") != "")
				| (Coalesce(Lead.whatsapp_no, "") != "")
			)
		)
	).run()
	counts: dict[str, int] = {}
	for (product,) in rows:
		key = (product or "").strip()
		counts[key] = counts.get(key, 0) + 1
	return counts


def serialize(department, day) -> dict:
	"""{department, slots: [{label, start_time, end_time, size}], products: [{product_name,
	intro_line}], campaign_product, pull_unassigned, unassigned: {product: leads}, members:
	[{user, full_name, product, sizes: {slot: calls}, total, has_plan, today: {size, called},
	leads (open leads they own)}]}."""
	from tbo_smart.sales.call_batches.settings import serialize as rules

	slots = rules(department)["call_slots"]
	today = handed_today(department.name, day)
	owned = owned_leads(department.name)
	members = []
	for row in department.members:
		sizes = sizes_of(row)
		members.append(
			{
				"user": row.user,
				"full_name": row.full_name or frappe.utils.get_fullname(row.user),
				"product": plan_product(department, row.user) or None,
				"sizes": {slot["label"]: sizes.get(slot["label"], slot["size"]) for slot in slots},
				"total": sum(sizes.get(slot["label"], slot["size"]) for slot in slots),
				"has_plan": has_plan(department, row.user),
				"today": today.get(row.user, {"size": 0, "called": 0}),
				"leads": owned.get(row.user, 0),
			}
		)
	return {
		"department": department.name,
		"slots": slots,
		"products": [
			{"product_name": row["product_name"], "intro_line": row["intro_line"]}
			for row in products.active_serialized(department.name)
		],
		"campaign_product": department.campaign_product or None,
		"pull_unassigned": cint(department.pull_unassigned),
		"unassigned": unassigned_by_product(department.name),
		"members": members,
	}


def get_call_plans() -> dict:
	"""{date, departments: [serialize(...)]} for the departments the session user plans."""
	day = frappe.utils.getdate()
	return {"date": str(day), "departments": [serialize(d, day) for d in managed_departments()]}


def save_call_plan(department: str, users, product: str | None = None, sizes=None, clear=False) -> dict:
	"""Set the plan of `users` (members of `department`): the `product` they are handed (blank:
	any of the department's) and `sizes` ({slot label: calls}). `clear` removes their plan.
	Returns the department as get_call_plans lists it. Batches already handed out are kept; the
	plan counts from the next slot (or the next batch asked for)."""
	doc = planned_department(department)
	users = list(dict.fromkeys(frappe.parse_json(users) if isinstance(users, str) else users or []))
	if not users:
		frappe.throw(_("Pick the people to assign leads to."))
	rows = {row.user: row for row in doc.members}
	outsiders = [user for user in users if user not in rows]
	if outsiders:
		frappe.throw(
			_("{0} is not a member of {1}. Add them to the department first.").format(
				", ".join(frappe.utils.get_fullname(user) for user in outsiders), department
			)
		)
	if cint(clear):
		for user in users:
			rows[user].call_product = None
			rows[user].slot_sizes = None
	else:
		chosen = clean_product(doc, product)
		planned = clean_sizes(doc, sizes)
		for user in users:
			rows[user].call_product = chosen or None
			rows[user].slot_sizes = json.dumps(planned) if planned else None
	doc.save(ignore_permissions=True)  # a head plans their department without editing it
	return serialize(doc, frappe.utils.getdate())


def clean_product(department, product: str | None) -> str:
	wanted = " ".join(str(product or "").split())
	if not wanted:
		return ""
	chosen = products.find(department.name, wanted)
	if not chosen:
		frappe.throw(_("{0} is not an active product of {1}.").format(wanted, department.name))
	locked = (department.campaign_product or "").strip()
	if locked and locked.lower() != chosen.lower():
		frappe.throw(_("{0} calls only for {1} (its campaign product).").format(department.name, locked))
	return chosen


def clean_sizes(department, sizes) -> dict[str, int]:
	"""{slot label: calls} for the department's own slots, each 0 to MAX_SLOT_CALLS."""
	sizes = frappe.parse_json(sizes) if isinstance(sizes, str) else sizes or {}
	if not isinstance(sizes, dict):
		frappe.throw(_("Give the calls per slot."))
	labels = {slot.label for slot in slots_of(department)}
	unknown = sorted(set(sizes) - labels)
	if unknown:
		frappe.throw(_("{0} is not a call slot of {1}.").format(", ".join(unknown), department.name))
	cleaned = {}
	for label, calls in sizes.items():
		calls = cint(calls)
		if calls < 0 or calls > MAX_SLOT_CALLS:
			frappe.throw(_("Calls per slot must be between 0 and {0}.").format(MAX_SLOT_CALLS))
		cleaned[label] = calls
	return cleaned
