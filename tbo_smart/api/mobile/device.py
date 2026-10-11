"""TBO Smart push devices (v1): register and unregister this phone's Expo push token."""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime

PLATFORMS = ("android", "ios")
TOKEN_PREFIXES = ("ExponentPushToken[", "ExpoPushToken[")


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def register_device(token: str, platform: str, app_version: str | None = None) -> dict:
	"""Save this install's Expo push token for the session user (idempotent)."""
	platform = (platform or "").lower()
	if platform not in PLATFORMS:
		frappe.throw(_("Platform must be android or ios."))
	if not (token or "").startswith(TOKEN_PREFIXES):
		frappe.throw(_("That is not an Expo push token."))

	existing = frappe.db.get_value(
		"TBO Mobile Device", {"expo_push_token": token}, ["name", "user"], as_dict=True
	)
	if existing and existing.user != frappe.session.user:
		# The token belongs to the install, not the person: the phone is now signed in as
		# someone else, so the previous user must stop receiving pushes on it.
		frappe.delete_doc("TBO Mobile Device", existing.name, ignore_permissions=True, force=True)
		existing = None

	device = (
		frappe.get_doc("TBO Mobile Device", existing.name)
		if existing
		else frappe.new_doc("TBO Mobile Device")
	)
	device.update(
		{
			"user": frappe.session.user,
			"platform": platform,
			"expo_push_token": token,
			"app_version": app_version,
			"last_seen": now_datetime(),
		}
	)
	device.save()
	return {"name": device.name, "platform": device.platform, "last_seen": device.last_seen}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def unregister_device(token: str) -> dict:
	"""Forget this install's token for the session user (on sign-out). Unknown tokens are a no-op."""
	name = frappe.db.get_value(
		"TBO Mobile Device", {"expo_push_token": token, "user": frappe.session.user}, "name"
	)
	if name:
		frappe.delete_doc("TBO Mobile Device", name)
	return {"removed": bool(name)}
