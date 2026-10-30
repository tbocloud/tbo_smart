"""Prospect runs (docs/prospecting.md §3): estimate, start, read.

A run is one search: an industry in one or more districts of a state, through one provider, into
one department's unassigned pool. Estimating is free of charge for the provider (OpenStreetMap
counts what the map holds, the paid ones compute a price); starting checks the monthly spend cap
and queues a background job.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, now_datetime

from tbo_smart import lead_source
from tbo_smart.prospecting import (
	MAX_DISTRICTS,
	RUN,
	can_prospect,
	geo,
	headed_departments,
	importer,
	mapping,
	require_prospector,
	setting,
	signals,
)
from tbo_smart.prospecting.providers import ProviderError, get_provider, registry
from tbo_smart.sales import departments
from tbo_smart.sales.departments import DEPARTMENT

DEFAULT_MAX_RESULTS = 100
LIST_LIMIT = 50
LEAD_LIMIT = 200
LOG_LIMIT = 20_000
RUN_FIELDS = [
	"name",
	"industry",
	"state",
	"districts",
	"provider",
	"department",
	"max_results",
	"require_phone",
	"status",
	"estimated_cost",
	"expected_count",
	"estimate_note",
	"actual_cost",
	"count_found",
	"count_duplicates",
	"count_skipped",
	"count_created",
	"started_at",
	"finished_at",
	"creation",
	"owner",
]


def parse_districts(value) -> list[str]:
	"""One district per line or comma separated; tidy, de-duplicated, at most MAX_DISTRICTS."""
	if isinstance(value, str):
		value = re.split(r"[\n,]", value)
	names = list(
		dict.fromkeys(re.sub(r"\s+", " ", str(v)).strip().title() for v in (value or []) if str(v).strip())
	)
	if not names:
		frappe.throw(_("Give at least one district."))
	if len(names) > MAX_DISTRICTS:
		frappe.throw(_("A run covers at most {0} districts.").format(MAX_DISTRICTS))
	return names


def max_results_limit() -> int:
	return cint(setting("prospect_max_results")) or DEFAULT_MAX_RESULTS


def month_spend() -> float:
	"""USD spent this month by finished and failed runs, plus what the running ones may still
	cost."""
	rows = frappe.get_all(
		RUN,
		filters={
			"started_at": (">=", get_first_day(now_datetime())),
			"status": ("in", ("Running", "Done", "Failed")),
		},
		fields=["status", "actual_cost", "estimated_cost"],
	)
	return sum(flt(row.estimated_cost) if row.status == "Running" else flt(row.actual_cost) for row in rows)


def check_cap(provider, cost: float) -> None:
	if not provider.paid:
		return
	cap = flt(setting("prospect_monthly_cap"))
	if cost + month_spend() > cap + 1e-9:
		frappe.throw(
			_("This run could cost USD {0}. This month's spend is USD {1} of the USD {2} cap.").format(
				round(cost, 2), round(month_spend(), 2), round(cap, 2)
			)
		)


def clean_values(values: dict) -> dict:
	"""The run's fields from what the hub sent, checked."""
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	industry = (values.get("industry") or "").strip()
	if not frappe.db.exists("Industry Type", industry):
		frappe.throw(_("{0} is not an industry.").format(industry or _("Choose an industry")))
	state = (values.get("state") or "").strip()
	if not state:
		frappe.throw(_("Give the state."))
	department = values.get("department") or setting("prospect_default_department")
	if not department or not cint(frappe.db.get_value(DEPARTMENT, department, "is_active")):
		frappe.throw(_("Choose an active department for the new leads."))
	provider = get_provider(values.get("provider") or "")
	if not provider.available():
		frappe.throw(_("{0} is not switched on or not set up in TBO Smart Settings.").format(provider.label))
	limit = max_results_limit()
	max_results = cint(values.get("max_results")) or limit
	if not 1 <= max_results <= limit:
		frappe.throw(_("A run finds between 1 and {0} businesses.").format(limit))
	return {
		"industry": industry,
		"state": state,
		"districts": "\n".join(parse_districts(values.get("districts"))),
		"provider": provider.key,
		"department": department,
		"max_results": max_results,
		"require_phone": 1 if cint(values.get("require_phone", 1)) else 0,
	}


def estimate_run(values: dict) -> dict:
	"""Create (or, with `name`, redo) the run as Estimated and return it. Needs a manager or the
	department's head."""
	values = frappe.parse_json(values) if isinstance(values, str) else dict(values or {})
	fields = clean_values(values)
	require_prospector(fields["department"])
	provider = get_provider(fields["provider"])
	spec = mapping.category_for(fields["industry"])
	districts = fields["districts"].splitlines()
	places = [geo.locate(district, fields["state"]) for district in districts]
	try:
		estimate = provider.estimate(spec, places, fields["max_results"], bool(fields["require_phone"]))
	except ProviderError as e:
		frappe.throw(str(e))
	check_cap(provider, estimate["cost"])
	if values.get("name"):
		doc = frappe.get_doc(RUN, values["name"])
		if doc.status not in ("Draft", "Estimated"):
			frappe.throw(_("Only a run that has not started can be estimated again."))
	else:
		doc = frappe.new_doc(RUN)
	note = " ".join(part for part in (estimate["note"], provider.coverage) if part)
	doc.update(
		{
			**fields,
			"status": "Estimated",
			"estimated_cost": estimate["cost"],
			"expected_count": estimate["expected"],
			"estimate_note": note,
		}
	)
	doc.save(ignore_permissions=True)
	return serialize(doc)


