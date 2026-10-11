"""Factories and toggles shared by the tbo_smart tests. Never define helpers inside test files."""

import functools
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
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request as WerkzeugRequest

from tbo_smart.api.mobile import auth
from tbo_smart.api.mobile.capabilities import get_capabilities
from tbo_smart.api.mobile.utils import INSIGHTS_CACHE_PREFIX

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
