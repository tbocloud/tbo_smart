"""Factories and toggles shared by the tbo_smart tests. Never define helpers inside test files."""

import base64
import functools
import io
import json
import math
import time
from unittest.mock import MagicMock, patch

import erpnext
import frappe
import jwt
import requests
from cryptography.hazmat.primitives.asymmetric import rsa
from erpnext.setup.doctype.employee.test_employee import make_employee
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.integrations.oauth2 import get_oauth_server, get_token
from frappe.utils import (
	add_days,
	add_to_date,
	get_year_ending,
	get_year_start,
	getdate,
	now_datetime,
	nowdate,
)
from frappe.utils.nestedset import rebuild_tree
from frappe.utils.password import remove_encrypted_password, set_encrypted_password
from hrms.tests.test_utils import before_tests as hrms_before_tests
from jwt.algorithms import RSAAlgorithm
from PIL import Image
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request as WerkzeugRequest

from tbo_smart.api.mobile import auth
from tbo_smart.api.mobile.capabilities import get_capabilities
from tbo_smart.api.mobile.utils import INSIGHTS_CACHE_PREFIX
from tbo_smart.checkin import place
from tbo_smart.install import setup_quotation_approval
from tbo_smart.sales.opportunity import customer_of
from tbo_smart.smart_leads.migration import note_html

TEST_HOLIDAY_LIST = "TBO Smart Test Holidays"
MS_TEST_TENANT = "11111111-2222-3333-4444-555555555555"
MS_TEST_CLIENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MS_TEST_KEY_ID = "tbo-smart-test-key"
TEST_OAUTH_CLIENT_APP = "TBO Smart SSO Test"


def before_tests() -> None:
	"""Set up a fresh test site the way HRMS does before its own tests: run the setup wizard
	(company _Test Company in INR, fiscal year, chart of accounts, departments, designations)
	when no Company exists, enable all roles and give the company a holiday list.

	`run-tests --app tbo_smart` runs only this app's before_tests hook, so without this a new
	CI site has no Company or Department and make_employee fails."""
	hrms_before_tests()


def make_user(email: str, roles: list[str] | tuple = ()) -> str:
	"""A website-enabled User with exactly the extra roles given (adds, never removes)."""
	if not frappe.db.exists("User", email):
		# the suite creates more than the 60 users an hour Frappe's sign-up throttle allows on a
		# fresh site (some tests commit, so test users stay)
		with patch("frappe.core.doctype.user.user.throttle_user_creation"):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	# Tests call this while signed in as a plain user, who may not save a User.
	user.flags.ignore_permissions = True
	user.add_roles(*roles)
	return email


def make_staff(email: str, roles: list[str] | tuple = (), **employee_fields) -> str:
	"""An active Employee linked to a User with the Employee role plus `roles`.

	`employee_fields` go onto the Employee (reports_to, leave_approver, expense_approver, ...).
	Returns the Employee name.
	"""
	make_user(email, ("Employee", *roles))
	ensure_company_holiday_list()
	return make_employee(email, company=erpnext.get_default_company(), **employee_fields)


def ensure_company_holiday_list() -> str:
	"""Give the default company a holiday list for this year (one holiday, no weekly offs)."""
	company = erpnext.get_default_company()
	if not frappe.db.exists("Holiday List", TEST_HOLIDAY_LIST):
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": TEST_HOLIDAY_LIST,
				"from_date": get_year_start(getdate()),
				"to_date": get_year_ending(getdate()),
				"holidays": [{"holiday_date": get_year_ending(getdate()), "description": "Year end"}],
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value("Company", company, "default_holiday_list", TEST_HOLIDAY_LIST)
	return TEST_HOLIDAY_LIST


def delete_checkins(employee: str) -> None:
	"""Remove an employee's Employee Checkins, so the 60-second double-punch guard starts clean.

	Tests in a class share one transaction, so a punch from an earlier test is still there."""
	frappe.db.delete("Employee Checkin", {"employee": employee})


def set_hr_settings(**values) -> None:
	"""Write HR Settings fields directly (e.g. allow_employee_checkin_from_mobile_app=1)."""
	for field, value in values.items():
		frappe.db.set_single_value("HR Settings", field, value)


# A point near Kottayam; the test locations sit around it.
GEO_ORIGIN = (9.5091781, 76.5302819)
GEO_SHIFT_TYPE = "TBO Smart Geo Shift"


def offset_point(metres_north: float = 0, metres_east: float = 0, origin=GEO_ORIGIN) -> tuple[float, float]:
	"""A (latitude, longitude) the given metres north and east of `origin` (flat-earth
	approximation, accurate to well under a metre at these distances)."""
	latitude, longitude = origin
	return (
		latitude + metres_north / 111_320,
		longitude + metres_east / (111_320 * math.cos(math.radians(latitude))),
	)


def make_shift_location(name: str, radius: int = 100, metres_north: float = 0, metres_east: float = 0) -> str:
	"""A Shift Location centred `metres_north`/`metres_east` of GEO_ORIGIN with a check-in radius."""
	latitude, longitude = offset_point(metres_north, metres_east)
	if frappe.db.exists("Shift Location", name):
		frappe.db.set_value(
			"Shift Location",
			name,
			{"latitude": latitude, "longitude": longitude, "checkin_radius": radius},
		)
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Shift Location",
				"location_name": name,
				"latitude": latitude,
				"longitude": longitude,
				"checkin_radius": radius,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_shift_assignment(employee: str, shift_location: str | None = None) -> str:
	"""A submitted, active Shift Assignment from today (open-ended) on a 09:00-18:00 shift."""
	if not frappe.db.exists("Shift Type", GEO_SHIFT_TYPE):
		frappe.get_doc(
			{
				"doctype": "Shift Type",
				"__newname": GEO_SHIFT_TYPE,
				"start_time": "09:00:00",
				"end_time": "18:00:00",
			}
		).insert(ignore_permissions=True)
	assignment = frappe.get_doc(
		{
			"doctype": "Shift Assignment",
			"employee": employee,
			"shift_type": GEO_SHIFT_TYPE,
			"company": erpnext.get_default_company(),
			"start_date": nowdate(),
			"shift_location": shift_location,
			"status": "Active",
		}
	)
	assignment.insert(ignore_permissions=True)
	assignment.submit()
	return assignment.name


def set_checkin_settings(locations: list[str] | tuple = (), **values) -> None:
	"""Configure TBO Smart Settings → Check-in location in place: the fallback locations
	and any other field (enforce_checkin_radius, checkin_max_accuracy, checkin_anywhere_roles,
	reverse_geocode_url, reverse_geocode_email)."""
	settings = frappe.get_single("TBO Smart Settings")
	settings.update(values)
	settings.set("checkin_locations", [{"shift_location": name} for name in locations])
	settings.save(ignore_permissions=True)


def reset_checkin_settings() -> None:
	"""Check-in location settings back to their shipped defaults, with no fallback locations."""
	set_checkin_settings(
		enforce_checkin_radius=1,
		checkin_max_accuracy=100,
		checkin_anywhere_roles="HR Manager",
		reverse_geocode_url="https://nominatim.openstreetmap.org/reverse",
		reverse_geocode_email=None,
	)


def nominatim_response(address: dict | None = None, display_name: str = "", status: int = 200):
	"""A mocked `requests.get` result shaped like Nominatim's jsonv2 /reverse answer."""
	response = MagicMock()
	response.status_code = status
	response.json.return_value = {"address": address or {}, "display_name": display_name}
	if status >= 400:
		response.raise_for_status.side_effect = requests.HTTPError(f"{status}")
	return response


def clear_place_cache() -> None:
	"""Drop cached place names and the Nominatim rate-limit slot."""
	frappe.cache.delete_keys(place.CACHE_PREFIX)
	frappe.cache.delete(frappe.cache.make_key(place.SLOT_KEY))


def make_leave_type(name: str = "TBO Smart Casual Leave", **fields) -> str:
	"""A paid Leave Type that allows a negative balance, so tests can apply without an allocation.
	`fields` go onto a new type (e.g. include_holiday=1)."""
	if not frappe.db.exists("Leave Type", name):
		frappe.get_doc(
			{"doctype": "Leave Type", "leave_type_name": name, "allow_negative": 1, **fields}
		).insert(ignore_permissions=True)
	return name


def make_leave_allocation(employee: str, leave_type: str, days: float = 10) -> str:
	"""A submitted Leave Allocation for this calendar year."""
	allocation = frappe.get_doc(
		{
			"doctype": "Leave Allocation",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": get_year_start(getdate()),
			"to_date": get_year_ending(getdate()),
			"new_leaves_allocated": days,
		}
	)
	allocation.insert(ignore_permissions=True)
	allocation.submit()
	return allocation.name


def make_leave_application(
	employee: str, leave_type: str, days_ahead: int = 7, approver: str | None = None
) -> str:
	"""An Open one-day Leave Application, inserted as Administrator."""
	day = add_days(nowdate(), days_ahead)
	application = frappe.get_doc(
		{
			"doctype": "Leave Application",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": day,
			"to_date": day,
			"leave_approver": approver,
			"status": "Open",
			"posting_date": nowdate(),
		}
	)
	application.insert(ignore_permissions=True)
	return application.name


def ensure_employee_advance_account() -> str:
	"""The default company's Default Employee Advance Account. When it is missing or not a
	Receivable ledger (which HRMS requires), the company's first Receivable ledger becomes it."""
	company = erpnext.get_default_company()
	account = frappe.db.get_value("Company", company, "default_employee_advance_account")
	if not account or frappe.db.get_value("Account", account, "account_type") != "Receivable":
		account = frappe.db.get_value(
			"Account", {"company": company, "account_type": "Receivable", "is_group": 0}, "name"
		)
		frappe.db.set_value("Company", company, "default_employee_advance_account", account)
	return account


def make_employee_advance(
	employee: str, amount: float = 1000, purpose: str = "Client visit", submit: bool = False
) -> str:
	"""An Employee Advance in the company currency, inserted (and optionally submitted) as
	Administrator."""
	company = erpnext.get_default_company()
	advance = frappe.get_doc(
		{
			"doctype": "Employee Advance",
			"employee": employee,
			"company": company,
			"posting_date": nowdate(),
			"purpose": purpose,
			"advance_amount": amount,
			"currency": frappe.get_cached_value("Company", company, "default_currency"),
			"exchange_rate": 1,
			"advance_account": ensure_employee_advance_account(),
		}
	)
	advance.insert(ignore_permissions=True)
	if submit:
		advance.submit()
	return advance.name


ADVANCE_TEST_WORKFLOW = "TBO Smart Advance Approval"


