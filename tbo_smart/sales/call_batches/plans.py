"""Call plans (docs/call-batches.md §11): a manager or a department head decides, per member, which
product they are handed and how many calls each slot (Morning 40, Afternoon 30, Evening 30).

The plan sits on the member's row of the department (`call_product`, `call_industry`,
`call_district`, `slot_sizes` and, when a slot is split by district, `slot_districts`). The clock and the next batch read it: a slot's size becomes the
member's own, the product, industry and district narrow the new leads like a focus that the person
cannot change, and the day's cap grows to the plan's total.
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
from tbo_smart.sales.call_batches.focus import clean

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


def slot_split(row) -> dict[str, list[tuple[str, int]]]:
	"""{slot label: [(district, calls)]} the member's plan splits by district ("" is Mixed: any
	district), the named districts first and Mixed last, so Mixed never takes the leads a named
	district of the slot is waiting for. Empty: no slot is split."""
	value = row.get("slot_districts") if row else None
	if isinstance(value, str):
		try:
			value = json.loads(value or "{}")
		except ValueError:
			value = {}
	if not isinstance(value, dict):
		return {}
	split = {}
	for label, rows in value.items():
		shares = [
			(clean(share.get("district")), max(cint(share.get("calls")), 0))
			for share in (rows if isinstance(rows, list) else [])
			if isinstance(share, dict)
		]
		split[str(label)] = sorted(
			(share for share in shares if share[1] > 0), key=lambda share: share[0] == ""
		)
	return split


def slot_shares(department, user: str, slot) -> list[tuple[str, int]] | None:
	"""[(district, calls)] the member's plan splits `slot` into ("" is Mixed); None when the slot
	is not split, so it is filled as one share of the plan's district."""
	return slot_split(member_row(department, user)).get(slot.label)


def day_shares(department, user: str) -> list[tuple[str, int]]:
	"""[(district, calls a day)] of the member's plan over every slot ("" is Mixed: any district,
	or the plan's own district when its slots are not split), named districts first. Empty: no
	plan for the slots."""
	row = member_row(department, user)
	if not sizes_of(row):
		return []
	split = slot_split(row)
	whole = plan_district(department, user)
	totals: dict[str, int] = {}
	names: dict[str, str] = {}
	for slot in slots_of(department):
		shares = split.get(slot.label)
		if shares is None:
			shares = [(whole, slot_size(department, user, slot))]
		for district, calls in shares:
			key = district.casefold()
			names.setdefault(key, district)
			totals[key] = totals.get(key, 0) + calls
	return sorted(
		((names[key], calls) for key, calls in totals.items() if calls > 0), key=lambda share: share[0] == ""
	)


def has_plan(department, user: str) -> bool:
	row = member_row(department, user)
	return bool(
		row
		and (
			sizes_of(row)
			or slot_split(row)
			or (row.get("call_product") or "").strip()
			or (row.get("call_industry") or "").strip()
			or (row.get("call_district") or "").strip()
		)
	)


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


def plan_industry(department, user: str) -> str:
	"""The industry the member's plan calls for (a department without products plans by
	industry); "" for any."""
	row = member_row(department, user)
	return (row.get("call_industry") or "").strip() if row else ""


def plan_district(department, user: str) -> str:
	"""The district the member's plan calls for; "" for any."""
	row = member_row(department, user)
	return (row.get("call_district") or "").strip() if row else ""


# ---------- the managers' page ----------


EDIT, VIEW = "edit", "view"


def department_access() -> dict[str, str]:
	"""{department: EDIT or VIEW} for the departments with batches on that the session user may
	open on Assign leads. A head plans their department; a viewer only watches it, even a Sales
	Manager listed there (they asked to be kept from changing it); any other Sales Manager or
	System Manager plans every one."""
	listed = {
		(row.parent, row.parentfield)
		for row in frappe.get_all(
			lead_departments.DEPARTMENT_USER,
			filters={
				"user": frappe.session.user,
				"parentfield": ("in", ("heads", "viewers")),
				"parenttype": lead_departments.DEPARTMENT,
			},
			fields=["parent", "parentfield"],
		)
	}
	manager = lead_departments.can_manage()
	access = {}
	for department in batch_departments():
		if (department.name, "heads") in listed:
			access[department.name] = EDIT
		elif (department.name, "viewers") in listed:
			access[department.name] = VIEW
		elif manager:
			access[department.name] = EDIT
	return access


def visible_departments() -> list:
	"""(department, can_edit) for the departments the session user may open on Assign leads.
	PermissionError for anyone else."""
	access = department_access()
	if not access:
		frappe.throw(_("Only a department head or a Sales Manager may assign leads."), frappe.PermissionError)
	return [(d, access[d.name] == EDIT) for d in batch_departments() if d.name in access]


def managed_departments() -> list:
	"""Departments with batches on that the session user plans (department_access EDIT)."""
	return [department for department, can_edit in visible_departments() if can_edit]


