app_name = "tbo_smart"
app_title = "Tbo Smart"
app_publisher = "sammish"
app_description = "tbo_smart"
app_email = "sammish.thundiyil@gmail.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["erpnext", "hrms"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "tbo_smart",
# 		"logo": "/assets/tbo_smart/logo.png",
# 		"title": "Tbo Smart",
# 		"route": "/tbo_smart",
# 		"has_permission": "tbo_smart.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/tbo_smart/css/tbo_smart.css"
# app_include_js = "/assets/tbo_smart/js/tbo_smart.js"

# include js, css files in header of web template
# web_include_css = "/assets/tbo_smart/css/tbo_smart.css"
# web_include_js = "/assets/tbo_smart/js/tbo_smart.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "tbo_smart/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
doctype_js = {"Quotation": "public/js/quotation_adjustment.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "tbo_smart/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "tbo_smart.utils.jinja_methods",
# 	"filters": "tbo_smart.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "tbo_smart.install.before_install"
after_install = "tbo_smart.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "tbo_smart.uninstall.before_uninstall"
# after_uninstall = "tbo_smart.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "tbo_smart.utils.before_app_install"
# after_app_install = "tbo_smart.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "tbo_smart.utils.before_app_uninstall"
# after_app_uninstall = "tbo_smart.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "tbo_smart.notifications.get_notification_config"

# Awesome Bar
# -----------
# Extra search results: list of dicts with label, description, route, index.
# route: ["List", "ToDo"], "/desk/docs/some/page", or "https://example.com"
# awesomebar_search = ["tbo_smart.search.awesomebar_results"]

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Notification Log": {"after_insert": "tbo_smart.push.on_notification_log"},
	"PWA Notification": {"after_insert": "tbo_smart.push.on_pwa_notification"},
	"User": {"on_update": "tbo_smart.api.mobile.auth.revoke_tokens_of_disabled_user"},
	# Check-in radius and place name (docs/checkin-location.md)
	"Employee Checkin": {
		"validate": "tbo_smart.checkin.employee_checkin.validate",
		"after_insert": "tbo_smart.checkin.employee_checkin.after_insert",
	},
	"Quotation": {
		"validate": ["tbo_smart.sales.remember_lead", "tbo_smart.quotation_control.validate"],
		"on_update": [
			"tbo_smart.quotation_control.on_update",
			"tbo_smart.finance.stage.stage_changed",
			"tbo_smart.lead_pipeline.quotations.quotation_changed",
		],
		"on_submit": [
			"tbo_smart.quotation_control.on_submit",
			"tbo_smart.finance.stage.stage_changed",
			"tbo_smart.lead_pipeline.quotations.quotation_changed",
		],
		"on_cancel": "tbo_smart.finance.stage.stage_changed",
		"on_trash": "tbo_smart.finance.stage.stage_changed",
	},
	# Finance workflow Phase 1 (docs/crm-finance-workflow.md); every hook no-ops while it is off
	# The deal: its stage history, won/lost day and ERPNext's Lost status (docs/erpnext-lead-cutover.md)
	"Opportunity": {
		"before_insert": "tbo_smart.finance.stage.default_company",
		"validate": ["tbo_smart.sales.remember_lead", "tbo_smart.sales.opportunity.validate_opportunity"],
	},
	"Sales Order": {
		"validate": "tbo_smart.finance.documents.validate",
		"on_submit": "tbo_smart.finance.stage.stage_changed",
		"on_cancel": "tbo_smart.finance.stage.stage_changed",
	},
	"Sales Invoice": {
		"validate": "tbo_smart.finance.documents.validate",
		"on_submit": "tbo_smart.finance.stage.stage_changed",
		"on_cancel": "tbo_smart.finance.stage.stage_changed",
	},
	"Payment Entry": {
		"validate": "tbo_smart.finance.documents.validate",
		"on_submit": [
			"tbo_smart.finance.stage.stage_changed",
			"tbo_smart.finance.allocation.payment_submitted",
		],
		"on_cancel": [
			"tbo_smart.finance.stage.stage_changed",
			"tbo_smart.finance.allocation.payment_cancelled",
		],
	},
	"Journal Entry": {"on_submit": "tbo_smart.finance.allocation.journal_submitted"},
	# The lead's stage rules and history (docs/erpnext-lead-cutover.md)
	# Smart leads (docs/smart-leads.md): re-score a lead when it or its activity changes; no-op while off
	# Lead pipeline (docs/lead-pipeline.md): the deal at Closed – Won; no-op while off
	# AI runs leads (docs/smart-leads.md §11): the AI reads a lead again when it or its activity
	# changes; no-op while off
	"Lead": {
		"validate": [
			"tbo_smart.sales.stages.validate_lead",
			"tbo_smart.lead_pipeline.rate_card.validate_lead_segment",
		],
		"after_insert": "tbo_smart.smart_leads.autopilot.lead_changed",
		"on_update": [
			"tbo_smart.smart_leads.scoring.lead_changed",
			"tbo_smart.lead_pipeline.won.lead_updated",
			"tbo_smart.smart_leads.autopilot.lead_changed",
		],
	},
	"Comment": {
		"after_insert": [
			"tbo_smart.smart_leads.scoring.activity_changed",
			"tbo_smart.smart_leads.autopilot.activity_changed",
		]
	},
	"Event": {
		"after_insert": [
			"tbo_smart.smart_leads.scoring.activity_changed",
			"tbo_smart.smart_leads.autopilot.activity_changed",
		]
	},
	"Communication": {"after_insert": "tbo_smart.smart_leads.autopilot.activity_changed"},
	"ToDo": {"on_update": "tbo_smart.smart_leads.scoring.activity_changed"},
	# Lead quotations: an approval request or decision changes the quotation's draft state
	"TBO Quotation Adjustment": {"on_update": "tbo_smart.lead_pipeline.quotations.adjustment_changed"},
	"Bank Transaction": {
		"before_insert": "tbo_smart.finance.reconciliation.line_before_insert",
		"after_insert": "tbo_smart.finance.reconciliation.line_after_insert",
		"before_update_after_submit": "tbo_smart.finance.reconciliation.line_before_update_after_submit",
		"on_update_after_submit": "tbo_smart.finance.reconciliation.line_on_update_after_submit",
	},
}