def make_advance_workflow(approver_role: str = "Expense Approver") -> str:
	"""An active Workflow on Employee Advance: Pending (draft) to Approved (submitted) or
	Rejected (draft), both actions allowed to `approver_role`. Pair with
	delete_advance_workflow."""
	for state in ("Pending", "Approved", "Rejected"):
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state}).insert(
				ignore_permissions=True
			)
	for action in ("Approve", "Reject"):
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)
	delete_advance_workflow()
	frappe.get_doc(
		{
			"doctype": "Workflow",
			"workflow_name": ADVANCE_TEST_WORKFLOW,
			"document_type": "Employee Advance",
			"workflow_state_field": "workflow_state",
			"is_active": 1,
			"send_email_alert": 0,
			"states": [
				{"state": "Pending", "doc_status": "0", "allow_edit": "Employee"},
				{"state": "Approved", "doc_status": "1", "allow_edit": approver_role},
				{"state": "Rejected", "doc_status": "0", "allow_edit": approver_role},
			],
			"transitions": [
				{"state": "Pending", "action": "Approve", "next_state": "Approved", "allowed": approver_role},
				{"state": "Pending", "action": "Reject", "next_state": "Rejected", "allowed": approver_role},
			],
		}
	).insert(ignore_permissions=True)
	frappe.cache.hdel("workflow", "Employee Advance")
	return ADVANCE_TEST_WORKFLOW


def delete_advance_workflow() -> None:
	"""Remove the test workflow and commit. The first workflow on a site adds the
	`workflow_state` column (DDL, an implicit commit), so a rolled-back delete would leave
	the workflow active for every later test."""
	if frappe.db.exists("Workflow", ADVANCE_TEST_WORKFLOW):
		frappe.delete_doc("Workflow", ADVANCE_TEST_WORKFLOW, ignore_permissions=True, force=True)
		frappe.db.commit()  # nosemgrep
	frappe.cache.hdel("workflow", "Employee Advance")


def make_half_day_leave_application(employee: str, leave_type: str, approver: str | None = None):
	"""An Open three-day Leave Application, 20-22 days ahead, whose middle day is a half day."""
	application = frappe.get_doc(
		{
			"doctype": "Leave Application",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": add_days(nowdate(), 20),
			"to_date": add_days(nowdate(), 22),
			"half_day": 1,
			"half_day_date": add_days(nowdate(), 21),
			"leave_approver": approver,
			"status": "Open",
			"posting_date": nowdate(),
		}
	)
	application.insert(ignore_permissions=True)
	return application


def make_expense_claim_type(name: str = "TBO Smart Travel") -> str:
	"""An Expense Claim Type with a default expense account for the default company."""
	company = erpnext.get_default_company()
	if not frappe.db.exists("Expense Claim Type", name):
		account = frappe.db.get_value(
			"Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name"
		)
		frappe.get_doc(
			{
				"doctype": "Expense Claim Type",
				"expense_type": name,
				"accounts": [{"company": company, "default_account": account}],
			}
		).insert(ignore_permissions=True)
	return name


def make_salary_slip_record(employee: str, start_date: str, net_pay: float = 1000) -> str:
	"""A submitted Salary Slip row written straight to the DB (no salary structure needed).

	Only for permission and listing tests: it skips Salary Slip validation on purpose.
	"""
	slip = frappe.new_doc("Salary Slip")
	slip.update(
		{
			"employee": employee,
			"employee_name": frappe.db.get_value("Employee", employee, "employee_name"),
			"company": erpnext.get_default_company(),
			"posting_date": start_date,
			"start_date": start_date,
			"end_date": add_days(start_date, 27),
			"gross_pay": net_pay,
			"net_pay": net_pay,
			"docstatus": 1,
		}
	)
	slip.set_new_name()
	slip.db_insert()
	return slip.name


def make_lead(owner: str, first_name: str = "Test Lead", **fields) -> str:
	"""An ERPNext Lead owned by `owner`, inserted as Administrator; `fields` are Lead fields
	(company_name, email_id, mobile_no, source, tbo_stage, …)."""
	lead = frappe.get_doc({"doctype": "Lead", "first_name": first_name, "lead_owner": owner, **fields})
	lead.insert(ignore_permissions=True)
	return lead.name


def make_sales_stage(name: str, kind: str = "Ongoing", probability: float = 0, position: int = 0) -> str:
	"""A Sales Stage typed for the deal pipeline (tbo_stage_type), created or updated."""
	values = {"tbo_stage_type": kind, "tbo_probability": probability, "tbo_position": position}
	if frappe.db.exists("Sales Stage", name):
		frappe.db.set_value("Sales Stage", name, values)
	else:
		frappe.get_doc({"doctype": "Sales Stage", "stage_name": name, **values}).insert(
			ignore_permissions=True
		)
	frappe.clear_document_cache("Sales Stage", name)
	return name


def make_deal(owner: str, status: str = "Qualification", deal_value: float = 1000, **fields) -> str:
	"""An Opportunity (a deal) owned by `owner` in the Sales Stage `status`, made out to a new
	organization (Prospect) unless `fields` name the party; inserted as Administrator."""
	if not frappe.db.exists("Sales Stage", status):
		make_sales_stage(status)
	if "party_name" not in fields:
		fields.update(
			opportunity_from="Prospect",
			party_name=make_organization(f"TBO Deal {frappe.generate_hash(length=8)}"),
		)
	deal = frappe.get_doc(
		{
			"doctype": "Opportunity",
			"opportunity_owner": owner,
			"sales_stage": status,
			"opportunity_amount": deal_value,
			"company": erpnext.get_default_company(),
			**fields,
		}
	)
	deal.insert(ignore_permissions=True)
	return deal.name


def make_test_mobile() -> str:
	"""A random 9-digit mobile number without country code or trunk 0 (e.g. 504182276), so
	duplicate-check tests never match a lead left by another test or an earlier run."""
	return "50" + str(int(frappe.generate_hash(length=12), 16))[-7:].zfill(7)


def make_organization(name: str) -> str:
	"""A Prospect (an organization) named `name`, inserted as Administrator."""
	organization = frappe.get_doc({"doctype": "Prospect", "company_name": name})
	organization.insert(ignore_permissions=True)
	return organization.name


def make_contact(first_name: str, phone: str | None = None, email: str | None = None) -> str:
	"""A Contact with an optional primary mobile (phone_nos) and primary email (email_ids)."""
	contact = frappe.get_doc({"doctype": "Contact", "first_name": first_name})
	if phone:
		contact.append("phone_nos", {"phone": phone, "is_primary_mobile_no": 1})
	if email:
		contact.append("email_ids", {"email_id": email, "is_primary": 1})
	contact.insert(ignore_permissions=True)
	return contact.name


def make_customer(customer_name: str) -> str:
	"""An ERPNext Customer (Company) in the first leaf customer group and territory."""
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer_name,
			"customer_type": "Company",
			"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
			"territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
		}
	)
	customer.insert(ignore_permissions=True)
	return customer.name


# ---------- Quotation discount and commission control ----------

QUOTATION_TEST_CUSTOMER = "TBO Smart Quotation Customer"
QUOTATION_TEST_ITEM = "TBO Smart Quotation Service"


def configure_quotation_approval(
	enabled: int = 1, applies_to: str = "Both", limits: dict | None = None
) -> None:
	"""Turn quotation adjustment control on (or off) and set every level's limits.

	`limits` maps a level name to (max discount %, max commission %); levels left out get 0,
	which means not set. Saves the settings so the cached copy is refreshed."""
	setup_quotation_approval()
	settings = frappe.get_single("TBO Quotation Approval Settings")
	settings.enabled = enabled
	settings.applies_to = applies_to
	for row in settings.levels:
		row.max_discount, row.max_commission = (limits or {}).get(row.level, (0, 0))
	settings.save(ignore_permissions=True)


def make_quotation_item(item_code: str = QUOTATION_TEST_ITEM) -> str:
	"""A non-stock sales Item in the first leaf item group, if the site does not have it yet."""
	if not frappe.db.exists("Item", item_code):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_code,
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_sales_item": 1,
			}
		).insert(ignore_permissions=True)
	return item_code


def make_quotation(rate: float = 1000, qty: float = 1, discount: float = 0, commission: float = 0):
	"""A draft Quotation for the test customer and item, inserted as the session user so the
	adjustment control judges that user. `discount` is the additional discount %, `commission`
	the Commission %. Returns the Document."""
	customer = frappe.db.get_value("Customer", {"customer_name": QUOTATION_TEST_CUSTOMER}, "name")
	quotation = frappe.get_doc(
		{
			"doctype": "Quotation",
			"quotation_to": "Customer",
			"party_name": customer or make_customer(QUOTATION_TEST_CUSTOMER),
			"company": erpnext.get_default_company(),
			"transaction_date": nowdate(),
			"valid_till": add_days(nowdate(), 30),
			"additional_discount_percentage": discount,
			"tbo_commission_rate": commission,
			"items": [{"item_code": make_quotation_item(), "qty": qty, "rate": rate}],
		}
	)
	quotation.insert(ignore_permissions=True)
	return quotation


def save_quotation(quotation, **values):
	"""Reload, set `values` and save the Quotation as the session user. Returns the Document."""
	quotation.reload()
	quotation.update(values)
	quotation.save(ignore_permissions=True)
	return quotation


def get_adjustments(quotation: str, kind: str | None = None) -> list[frappe._dict]:
	"""A quotation's TBO Quotation Adjustments, oldest first, with their steps' actions."""
	filters = {"quotation": quotation, **({"kind": kind} if kind else {})}
	rows = frappe.get_all(
		"TBO Quotation Adjustment", filters=filters, fields=["*"], order_by="creation asc, name asc"
	)
	for row in rows:
		row.actions = frappe.get_all(
			"TBO Quotation Adjustment Step", filters={"parent": row.name}, pluck="action", order_by="idx"
		)
	return rows


def make_lead_source(name: str) -> str:
	"""A Lead Source named `name`, if the site does not have it yet."""
	if not frappe.db.exists("Lead Source", name):
		frappe.get_doc({"doctype": "Lead Source", "source_name": name}).insert(ignore_permissions=True)
	return name


def make_card_image_base64() -> str:
	"""A small real JPEG as base64, as the app sends a scanned business card."""
	content = io.BytesIO()
	Image.new("RGB", (8, 8), "white").save(content, format="JPEG")
	return base64.b64encode(content.getvalue()).decode()