def planned_department(department: str, view: bool = False):
	"""The department, when the session user plans it (`view`: or only watches it); else
	PermissionError."""
	access = department_access().get(department)
	if access == VIEW and not view:
		frappe.throw(
			_("You can only view {0} on Assign leads. Ask a Sales Manager to change it.").format(department),
			frappe.PermissionError,
		)
	if not access:
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


def department_industries(department: str) -> list[str]:
	"""The industries the department's open leads are in, by name."""
	from tbo_smart.sales import stale

	Lead = frappe.qb.DocType("Lead")
	return [
		industry
		for (industry,) in (
			frappe.qb.from_(Lead)
			.select(Lead.industry)
			.distinct()
			.where(
				(Lead.tbo_department == department)
				& Lead.tbo_stage.isin(list(stale.STALE_STAGES))
				& (Coalesce(Lead.industry, "") != "")
			)
			.orderby(Lead.industry)
		).run()
	]


def unassigned_by_industry(department: str) -> dict[str, int]:
	"""{industry ("" for none): open unassigned leads with a phone} in the department."""
	from tbo_smart.sales import stale

	Lead = frappe.qb.DocType("Lead")
	counts: dict[str, int] = {}
	for (industry,) in (
		frappe.qb.from_(Lead)
		.select(Lead.industry)
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
	).run():
		key = (industry or "").strip()
		counts[key] = counts.get(key, 0) + 1
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


def department_districts(department: str) -> dict:
	"""{districts: the districts the department's open leads are in, by name; unassigned:
	{district ("" for none): {products: {product: leads}, industries: {industry: leads}}}, its
	open unassigned leads with a phone}. Spellings that differ only in case or spacing are one
	district. Empty while leads have no district."""
	from tbo_smart.sales import stale
	from tbo_smart.sales.call_batches.handout import has_phone

	if not frappe.db.has_column("Lead", "tbo_district"):
		return {"districts": [], "unassigned": {}}
	Lead = frappe.qb.DocType("Lead")
	open_leads = (Lead.tbo_department == department) & Lead.tbo_stage.isin(list(stale.STALE_STAGES))
	names: dict[str, str] = {}
	for (district,) in frappe.qb.from_(Lead).select(Lead.tbo_district).distinct().where(open_leads).run():
		names.setdefault(clean(district).casefold(), clean(district))
	fields = [Lead.tbo_district, Lead.industry]
	if frappe.db.has_column("Lead", "tbo_product"):
		fields.append(Lead.tbo_product)
	unassigned: dict[str, dict] = {}
	for row in (
		frappe.qb.from_(Lead)
		.select(*fields, Count(Lead.name).as_("leads"))
		.where(open_leads & (Lead.lead_owner.isnull() | (Lead.lead_owner == "")) & has_phone(Lead))
		.groupby(*fields)
	).run(as_dict=True):
		place = names.get(clean(row.tbo_district).casefold(), clean(row.tbo_district))
		counts = unassigned.setdefault(place, {"products": {}, "industries": {}})
		for kind, value in (("products", row.get("tbo_product")), ("industries", row.industry)):
			key = clean(value)
			counts[kind][key] = counts[kind].get(key, 0) + row.leads
	return {
		"districts": sorted((name for name in names.values() if name), key=str.casefold),
		"unassigned": unassigned,
	}


