"""Factories and toggles shared by the tbo_smart tests. Never define helpers inside test files."""

import base64
import functools
import io
import json
import time
from unittest.mock import patch

import erpnext
import frappe
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from erpnext.setup.doctype.employee.test_employee import make_employee
from frappe.integrations.oauth2 import get_oauth_server, get_token
from frappe.utils import add_days, get_year_ending, get_year_start, getdate, now_datetime, nowdate
from frappe.utils.nestedset import rebuild_tree
from hrms.tests.test_utils import before_tests as hrms_before_tests
from jwt.algorithms import RSAAlgorithm
from PIL import Image
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request as WerkzeugRequest

from tbo_smart.api.mobile import auth
from tbo_smart.api.mobile.capabilities import get_capabilities
from tbo_smart.api.mobile.utils import INSIGHTS_CACHE_PREFIX
from tbo_smart.install import setup_quotation_approval

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


def make_leave_type(name: str = "TBO Smart Casual Leave") -> str:
	"""A paid Leave Type that allows a negative balance, so tests can apply without an allocation."""
	if not frappe.db.exists("Leave Type", name):
		frappe.get_doc({"doctype": "Leave Type", "leave_type_name": name, "allow_negative": 1}).insert(
			ignore_permissions=True
		)
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


def make_crm_lead(owner: str, first_name: str = "Test Lead", **fields) -> str:
	"""A CRM Lead owned by `owner`, inserted as Administrator."""
	lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": first_name, "lead_owner": owner, **fields})
	lead.insert(ignore_permissions=True)
	return lead.name


def make_crm_deal(owner: str, status: str = "Qualification", deal_value: float = 1000, **fields) -> str:
	"""A CRM Deal owned by `owner`, inserted as Administrator."""
	deal = frappe.get_doc(
		{"doctype": "CRM Deal", "deal_owner": owner, "status": status, "deal_value": deal_value, **fields}
	)
	deal.insert(ignore_permissions=True)
	return deal.name


def make_test_mobile() -> str:
	"""A random 9-digit mobile number without country code or trunk 0 (e.g. 504182276).

	CRM inserts commit, so duplicate-check tests use a fresh number each time and never
	match a lead left by another test or an earlier run."""
	return "50" + str(int(frappe.generate_hash(length=12), 16))[-7:].zfill(7)


def make_crm_organization(name: str) -> str:
	"""A CRM Organization named `name`, inserted as Administrator."""
	organization = frappe.get_doc({"doctype": "CRM Organization", "organization_name": name})
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


def make_crm_lead_source(name: str) -> str:
	"""A CRM Lead Source named `name`, if the site does not have it yet."""
	if not frappe.db.exists("CRM Lead Source", name):
		frappe.get_doc({"doctype": "CRM Lead Source", "source_name": name}).insert(ignore_permissions=True)
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
	"""A CRM Sales Hierarchy node for `user` under the node `reports_to`; rebuilds the tree."""
	node = frappe.get_doc(
		{
			"doctype": "CRM Sales Hierarchy",
			"user": user,
			"reports_to": reports_to,
			"is_group": 1,
		}
	)
	node.insert(ignore_permissions=True)
	rebuild_tree("CRM Sales Hierarchy")
	return node.name


def enable_sales_hierarchy() -> None:
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 1)


def disable_sales_hierarchy(users: list[str] | tuple = ()) -> None:
	"""Turn the CRM sales hierarchy off and delete the nodes of `users`.

	CRM Lead and Deal inserts commit (their after_insert assigns and shares), so a setting
	changed in a test is already persisted; the cleanup is committed for the same reason.
	"""
	frappe.db.set_single_value("FCRM Settings", "enable_sales_hierarchy", 0)
	for user in users:
		frappe.db.delete("CRM Sales Hierarchy", {"user": user})
	frappe.cache.delete_value("crm_sales_hierarchy_subtree")
	# CRM's committing after_insert already persisted the fixtures, so the undo must persist too.
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
	"""A deal's value in the CRM base currency (deal_value x exchange_rate), as insights count it."""
	deal_value, exchange_rate = frappe.db.get_value("CRM Deal", name, ["deal_value", "exchange_rate"])
	return deal_value * (exchange_rate or 1)


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


