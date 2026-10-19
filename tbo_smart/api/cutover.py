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


def require_system_manager() -> None:
	if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can run the CRM cutover."), frappe.PermissionError)


@frappe.whitelist()
def dry_run() -> dict:
	"""What a run would copy: counts and problems, nothing written."""
	require_system_manager()
	return crm_to_erpnext.run(dry_run=True)


@frappe.whitelist(methods=["POST"])
def start() -> dict:
	"""Queue the real run (it can take minutes); `status` says when it's done."""
	require_system_manager()
	from frappe.utils.background_jobs import is_job_enqueued

	if is_job_enqueued(JOB_ID):
		return {"queued": False, "message": _("The cutover is already running.")}
	frappe.enqueue(
		"tbo_smart.cutover.crm_to_erpnext.run",
		queue="long",
		timeout=3600,
		job_id=JOB_ID,
		dry_run=False,
	)
	return {"queued": True, "message": _("The cutover is running. Check status in a few minutes.")}


@frappe.whitelist()
def status() -> dict:
	"""Whether the run is still going, and the latest run's report."""
	require_system_manager()
	from frappe.utils.background_jobs import is_job_enqueued

	folder = frappe.get_site_path("private", "files", crm_to_erpnext.REPORT_FOLDER)
	reports = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
	report = None
	if reports:
		with open(os.path.join(folder, reports[-1])) as file:  # the site's own report folder - nosemgrep
			report = json.load(file)
	return {"running": is_job_enqueued(JOB_ID), "last_report": report}


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