def serialize(department, day) -> dict:
	"""{department, slots: [{label, start_time, end_time, size}], products: [{product_name,
	intro_line}], campaign_product, campaign_district, pull_unassigned, unassigned: {product:
	leads}, industries, unassigned_industries, districts, unassigned_districts
	(department_districts), auto_assign (settings.auto_assign_rules), members:
	[{user, full_name, product, industry, district, sizes: {slot: calls}, total, slot_districts:
	{slot: [{district ("" Mixed), calls}]}, has_plan, today: {size, called}, leads (open leads
	they own)}]}."""
	from tbo_smart.sales.call_batches.settings import auto_assign_rules
	from tbo_smart.sales.call_batches.settings import serialize as rules

	slots = rules(department)["call_slots"]
	today = handed_today(department.name, day)
	owned = owned_leads(department.name)
	places = department_districts(department.name)
	members = []
	for row in department.members:
		sizes = sizes_of(row)
		split = slot_split(row)
		whole = plan_district(department, row.user) or ""
		members.append(
			{
				"user": row.user,
				"full_name": row.full_name or frappe.utils.get_fullname(row.user),
				"product": plan_product(department, row.user) or None,
				"industry": plan_industry(department, row.user) or None,
				"district": plan_district(department, row.user) or None,
				"sizes": {slot["label"]: sizes.get(slot["label"], slot["size"]) for slot in slots},
				"total": sum(sizes.get(slot["label"], slot["size"]) for slot in slots),
				# each slot's calls by district ("" is Mixed); a slot not split is one share
				"slot_districts": {
					slot["label"]: [
						{"district": district, "calls": calls}
						for district, calls in split.get(
							slot["label"], [(whole, sizes.get(slot["label"], slot["size"]))]
						)
						if calls > 0
					]
					for slot in slots
				},
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
		"campaign_district": clean(department.campaign_district) or None,
		"pull_unassigned": cint(department.pull_unassigned),
		"unassigned": unassigned_by_product(department.name),
		# a department without products plans by industry: what it has, and what waits
		"industries": department_industries(department.name),
		"unassigned_industries": unassigned_by_industry(department.name),
		# a plan may call for one district: the districts there are, and what waits in each
		"districts": places["districts"],
		"unassigned_districts": places["unassigned"],
		"members": members,
		# when the members nobody handed leads to are topped up automatically (§14)
		"auto_assign": auto_assign_rules(department),
	}


def get_call_plans() -> dict:
	"""{date, departments: [serialize(...) + can_edit]} for the departments the session user plans
	or only views (can_edit false: the page is read-only)."""
	day = frappe.utils.getdate()
	return {
		"date": str(day),
		"departments": [{**serialize(d, day), "can_edit": can_edit} for d, can_edit in visible_departments()],
	}


def save_call_plan(
	department: str,
	users,
	product: str | None = None,
	sizes=None,
	clear=False,
	industry: str | None = None,
	district: str | None = None,
	slot_districts=None,
) -> dict:
	"""Set the plan of `users` (members of `department`): the `product` they are handed (blank:
	any of the department's), the `industry` (for a department without products; blank: any), the
	`district` (blank: any) and `sizes` ({slot label: calls}). `slot_districts` ({slot label:
	[{district, calls}]}, a blank district is Mixed) splits slots by district instead: the slot's
	size is then their total and the one `district` is not kept. `clear` removes their plan.
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
			rows[user].call_industry = None
			rows[user].call_district = None
			rows[user].slot_sizes = None
			rows[user].slot_districts = None
	else:
		chosen = clean_product(doc, product)
		trade = clean_industry(industry)
		place = clean_district(doc, district)
		planned = clean_sizes(doc, sizes)
		split = clean_slot_districts(doc, slot_districts)
		if split:
			place = ""
			planned.update(
				{label: sum(share["calls"] for share in shares) for label, shares in split.items()}
			)
		for user in users:
			rows[user].call_product = chosen or None
			rows[user].call_industry = trade or None
			rows[user].call_district = place or None
			rows[user].slot_sizes = json.dumps(planned) if planned else None
			rows[user].slot_districts = json.dumps(split) if split else None
	doc.save(ignore_permissions=True)  # a head plans their department without editing it
	return serialize(doc, frappe.utils.getdate())


def clean_industry(industry: str | None) -> str:
	"""An industry as leads carry it (ERPNext's Industry Type), or an error in words."""
	wanted = " ".join(str(industry or "").split())
	if not wanted:
		return ""
	if not frappe.db.exists("Industry Type", wanted):
		frappe.throw(_("{0} is not an industry.").format(wanted))
	return wanted


def clean_district(department, district: str | None) -> str:
	"""A district as a plan keeps it (any text: leads carry districts as typed). A department's
	campaign district is a manager's limit: the only district a plan can take."""
	wanted = clean(district)
	locked = clean(department.campaign_district)
	if wanted and locked and wanted.casefold() != locked.casefold():
		frappe.throw(_("{0} calls only in {1} (its campaign district).").format(department.name, locked))
	return wanted


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


def clean_slot_districts(department, value) -> dict[str, list[dict]]:
	"""{slot label: [{district, calls}]} for the department's own slots: each district once a slot
	(Mixed, a blank district, too), the department's campaign district the only named one, each
	slot 0 to MAX_SLOT_CALLS in all. Rows of 0 calls are dropped; a slot left with none hands out
	nothing."""
	value = frappe.parse_json(value) if isinstance(value, str) else value or {}
	if not isinstance(value, dict):
		frappe.throw(_("Give the calls of each slot by district."))
	labels = {slot.label for slot in slots_of(department)}
	unknown = sorted(set(value) - labels)
	if unknown:
		frappe.throw(_("{0} is not a call slot of {1}.").format(", ".join(unknown), department.name))
	cleaned = {}
	for label, rows in value.items():
		if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
			frappe.throw(_("Give the calls of the {0} slot by district.").format(label))
		seen, shares = set(), []
		for row in rows:
			place = clean_district(department, row.get("district"))
			calls = cint(row.get("calls"))
			if calls < 0 or calls > MAX_SLOT_CALLS:
				frappe.throw(_("Calls per slot must be between 0 and {0}.").format(MAX_SLOT_CALLS))
			if place.casefold() in seen:
				frappe.throw(
					_("{0} is in the {1} slot twice. Put its calls in one row.").format(
						place or _("Mixed"), label
					)
				)
			seen.add(place.casefold())
			if calls:
				shares.append({"district": place, "calls": calls})
		if sum(share["calls"] for share in shares) > MAX_SLOT_CALLS:
			frappe.throw(_("The {0} slot adds up to more than {1} calls.").format(label, MAX_SLOT_CALLS))
		cleaned[label] = shares
	return cleaned


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