def start_run(name: str) -> dict:
	"""Queue an Estimated run. The spend cap is checked again at this moment."""
	doc = frappe.get_doc(RUN, name)
	require_prospector(doc.department)
	status = frappe.db.get_value(RUN, name, "status", for_update=True)
	if status != "Estimated":
		frappe.throw(_("Estimate the run first; only an estimated run can be started."))
	check_cap(get_provider(doc.provider), flt(doc.estimated_cost))
	frappe.db.set_value(
		RUN,
		name,
		{"status": "Running", "started_at": now_datetime(), "finished_at": None},
		update_modified=True,
	)
	frappe.enqueue(
		"tbo_smart.prospecting.runs.run_job", name=name, queue="long", timeout=3600, enqueue_after_commit=True
	)
	return serialize(frappe.get_doc(RUN, name))


def run_job(name: str) -> None:
	"""Background job: search each district, import what is found, then queue the website reading."""
	doc = frappe.get_doc(RUN, name)
	log = []
	failed = False
	try:
		execute(doc, log)
	except Exception as e:
		failed = True
		expected = isinstance(e, ProviderError | frappe.ValidationError)
		if not expected:
			frappe.log_error(title=f"Find prospects: {name}")
		log.append(f"Stopped: {e if expected else 'unexpected error, see the Error Log'}")
	doc.reload()  # the counts the districts saved along the way
	doc.status = "Failed" if failed else "Done"
	doc.finished_at = now_datetime()
	doc.log = "\n".join(log)[-LOG_LIMIT:]
	doc.save(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep
	if doc.status == "Done" and cint(doc.count_created):
		frappe.enqueue(
			"tbo_smart.prospecting.runs.read_websites",
			name=name,
			queue="long",
			timeout=7200,
			enqueue_after_commit=True,
		)


def execute(doc, log: list[str]) -> None:
	provider = get_provider(doc.provider)
	spec = mapping.category_for(doc.industry)
	lead_source.ensure(importer.source_name(provider.key))
	context = frappe._dict(
		name=doc.name,
		industry=doc.industry,
		state=doc.state,
		department=doc.department,
		provider=provider.key,
		require_phone=cint(doc.require_phone),
		district=None,
	)
	counts = {"found": 0, "duplicate": 0, "skipped": 0, "created": 0}
	for district in doc.districts.splitlines():
		remaining = cint(doc.max_results) - counts["found"]
		if remaining <= 0:
			break
		place = geo.locate(district, doc.state)
		context.district = district
		found, cost = provider.search(spec, place, remaining, bool(cint(doc.require_phone)))
		doc.actual_cost = flt(doc.actual_cost) + cost
		inside = [b for b in found if geo.inside(place, b.get("latitude"), b.get("longitude"))][:remaining]
		log.append(f"{district}: {len(found)} from {provider.key}, {len(inside)} inside the district.")
		counts["found"] += len(inside)
		for business in inside:
			counts[importer.import_business(business, context)] += 1
		frappe.db.set_value(
			RUN,
			doc.name,
			{
				"actual_cost": doc.actual_cost,
				"count_found": counts["found"],
				"count_duplicates": counts["duplicate"],
				"count_skipped": counts["skipped"],
				"count_created": counts["created"],
			},
			update_modified=False,
		)
		frappe.db.commit()  # nosemgrep
	log.append(
		"Found {found}, created {created}, duplicates {duplicate}, skipped {skipped}.".format(**counts)
	)


def read_websites(name: str) -> None:
	"""Background job: the website signals of the run's new leads."""
	signals.read_leads(signals.unread_leads(name))


# ---------- reading, for the hub ----------


def serialize(doc) -> dict:
	data = {key: doc.get(key) for key in RUN_FIELDS}
	data["districts"] = (doc.districts or "").splitlines()
	data["require_phone"] = cint(doc.require_phone)
	data["owner_name"] = frappe.utils.get_fullname(doc.owner)
	return data


def visible_filters() -> dict:
	"""Managers see every run; a department head the runs of their departments."""
	if set(frappe.get_roles()) & departments.MANAGER_ROLES:
		return {}
	return {"department": ("in", headed_departments() or [""])}


def list_runs() -> list[dict]:
	require_prospector()
	return [
		serialize(frappe._dict(row))
		for row in frappe.get_all(
			RUN, filters=visible_filters(), fields=RUN_FIELDS, order_by="creation desc", limit=LIST_LIMIT
		)
	]


def read_run(name: str) -> dict:
	"""The run, its log and the leads it created (up to 200, each with its Why call line)."""
	doc = frappe.get_doc(RUN, name)
	require_prospector(doc.department)
	leads = frappe.get_all(
		"Lead",
		filters={"tbo_prospect_run": name},
		fields=[
			"name",
			"company_name",
			"mobile_no",
			"website",
			"city",
			"tbo_stage",
			"lead_owner",
			"tbo_web_profile",
		],
		order_by="creation asc",
		limit=LEAD_LIMIT,
	)
	return {
		**serialize(doc),
		"log": doc.log,
		"leads": [
			{
				"name": lead.name,
				"company_name": lead.company_name,
				"phone": lead.mobile_no,
				"website": lead.website,
				"city": lead.city,
				"stage": lead.tbo_stage,
				"owner": lead.lead_owner,
				"why_call": (signals.prospect_of(lead.tbo_web_profile) or {}).get("why_call"),
			}
			for lead in leads
		],
	}


def options() -> dict | None:
	"""For get_options: None when the acting user may not find prospects, else the providers,
	the default department and the largest run."""
	from tbo_smart.prospecting import enabled

	if not (enabled() and can_prospect()):
		return None
	return {
		"providers": [provider.describe() for provider in registry().values()],
		"default_department": setting("prospect_default_department"),
		"max_results": max_results_limit(),
		"max_districts": MAX_DISTRICTS,
		"monthly_cap": flt(setting("prospect_monthly_cap")),
		"month_spend": round(month_spend(), 4),
	}
