"""Single sign-in with Microsoft (v1): trade an Entra ID token for this site's own OAuth tokens.

The app signs in once with Microsoft (Authorization Code + PKCE) and sends the ID token to
each site. This site verifies it, finds the enabled User with that email and issues a
Frappe OAuth Bearer Token for the TBO Smart OAuth Client: an access token only, with no
refresh token. Microsoft is the only login, so the app gets new access by exchanging a fresh
ID token; once TBO disables someone's Microsoft account, their access here ends within the
hour. The hub exposes the same contract.
Contract: docs/tbo-smart-api-ess-crm.md §2.5.
"""

import hmac
import re
import time

import frappe
import jwt
from frappe import _
from frappe.oauth import OAuthWebRequestValidator
from frappe.rate_limiter import rate_limit
from oauthlib.common import Request
from oauthlib.oauth2 import BearerToken

API_VERSION = 1
SCOPES = ("all", "openid")
MICROSOFT_LOGIN = "https://login.microsoftonline.com"
# Clock skew allowed on exp, nbf and iat.
LEEWAY_SECONDS = 120
# An ID token's uti/jti is remembered at least this long (and until it expires).
REPLAY_SECONDS = 600
REPLAY_CACHE_PREFIX = "tbo_smart:sso:used"
# A key id that is not in the cached set triggers at most one key download per tenant this often.
JWKS_REFRESH_COOLDOWN_SECONDS = 300
JWKS_REFRESH_CACHE_PREFIX = "tbo_smart:sso:jwks_refresh"
# Delete the cooldown only while it still holds this worker's owner token (atomic in Redis).
RELEASE_IF_OWNER = (
	'if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) end return 0'
)
# Microsoft's key set per tenant, shared by every worker through Redis.
JWKS_CACHE_PREFIX = "tbo_smart:sso:jwks"
JWKS_CACHE_SECONDS = 3600
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
USER_CLAIMS = ("email", "preferred_username", "upn")
BLOCKED_USERS = ("Administrator", "Guest")


# Guest by design: the caller has no session yet. Reviewed: POST only, rate-limited, and
# nothing is returned unless the Microsoft token passes every check below.
@frappe.whitelist(allow_guest=True, methods=["POST"])  # nosemgrep
@rate_limit(limit=30, seconds=60)
def exchange_microsoft_token(id_token: str, nonce: str | None = None) -> dict:
	"""Verify a Microsoft ID token and issue this site's access token (no refresh token) for its user."""
	settings = get_sso_settings()
	claims = verify_microsoft_id_token(id_token, settings, nonce)
	user = find_user(claims)
	check_client_roles(user.name, settings.oauth_client)
	token = issue_bearer_token(user.name, settings.oauth_client)
	return {
		"v": API_VERSION,
		"data": {
			"access_token": token["access_token"],
			"expires_in": token["expires_in"],
			"token_type": "Bearer",
			"scope": token["scope"],
			"user": user.name,
			"full_name": user.full_name or user.name,
		},
	}


def get_sso_settings() -> frappe._dict:
	"""The SSO fields of TBO Smart Settings; 403 while the switch is off or a field is empty."""
	settings = frappe.get_cached_doc("TBO Smart Settings")
	if not settings.enable_microsoft_sso:
		frappe.throw(
			_("Single sign-in with Microsoft is turned off on this site."),
			frappe.PermissionError,
			title=_("Single sign-in is off"),
		)
	tenants = parse_tenant_ids(settings.microsoft_tenant_id)
	if not (tenants and settings.microsoft_mobile_client_id and settings.oauth_client):
		frappe.throw(
			_("Single sign-in with Microsoft is not fully set up on this site. Ask an administrator."),
			frappe.PermissionError,
			title=_("Single sign-in is not set up"),
		)
	return frappe._dict(
		tenants=tenants,
		client_id=settings.microsoft_mobile_client_id.strip(),
		oauth_client=settings.oauth_client,
	)


def parse_tenant_ids(value: str | None) -> list[str]:
	"""Tenant IDs from a comma or newline separated field, lowercased."""
	return [part.strip().lower() for part in re.split(r"[,\s]+", value or "") if part.strip()]