def find_match(matches: list[dict], kind: str, name: str | None = None) -> dict:
	"""The first check_duplicates match of `kind` (and `name`, when given); StopIteration if none."""
	return next(row for row in matches if row["kind"] == kind and (name is None or row["name"] == name))


def get_note_texts(lead: dict) -> list[str]:
	"""The note texts of a lead returned by crm.get_lead / create_lead."""
	return [note["text"] for note in lead["notes"]]


def enable_mobile_push() -> None:
	frappe.db.set_single_value("TBO Smart Settings", "enable_mobile_push", 1)


def disable_mobile_push() -> None:
	frappe.db.set_single_value("TBO Smart Settings", "enable_mobile_push", 0)


def make_mobile_device(user: str, token: str, platform: str = "android") -> str:
	"""A TBO Mobile Device row for `user`, owned by that user."""
	device = frappe.get_doc(
		{"doctype": "TBO Mobile Device", "user": user, "expo_push_token": token, "platform": platform}
	)
	device.insert(ignore_permissions=True)
	frappe.db.set_value("TBO Mobile Device", device.name, "owner", user)
	return device.name


def make_attendance(employee: str, day: str, status: str = "Present", late_entry: int = 0) -> str:
	"""A submitted Attendance for one employee and day, inserted as Administrator."""
	attendance = frappe.get_doc(
		{
			"doctype": "Attendance",
			"employee": employee,
			"company": frappe.db.get_value("Employee", employee, "company"),
			"attendance_date": day,
			"status": status,
			"late_entry": late_entry,
		}
	)
	attendance.insert(ignore_permissions=True)
	attendance.submit()
	return attendance.name


def make_sales_hierarchy_node(user: str, reports_to: str | None = None) -> str:
	"""A TBO Sales Hierarchy node for `user` under the node `reports_to`; rebuilds the tree."""
	node = frappe.get_doc(
		{
			"doctype": "TBO Sales Hierarchy",
			"user": user,
			"reports_to": reports_to,
			"is_group": 1,
		}
	)
	node.insert(ignore_permissions=True)
	rebuild_tree("TBO Sales Hierarchy")
	return node.name


def enable_sales_hierarchy() -> None:
	frappe.db.set_single_value("TBO Smart Settings", "enable_sales_hierarchy", 1)


def disable_sales_hierarchy(users: list[str] | tuple = ()) -> None:
	"""Turn the sales hierarchy off and delete the nodes of `users`, and commit (some tests
	commit, so a node they made may already be persisted)."""
	frappe.db.set_single_value("TBO Smart Settings", "enable_sales_hierarchy", 0)
	for user in users:
		frappe.db.delete("TBO Sales Hierarchy", {"user": user})
	frappe.db.commit()  # nosemgrep


def clear_insights_cache() -> None:
	"""Drop every cached team or sales insight so a test sees its own fixtures."""
	frappe.cache.delete_keys(INSIGHTS_CACHE_PREFIX)


def age_document(doctype: str, name: str, days: int) -> None:
	"""Move a document's `modified` back by `days` without touching anything else."""
	frappe.db.set_value(doctype, name, "modified", add_days(now_datetime(), -days), update_modified=False)


def capabilities_as(user: str) -> frappe._dict:
	"""Switch the session to `user` and return their capabilities."""
	frappe.set_user(user)
	return get_capabilities()


def get_deal_base_value(name: str) -> float:
	"""A deal's value in the company currency, as insights count it."""
	return frappe.db.get_value("Opportunity", name, "base_opportunity_amount")


def get_all_keys(value) -> list[str]:
	"""Every dict key anywhere in a nested API response."""
	keys = []
	if isinstance(value, dict):
		for key, item in value.items():
			keys += [key, *get_all_keys(item)]
	elif isinstance(value, list):
		for item in value:
			keys += get_all_keys(item)
	return keys


def delete_sales_records(owners: list[str] | tuple) -> None:
	"""Delete the deals, leads, notes, calls and tasks of `owners` and commit (some tests commit,
	e.g. through an email sent at once, so fixtures can outlive the test's rollback)."""
	owners = list(owners)
	for doctype, owner_field in (
		("Opportunity", "opportunity_owner"),
		("Lead", "lead_owner"),
		("Comment", "owner"),
		("Event", "owner"),
		("ToDo", "allocated_to"),
	):
		frappe.db.delete(doctype, {owner_field: ("in", owners)})
	frappe.db.commit()  # nosemgrep


# ---------- Smart leads (docs/smart-leads.md) ----------

SMART_LEAD_SWITCHES = (
	"enable_lead_scoring",
	"lead_scoring_use_ai",
	"enable_lead_follow_ups",
	"enable_lead_notes",
	"enable_lead_triage",
)
TEST_TEAMS_DM_URL = (
	"https://prod-01.westeurope.logic.azure.com/workflows/abc/triggers/manual/paths/invoke"
	"?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=test-signature"
)
TEST_OPENAI_KEY = "sk-test-openai-key-123456789"
TEST_ANTHROPIC_KEY = "sk-test-kimi-key-987654321"


def set_smart_lead_settings(**values) -> None:
	"""Write TBO Smart Settings fields directly and drop the cached settings document."""
	for field, value in values.items():
		frappe.db.set_single_value("TBO Smart Settings", field, value)
	frappe.clear_document_cache("TBO Smart Settings", "TBO Smart Settings")


def enable_smart_leads(**values) -> None:
	"""Every smart lead switch on (scoring, AI scoring, follow-ups, notes, triage) with the
	default numbers, plus any `values`."""
	defaults = {
		**{switch: 1 for switch in SMART_LEAD_SWITCHES},
		"call_today_size": 12,
		"lead_stale_days": 21,
		"lead_ladder_l1_days": 2,
		"lead_ladder_l2_days": 4,
		"lead_ladder_l3_days": 7,
		"lead_manager_role": "Sales Manager",
		"triage_max_leads": 300,
		"triage_daily_capacity": 15,
		"ai_daily_lead_limit": 200,
		"lead_fallback_owner": None,
		"lead_digest_sent_on": None,
	}
	set_smart_lead_settings(**{**defaults, **values})


def disable_smart_leads() -> None:
	"""Every smart lead switch off, no AI keys, no Teams URL, and no AI pause left in Redis."""
	from tbo_smart.ai import reset_provider_state

	set_smart_lead_settings(
		**{switch: 0 for switch in SMART_LEAD_SWITCHES}, enable_ai_leads=0, lead_fallback_owner=None
	)
	for field in ("openai_api_key", "anthropic_api_key", "teams_dm_workflow_url"):
		store_settings_secret(field, None)
	reset_provider_state()


def configure_ai_providers(openai: bool = True, anthropic: bool = False, primary: str = "OpenAI") -> None:
	"""Store test keys and models for the AI provider sections (prices: $2 / $8 per 1M tokens)."""
	from tbo_smart.ai import reset_provider_state

	set_smart_lead_settings(
		ai_primary_provider=primary,
		ai_failover=1,
		openai_base_url="https://api.openai.com/v1",
		openai_model="gpt-5.6-luna",
		openai_input_price=2,
		openai_output_price=8,
		anthropic_base_url="https://api.moonshot.ai/anthropic",
		anthropic_model="kimi-k2.6",
		anthropic_input_price=1,
		anthropic_output_price=4,
	)
	for field, key, wanted in (
		("openai_api_key", TEST_OPENAI_KEY, openai),
		("anthropic_api_key", TEST_ANTHROPIC_KEY, anthropic),
	):
		store_settings_secret(field, key if wanted else None)
	reset_provider_state()


def set_teams_dm_url(url: str | None = TEST_TEAMS_DM_URL) -> None:
	"""Store (or with None remove) the Teams DM workflow URL."""
	store_settings_secret("teams_dm_workflow_url", url)


def store_settings_secret(field: str, value: str | None) -> None:
	"""A TBO Smart Settings Password field as a save from the form leaves it: encrypted in
	__Auth and "*****" in the document (None removes both)."""
	if value:
		set_encrypted_password("TBO Smart Settings", "TBO Smart Settings", value, field)
	else:
		remove_encrypted_password("TBO Smart Settings", "TBO Smart Settings", field)
	set_smart_lead_settings(**{field: "*****" if value else None})


def ai_http_response(status: int = 200, body: dict | None = None) -> MagicMock:
	"""A fake requests.Response with this status and JSON body."""
	response = MagicMock()
	response.status_code = status
	response.ok = status < 400
	response.reason = "Error" if status >= 400 else "OK"
	response.json.return_value = body or {}
	return response


def openai_answer(answer: dict, prompt_tokens: int = 1000, completion_tokens: int = 200) -> MagicMock:
	"""An OpenAI Chat Completions 200 whose message content is `answer` as JSON."""
	return ai_http_response(
		200,
		{
			"choices": [{"message": {"content": json.dumps(answer)}, "finish_reason": "stop"}],
			"usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
		},
	)


def anthropic_answer(answer: dict) -> MagicMock:
	"""An Anthropic Messages API 200 whose text block is `answer` as JSON."""
	return ai_http_response(
		200,
		{
			"content": [{"type": "text", "text": json.dumps(answer)}],
			"usage": {"input_tokens": 500, "output_tokens": 100},
			"stop_reason": "end_turn",
		},
	)


def mock_ai(*responses):
	"""Patch the AI module's HTTP calls to answer `responses` in order (a function may build
	one from the request: fn(url, json) -> response). Use as a context manager; the mock's
	call_args_list shows each request."""

	def answer(url, json=None, headers=None, timeout=None):
		reply = queue.pop(0) if queue else ai_http_response(500, {"error": {"message": "no more answers"}})
		return reply if isinstance(reply, MagicMock) else reply(url, json)

	queue = list(responses)
	return patch("tbo_smart.ai.requests.post", side_effect=answer)


def enable_ai_leads(**values) -> None:
	"""AI runs leads on with an OpenAI test key, the smart lead switches as given in `values`
	(scoring and the rest stay as they are)."""
	configure_ai_providers(openai=True)
	set_smart_lead_settings(
		**{"enable_ai_leads": 1, "ai_daily_lead_limit": 200, "lead_stale_days": 21, **values}
	)


def read_lead_with_ai(lead: str, text: str = "Asked for a demo of billing") -> None:
	"""Switch AI runs leads on and let the AI read `lead` (answering ai_lead_insight) after a
	note with `text`."""
	enable_ai_leads()
	with mock_ai(openai_answer(ai_lead_insight())):
		make_lead_note(lead, text)


def get_mobile_lead_names(**filters) -> set[str]:
	"""The names the app's lead list (crm.get_leads) returns for `filters`, as the session user."""
	from tbo_smart.api.mobile import crm

	return {row["name"] for row in crm.get_leads(page_length=100, **filters)["data"]}