def delete_crm_records(owners: list[str] | tuple) -> None:
	"""Delete the CRM Deals, Leads, Notes and Tasks of `owners` and commit.

	CRM inserts commit, so their fixtures outlive the test's rollback; this keeps reruns
	and later classes starting from a clean slate.
	"""
	for doctype, owner_field in (
		("CRM Deal", "deal_owner"),
		("CRM Lead", "lead_owner"),
		("FCRM Note", "owner"),
		("CRM Task", "assigned_to"),
	):
		frappe.db.delete(doctype, {owner_field: ("in", list(owners))})
	# CRM's committing after_insert already persisted these fixtures, so the delete must persist too.
	frappe.db.commit()  # nosemgrep


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


def enable_erpnext_crm() -> None:
	"""ERPNext CRM Settings on, same site, with the default company: creates Frappe CRM's
	Quotation.crm_deal and Customer.crm_deal fields, as on tboindia."""
	settings = frappe.get_single("ERPNext CRM Settings")
	settings.update(
		{
			"enabled": 1,
			"is_erpnext_in_different_site": 0,
			"erpnext_company": erpnext.get_default_company(),
			"create_customer_on_status_change": 0,
			"sync_products": 0,
		}
	)
	settings.save(ignore_permissions=True)


def make_finance_deal(owner: str, **fields) -> str:
	"""A CRM Deal owned by `owner` with its own ERPNext Customer (erpnext_customer and
	Customer.crm_deal set both ways, as Frappe CRM links them). Returns the deal name."""
	deal = make_crm_deal(owner, **fields)
	customer = make_customer(f"TBO Finance {frappe.generate_hash(length=8)}")
	frappe.db.set_value("Customer", customer, "crm_deal", deal)
	frappe.db.set_value("CRM Deal", deal, "erpnext_customer", customer)
	return deal


def make_deal_quotation(deal: str, rate: float = 1000, qty: float = 1, submit: bool = True, **fields):
	"""A Quotation to the deal's customer with `crm_deal` set, inserted as the session user and
	submitted unless `submit` is False. Returns the Document."""
	quotation = frappe.get_doc(
		{
			"doctype": "Quotation",
			"quotation_to": "Customer",
			"party_name": frappe.db.get_value("CRM Deal", deal, "erpnext_customer"),
			"crm_deal": deal,
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
	week. `keep_deal=False` clears the crm_deal the mapper copies, to exercise the hook."""
	from erpnext.selling.doctype.quotation.quotation import make_sales_order

	order = make_sales_order(quotation)
	order.delivery_date = add_days(nowdate(), 7)
	if not keep_deal:
		order.crm_deal = None
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
	"""A Sales Invoice mapped from a submitted Sales Order. `keep_deal=False` clears the
	crm_deal the mapper copies."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	invoice = make_sales_invoice(order)
	if not keep_deal:
		invoice.crm_deal = None
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


def make_crm_product(item_code: str, rate: float = 1000) -> str:
	"""A CRM Product linked to the ERPNext Item `item_code` (erpnext_item_code), as product
	sync makes it. Returns the product code."""
	code = f"TBO-{item_code}"
	if not frappe.db.exists("CRM Product", code):
		frappe.get_doc(
			{
				"doctype": "CRM Product",
				"product_code": code,
				"product_name": item_code,
				"standard_rate": rate,
				"erpnext_item_code": item_code,
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value("CRM Product", code, "erpnext_item_code", item_code)
	return code


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
	"""A Sales Invoice to the deal's customer with `crm_deal` set, posted `posted_days_ago`
	days ago and due `due_in` days from today (negative: already overdue). One payment-term
	row, as ERPNext builds it from the due date. Returns the Document."""
	posting = add_days(nowdate(), -max(posted_days_ago, -due_in if due_in < 0 else 0))
	invoice = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"customer": frappe.db.get_value("CRM Deal", deal, "erpnext_customer"),
			"crm_deal": deal,
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
	"""The invoice's collection CRM Task, if any."""
	name = frappe.db.get_value("CRM Task", {"tbo_sales_invoice": invoice, "tbo_task_type": "Collection"})
	return frappe.get_doc("CRM Task", name) if name else None


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
			"customer": frappe.db.get_value("CRM Deal", deal, "erpnext_customer"),
			"crm_deal": deal,
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
	"""The Phase 5 finance cards the session user gets on the CRM dashboard, as {name: data}.
	`window` is the dashboard's (from_date, to_date); `user` is its user filter (blank: everyone)."""
	from tbo_smart.finance import dashboard

	return {card["name"]: card["data"] for card in dashboard.get_cards(*window, user)}