def verify_microsoft_id_token(id_token: str, settings: frappe._dict, nonce: str | None = None) -> dict:
	"""The claims of a Microsoft v2 ID token, or AuthenticationError (401).

	Checks the RS256 signature against the tenant's published keys, the issuer, tenant,
	audience, expiry and not-before times, the nonce when one is given, and that the token
	has not been exchanged before. The token itself is never logged.
	"""
	try:
		tenant = str(jwt.decode(id_token, options={"verify_signature": False}).get("tid") or "").lower()
		if tenant not in settings.tenants:
			raise jwt.InvalidIssuerError("tenant not allowed")
		claims = jwt.decode(
			id_token,
			get_signing_key(tenant, jwt.get_unverified_header(id_token).get("kid")),
			algorithms=["RS256"],
			audience=settings.client_id,
			issuer=f"{MICROSOFT_LOGIN}/{tenant}/v2.0",
			leeway=LEEWAY_SECONDS,
			options={"require": ["exp", "iat", "iss", "aud", "tid"]},
		)
	except jwt.PyJWKClientConnectionError:
		frappe.throw(
			_("Could not reach Microsoft to check the sign-in. Try again in a moment."),
			frappe.AuthenticationError,
			title=_("Sign-in not verified"),
		)
	except jwt.PyJWTError:
		reject_token(_("The Microsoft sign-in could not be verified. Sign in again."))

	if str(claims.get("tid") or "").lower() != tenant:
		reject_token(_("The Microsoft sign-in could not be verified. Sign in again."))
	if nonce is not None and not hmac.compare_digest(str(claims.get("nonce") or ""), str(nonce)):
		reject_token(_("The Microsoft sign-in does not match this request. Sign in again."))
	claim_token_once(claims)
	return claims


def get_signing_key(tenant: str, kid: str | None):
	"""The tenant's public key for `kid`, from a key set shared by all workers for an hour.

	An unknown `kid` downloads the set again at most once per tenant every 5 minutes, and the
	new set replaces the shared one, so every worker sees rotated keys at once while made-up
	key ids cannot make this site hammer Microsoft.
	"""
	key = find_signing_key(get_jwk_set(tenant), kid)
	if key is None and (owner := claim_jwks_refresh(tenant)):
		try:
			jwk_set = download_jwk_set(tenant)
		except jwt.PyJWTError:
			# A failed download must not hold off the next try for the whole cooldown.
			release_jwks_refresh(tenant, owner)
			raise
		key = find_signing_key(jwk_set, kid)
	if key is None:
		raise jwt.PyJWKClientError("unknown signing key")
	return key


def find_signing_key(jwk_set: dict, kid: str | None):
	keys = jwt.PyJWKSet.from_dict(jwk_set).keys
	return next(
		(key.key for key in keys if kid and key.key_id == kid and key.public_key_use in (None, "sig")), None
	)


def get_jwk_set(tenant: str) -> dict:
	"""The tenant's key set from the shared cache, downloading it when there is none."""
	return frappe.cache.get_value(jwks_cache_key(tenant), expires=True) or download_jwk_set(tenant)


def download_jwk_set(tenant: str) -> dict:
	"""Fetch the tenant's v2.0 signing keys with PyJWT's PyJWKClient and share them for an hour."""
	client = jwt.PyJWKClient(
		f"{MICROSOFT_LOGIN}/{tenant}/discovery/v2.0/keys", cache_jwk_set=False, timeout=10
	)
	jwk_set = client.fetch_data()
	frappe.cache.set_value(jwks_cache_key(tenant), jwk_set, expires_in_sec=JWKS_CACHE_SECONDS)
	return jwk_set


def jwks_cache_key(tenant: str) -> str:
	return f"{JWKS_CACHE_PREFIX}:{tenant}"


def jwks_refresh_key(tenant: str) -> str:
	return frappe.cache.make_key(f"{JWKS_REFRESH_CACHE_PREFIX}:{tenant}")


def claim_jwks_refresh(tenant: str) -> str | None:
	"""An owner token for the first caller in each cooldown window; None for everyone else,
	who uses the shared set."""
	owner = frappe.generate_hash(length=16)
	# Raw SET NX for an atomic check-and-mark; make_key adds the site prefix, so it stays per site.
	claimed = frappe.cache.set(  # nosemgrep
		jwks_refresh_key(tenant), owner, ex=JWKS_REFRESH_COOLDOWN_SECONDS, nx=True
	)
	return owner if claimed else None