def ai_lead_insight(**overrides) -> dict:
	"""The JSON an AI answers for a lead insight (autopilot.INSIGHT_PROMPT), with `overrides`."""
	return {
		"summary": "Owner of a textile shop who wants billing software; asked for a demo.",
		"sentiment": "Positive",
		"intent": "High",
		"signals": ["asked for a demo"],
		"next_action": {
			"what": "Call to fix the demo",
			"why": "They asked for a demo",
			"channel": "call",
			"due": str(add_days(nowdate(), 2)),
		},
		"suggested_stage": "Positive / Interested",
		"stage_reason": "They asked for a demo",
		"message": {
			"channel": "whatsapp",
			"subject": None,
			"text": "Hello, thanks for your interest. Shall we set up the demo this week? Regards, Anu",
		},
		**overrides,
	}


def make_lead_email(lead: str, subject: str, content: str, received: bool = True) -> str:
	"""An email (Communication) on a lead, as a received or sent mail leaves it."""
	email = frappe.get_doc(
		{
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "Email",
			"sent_or_received": "Received" if received else "Sent",
			"subject": subject,
			"content": content,
			"reference_doctype": "Lead",
			"reference_name": lead,
			"communication_date": now_datetime(),
		}
	)
	email.insert(ignore_permissions=True)
	return email.name


def make_migration_note(lead: str, old_id: str = "CRM-LEAD-2025-00001", **fields) -> str:
	"""The note the 2026-10-14 migration left on a lead, as the cutover copies it (a Comment
	starting with its title): "Label: value" lines for status, source, owner, created, remarks,
	priority and project_type (labels as the parser reads them)."""
	labels = {
		"status": "Status",
		"source": "Source",
		"owner": "Lead Owner",
		"created": "Created On",
		"priority": "Priority",
		"project_type": "Project Type",
		"remarks": "Remarks",
	}
	content = "".join(f"<p>{labels[key]}: {value}</p>" for key, value in fields.items())
	return make_lead_note(lead, note_html(f"Migrated from tboindia-old {old_id}", content))


def make_lead_note(lead: str, text: str, days_ago: int = 0) -> str:
	"""A note (Comment) on a lead, its creation moved `days_ago` days back."""
	note = frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"content": text,
			"reference_doctype": "Lead",
			"reference_name": lead,
		}
	)
	note.insert(ignore_permissions=True)
	if days_ago:
		frappe.db.set_value(
			"Comment", note.name, "creation", add_days(now_datetime(), -days_ago), update_modified=False
		)
	return note.name


def make_lead_task(
	lead: str, due, assigned_to: str, status: str = "Open", task_type: str | None = "Sales follow-up"
) -> str:
	"""An open ToDo (a follow-up) on a lead due on `due`'s day, inserted as Administrator."""
	task = frappe.get_doc(
		{
			"doctype": "ToDo",
			"description": "Follow up",
			"status": status,
			"priority": "Medium",
			"allocated_to": assigned_to,
			"date": getdate(due),
			"reference_type": "Lead",
			"reference_name": lead,
			"tbo_task_type": task_type,
		}
	)
	task.insert(ignore_permissions=True)
	return task.name


def change_lead_stage(lead: str, stage: str, **fields) -> None:
	"""Save a new stage on a lead as Administrator, so its stage log keeps the change."""
	doc = frappe.get_doc("Lead", lead)
	doc.update({"tbo_stage": stage, **fields})
	doc.save(ignore_permissions=True)


def age_lead(lead: str, days: int) -> None:
	"""Move a lead's creation `days` back (and its first stage log row with it)."""
	when = add_days(now_datetime(), -days)
	frappe.db.set_value("Lead", lead, "creation", when, update_modified=False)
	frappe.db.set_value(
		"TBO Stage Change", {"parent": lead, "idx": 1}, "changed_on", when, update_modified=False
	)


# ---------- Lead pipeline (docs/lead-pipeline.md) ----------


def enable_lead_pipeline(**values) -> None:
	set_smart_lead_settings(enable_lead_pipeline=1, meeting_reminder_minutes=60, **values)


def disable_lead_pipeline() -> None:
	set_smart_lead_settings(enable_lead_pipeline=0)


def hours_from_now(hours: float):
	"""The datetime `hours` from now (negative: in the past), for meeting times."""
	return add_to_date(now_datetime(), hours=hours)


def make_lead_meeting(lead: str, scheduled_at=None, **fields) -> str:
	"""A TBO Lead Meeting on a lead, inserted as Administrator (an hour from now by default)."""
	meeting = frappe.get_doc(
		{
			"doctype": "TBO Lead Meeting",
			"lead": lead,
			"scheduled_at": scheduled_at or add_to_date(now_datetime(), hours=1),
			"meeting_type": "Online",
			"meeting_link": "https://meet.google.com/abc-defg-hij",
			**fields,
		}
	)
	meeting.insert(ignore_permissions=True)
	return meeting.name


def move_meeting(meeting: str, scheduled_at, **values) -> None:
	"""Put a meeting at another time straight in the database (as if the clock had moved)."""
	frappe.db.set_value("TBO Lead Meeting", meeting, {"scheduled_at": scheduled_at, **values})


def delete_lead_deals(leads: list[str] | tuple) -> None:
	"""Delete the deals made from these leads, with their child rows."""
	deals = frappe.get_all(
		"Opportunity",
		filters={"tbo_lead": ("in", list(leads) or [""])},
		pluck="name",
	)
	if deals:
		for child in ("Opportunity Item", "TBO Stage Change", "Opportunity Lost Reason Detail", "CRM Note"):
			frappe.db.delete(child, {"parenttype": "Opportunity", "parent": ("in", deals)})
		frappe.db.delete("Opportunity", {"name": ("in", deals)})


def set_lead_values(lead: str, **values) -> None:
	"""Write lead fields straight in the database, as the old site's data came in."""
	frappe.db.set_value("Lead", lead, values, update_modified=False)


def delete_lead_meetings(leads: list[str] | tuple) -> None:
	"""Delete the meetings of these leads and commit."""
	if leads:
		meetings = frappe.get_all("TBO Lead Meeting", filters={"lead": ("in", list(leads))}, pluck="name")
		if meetings:
			frappe.db.delete("TBO Lead Meeting Action", {"parent": ("in", meetings)})
			frappe.db.delete("TBO Lead Meeting", {"name": ("in", meetings)})
	frappe.db.commit()  # nosemgrep


def make_gst_template(rate: float = 18, company: str | None = None) -> str:
	"""A Sales Taxes and Charges Template "TBO GST <rate>%" on the net total, for the company."""
	company = company or erpnext.get_default_company()
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"TBO GST {rate:g}% - {abbr}"
	if not frappe.db.exists("Sales Taxes and Charges Template", name):
		frappe.get_doc(
			{
				"doctype": "Sales Taxes and Charges Template",
				"title": f"TBO GST {rate:g}%",
				"company": company,
				"taxes": [
					{
						"charge_type": "On Net Total",
						"account_head": get_vat_account(company),
						"description": f"GST {rate:g}%",
						"rate": rate,
					}
				],
			}
		).insert(ignore_permissions=True)
	return name


def set_leaf_customer_group() -> None:
	"""Selling Settings' default customer group on a leaf group: a fresh test site's default is
	the "All Customer Groups" group node, which ERPNext refuses for a new Customer."""
	group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
	frappe.db.set_single_value("Selling Settings", "customer_group", group)
	# a new Customer takes it from the defaults, which Selling Settings' own save would set
	frappe.db.set_default("customer_group", group)


def quotation_line(
	item_code: str, qty: float = 1, rate: float = 1000, discount: float = 0, description=None
) -> dict:
	"""One line of the quotation builder's payload (save_quotation's `items`)."""
	return {
		"item_code": item_code,
		"qty": qty,
		"price_list_rate": rate,
		"discount_percentage": discount,
		"description": description,
	}


def make_pdf_bytes() -> bytes:
	"""A real one-page PDF, for mocking the print renderer (File parses PDFs on save)."""
	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=200, height=200)
	buffer = io.BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


def enable_lead_quotations(**values) -> None:
	set_smart_lead_settings(
		enable_lead_pipeline=1,
		enable_lead_quotations=1,
		quotation_validity_days=30,
		quotation_follow_up_days=3,
		quotation_preset_roles="Sales Manager\nSystem Manager",
		**values,
	)


def disable_lead_quotations() -> None:
	set_smart_lead_settings(enable_lead_pipeline=0, enable_lead_quotations=0)


def delete_lead_quotations(leads: list[str] | tuple) -> None:
	"""Cancel and delete the quotations and sales orders of these leads, and commit."""
	if not leads:
		return
	quotations = frappe.get_all("Quotation", filters={"tbo_lead": ("in", list(leads))}, pluck="name")
	if not quotations:
		return
	for order in frappe.get_all(
		"Sales Order Item", filters={"prevdoc_docname": ("in", quotations)}, pluck="parent", distinct=True
	):
		doc = frappe.get_doc("Sales Order", order)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Sales Order", order, force=True, ignore_permissions=True)
	for name in quotations:
		doc = frappe.get_doc("Quotation", name)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Quotation", name, force=True, ignore_permissions=True)
	frappe.db.delete("TBO Quotation Adjustment", {"quotation": ("in", quotations)})
	frappe.db.delete("File", {"attached_to_doctype": "Lead", "attached_to_name": ("in", list(leads))})
	frappe.db.commit()  # nosemgrep


def delete_owner_lead_records(owners: list[str] | tuple) -> None:
	"""Delete the proposals, quotations, deals, smart-lead records and leads of these lead owners,
	and commit."""
	leads = frappe.get_all("Lead", filters={"lead_owner": ("in", list(owners))}, pluck="name")
	delete_lead_proposals(leads)
	delete_lead_quotations(leads)
	delete_lead_deals(leads)
	delete_smart_lead_records(owners)


def save_lead_quotation(lead: str, item_code: str, rate: float, discount: float = 0, quotation=None) -> dict:
	"""A lead quotation of one line through the builder (lead_quotation.save_quotation) as the
	session user, `discount` the additional discount %, valid for 20 days. Returns its payload."""
	from tbo_smart.api import lead_quotation

	payload = {
		"valid_till": add_days(nowdate(), 20),
		"items": [quotation_line(item_code, 1, rate)],
		"additional_discount_percentage": discount,
	}
	return lead_quotation.save_quotation(lead, json.dumps(payload), quotation)


