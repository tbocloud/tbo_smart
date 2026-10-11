import time

import frappe
from frappe.tests.utils import FrappeTestCase

from tbo_smart.api.mobile.auth import exchange_microsoft_token
from tbo_smart.test_utils import (
	MS_TEST_CLIENT_ID,
	MS_TEST_TENANT,
	delete_oauth_tokens,
	disable_microsoft_sso,
	enable_microsoft_sso,
	get_test_rsa_key,
	make_microsoft_id_token,
	make_oauth_client,
	make_staff,
	make_user,
	mock_microsoft_jwks,
	refresh_oauth_token,
)

STAFF = "sso.staff@tbosmart.test"
SALES_ONLY = "sso.sales@tbosmart.test"
DISABLED = "sso.disabled@tbosmart.test"
OTHER_TENANT = "99999999-8888-7777-6666-555555555555"


class TestMobileSSO(FrappeTestCase):
	def setUp(self):
		# ERPNext drops the Employee role from a User without an Employee, so STAFF needs one.
		make_staff(STAFF)
		make_user(SALES_ONLY, ["Sales User"])
		make_user(DISABLED, ["Employee"])
		frappe.db.set_value("User", DISABLED, "enabled", 0)
		self.client = make_oauth_client(["Employee", "Sales User"])
		enable_microsoft_sso(self.client)
		jwks = mock_microsoft_jwks()
		self.jwks_download = jwks.start()
		self.addCleanup(jwks.stop)
		frappe.set_user("Guest")

	def tearDown(self):
		frappe.set_user("Administrator")
		disable_microsoft_sso()
		delete_oauth_tokens(self.client)

	def exchange(self, **claims):
		return exchange_microsoft_token(make_microsoft_id_token(STAFF, **claims))

	def assertRejected(self, id_token, nonce=None):
		with self.assertRaises(frappe.AuthenticationError):
			exchange_microsoft_token(id_token, nonce)

	def test_valid_token_issues_tokens_linked_to_the_client(self):
		response = exchange_microsoft_token(make_microsoft_id_token(STAFF, nonce="n-1"), "n-1")
		self.assertEqual(response["v"], 1)
		data = response["data"]
		self.assertEqual(data["user"], STAFF)
		self.assertEqual(data["token_type"], "Bearer")
		self.assertEqual(data["expires_in"], 3600)
		self.assertEqual(data["scope"], "all openid")
		self.assertTrue(data["full_name"])

		token = frappe.get_doc("OAuth Bearer Token", data["access_token"])
		self.assertEqual(token.client, self.client)
		self.assertEqual(token.user, STAFF)
		self.assertEqual(token.refresh_token, data["refresh_token"])
		self.assertEqual(token.status, "Active")
		self.assertEqual(token.scopes, "all openid")

	def test_user_is_matched_case_insensitively_and_by_fallback_claims(self):
		response = exchange_microsoft_token(make_microsoft_id_token(STAFF.upper()))
		self.assertEqual(response["data"]["user"], STAFF)

		response = exchange_microsoft_token(
			make_microsoft_id_token(STAFF, email=None, preferred_username=None, upn=STAFF)
		)
		self.assertEqual(response["data"]["user"], STAFF)

	def test_user_without_an_employee_signs_in(self):
		response = exchange_microsoft_token(make_microsoft_id_token(SALES_ONLY))
		self.assertEqual(response["data"]["user"], SALES_ONLY)

	def test_wrong_audience_issuer_or_tenant_is_rejected(self):
		self.assertRejected(make_microsoft_id_token(STAFF, aud="another-app"))
		self.assertRejected(
			make_microsoft_id_token(STAFF, iss="https://login.microsoftonline.com/common/v2.0")
		)
		self.assertRejected(
			make_microsoft_id_token(
				STAFF, tid=OTHER_TENANT, iss=f"https://login.microsoftonline.com/{OTHER_TENANT}/v2.0"
			)
		)
		self.assertRejected(make_microsoft_id_token(STAFF, tid=None))

	def test_expired_or_not_yet_valid_token_is_rejected(self):
		now = int(time.time())
		self.assertRejected(make_microsoft_id_token(STAFF, iat=now - 7200, nbf=now - 7200, exp=now - 3600))
		self.assertRejected(make_microsoft_id_token(STAFF, nbf=now + 3600))

	def test_bad_signature_is_rejected(self):
		self.assertRejected(make_microsoft_id_token(STAFF, key=get_test_rsa_key("attacker")))
		self.assertRejected("not-a-jwt")

	def test_unknown_key_id_refetches_keys_once_per_cooldown(self):
		self.assertRejected(make_microsoft_id_token(STAFF, kid="made-up-1"))
		self.assertRejected(make_microsoft_id_token(STAFF, kid="made-up-2"))
		# The first download fills the cache; only one refresh is allowed in the cooldown.
		self.assertEqual(self.jwks_download.call_count, 2)
		exchange_microsoft_token(make_microsoft_id_token(STAFF))
		self.assertEqual(self.jwks_download.call_count, 2)

	def test_user_is_matched_by_email_not_by_user_name(self):
		# The exchange commits, so the changed email is put back before tearDown commits again.
		frappe.db.set_value("User", SALES_ONLY, "email", "sso.sales.new@tbosmart.test")
		try:
			with self.assertRaises(frappe.PermissionError):
				exchange_microsoft_token(make_microsoft_id_token(SALES_ONLY))
			response = exchange_microsoft_token(make_microsoft_id_token("sso.sales.new@tbosmart.test"))
			self.assertEqual(response["data"]["user"], SALES_ONLY)
		finally:
			frappe.db.set_value("User", SALES_ONLY, "email", SALES_ONLY)

	def test_nonce_mismatch_is_rejected(self):
		self.assertRejected(make_microsoft_id_token(STAFF, nonce="expected"), nonce="other")
		self.assertRejected(make_microsoft_id_token(STAFF, nonce=None), nonce="expected")

	def test_a_token_is_exchanged_only_once(self):
		id_token = make_microsoft_id_token(STAFF)
		exchange_microsoft_token(id_token)
		self.assertRejected(id_token)
		self.assertRejected(make_microsoft_id_token(STAFF, uti=None))

	def test_unknown_or_disabled_user_gets_a_403(self):
		with self.assertRaises(frappe.PermissionError):
			exchange_microsoft_token(make_microsoft_id_token("nobody@tbosmart.test"))
		with self.assertRaises(frappe.PermissionError):
			exchange_microsoft_token(make_microsoft_id_token(DISABLED))
		self.assertFalse(frappe.db.exists("User", "nobody@tbosmart.test"))

	def test_client_allowed_roles_apply(self):
		frappe.set_user("Administrator")
		make_oauth_client(["Employee"])
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			exchange_microsoft_token(make_microsoft_id_token(SALES_ONLY))

	def test_refused_while_the_switch_is_off(self):
		disable_microsoft_sso()
		with self.assertRaises(frappe.PermissionError):
			self.exchange()

	def test_settings_refuse_a_shared_tenant(self):
		frappe.set_user("Administrator")
		settings = frappe.get_doc("TBO Smart Settings")
		settings.microsoft_tenant_id = "common"
		with self.assertRaises(frappe.ValidationError):
			settings.save()
		settings = frappe.get_doc("TBO Smart Settings")
		settings.microsoft_tenant_id = f"{MS_TEST_TENANT.upper()}\n{OTHER_TENANT}"
		settings.microsoft_mobile_client_id = MS_TEST_CLIENT_ID
		settings.save()
		self.assertEqual(settings.microsoft_tenant_id, f"{MS_TEST_TENANT}, {OTHER_TENANT}")

	def test_refresh_through_frappe_get_token(self):
		data = self.exchange()["data"]
		refreshed = refresh_oauth_token(data["refresh_token"], self.client)
		self.assertTrue(refreshed.access_token)
		self.assertNotEqual(refreshed.access_token, data["access_token"])
		token = frappe.get_doc("OAuth Bearer Token", refreshed.access_token)
		self.assertEqual(token.user, STAFF)
		self.assertEqual(token.client, self.client)