def release_jwks_refresh(tenant: str, owner: str) -> None:
	"""Drop this caller's cooldown claim, never a newer one from another worker."""
	frappe.cache.eval(RELEASE_IF_OWNER, 1, jwks_refresh_key(tenant), owner)


def claim_token_once(claims: dict) -> None:
	"""Mark the token's uti (or jti) as used; a second exchange of the same token is refused.

	The mark lasts 10 minutes, or until the token expires if that is later, so a captured
	token can never be exchanged again while it is still valid.
	"""
	token_id = claims.get("uti") or claims.get("jti")
	if not token_id:
		reject_token(_("The Microsoft sign-in could not be verified. Sign in again."))
	ttl = max(REPLAY_SECONDS, int(claims["exp"]) + LEEWAY_SECONDS - int(time.time()))
	key = frappe.cache.make_key(f"{REPLAY_CACHE_PREFIX}:{claims['tid']}:{token_id}")
	# Raw SET NX for an atomic check-and-mark; make_key adds the site prefix, so it stays per site.
	if not frappe.cache.set(key, 1, ex=ttl, nx=True):  # nosemgrep
		reject_token(_("This Microsoft sign-in was already used. Sign in again."))


def reject_token(message: str) -> None:
	frappe.throw(message, frappe.AuthenticationError, title=_("Sign-in not verified"))


def find_user(claims: dict) -> frappe._dict:
	"""The User named by the token's email, preferred_username or upn (first match, any case).

	Never creates a user. Unknown and disabled users get a 403.
	"""
	for claim in USER_CLAIMS:
		address = str(claims.get(claim) or "").strip().lower()
		if "@" not in address:
			continue
		# By User.email only: a User's name can be an old address that now belongs to someone else.
		user = frappe.db.get_value("User", {"email": address}, ["name", "full_name", "enabled"], as_dict=True)
		if not user or user.name in BLOCKED_USERS:
			continue
		if not user.enabled:
			frappe.throw(
				_("Your account {0} is disabled on this site. Ask an administrator.").format(user.name),
				frappe.PermissionError,
				title=_("Account disabled"),
			)
		return user
	frappe.throw(
		_("No account on this site matches your Microsoft sign-in. Ask an administrator to add you."),
		frappe.PermissionError,
		title=_("No account"),
	)


def check_client_roles(user: str, oauth_client: str) -> None:
	"""Apply the OAuth Client's Allowed Roles, exactly as the normal sign-in does."""
	allowed_roles = {row.role for row in frappe.get_doc("OAuth Client", oauth_client).allowed_roles}
	if not allowed_roles & set(frappe.get_roles(user)):
		frappe.throw(
			_("Your account {0} may not use TBO Smart on this site. Ask an administrator.").format(user),
			frappe.PermissionError,
			title=_("Not allowed"),
		)


def issue_bearer_token(user: str, oauth_client: str) -> dict:
	"""An access token made and saved the way Frappe's OAuth server does, without a refresh token.

	oauthlib's BearerToken generates it (3600 s) and Frappe's own validator saves the OAuth
	Bearer Token, so Bearer calls and revoke treat it like any other. With no refresh token
	stored, `get_token` has nothing to refresh: new access always goes back to Microsoft.
	"""
	request = Request("")
	request.user = user
	request.scopes = list(SCOPES)
	request.client = {"name": oauth_client}
	validator = OAuthWebRequestValidator()
	token = BearerToken(validator).create_token(request, refresh_token=False)
	validator.save_bearer_token(token, request)
	return token


def revoke_tokens_of_disabled_user(doc, method=None) -> None:
	"""User on_update: a disabled User loses every active OAuth Bearer Token at once.

	Frappe already refuses Bearer calls for a disabled user; revoking also covers anyone who
	is enabled again later, so old tokens never come back to life.
	"""
	if doc.enabled:
		return
	OAuthBearerToken = frappe.qb.DocType("OAuth Bearer Token")
	(
		frappe.qb.update(OAuthBearerToken)
		.set(OAuthBearerToken.status, "Revoked")
		.where((OAuthBearerToken.user == doc.name) & (OAuthBearerToken.status == "Active"))
		.run()
	)
