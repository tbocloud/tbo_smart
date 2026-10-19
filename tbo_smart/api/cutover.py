"""The CRM → ERPNext cutover from the browser (docs/erpnext-lead-cutover.md §5).

The System Console runs scripts sandboxed (no imports), and Press gives no bench shell, so the
owner runs the cutover through these System Manager-only methods instead, e.g. from the System
Console: `print(frappe.call("tbo_smart.api.cutover.dry_run"))`.
"""

import json
import os

import frappe
from frappe import _
from frappe.utils.background_jobs import is_job_enqueued

from tbo_smart.cutover import crm_to_erpnext

JOB_ID = "tbo-crm-to-erpnext-cutover"
TIMEOUT = 2 * 3600
FAILED_KEY = "tbo_cutover_failed"


def require_system_manager() -> None:
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can run the CRM cutover."), frappe.PermissionError)


def lock_name() -> str:
	# a MariaDB named lock is server-wide, so it carries the site's database
	return f"tbo_cutover:{frappe.conf.db_name}"


def lock_held() -> bool:
	return frappe.db.sql("SELECT IS_USED_LOCK(%s)", lock_name())[0][0] is not None


@frappe.whitelist()
def dry_run() -> dict:
	"""What a run would copy: counts and problems, nothing written."""
	require_system_manager()
	return crm_to_erpnext.run(dry_run=True)


@frappe.whitelist(methods=["POST"])
def start() -> dict:
	"""Queue the real run (it can take minutes); `status` says when it's done."""
	require_system_manager()
	if is_job_enqueued(JOB_ID) or lock_held():
		return {"queued": False, "message": _("The cutover is already running.")}
	frappe.cache.delete_value(FAILED_KEY)
	frappe.enqueue("tbo_smart.api.cutover.run_once", queue="long", timeout=TIMEOUT, job_id=JOB_ID)
	return {"queued": True, "message": _("The cutover is running. Check status in a few minutes.")}


def run_once() -> None:
	"""The queued run. It holds a MariaDB named lock on its own connection for as long as it
	runs, so a second run that slipped through start() exits at once, and the lock goes when
	the worker's connection does, however the run ends. A failure is kept for `status`."""
	if not frappe.db.sql("SELECT GET_LOCK(%s, 0)", lock_name())[0][0]:
		return
	try:
		crm_to_erpnext.run(dry_run=False)
	except Exception:
		frappe.cache.set_value(
			FAILED_KEY, {"at": str(frappe.utils.now_datetime()), "error": frappe.get_traceback()[-2000:]}
		)
		raise
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", lock_name())


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
		"running": is_job_enqueued(JOB_ID) or lock_held(),
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