def make_selling_price_list(name: str, selling: int = 1) -> str:
	"""A Price List in the company currency (selling, or buying only with `selling=0`)."""
	if not frappe.db.exists("Price List", name):
		frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": name,
				"currency": frappe.get_cached_value(
					"Company", erpnext.get_default_company(), "default_currency"
				),
				"selling": selling,
				"buying": 0 if selling else 1,
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	return name


def set_segment_rates(rows: list[dict] | tuple = ()) -> None:
	"""Replace TBO Smart Settings' segment rate card with `rows` ({segment, price_list,
	max_discount}); no rows clears it."""
	settings = frappe.get_single("TBO Smart Settings")
	settings.set("segment_rates", [dict(row) for row in rows])
	settings.save(ignore_permissions=True)


def get_item_rate(item_code: str, price_list: str) -> float | None:
	"""The item's selling rate on a price list, or None when it has none there."""
	return frappe.db.get_value(
		"Item Price", {"item_code": item_code, "price_list": price_list, "selling": 1}, "price_list_rate"
	)


def make_outgoing_email_account() -> str:
	"""A default outgoing Email Account (no SMTP login; emails are muted in tests), if the site
	has none."""
	name = frappe.db.get_value("Email Account", {"default_outgoing": 1, "enable_outgoing": 1}, "name")
	if name:
		return name
	return (
		frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": "TBO Smart Test Outbox",
				"email_id": "outbox@tbosmart.test",
				"enable_outgoing": 1,
				"default_outgoing": 1,
				"smtp_server": "localhost",
				"no_smtp_authentication": 1,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def enable_proposals(ai: bool = False, **values) -> None:
	"""Quotations on the lead and Proposals on (with `ai`, AI proposal drafts too), with a daily AI
	limit no test run reaches unless `values` sets one."""
	enable_lead_quotations(
		enable_proposals=1,
		enable_proposal_ai=int(ai),
		proposal_validity_days=30,
		# high, so a day of test runs never hits it (test_the_daily_limit sets its own)
		**{"proposal_ai_daily_limit": 100000, **values},
	)


def disable_proposals() -> None:
	set_smart_lead_settings(enable_proposals=0, enable_proposal_ai=0)


def delete_lead_proposals(leads: list[str] | tuple) -> None:
	"""Delete the proposals of these leads with their blocks and image files, and commit."""
	names = frappe.get_all("TBO Proposal", filters={"lead": ("in", list(leads) or [""])}, pluck="name")
	if names:
		for file in frappe.get_all(
			"File",
			filters={"attached_to_doctype": "TBO Proposal", "attached_to_name": ("in", names)},
			pluck="name",
		):
			frappe.delete_doc("File", file, ignore_permissions=True, force=True)
		frappe.db.delete("TBO Proposal Section", {"parenttype": "TBO Proposal", "parent": ("in", names)})
		frappe.db.delete("TBO Proposal", {"name": ("in", names)})
	frappe.db.commit()  # nosemgrep


CLIENT_PROFILE = {
	"website": "https://client.example",
	"industry": "Spice export",
	"size": "120 staff",
	"locations": "Kochi",
	"current_software": "Tally and Excel",
	"key_processes": "Procurement, processing, export",
	"pain_points": "Payroll by hand, GST in Excel",
}


def make_hub_proposal(
	lead: str, acting_user: str, template: str | None = None, ai_draft: int = 0, scoping: dict | None = None
) -> dict:
	"""A proposal for the lead through the hub API (hub_proposals.create_proposal) as `acting_user`,
	with a filled client profile (CLIENT_PROFILE) and `scoping`; returns the proposal's payload."""
	from tbo_smart.api import hub_proposals

	return hub_proposals.create_proposal(
		lead,
		template,
		ai_draft,
		json.dumps(scoping) if scoping else None,
		None,
		json.dumps(CLIENT_PROFILE),
		acting_user=acting_user,
	)["proposal"]


def price_hub_proposal(
	name: str, acting_user: str, item_code: str, rate: float = 100000, discount: float = 0
) -> dict:
	"""Price a proposal with one line through hub_proposals.save_pricing, `discount` the additional
	discount %; returns the proposal's payload."""
	from tbo_smart.api import hub_proposals

	values = {
		"valid_till": add_days(nowdate(), 20),
		"items": [quotation_line(item_code, 1, rate)],
		"additional_discount_percentage": discount,
	}
	return hub_proposals.save_pricing(name, json.dumps(values), acting_user=acting_user)


def send_hub_proposal(name: str, acting_user: str, **kwargs) -> tuple:
	"""hub_proposals.send_proposal with Frappe's email `make` mocked; returns (payload, the mock)."""
	from tbo_smart.api import hub_proposals

	with patch("frappe.core.doctype.communication.email.make") as make:
		result = hub_proposals.send_proposal(name, acting_user=acting_user, **kwargs)
	return result, make


def make_sent_proposal(lead: str, acting_user: str, template: str, item_code: str) -> tuple:
	"""A proposal from `template`, priced with one line of `item_code` and sent (email mocked);
	returns (name, link token)."""
	name = make_hub_proposal(lead, acting_user, template)["name"]
	price_hub_proposal(name, acting_user, item_code)
	send_hub_proposal(name, acting_user)
	return name, proposal_token(name)


def proposal_token(name: str) -> str:
	"""The proposal's link token."""
	return frappe.db.get_value("TBO Proposal", name, "public_token")


def call_as_guest(fn, *args, **kwargs):
	"""Call `fn` as Guest (the client on the proposal page), then return to the session user."""
	user = frappe.session.user
	frappe.set_user("Guest")
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user(user)


def make_image_bytes(kind: str = "PNG", size: tuple = (4, 4)) -> bytes:
	"""A real image of `kind` (PNG, JPEG, WEBP, GIF) for upload tests."""
	from PIL import Image

	buffer = io.BytesIO()
	Image.new("RGB", size, (58, 116, 141)).save(buffer, format=kind)
	return buffer.getvalue()


def upload_hub_image(name: str, acting_user: str, content: bytes, filename: str = "photo.png") -> dict:
	"""hub_proposals.upload_proposal_image with `content` base64-encoded; returns {file, url}."""
	from tbo_smart.api import hub_proposals

	return hub_proposals.upload_proposal_image(
		name, filename, base64.b64encode(content).decode(), acting_user=acting_user
	)


def make_private_file(content: bytes, filename: str = "private.png", **attached) -> str:
	"""A private File (optionally attached via attached_to_doctype / attached_to_name)."""
	return (
		frappe.get_doc(
			{"doctype": "File", "file_name": filename, "is_private": 1, "content": content, **attached}
		)
		.insert(ignore_permissions=True)
		.name
	)


def make_letter_head(name: str, header_html: str = "", footer_html: str = "", **values) -> str:
	"""A Letter Head (HTML header and footer unless `values` say otherwise), replacing one of
	the same name. With is_default=1 it becomes the site's default."""
	if frappe.db.exists("Letter Head", name):
		frappe.delete_doc("Letter Head", name, force=True)
	values = {
		"source": "HTML",
		"content": header_html,
		"footer_source": "HTML",
		"footer": footer_html,
		**values,
	}
	doc = frappe.get_doc({"doctype": "Letter Head", "letter_head_name": name, **values})
	doc.insert(ignore_permissions=True)
	# Letter Head's before_insert always sets source to Image; put the intended one back
	doc.db_set({"source": values["source"], "content": values["content"]})
	return doc.name


def clear_default_letter_heads() -> None:
	"""No Letter Head is the default, and TBO Smart Settings → Proposal letter head is blank."""
	frappe.db.set_value("Letter Head", {"is_default": 1}, "is_default", 0)
	set_smart_lead_settings(proposal_letter_head=None)


def make_library_image(title: str, sections: str = "Cover", **values) -> str:
	"""A proposal library image (a public PNG) tagged for `sections` (one per line)."""
	url = (
		frappe.get_doc(
			{"doctype": "File", "file_name": f"{title}.png", "is_private": 0, "content": make_image_bytes()}
		)
		.insert(ignore_permissions=True)
		.file_url
	)
	return (
		frappe.get_doc(
			{
				"doctype": "TBO Proposal Image",
				"title": title,
				"image": url,
				"alt_text": f"{title} picture",
				"sections": sections,
				**values,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def page_response(html: str, url: str = "https://client.example/") -> MagicMock:
	"""A requests response streaming `html`, for reading a client's website."""
	response = MagicMock()
	response.__enter__.return_value = response
	response.status_code = 200
	response.is_redirect = False
	response.headers = {"Content-Type": "text/html; charset=utf-8"}
	response.encoding = "utf-8"
	response.iter_content.return_value = [html.encode()]
	return response


def set_proposal_values(name: str, **values) -> None:
	"""Write proposal fields straight in the database (e.g. a validity already past)."""
	frappe.db.set_value("TBO Proposal", name, values, update_modified=False)


def make_approved_leave(employee: str, leave_type: str, from_date, to_date=None, half_day: int = 0) -> str:
	"""A submitted, Approved Leave Application (half_day on the first day when asked)."""
	application = frappe.get_doc(
		{
			"doctype": "Leave Application",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": from_date,
			"to_date": to_date or from_date,
			"half_day": half_day,
			"half_day_date": from_date if half_day else None,
			"status": "Approved",
			"posting_date": nowdate(),
		}
	)
	application.insert(ignore_permissions=True)
	application.submit()
	return application.name


def make_role(name: str) -> str:
	"""A desk Role, created when missing."""
	if not frappe.db.exists("Role", name):
		frappe.get_doc({"doctype": "Role", "role_name": name, "desk_access": 1}).insert(
			ignore_permissions=True
		)
	return name


def set_user_enabled(email: str, enabled: bool) -> None:
	frappe.db.set_value("User", email, "enabled", int(enabled))


def delete_smart_lead_records(owners: list[str] | tuple) -> None:
	"""Delete the leads of `owners` with every note, call, task and triage row on them, and
	commit (the digest's marker and its emails commit, so fixtures can outlive the rollback)."""
	leads = frappe.get_all("Lead", filters={"lead_owner": ("in", list(owners))}, pluck="name")
	if leads:
		frappe.db.delete("Comment", {"reference_doctype": "Lead", "reference_name": ("in", leads)})
		frappe.db.delete("Communication", {"reference_doctype": "Lead", "reference_name": ("in", leads)})
		frappe.db.delete("ToDo", {"reference_type": "Lead", "reference_name": ("in", leads)})
		events = frappe.get_all(
			"Event Participants",
			filters={"reference_doctype": "Lead", "reference_docname": ("in", leads)},
			pluck="parent",
		)
		if events:
			frappe.db.delete("Event Participants", {"parent": ("in", events)})
			frappe.db.delete("Event", {"name": ("in", events)})
		frappe.db.delete("TBO Lead Triage Row", {"lead": ("in", leads)})
		frappe.db.delete("TBO Stage Change", {"parenttype": "Lead", "parent": ("in", leads)})
	frappe.db.delete("ToDo", {"allocated_to": ("in", list(owners)), "reference_type": "Lead"})
	frappe.db.delete("Lead", {"lead_owner": ("in", list(owners))})
	frappe.db.delete("TBO Lead Triage", {"name": ("like", "TBO-TRIAGE-%")})
	frappe.db.commit()  # nosemgrep


def delete_lead_pipeline_records(owners: list[str] | tuple) -> None:
	"""Delete the leads of `owners` with their quotations, meetings and deals, and every note,
	call, task and stage change on them (delete_smart_lead_records), and commit."""
	leads = frappe.get_all("Lead", filters={"lead_owner": ("in", list(owners))}, pluck="name")
	delete_lead_quotations(leads)
	delete_lead_meetings(leads)
	delete_lead_deals(leads)
	delete_smart_lead_records(owners)


def get_fixture(name: str) -> dict:
	"""A response fixture from docs/fixtures (the mobile contract's examples)."""
	path = frappe.get_app_path("tbo_smart", "..", "docs", "fixtures", f"{name}.json")
	with open(path) as handle:
		return json.load(handle)


# ---------- Single sign-in with Microsoft ----------


@functools.cache
def get_test_rsa_key(label: str = "signing") -> rsa.RSAPrivateKey:
	"""A 2048-bit RSA private key generated once per test run; `label` gives a different key."""
	return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_microsoft_id_token(
	address: str, key: rsa.RSAPrivateKey | None = None, kid: str = MS_TEST_KEY_ID, **claims
) -> str:
	"""An RS256 ID token shaped like Entra ID v2 for the test tenant and mobile client.

	`address` goes into `email` and `preferred_username`. Signed with the test key unless
	`key` is given, with header `kid`. `claims` override the defaults; a claim set to None is left out. Each call
	gets a fresh `uti`.
	"""
	now = int(time.time())
	payload = {
		"iss": f"{auth.MICROSOFT_LOGIN}/{MS_TEST_TENANT}/v2.0",
		"aud": MS_TEST_CLIENT_ID,
		"tid": MS_TEST_TENANT,
		"sub": frappe.generate_hash(length=20),
		"oid": frappe.generate_hash(length=20),
		"email": address,
		"preferred_username": address,
		"name": address.split("@")[0],
		"iat": now,
		"nbf": now,
		"exp": now + 3600,
		"uti": frappe.generate_hash(length=22),
		"ver": "2.0",
		**claims,
	}
	payload = {name: value for name, value in payload.items() if value is not None}
	return jwt.encode(payload, key or get_test_rsa_key(), algorithm="RS256", headers={"kid": kid})


def mock_microsoft_jwks():
	"""Patch the JWKS download so Microsoft's key set is the test public key.

	Also forgets the shared key set and the key-refresh cooldown. Use as a context manager or
	start()/stop(); the started mock counts the downloads.
	"""
	jwk = json.loads(RSAAlgorithm.to_jwk(get_test_rsa_key().public_key()))
	jwk.update({"kid": MS_TEST_KEY_ID, "use": "sig", "alg": "RS256"})
	frappe.cache.delete_value(auth.jwks_cache_key(MS_TEST_TENANT))
	frappe.cache.delete_value(auth.jwks_refresh_key(MS_TEST_TENANT), make_keys=False)
	return patch.object(jwt.PyJWKClient, "fetch_data", return_value={"keys": [jwk]})


def make_oauth_client(roles: list[str] | tuple = ("Employee",)) -> str:
	"""The test OAuth Client (scopes `all openid`, redirect tbosmart://oauth/callback) with
	exactly `roles` as its Allowed Roles. Returns its name, which is also its client_id."""
	name = frappe.db.get_value("OAuth Client", {"app_name": TEST_OAUTH_CLIENT_APP})
	client = frappe.get_doc("OAuth Client", name) if name else frappe.new_doc("OAuth Client")
	client.update(
		{
			"app_name": TEST_OAUTH_CLIENT_APP,
			"scopes": "all openid",
			"redirect_uris": "tbosmart://oauth/callback",
			"default_redirect_uri": "tbosmart://oauth/callback",
			"skip_authorization": 1,
			"grant_type": "Authorization Code",
			"response_type": "Code",
		}
	)
	client.set("allowed_roles", [{"role": role} for role in roles])
	client.save(ignore_permissions=True)
	return client.name


def enable_microsoft_sso(oauth_client: str) -> None:
	"""Turn single sign-in on for the test tenant, mobile client and `oauth_client`."""
	frappe.db.set_single_value(
		"TBO Smart Settings",
		{
			"enable_microsoft_sso": 1,
			"microsoft_tenant_id": MS_TEST_TENANT,
			"microsoft_mobile_client_id": MS_TEST_CLIENT_ID,
			"oauth_client": oauth_client,
		},
	)


def disable_microsoft_sso() -> None:
	frappe.db.set_single_value("TBO Smart Settings", "enable_microsoft_sso", 0)


def delete_oauth_tokens(oauth_client: str) -> None:
	"""Delete every OAuth Bearer Token of `oauth_client` and commit.

	Frappe's save_bearer_token commits, so tokens outlive the test's rollback."""
	frappe.db.delete("OAuth Bearer Token", {"client": oauth_client})
	# The tokens were committed by Frappe's OAuth validator, so the cleanup must persist too.
	frappe.db.commit()  # nosemgrep


def refresh_oauth_token(refresh_token: str, client_id: str) -> frappe._dict:
	"""Call Frappe's own `get_token` with grant_type=refresh_token, as the app does, and
	return the response body it sets."""
	environ = EnvironBuilder(
		method="POST",
		base_url="https://tbosmart.test",
		path="/api/method/frappe.integrations.oauth2.get_token",
		data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
	).get_environ()
	previous_request = getattr(frappe.local, "request", None)
	previous_response = frappe.local.response
	frappe.local.request = WerkzeugRequest(environ)
	try:
		get_token()
		return frappe._dict(frappe.local.response)
	finally:
		frappe.local.request = previous_request
		frappe.local.response = previous_response


def bearer_token_is_valid(access_token: str) -> bool:
	"""Whether Frappe's OAuth server accepts `access_token` for an `all` scoped Bearer call,
	the check frappe.auth.validate_oauth runs on every request."""
	valid, _request = get_oauth_server().verify_request(
		f"https://tbosmart.test/api/method/frappe.auth.get_logged_user?access_token={access_token}",
		"GET",
		None,
		{},
		["all"],
	)
	return valid


# ---------- The CRM cutover (docs/erpnext-lead-cutover.md) ----------

CUTOVER_TEST_ITEM = "TBO Smart Cutover Service"
# every table of Frappe CRM the cutover reads, emptied around its tests
CRM_TABLES = (
	"CRM Lead",
	"CRM Deal",
	"CRM Organization",
	"FCRM Note",
	"CRM Call Log",
	"CRM Task",
	"CRM Sales Hierarchy",
	"CRM Status Change Log",
	"CRM Products",
	"CRM Contacts",
)


def crm_installed() -> bool:
	"""Whether Frappe CRM is installed here; the cutover tests read its tables and skip without it."""
	return "crm" in frappe.get_installed_apps()


def clear_crm_cutover(owners: list[str] | tuple = ()) -> None:
	"""Empty the CRM's tables and delete what a cutover of the fixture `owners`' records copied,
	and commit (the CRM's inserts commit, so its fixtures outlive the test's rollback). Only in
	tests, on a test site: it empties every CRM table."""
	if not frappe.flags.in_test:
		frappe.throw(_("clear_crm_cutover runs only in tests."))
	for doctype in CRM_TABLES:
		frappe.db.delete(doctype)
	for doctype, field, owner_field in (
		("Lead", "crm_lead_ref", "lead_owner"),
		("Opportunity", "crm_deal_ref", "opportunity_owner"),
	):
		names = frappe.get_all(
			doctype, filters={field: ("is", "set"), owner_field: ("in", list(owners) or [""])}, pluck="name"
		)
		if names:
			frappe.db.delete("TBO Stage Change", {"parenttype": doctype, "parent": ("in", names)})
			frappe.db.delete("Comment", {"reference_doctype": doctype, "reference_name": ("in", names)})
			frappe.db.delete("ToDo", {"reference_type": doctype, "reference_name": ("in", names)})
			frappe.db.delete(doctype, {"name": ("in", names)})
	frappe.db.delete(
		"Comment", {"reference_doctype": "Prospect", "reference_name": ("like", "TBO Cutover %")}
	)
	frappe.db.delete("Event", {"name": ("like", "TBO-CALL-%")})
	frappe.db.delete("Event Participants", {"parent": ("like", "TBO-CALL-%")})
	frappe.db.delete("TBO Sales Hierarchy", {"user": ("in", list(owners))})
	frappe.db.delete("Prospect", {"name": ("like", "TBO Cutover %")})
	frappe.db.delete("Lead", {"lead_owner": ("in", list(owners))})
	frappe.db.delete("Opportunity", {"opportunity_owner": ("in", list(owners))})
	frappe.db.commit()  # nosemgrep


def ensure_crm_lead_status(name: str, kind: str) -> str:
	"""A CRM Lead Status, as the pipeline migration left them on tboindia."""
	if not frappe.db.exists("CRM Lead Status", name):
		frappe.get_doc({"doctype": "CRM Lead Status", "lead_status": name, "type": kind}).insert(
			ignore_permissions=True
		)
	return name


def ensure_quotation_crm_lead_field() -> None:
	"""The `tbo_crm_lead` link tboindia's quotations carried before the cutover (finalize() drops it)."""
	if not frappe.db.exists("Custom Field", {"dt": "Quotation", "fieldname": "tbo_crm_lead"}):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Quotation",
				"fieldname": "tbo_crm_lead",
				"fieldtype": "Data",
				"label": "CRM Lead",
			}
		).insert(ignore_permissions=True)


def make_crm_records(owner: str, manager: str) -> frappe._dict:
	"""A small Frappe CRM data set with the shapes tboindia has: a lead with a stage history, its
	migration note, a note, a call with its note, an open and a done task and an assignment; a
	won lead with its deal (an organization, a contact, a product, finance fields) and a quotation
	to each; the owner under `manager` in the sales hierarchy. Returns the names."""
	names = frappe._dict()
	for status, kind in (("Contacted", "Ongoing"), ("Proposal Sent", "Ongoing"), ("Closed – Won", "Won")):
		ensure_crm_lead_status(status, kind)
	top = frappe.get_doc({"doctype": "CRM Sales Hierarchy", "user": manager, "is_group": 1}).insert(
		ignore_permissions=True
	)
	frappe.get_doc({"doctype": "CRM Sales Hierarchy", "user": owner, "reports_to": top.name}).insert(
		ignore_permissions=True
	)
	lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": "Cutover",
			"last_name": "Lead",
			"organization": "Cutover Traders",
			"mobile_no": "9847000901",
			"email": f"cutover.{frappe.generate_hash(length=6)}@example.com",
			"lead_owner": owner,
			"status": "Contacted",
			"lead_score": 41,
			"lead_temperature": "Warm",
		}
	).insert(ignore_permissions=True)
	lead.status = "Proposal Sent"
	lead.save(ignore_permissions=True)
	names.lead = lead.name
	names.migration_note = (
		frappe.get_doc(
			{
				"doctype": "FCRM Note",
				"title": "Migrated from tboindia-old CRM-LEAD-2025-00777",
				"content": "<p><b>Status:</b> Interested</p><p><b>Source:</b> Website</p>",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead.name,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
	names.note = (
		frappe.get_doc(
			{
				"doctype": "FCRM Note",
				"title": "Wants the stock module",
				"content": "Wants the stock module",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead.name,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
	call_note = frappe.get_doc(
		{
			"doctype": "FCRM Note",
			"title": "Rang about the demo",
			"content": "Rang about the demo",
			"reference_doctype": "CRM Lead",
			"reference_docname": lead.name,
		}
	).insert(ignore_permissions=True)
	names.call = (
		frappe.get_doc(
			{
				"doctype": "CRM Call Log",
				"id": f"TBO-CALL-{frappe.generate_hash(length=8)}",
				"type": "Outgoing",
				"status": "Completed",
				"telephony_medium": "Manual",
				"from": "+919847000001",
				"to": "+919847000901",
				"caller": owner,
				"start_time": now_datetime(),
				"note": call_note.name,
				"reference_doctype": "CRM Lead",
				"reference_docname": lead.name,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
	names.open_task = make_crm_task(lead.name, "CRM Lead", owner, "Todo")
	names.done_task = make_crm_task(lead.name, "CRM Lead", owner, "Done")
	won_lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": "Cutover",
			"last_name": "Winner",
			"organization": "TBO Cutover Winner Co",
			"lead_owner": owner,
			"status": "Closed – Won",
		}
	).insert(ignore_permissions=True)
	names.won_lead = won_lead.name
	names.organization = (
		frappe.get_doc({"doctype": "CRM Organization", "organization_name": "TBO Cutover Winner Co"})
		.insert(ignore_permissions=True)
		.name
	)
	names.organization_note = (
		frappe.get_doc(
			{
				"doctype": "FCRM Note",
				"title": "Head office in Kochi",
				"content": "Head office in Kochi",
				"reference_doctype": "CRM Organization",
				"reference_docname": names.organization,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)
	deal = frappe.get_doc(
		{
			"doctype": "CRM Deal",
			"lead": won_lead.name,
			"organization": names.organization,
			"deal_owner": owner,
			"status": "Won",
			"deal_value": 120000,
			"next_step": "Kick-off",
		}
	).insert(ignore_permissions=True)
	names.deal = deal.name
	# the finance fields the deals carried on tboindia
	create_custom_fields(
		{
			"CRM Deal": [
				{"fieldname": "tbo_stage", "fieldtype": "Data", "label": "Post-sale stage"},
				{
					"fieldname": "tbo_onboarding_complete",
					"fieldtype": "Check",
					"label": "Onboarding complete",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.set_value("CRM Deal", deal.name, {"tbo_stage": "Invoiced", "tbo_onboarding_complete": 1})
	names.deal_task = make_crm_task(deal.name, "CRM Deal", owner, "Todo")
	names.lead_quotation = make_cutover_quotation({"quotation_to": "CRM Lead", "party_name": lead.name})
	names.deal_quotation = make_cutover_quotation({"quotation_to": "CRM Deal", "party_name": deal.name})
	frappe.get_doc(
		{
			"doctype": "ToDo",
			"description": "Assigned in the CRM",
			"allocated_to": owner,
			"reference_type": "CRM Lead",
			"reference_name": lead.name,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()  # nosemgrep
	return names


def make_cutover_quotation(party: dict) -> str:
	"""A draft Quotation made out to a CRM record, as the CRM's quotations were stored. It has its
	own item: ERPNext gives an item a price the first time it is quoted, which would change the
	other tests' list prices."""
	quotation = frappe.get_doc(
		{
			"doctype": "Quotation",
			"quotation_to": "Customer",
			"party_name": make_customer(f"TBO Cutover Client {frappe.generate_hash(length=6)}"),
			"company": erpnext.get_default_company(),
			"transaction_date": nowdate(),
			"valid_till": add_days(nowdate(), 30),
			"items": [{"item_code": make_quotation_item(CUTOVER_TEST_ITEM), "qty": 1, "rate": 5000}],
		}
	).insert(ignore_permissions=True)
	frappe.db.set_value("Quotation", quotation.name, party)
	return quotation.name


def make_crm_task(reference: str, doctype: str, owner: str, status: str) -> str:
	"""A Frappe CRM Task on a CRM record, due tomorrow."""
	return (
		frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": f"Follow up ({status})",
				"status": status,
				"assigned_to": owner,
				"due_date": add_days(now_datetime(), 1),
				"reference_doctype": doctype,
				"reference_docname": reference,
				"tbo_task_type": "Sales follow-up",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


# ---------- Finance workflow (docs/crm-finance-workflow.md) ----------


FINANCE_TEST_ITEM = "TBO Smart Finance Service"


def make_finance_item() -> str:
	"""The finance tests' own sales item. ERPNext adds an Item Price the first time an item is
	sold at a rate, so sharing the quotation tests' item would change their list price."""
	return make_quotation_item(FINANCE_TEST_ITEM)


def enable_finance_workflow(**settings) -> None:
	"""Turn the finance workflow Phase 1 on; `settings` sets other TBO Smart Settings fields
	(e.g. finance_crm_access=1, finance_bde_can_see_amounts=0)."""
	frappe.db.set_single_value("TBO Smart Settings", {"enable_finance_workflow": 1, **settings})


def disable_finance_workflow() -> None:
	frappe.db.set_single_value("TBO Smart Settings", "enable_finance_workflow", 0)


def make_finance_deal(owner: str, **fields) -> str:
	"""A deal (Opportunity) owned by `owner` for its own new ERPNext Customer. Returns its name."""
	customer = make_customer(f"TBO Finance {frappe.generate_hash(length=8)}")
	return make_deal(owner, opportunity_from="Customer", party_name=customer, **fields)


def deal_customer(deal: str) -> str | None:
	"""The deal's ERPNext Customer."""
	return customer_of(frappe.get_doc("Opportunity", deal))


def make_deal_quotation(deal: str, rate: float = 1000, qty: float = 1, submit: bool = True, **fields):
	"""A Quotation to the deal's customer linked to the deal (`opportunity`), inserted as the
	session user and submitted unless `submit` is False. Returns the Document."""
	quotation = frappe.get_doc(
		{
			"doctype": "Quotation",
			"quotation_to": "Customer",
			"party_name": deal_customer(deal),
			"opportunity": deal,
			"company": erpnext.get_default_company(),
			"transaction_date": nowdate(),
			"valid_till": add_days(nowdate(), 30),
			"items": [{"item_code": make_finance_item(), "qty": qty, "rate": rate}],
			**fields,
		}
	)
	quotation.insert(ignore_permissions=True)
	if submit:
		quotation.submit()
	return quotation


def make_order_from_quotation(quotation: str, submit: bool = True, keep_deal: bool = True):
	"""A Sales Order mapped from a submitted Quotation by ERPNext's own mapper, delivery in a
	week. `keep_deal=False` clears the deal (`opportunity`) the mapper copies, to exercise the
	hook."""
	from erpnext.selling.doctype.quotation.quotation import make_sales_order

	order = make_sales_order(quotation)
	order.delivery_date = add_days(nowdate(), 7)
	if not keep_deal:
		order.opportunity = None
	order.insert(ignore_permissions=True)
	if submit:
		order.submit()
	return order


def make_manual_order(quotation: str, rate: float = 1000):
	"""An unsaved Sales Order typed by hand that references `quotation` on its item (no
	mapper), for the pending-approval guard and the deal-link hook."""
	source = frappe.get_doc("Quotation", quotation)
	return frappe.get_doc(
		{
			"doctype": "Sales Order",
			"customer": source.party_name
			if source.quotation_to == "Customer"
			else make_customer(f"TBO Manual {frappe.generate_hash(length=8)}"),
			"company": source.company,
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 7),
			"items": [
				{
					"item_code": source.items[0].item_code,
					"qty": source.items[0].qty,
					"rate": rate,
					"prevdoc_docname": quotation,
				}
			],
		}
	)


def make_invoice_from_order(order: str, submit: bool = True, keep_deal: bool = True):
	"""A Sales Invoice mapped from a submitted Sales Order. `keep_deal=False` clears the deal
	(`opportunity`) the mapper copies."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	invoice = make_sales_invoice(order)
	if not keep_deal:
		invoice.opportunity = None
	invoice.insert(ignore_permissions=True)
	if submit:
		invoice.submit()
	return invoice


def make_receipt(doctype: str, name: str, amount: float | None = None, submit: bool = True):
	"""A customer receipt (Payment Entry) against a Sales Order or Sales Invoice, for `amount`
	or the full outstanding, into the company's default cash account."""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	entry = get_payment_entry(doctype, name, party_amount=amount)
	entry.reference_no = frappe.generate_hash(length=8)
	entry.reference_date = nowdate()
	entry.insert(ignore_permissions=True)
	if submit:
		entry.submit()
	return entry


def get_versions(doctype: str, name: str) -> list[dict]:
	"""The changed fields of every Version of a document, oldest first: [{field: (old, new)}]."""
	rows = frappe.get_all(
		"Version", filters={"ref_doctype": doctype, "docname": name}, pluck="data", order_by="creation asc"
	)
	changes = []
	for data in rows:
		changed = json.loads(data).get("changed") or []
		changes.append({field: (old, new) for field, old, new in changed})
	return changes


def reset_finance_settings() -> None:
	"""Finance workflow off and its other settings back to their defaults."""
	frappe.db.set_single_value(
		"TBO Smart Settings",
		{
			"enable_finance_workflow": 0,
			"finance_bde_can_see_amounts": 1,
			"finance_crm_access": 0,
			"finance_crm_roles": "Accounts User\nAccounts Manager",
			"enable_finance_receivables": 0,
			"send_client_reminders": 0,
			"weekly_client_reminders": 0,
			"invoicing_company": None,
			"enable_fund_allocation": 0,
			"enable_reconciliation": 0,
			"enable_finance_dashboard": 0,
			"second_approval_amount": 0,
			"partner_company": None,
			"accounts_lead": None,
			"escalation_sales_head": None,
			"escalation_finance_manager": None,
			"management_digest_recipients": None,
		},
	)


def enable_finance_receivables(**settings) -> None:
	"""Turn Phase 1 and Phase 2 (receivables and reminders) on; `settings` sets other fields."""
	enable_finance_workflow(enable_finance_receivables=1, **settings)


def make_deal_invoice(
	deal: str, rate: float = 1000, due_in: int = 0, posted_days_ago: int = 0, submit: bool = True
):
	"""A Sales Invoice to the deal's customer linked to the deal, posted `posted_days_ago`
	days ago and due `due_in` days from today (negative: already overdue). One payment-term
	row, as ERPNext builds it from the due date. Returns the Document."""
	posting = add_days(nowdate(), -max(posted_days_ago, -due_in if due_in < 0 else 0))
	invoice = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"customer": deal_customer(deal),
			"opportunity": deal,
			"company": erpnext.get_default_company(),
			"set_posting_time": 1,
			"posting_date": posting,
			"due_date": add_days(nowdate(), due_in),
			"items": [{"item_code": make_finance_item(), "qty": 1, "rate": rate}],
		}
	)
	invoice.insert(ignore_permissions=True)
	if submit:
		invoice.submit()
	return invoice


def get_terms(invoice: str) -> list[frappe._dict]:
	"""An invoice's Payment Schedule rows with their reminder markers."""
	return frappe.get_all(
		"Payment Schedule",
		filters={"parent": invoice, "parenttype": "Sales Invoice"},
		fields=["name", "due_date", "outstanding", "tbo_last_reminder", "tbo_last_reminder_on"],
		order_by="idx",
	)


def get_collection_task(invoice: str) -> frappe._dict | None:
	"""The invoice's collection ToDo, if any."""
	name = frappe.db.get_value("ToDo", {"tbo_sales_invoice": invoice, "tbo_task_type": "Collection"})
	return frappe.get_doc("ToDo", name) if name else None


# ---------- Finance workflow Phase 3: allocation and transfers ----------

PARTNER_TEST_COMPANY = "TBO Partner Test Company"


def make_partner_company() -> str:
	"""A second Company (INR, India, standard chart) standing in for TBO India."""
	if not frappe.db.exists("Company", PARTNER_TEST_COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": PARTNER_TEST_COMPANY,
				"abbr": "TPTC",
				"default_currency": "INR",
				"country": "India",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert(ignore_permissions=True)
	return PARTNER_TEST_COMPANY


def enable_fund_allocation(**settings) -> None:
	"""Phases 1 and 3 on: the default company invoices, the partner test company gets 50%,
	allocation from today. `settings` overrides any TBO Smart Settings field."""
	values = {
		"enable_fund_allocation": 1,
		"invoicing_company": erpnext.get_default_company(),
		"partner_company": make_partner_company(),
		"allocation_percent": 50,
		"allocation_start_date": nowdate(),
		"default_vat_rate": 15,
		"amount_tolerance": 1,
		"allocation_base_after_bank_charges": 0,
		"finance_crm_access": 1,
	}
	enable_finance_workflow(**{**values, **settings})


def get_vat_account(company: str | None = None) -> str:
	"""A 'TBO VAT' tax account of the company, under its Duties and Taxes group."""
	company = company or erpnext.get_default_company()
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"TBO VAT - {abbr}"
	if not frappe.db.exists("Account", name):
		parent = frappe.db.get_value(
			"Account", {"company": company, "is_group": 1, "account_name": ("like", "%Duties and Taxes%")}
		) or frappe.db.get_value("Account", {"company": company, "is_group": 1, "root_type": "Liability"})
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "TBO VAT",
				"parent_account": parent,
				"company": company,
				"account_type": "Tax",
				"tax_rate": 15,
			}
		).insert(ignore_permissions=True)
	return name


def make_vat_order(deal: str, rate: float, vat: float = 15, submit: bool = True):
	"""A Sales Order for the deal's customer: one line at `rate` plus `vat`% VAT on net total."""
	order = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"customer": deal_customer(deal),
			"opportunity": deal,
			"company": erpnext.get_default_company(),
			"transaction_date": nowdate(),
			"delivery_date": add_days(nowdate(), 30),
			"items": [{"item_code": make_finance_item(), "qty": 1, "rate": rate}],
			"taxes": [
				{
					"charge_type": "On Net Total",
					"account_head": get_vat_account(),
					"description": "VAT",
					"rate": vat,
				}
			],
		}
	)
	order.insert(ignore_permissions=True)
	if submit:
		order.submit()
	return order


def make_invoice_with_advances(order: str):
	"""A submitted Sales Invoice from the order with its advance receipts pulled in."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	invoice = make_sales_invoice(order)
	invoice.set_advances()
	invoice.insert(ignore_permissions=True)
	invoice.submit()
	return invoice


def make_internal_payment(company: str, amount: float, payment_type: str = "Pay", party: str | None = None):
	"""A submitted cash Payment Entry of `company`: Pay to a supplier (the sending leg of a
	transfer) or Receive from a customer (India's receipt)."""
	from erpnext.accounts.party import get_party_account

	cash = frappe.get_cached_value("Company", company, "default_cash_account")
	if payment_type == "Pay":
		party_type = "Supplier"
		party = party or make_test_supplier("TBO Partner Supplier")
		paid_from, paid_to = cash, get_party_account(party_type, party, company)
	else:
		party_type = "Customer"
		party = party or make_customer(f"TBO Partner Customer {frappe.generate_hash(length=6)}")
		paid_from, paid_to = get_party_account(party_type, party, company), cash
	entry = frappe.get_doc(
		{
			"doctype": "Payment Entry",
			"payment_type": payment_type,
			"company": company,
			"posting_date": nowdate(),
			"party_type": party_type,
			"party": party,
			"paid_from": paid_from,
			"paid_to": paid_to,
			"paid_amount": amount,
			"received_amount": amount,
			"source_exchange_rate": 1,
			"target_exchange_rate": 1,
			"reference_no": frappe.generate_hash(length=8),
			"reference_date": nowdate(),
		}
	)
	entry.insert(ignore_permissions=True)
	entry.submit()
	return entry


def make_test_supplier(name: str) -> str:
	"""A Supplier in the first leaf supplier group."""
	if not frappe.db.exists("Supplier", name):
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": name,
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
			}
		).insert(ignore_permissions=True)
	return name


def get_allocation(payment_entry: str, kind: str = "Receipt") -> frappe._dict | None:
	"""The submitted allocation of a receipt (or its reversal) with its lines."""
	name = frappe.db.get_value(
		"TBO Fund Allocation", {"payment_entry": payment_entry, "allocation_type": kind, "docstatus": 1}
	)
	return frappe.get_doc("TBO Fund Allocation", name) if name else None


# ---------- Finance workflow Phase 4: reconciliation ----------


def enable_reconciliation(**settings) -> None:
	"""Phases 1 and 4 on (plus anything in `settings`, e.g. enable_fund_allocation=1)."""
	enable_finance_workflow(enable_reconciliation=1, unmatched_alert_days=3, **settings)


def make_company_bank_account(company: str | None = None) -> str:
	"""A company Bank Account ("TBO Test Bank") on its own Bank-type GL account. Returns its name."""
	company = company or erpnext.get_default_company()
	abbr = frappe.get_cached_value("Company", company, "abbr")
	if not frappe.db.exists("Bank", "TBO Test Bank"):
		frappe.get_doc({"doctype": "Bank", "bank_name": "TBO Test Bank"}).insert(ignore_permissions=True)
	gl = f"TBO Bank - {abbr}"
	if not frappe.db.exists("Account", gl):
		parent = frappe.db.get_value(
			"Account", {"company": company, "is_group": 1, "account_type": "Bank"}
		) or frappe.db.get_value("Account", {"company": company, "is_group": 1, "root_type": "Asset"})
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "TBO Bank",
				"parent_account": parent,
				"company": company,
				"account_type": "Bank",
			}
		).insert(ignore_permissions=True)
	name = frappe.db.get_value("Bank Account", {"account": gl})
	if not name:
		name = (
			frappe.get_doc(
				{
					"doctype": "Bank Account",
					"account_name": f"TBO Main {abbr}",
					"bank": "TBO Test Bank",
					"account": gl,
					"is_company_account": 1,
					"company": company,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	return name


def make_bank_line(
	bank_account: str, amount: float, reference: str = "", days_ago: int = 0, submit: bool = True
):
	"""A Bank Transaction (deposit when `amount` > 0, withdrawal when < 0), as Bank Statement
	Import makes it."""
	line = frappe.get_doc(
		{
			"doctype": "Bank Transaction",
			"date": add_days(nowdate(), -days_ago),
			"bank_account": bank_account,
			"company": frappe.db.get_value("Bank Account", bank_account, "company"),
			"deposit": amount if amount > 0 else 0,
			"withdrawal": -amount if amount < 0 else 0,
			"currency": frappe.get_cached_value(
				"Company", frappe.db.get_value("Bank Account", bank_account, "company"), "default_currency"
			),
			"reference_number": reference,
			"description": f"Statement line {reference}",
		}
	)
	line.insert(ignore_permissions=True)
	if submit:
		line.submit()
	return line


def match_bank_line(line: str, payment_entry: str, amount: float) -> None:
	"""Match a bank line to a Payment Entry the way the Bank Reconciliation Tool does."""
	from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import reconcile_vouchers

	reconcile_vouchers(
		line,
		json.dumps([{"payment_doctype": "Payment Entry", "payment_name": payment_entry, "amount": amount}]),
	)


def get_dashboard_cards(window: tuple, user: str | None = None) -> dict:
	"""The Phase 5 finance cards the session user gets on the sales dashboard, as {name: data}.
	`window` is the dashboard's (from_date, to_date); `user` is its user filter (blank: everyone)."""
	from tbo_smart.finance import dashboard

	return {card["name"]: card["data"] for card in dashboard.get_cards(*window, user)}
