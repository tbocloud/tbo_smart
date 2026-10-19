"""The CRM → ERPNext cutover from the browser (docs/erpnext-lead-cutover.md §5).

The System Console runs scripts sandboxed (no imports), and Press gives no bench shell, so the
owner runs the cutover through these System Manager-only methods instead, e.g. from the System
Console: `print(frappe.call("tbo_smart.api.cutover.dry_run"))`.
"""

import json
import os

import frappe
from frappe import _

from tbo_smart.cutover import crm_to_erpnext

JOB_ID = "tbo-crm-to-erpnext-cutover"
LOCK_SECONDS = 2 * 3600
FAILED_KEY = "tbo_cutover_failed"


def require_system_manager() -> None:
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can run the CRM cutover."), frappe.PermissionError)


def lock_key() -> str:
	return frappe.cache.make_key("tbo_cutover_running")


@frappe.whitelist()
def dry_run() -> dict:
	"""What a run would copy: counts and problems, nothing written."""
	require_system_manager()
	return crm_to_erpnext.run(dry_run=True)


@frappe.whitelist(methods=["POST"])
def start() -> dict:
	"""Queue the real run (it can take minutes); `status` says when it's done. One at a time:
	the slot is taken atomically before queueing and freed when the run ends."""
	require_system_manager()
	if not frappe.cache.set(lock_key(), frappe.session.user, nx=True, ex=LOCK_SECONDS):
		return {"queued": False, "message": _("The cutover is already running.")}
	frappe.cache.delete_value(FAILED_KEY)
	frappe.enqueue("tbo_smart.api.cutover.run_and_release", queue="long", timeout=LOCK_SECONDS, job_id=JOB_ID)
	return {"queued": True, "message": _("The cutover is running. Check status in a few minutes.")}


def run_and_release() -> None:
	"""The queued run: frees the slot however it ends, and keeps a failure for `status`."""
	try:
		crm_to_erpnext.run(dry_run=False)
	except Exception:
		frappe.cache.set_value(
			FAILED_KEY, {"at": str(frappe.utils.now_datetime()), "error": frappe.get_traceback()[-2000:]}
		)
		raise
	finally:
		frappe.cache.delete(lock_key())


@frappe.whitelist()
def status() -> dict:
	"""Whether a run is going, the last run's failure if it failed, and the last completed
	report (a failed run writes none)."""
	require_system_manager()
	folder = frappe.get_site_path("private", "files", crm_to_erpnext.REPORT_FOLDER)
	reports = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
	report = None
	if reports:
		with open(os.path.join(folder, reports[-1])) as file:  # the site's own report folder - nosemgrep
			report = json.load(file)
	return {
		"running": bool(frappe.cache.get(lock_key())),
		"failed": frappe.cache.get_value(FAILED_KEY),
		"last_report": report,
	}


@frappe.whitelist()
def verify() -> dict:
	"""PASS, or the differences between the CRM and the copies."""
	require_system_manager()
	return crm_to_erpnext.verify()


@frappe.whitelist(methods=["POST"])
def finalize() -> dict:
	"""After verify passes: remove tbo_smart's links to CRM doctypes, before uninstalling crm."""
	require_system_manager()
	return crm_to_erpnext.finalize()