after_migrate = [
	"tbo_smart.finance.setup.after_migrate",
	"tbo_smart.checkin.setup.setup_checkin_location",
	"tbo_smart.smart_leads.setup.after_migrate",
	"tbo_smart.lead_pipeline.setup.after_migrate",
	"tbo_smart.telephony.setup.setup_telephony",
]

scheduler_events = {
	"daily": [
		"tbo_smart.finance.stage.recompute_all",
		"tbo_smart.finance.transfers.flag_pending_transfers",
		"tbo_smart.finance.reconciliation.flag_unmatched_lines",
		"tbo_smart.smart_leads.scoring.score_all",
		"tbo_smart.lead_pipeline.quotations.expire_quotations",
		"tbo_smart.lead_pipeline.proposals.expire_proposals",
		# Telephony: recordings and transcripts past their retention (docs/telephony-myoperator.md)
		"tbo_smart.telephony.calls.purge_old_recordings",
	],
	"cron": {
		# Phase 2 (receivables): digest at 08:00, reminders at 09:00, site time
		"0 8 * * *": ["tbo_smart.finance.digest.send_digest"],
		# AI runs leads: drafts for leads gone quiet, before the 09:30 digest
		"0 9 * * *": [
			"tbo_smart.finance.reminders.run_reminders",
			"tbo_smart.smart_leads.autopilot.nudge_stale_leads",
		],
		# Smart leads: escalation notices and the follow-up digest at 09:30, site time
		"30 9 * * *": [
			"tbo_smart.smart_leads.follow_ups.run_follow_ups",
			"tbo_smart.lead_pipeline.meetings.run_meeting_morning",
		],
		# Lead pipeline: meetings in progress, MOM pending and reminders
		"*/15 * * * *": ["tbo_smart.lead_pipeline.meetings.run_meeting_clock"],
	},
}

# Leads and deals: owner, assignee and the sales hierarchy (docs/erpnext-lead-cutover.md §3)
permission_query_conditions = {
	"Lead": "tbo_smart.sales.permissions.get_lead_query_conditions",
	"Opportunity": "tbo_smart.sales.permissions.get_opportunity_query_conditions",
	"TBO Finance Exception": "tbo_smart.finance.access.exception_query_conditions",
	"TBO Lead Meeting": "tbo_smart.lead_pipeline.meetings.get_permission_query_conditions",
	"TBO Proposal": "tbo_smart.lead_pipeline.proposals.get_permission_query_conditions",
}

has_permission = {
	"Lead": "tbo_smart.sales.permissions.has_lead_permission",
	"Opportunity": "tbo_smart.sales.permissions.has_opportunity_permission",
	"TBO Finance Exception": "tbo_smart.finance.access.has_exception_permission",
	"TBO Lead Meeting": "tbo_smart.lead_pipeline.meetings.has_permission",
	"TBO Proposal": "tbo_smart.lead_pipeline.proposals.has_permission",
}

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"tbo_smart.tasks.all"
# 	],
# 	"daily": [
# 		"tbo_smart.tasks.daily"
# 	],
# 	"hourly": [
# 		"tbo_smart.tasks.hourly"
# 	],
# 	"weekly": [
# 		"tbo_smart.tasks.weekly"
# 	],
# 	"monthly": [
# 		"tbo_smart.tasks.monthly"
# 	],
# }

# Testing
# -------

# The client's proposal page (docs/proposals.md §5)
website_route_rules = [
	{"from_route": "/proposal/<token>", "to_route": "proposal"},
	# a BRD's questionnaire (docs/client-documents.md §5)
	{"from_route": "/questions/<token>", "to_route": "questions"},
]

before_tests = "tbo_smart.test_utils.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "tbo_smart.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "tbo_smart.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]
# A draft quotation can still be deleted; its adjustment audit trail stays.
ignore_links_on_delete = ["TBO Quotation Adjustment"]

# Request Events
# ----------------
# before_request = ["tbo_smart.utils.before_request"]
# after_request = ["tbo_smart.utils.after_request"]

# Job Events
# ----------
# before_job = ["tbo_smart.utils.before_job"]
# after_job = ["tbo_smart.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"tbo_smart.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
