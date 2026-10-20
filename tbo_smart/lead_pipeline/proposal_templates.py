"""What a site starts with (docs/proposals.md §3): the Universal IT Proposal's solution-type
presets, the module catalogue and the section libraries. Seeded once (setup.seed_proposal_master);
after that they are the preset editors' own, changed in the hub's Settings → Rates and proposals.

Plain English for the client to read. Nothing here is a price the owner hasn't set: modules and
AMC plans start at 0 until the owner fills them in (or links an Item priced on the rate card).
"""

# ---------- the presets: which master sections are on, and what the scoping form asks ----------

GUIDANCE = {
	"Executive Summary": "One page: the client's business, their main problems, what we propose and what it will change for them.",
	"Client Background": "Their current software and processes, what is hard today, the limits of the current system and what they want automated. Only what they told us.",
	"Business Challenges": "Only the client's real challenges, from the notes and meetings. Fewer is better than invented.",
	"Proposed Solution": "The business benefits of the solution, in the client's terms.",
}


def preset(
	name: str,
	description: str,
	page_guide: str,
	questions: tuple,
	modules: tuple = (),
) -> dict:
	"""A solution type's preset: its scoping questions and default modules. Every new proposal
	starts with all the master sections on; presales turns off what doesn't fit."""
	from tbo_smart.lead_pipeline.proposals import MASTER_SECTIONS

	# every template that asks for users asks for companies with it
	if "users" in questions and "companies" not in questions:
		questions = (
			*questions[: questions.index("users") + 1],
			"companies",
			*questions[questions.index("users") + 1 :],
		)
	return {
		"template_name": name,
		"solution_type": name,
		"description": description,
		"page_guide": page_guide,
		"questions": "\n".join(questions),
		"default_modules": "\n".join(modules),
		"sections": [
			{
				"section_type": kind,
				"enabled": 1,
				"heading": heading,
				"guidance": GUIDANCE.get(kind),
			}
			for kind, heading in MASTER_SECTIONS
		],
	}


ERP_MODULES = ("Accounts", "Selling & CRM", "Buying", "Stock & Inventory", "Reports & Dashboards", "Training")


def templates() -> tuple:
	from tbo_smart.lead_pipeline.proposals import QUESTIONS as ALL_QUESTIONS

	return (
		preset(
			"ERPNext Implementation",
			"ERPNext set up for the client: modules, data migration, training, go-live and support.",
			"ERP (10–15 pages)",
			ALL_QUESTIONS,
			ERP_MODULES,
		),
		preset(
			"ERPNext + Flutter Mobile App",
			"ERPNext with a Flutter mobile app for the field team or customers, on one backend.",
			"ERP + mobile (15–25 pages)",
			ALL_QUESTIONS,
			(*ERP_MODULES, "Flutter Mobile App"),
		),
		preset(
			"Flutter Mobile App",
			"A Flutter app for Android and iOS, with its backend and admin panel.",
			"Small project (5–8 pages)",
			("integrations", "users", "hosting", "amc", "timeline_weeks", "source_code", "architecture"),
			("Flutter Mobile App", "Admin Panel"),
		),
		preset(
			"Web Application",
			"A web application built for the client's process, with its admin panel and reports.",
			"ERP (10–15 pages)",
			(
				"integrations",
				"data_migration",
				"users",
				"hosting",
				"amc",
				"timeline_weeks",
				"source_code",
				"architecture",
			),
			("Web Application", "Admin Panel", "Reports & Dashboards"),
		),
		preset(
			"Custom Software Development",
			"Software built for a need no product covers: discovery, design, build, launch.",
			"ERP (10–15 pages)",
			ALL_QUESTIONS,
			("Web Application", "Admin Panel"),
		),
		preset(
			"Frappe HR / HRMS",
			"Frappe HR for employees, attendance, leave, payroll and appraisals.",
			"ERP (10–15 pages)",
			(
				"modules",
				"customisation",
				"mobile_app",
				"integrations",
				"data_migration",
				"users",
				"hosting",
				"amc",
				"timeline_weeks",
			),
			("HR & Payroll", "Reports & Dashboards", "Training"),
		),
		preset(
			"Helpdesk",
			"A helpdesk for customer or staff support: tickets, SLAs, knowledge base and reports.",
			"Small project (5–8 pages)",
			("modules", "customisation", "integrations", "users", "hosting", "amc", "timeline_weeks"),
			("Helpdesk", "Training"),
		),
		preset(
			"CRM",
			"A CRM for leads, deals, follow-ups and the sales pipeline.",
			"Small project (5–8 pages)",
			(
				"modules",
				"customisation",
				"mobile_app",
				"integrations",
				"data_migration",
				"users",
				"hosting",
				"amc",
				"timeline_weeks",
			),
			("Selling & CRM", "Training"),
		),
		preset(
			"Website / Digital",
			"A website and its digital presence: design, content, SEO and hosting.",
			"Small project (5–8 pages)",
			("integrations", "hosting", "amc", "timeline_weeks"),
			("Website & Portal",),
		),
		preset(
			"AMC / Support",
			"An annual maintenance contract for a system already running.",
			"Small project (5–8 pages)",
			("users", "hosting", "amc"),
			(),
		),
	)


# the first site templates (v1.0), superseded by the presets above and disabled once
OLD_TEMPLATES = ("Helpdesk / Support", "Custom Solution", "Digital Marketing")

# ---------- the module catalogue: pre-written once, assembled into every proposal ----------

DA = "Development & Configuration"
BA = "Business Analysis"
TE = "Testing"
DE = "Deployment"
TR = "Training & Handover"
ERP = ("ERPNext Implementation", "ERPNext + Flutter Mobile App")
CUSTOM = ("Flutter Mobile App", "Web Application", "Custom Software Development")


def module(
	name: str,
	solution_types: tuple,
	description: str,
	features: tuple,
	effort: float,
	fit: str = "Configured",
	scope: tuple = (),
	deliverables: tuple = (),
	assumptions: tuple = (),
	out_of_scope: tuple = (),
	workflow: tuple = (),
	order: int = 0,
) -> dict:
	"""A catalogue module with everything it adds to a proposal. Prices start at 0: the owner
	sets them (or links an Item priced on the segment rate card)."""
	return {
		"module_name": name,
		"solution_types": "\n".join(solution_types),
		"description": description,
		"features": "\n".join(features),
		"effort_days": effort,
		"fit": fit,
		"default_price": 0,
		"scope": "\n".join(scope),
		"deliverables": "\n".join(deliverables),
		"assumptions": "\n".join(assumptions),
		"out_of_scope": "\n".join(out_of_scope),
		"workflow": "\n".join(workflow),
		"sort_order": order,
	}


MODULES = (
	module(
		"Accounts",
		ERP,
		"Complete books of account on ERPNext: the general ledger, receivables and payables, banking, "
		"GST and the financial statements management reads every month.",
		(
			"Chart of accounts, cost centres and fiscal years",
			"Sales and purchase invoices with GST",
			"Receivables and payables with ageing",
			"Payments, journal entries and bank reconciliation",
			"GST: tax templates, e-invoice, e-way bill, GSTR-1 and GSTR-3B",
			"Profit and loss, balance sheet, cash flow and trial balance",
		),
		10,
		scope=(
			f"{BA} | Study of the current chart of accounts, tax set-up and month-end close",
			f"{DA} | Chart of accounts, cost centres, fiscal year and accounting dimensions",
			f"{DA} | GST settings, tax templates, e-invoice and e-way bill",
			f"{DA} | Bank accounts and bank reconciliation",
			f"{DA} | Print formats for invoices, receipts and vouchers",
			f"{TE} | Test of a full month: invoices, payments, GST returns and statements",
		),
		deliverables=("Accounts configured with GST, ready for daily entries",),
		assumptions=("The client's auditor confirms the chart of accounts and opening balances.",),
		out_of_scope=("Filing GST returns or audit work: the system prepares the reports.",),
		workflow=("Sales to cash: Quotation → Sales Order → Invoice → Payment → Bank Reconciliation",),
		order=1,
	),
	module(
		"HR & Payroll",
		("Frappe HR / HRMS", *ERP),
		"Frappe HR for the whole employee life cycle: records, attendance and check-in, leave, payroll "
		"with Indian statutory deductions, appraisals and expense claims.",
		(
			"Employee master with documents, onboarding and exit",
			"Attendance with check-in, shifts and holiday lists",
			"Leave types, policies, balances and approvals",
			"Salary structures, monthly payroll and salary slips",
			"Statutory PF, ESI and professional tax",
			"Appraisals with goals and reviews",
			"Expense claims and employee advances",
		),
		12,
		scope=(
			f"{BA} | Study of HR policies, leave rules and the salary structure",
			f"{DA} | Employee master, departments, designations and the reporting line",
			f"{DA} | Attendance, check-in, shifts and holiday lists",
			f"{DA} | Leave types, leave policies and approval workflows",
			f"{DA} | Salary components, structures, PF, ESI and professional tax",
			f"{DA} | Appraisal templates, expense claims and advances",
			f"{TE} | A parallel payroll run against the current month's salaries",
		),
		deliverables=("HR and payroll configured, with a verified parallel payroll run",),
		assumptions=(
			"The client gives the salary structure and statutory details (PF, ESI, PT registrations).",
		),
		out_of_scope=("Statutory filing with the PF and ESI portals: the system prepares the files.",),
		workflow=("Hire to retire: Onboarding → Attendance → Leave → Payroll → Appraisal → Exit",),
		order=2,
	),
	module(
		"Selling & CRM",
		(*ERP, "CRM"),
		"From the first enquiry to the sales order: leads, opportunities, quotations, price lists and the "
		"sales pipeline, in one place.",
		(
			"Leads, opportunities and follow-ups",
			"Quotations and sales orders",
			"Customers, contacts and price lists",
			"Sales pipeline and sales analytics",
		),
		5,
		scope=(
			f"{DA} | Lead sources, sales stages and the opportunity pipeline",
			f"{DA} | Customers, price lists, terms and the quotation print format",
			f"{DA} | Sales orders, delivery and sales reports",
		),
		deliverables=("Selling and CRM configured with the client's price lists",),
		workflow=("Lead to order: Lead → Opportunity → Quotation → Sales Order",),
		order=3,
	),
	module(
		"Buying",
		ERP,
		"Purchasing under control: material requests, supplier quotations, purchase orders, receipts and "
		"supplier payments.",
		(
			"Material requests and approvals",
			"Supplier quotations and comparison",
			"Purchase orders and purchase receipts",
			"Supplier ledger and payment terms",
		),
		5,
		scope=(
			f"{DA} | Suppliers, buying price lists and approval rules",
			f"{DA} | Material requests, purchase orders and receipts",
		),
		deliverables=("Buying configured with approval rules",),
		workflow=(
			"Purchase to pay: Material Request → Purchase Order → Receipt → Purchase Invoice → Payment",
		),
		order=4,
	),
	module(
		"Stock & Inventory",
		ERP,
		"Stock across warehouses with batches, serial numbers, reorder levels and valuation, so the books "
		"and the godown agree.",
		(
			"Items, units of measure and item groups",
			"Multiple warehouses and stock transfers",
			"Batch and serial number tracking",
			"Reorder levels and stock alerts",
			"Stock ledger, valuation and ageing",
		),
		6,
		scope=(
			f"{DA} | Item master, units, item groups and warehouses",
			f"{DA} | Batch and serial settings, reorder levels and valuation method",
			f"{TE} | Stock reconciliation against a physical count",
		),
		deliverables=("Stock configured with opening stock per warehouse",),
		assumptions=("A physical stock count is done before go-live for the opening stock.",),
		order=5,
	),
	module(
		"Manufacturing",
		ERP,
		"Production planned and tracked: bills of material, work orders, job cards and material "
		"consumption.",
		(
			"Bills of material with sub-assemblies",
			"Production plans and work orders",
			"Job cards and workstations",
			"Material consumption and finished goods",
		),
		8,
		scope=(
			f"{BA} | Study of products, routings and the production process",
			f"{DA} | Bills of material, workstations and operations",
			f"{DA} | Production planning and work orders",
		),
		deliverables=("Manufacturing configured for the client's products",),
		workflow=("Make to order: Sales Order → Production Plan → Work Order → Job Card → Finished Goods",),
		order=6,
	),
	module(
		"Projects & Timesheets",
		ERP,
		"Projects, tasks and timesheets, with billing and profitability per project.",
		(
			"Projects, tasks and milestones",
			"Timesheets and activity costs",
			"Project billing",
			"Project profitability",
		),
		4,
		scope=(f"{DA} | Project templates, activity types, costing and billing",),
		deliverables=("Projects and timesheets configured",),
		order=7,
	),
	module(
		"Assets",
		ERP,
		"Fixed assets with depreciation, movement and maintenance.",
		(
			"Asset register and categories",
			"Depreciation schedules (straight line or written down value)",
			"Asset movement, maintenance and disposal",
		),
		3,
		scope=(f"{DA} | Asset categories, depreciation methods and the asset register",),
		deliverables=("The asset register with depreciation schedules",),
		assumptions=("The client gives the current asset register with book values.",),
		order=8,
	),
	module(
		"Helpdesk",
		("Helpdesk", "AMC / Support", *ERP),
		"A helpdesk for customers or staff: tickets from email and the portal, SLAs, a knowledge base and "
		"support reports.",
		(
			"Ticket inbox from email and the portal",
			"Assignment rules, SLAs and escalations",
			"Canned replies and a knowledge base",
			"Support reports and customer satisfaction",
		),
		5,
		scope=(
			f"{DA} | Teams, ticket types, SLAs and assignment rules",
			f"{DA} | Email channels, the customer portal and the knowledge base",
		),
		deliverables=("The helpdesk live on the client's support email",),
		workflow=("Ticket life cycle: Ticket → Assignment → Response → Resolution → Feedback",),
		order=9,
	),
	module(
		"Website & Portal",
		("Website / Digital", *ERP),
		"A fast, responsive website the client's team can update, with a customer portal on the same "
		"system.",
		(
			"Responsive pages and a content editor",
			"Customer portal for orders, invoices and tickets",
			"Contact forms, SEO basics and analytics",
		),
		10,
		"Customised",
		scope=(
			"System Design | Page designs and the site map",
			f"{DA} | Pages, content editor, portal and forms",
			f"{DE} | Domain, SSL and launch",
		),
		deliverables=("The website and portal live on the client's domain",),
		assumptions=("The client gives the content and images for the pages.",),
		out_of_scope=("Writing content and producing photography or video.",),
		order=10,
	),
	module(
		"Flutter Mobile App",
		("ERPNext + Flutter Mobile App", "Flutter Mobile App"),
		"A Flutter app for Android and iOS on the same backend: attendance, leave, approvals and sales "
		"on the phone, with push notifications.",
		(
			"Sign-in with roles",
			"Attendance check-in with location",
			"Leave and expense requests",
			"Approvals on the go",
			"Sales orders and customer visits",
			"Push notifications",
		),
		25,
		"Customised",
		scope=(
			"System Design | Screen designs for the mobile app",
			f"{DA} | Flutter app for Android and iOS on the system's API",
			f"{TE} | Testing on Android and iOS devices",
			f"{DE} | Play Store and App Store release",
		),
		deliverables=("The mobile app published on the Play Store and App Store",),
		assumptions=("The Apple and Google developer accounts are in the client's name.",),
		out_of_scope=("Features beyond the screens listed in this proposal.",),
		workflow=("On the phone: Sign-in → Check-in → Request → Approve → Sync",),
		order=11,
	),
	module(
		"Web Application",
		("Web Application", "Custom Software Development"),
		"A web application built for the client's process, with role-based screens, workflows and "
		"approvals.",
		("Responsive screens", "Workflows and approvals", "Role-based access", "Audit trail"),
		20,
		"Customised",
		scope=(
			"System Design | Screen designs and the data model",
			f"{DA} | The web application, its workflows and its API",
		),
		deliverables=("The web application live on the production server",),
		workflow=("App flow: Sign-in → Dashboard → Create → Approve → Report",),
		order=12,
	),
	module(
		"Admin Panel",
		CUSTOM,
		"The web panel to manage users, content and settings.",
		("User and role management", "Masters and settings", "Activity log"),
		6,
		"Customised",
		scope=(f"{DA} | Admin panel for users, masters and settings",),
		order=13,
	),
	module(
		"Reports & Dashboards",
		(*ERP, "Frappe HR / HRMS", "Web Application", "Custom Software Development"),
		"The reports and dashboards management reads every day, and scheduled reports by email.",
		("Standard reports", "Custom dashboards and charts", "Scheduled email reports"),
		4,
		scope=(f"{DA} | Dashboards and custom reports agreed in the requirement document",),
		deliverables=("Dashboards for management",),
		order=14,
	),
	module(
		"Integrations",
		(*ERP, *CUSTOM, "Frappe HR / HRMS", "Helpdesk", "CRM"),
		"Connections with the client's other systems, so data moves without re-typing.",
		("REST API and webhooks", "Scheduled sync", "Error logs and retries"),
		5,
		"Customised",
		scope=(f"{DA} | Integrations with the systems listed in the scoping",),
		deliverables=("Working integrations with their error logs",),
		assumptions=("Third-party systems give API access, documentation and a test account.",),
		out_of_scope=("Changes on the third-party systems' side.",),
		order=15,
	),
	module(
		"Data Migration",
		(*ERP, "Frappe HR / HRMS", "CRM", "Helpdesk", "Web Application", "Custom Software Development"),
		"Masters and opening balances moved from the current system, checked with the client before go-live.",
		("Migration templates", "Trial load and checks", "Final load at go-live"),
		4,
		scope=(
			f"{DE} | Migration of masters (customers, suppliers, items, employees) and opening balances",
			f"{TE} | A trial load checked with the client before the final load",
		),
		deliverables=("Migrated master data and opening balances, signed off by the client",),
		assumptions=("Data is given in the agreed templates, cleaned, two weeks before go-live.",),
		out_of_scope=("Migrating past transactions; only opening balances move.",),
		order=16,
	),
	module(
		"Training",
		(*ERP, "Frappe HR / HRMS", "Helpdesk", "CRM", *CUSTOM),
		"Role-wise training for the client's users and an admin hand-over, with user guides.",
		("Role-wise user training", "Admin training", "User guides"),
		3,
		"Standard",
		scope=(
			f"{TR} | Role-wise training sessions for the key users",
			f"{TR} | Admin training and hand-over",
		),
		deliverables=("User guides and training sessions",),
		assumptions=("Key users attend the training sessions.",),
		order=17,
	),
)

# a scoping answer that brings a module with it
IMPLIED_MODULES = {
	"data_migration": "Data Migration",
	"integrations": "Integrations",
	"mobile_app": "Flutter Mobile App",
}


# ---------- the libraries ----------


def item(library: str, title: str, body: str = "", **values) -> dict:
	return {"library": library, "title": title, "body": body, **values}


SCOPE = "Scope of Work"
ERP_LINE = "ERPNext Implementation\nERPNext + Flutter Mobile App"
LIBRARY = (
	# 3. Company Overview (boilerplate, not regenerated)
	item(
		"Company Overview",
		"Who we are",
		"TBO (Team Back Office) builds and runs business software for growing companies in Kerala "
		"and beyond. We implement ERPNext and Frappe, build web and mobile applications, and stay "
		"with our clients after go-live.",
		sort_order=1,
	),
	item(
		"Company Overview",
		"What we do",
		"- ERPNext and Frappe implementation\n- Custom web and mobile applications (Flutter)\n"
		"- Helpdesk, CRM and HR systems\n- Annual maintenance and support",
		sort_order=2,
	),
	item(
		"Company Overview",
		"Why choose us",
		"- One team from analysis to support, so nothing is lost in hand-overs\n"
		"- Open-source platforms: no per-user licence lock-in\n"
		"- A local team, quick to respond and clear to work with",
		sort_order=3,
	),
	# 6. Proposed Solution: the component table
	item(
		"Solution Component",
		"ERP backend",
		"ERPNext on the Frappe Framework: the system of record for every module.",
		solution_types=f"{ERP_LINE}\nFrappe HR / HRMS\nHelpdesk\nCRM",
		sort_order=1,
	),
	item(
		"Solution Component",
		"Application backend",
		"A Frappe / Python backend with a REST API for the apps and integrations.",
		solution_types="Flutter Mobile App\nWeb Application\nCustom Software Development",
		sort_order=2,
	),
	item(
		"Solution Component",
		"Mobile app",
		"A Flutter app for Android and iOS on the same backend.",
		condition="Mobile app",
		sort_order=3,
	),
	item(
		"Solution Component",
		"Web application",
		"A responsive web application for desktop and tablet.",
		condition="Web app",
		sort_order=4,
	),
	item("Solution Component", "Database", "MariaDB, with daily backups.", sort_order=5),
	item(
		"Solution Component",
		"Hosting",
		"Hosted as agreed in this proposal, with SSL and monitoring.",
		condition="Hosting",
		sort_order=6,
	),
	item(
		"Solution Component",
		"Integrations",
		"Connections to the client's other systems, as listed in the scope.",
		condition="Integrations",
		sort_order=7,
	),
	item(
		"Solution Component",
		"Reporting",
		"Standard reports and dashboards, plus the custom ones in scope.",
		sort_order=8,
	),
	# 7. Scope of Work, by subsection
	item(SCOPE, "Requirement workshops with each department", group="Business Analysis", sort_order=1),
	item(SCOPE, "Gap analysis and the agreed requirement document", group="Business Analysis", sort_order=2),
	item(SCOPE, "Solution design and process mapping", group="System Design", sort_order=3),
	item(SCOPE, "Configuration of the selected modules", group="Development & Configuration", sort_order=5),
	item(
		SCOPE,
		"Customisations as listed in this proposal",
		group="Development & Configuration",
		condition="Customisation",
		sort_order=6,
	),
	item(SCOPE, "Functional testing by our QA team", group="Testing", sort_order=9),
	item(SCOPE, "User acceptance testing with the client's key users", group="Testing", sort_order=10),
	item(SCOPE, "Production set-up and go-live", group="Deployment", sort_order=12),
	# 9. Proposed Workflow: the default chain of each solution type
	item(
		"Workflow",
		"Ticket life cycle",
		"Ticket → Assignment → Response → Resolution → Feedback",
		solution_types="AMC / Support",
	),
	# 10. Technical Architecture
	item(
		"Architecture", "Frontend", "Frappe desk and portal in the browser; Flutter for mobile.", sort_order=1
	),
	item("Architecture", "Backend", "Frappe Framework (Python) with role-based permissions.", sort_order=2),
	item("Architecture", "Database", "MariaDB.", sort_order=3),
	item(
		"Architecture", "API", "REST API with token authentication for apps and integrations.", sort_order=4
	),
	item("Architecture", "Cloud", "Frappe Cloud or the client's own server, as agreed.", sort_order=5),
	item(
		"Architecture",
		"Security",
		"HTTPS everywhere, role permissions and an audit trail.",
		condition="Detailed architecture",
		sort_order=6,
	),
	item(
		"Architecture",
		"Backup",
		"Daily automated backups, kept off-site.",
		condition="Detailed architecture",
		sort_order=7,
	),
	item(
		"Architecture",
		"Integrations",
		"Webhooks and scheduled sync with the client's systems.",
		condition="Integrations",
		sort_order=8,
	),
	# 11. Deliverables
	item("Deliverable", "The configured system on the production server", sort_order=1),
	item("Deliverable", "Source code in the client's repository", condition="Source code", sort_order=4),
	item("Deliverable", "Open-source platform: no licence fees for ERPNext or Frappe", sort_order=6),
	# 12. Implementation plan: what happens in each phase
	item(
		"Phase",
		"Analysis",
		"Workshops with each department, the requirement document, sign-off",
		sort_order=1,
	),
	item("Phase", "Design", "Solution design, process maps, screen designs", sort_order=2),
	item(
		"Phase",
		"Development & Configuration",
		"Module set-up, customisations, integrations, data migration trial",
		sort_order=3,
	),
	item(
		"Phase",
		"Testing & UAT",
		"Functional testing, user acceptance testing with key users, fixes",
		sort_order=4,
	),
	item("Phase", "Deployment", "Production set-up, final data load, cut-over", sort_order=5),
	item(
		"Phase", "Training & Go-live", "Role-wise training, go-live and close support after it", sort_order=6
	),
	# 13. Project timeline: templates by length, each phase in days (presales picks one)
	item(
		"Timeline",
		"45 days",
		"Analysis | 5\nDesign | 5\nDevelopment & Configuration | 15\nTesting & UAT | 10\nDeployment | 5\nTraining & Go-live | 5",
		weeks=45,
		sort_order=1,
	),
	item(
		"Timeline",
		"60 days",
		"Analysis | 7\nDesign | 7\nDevelopment & Configuration | 22\nTesting & UAT | 12\nDeployment | 5\nTraining & Go-live | 7",
		weeks=60,
		sort_order=2,
	),
	item(
		"Timeline",
		"90 days",
		"Analysis | 10\nDesign | 10\nDevelopment & Configuration | 35\nTesting & UAT | 18\nDeployment | 7\nTraining & Go-live | 10",
		weeks=90,
		sort_order=3,
	),
	item(
		"Timeline",
		"120 days",
		"Analysis | 14\nDesign | 14\nDevelopment & Configuration | 46\nTesting & UAT | 24\nDeployment | 10\nTraining & Go-live | 12",
		weeks=120,
		sort_order=4,
	),
	# 14. Team
	item(
		"Role", "Project Manager", "The plan, progress reports and the single point of contact", sort_order=1
	),
	item(
		"Role",
		"ERP Consultant / Business Analyst",
		"Workshops, requirements, configuration and training",
		sort_order=2,
	),
	item("Role", "Backend Developer", "Customisations, integrations and APIs", sort_order=3),
	item(
		"Role",
		"Mobile Developer",
		"The Flutter app for Android and iOS",
		condition="Mobile app",
		sort_order=4,
	),
	item(
		"Role",
		"QA Engineer",
		"Test plans, functional testing and UAT support",
		condition="Large project",
		sort_order=5,
	),
	item(
		"Role",
		"DevOps Engineer",
		"Servers, deployment, backups and monitoring",
		condition="Large project",
		sort_order=6,
	),
	# 15. Investment notes
	item("Investment Note", "GST is extra, as shown in the totals.", sort_order=1),
	item(
		"Investment Note",
		"Third-party licences, hosting and payment gateway or API charges are paid by the client at actuals.",
		sort_order=2,
	),
	item("Investment Note", "Travel outside Kochi, if needed, is billed at actuals.", sort_order=3),
	# 16. Payment terms presets
	item(
		"Payment Terms",
		"30 / 30 / 30 / 10",
		"Advance on signing | 30\nAfter development | 30\nAfter UAT | 30\nOn go-live | 10",
		sort_order=1,
	),
	item(
		"Payment Terms",
		"50 / 40 / 10",
		"Advance on signing | 50\nAfter UAT | 40\nOn go-live | 10",
		sort_order=2,
	),
	# 17. Warranty
	item(
		"Warranty",
		"Warranty",
		"We fix any defect in what we delivered for 90 days after go-live, at no cost. The warranty "
		"covers the agreed scope; new features and changes to agreed requirements are separate work, "
		"quoted as change requests.",
	),
	# 18. AMC plans (their prices are the owner's to set)
	item(
		"AMC Plan",
		"Basic",
		"Bug fixes | Included\nSupport | Email, business hours\nHealth checks | Quarterly\n"
		"Response | Next business day\nNew features | Quoted separately",
		amount=0,
		sort_order=1,
	),
	item(
		"AMC Plan",
		"Premium",
		"Bug fixes | Included\nSupport | Email and phone, business hours\nHealth checks | Monthly\n"
		"Response | 4 business hours\nNew features | 2 days a year included",
		amount=0,
		sort_order=2,
	),
	item(
		"AMC Plan",
		"Enterprise",
		"Bug fixes | Included\nSupport | A dedicated contact, extended hours\n"
		"Health checks | Monthly, with a report\nResponse | 2 hours for critical issues\n"
		"New features | 6 days a year included",
		amount=0,
		sort_order=3,
	),
	# 19. Assumptions
	item("Assumption", "The client names a project owner who can take decisions.", sort_order=1),
	item("Assumption", "Key users are available for workshops, testing and training.", sort_order=2),
	# 20. Out of scope
	item("Out of Scope", "Hardware, networking and office IT.", sort_order=1),
	item("Out of Scope", "Features not listed in this proposal.", sort_order=2),
	item("Out of Scope", "Entering transactions beyond opening balances.", sort_order=3),
	# 21. Risks
	item(
		"Risk",
		"Key users not available",
		"Agree a weekly slot with each department at kick-off.",
		sort_order=1,
	),
	item(
		"Risk",
		"Requirements change during the build",
		"Sign-off at the end of discovery; changes go through change requests.",
		sort_order=2,
	),
	item(
		"Risk",
		"Late or unclean data",
		"Templates and a trial load two weeks before go-live.",
		condition="Data migration",
		sort_order=3,
	),
	item(
		"Risk",
		"App store review delays",
		"Submit early builds for review and keep a buffer week.",
		condition="Mobile app",
		sort_order=4,
	),
	# 22. Terms & Conditions (boilerplate)
	item("Terms", "Validity", "This proposal is valid until the date on the cover.", sort_order=1),
	item(
		"Terms", "Confidentiality", "Both parties keep each other's information confidential.", sort_order=2
	),
	item(
		"Terms",
		"Intellectual property and source code",
		"The client owns their data and configuration. The source code of custom work is handed "
		"over when this proposal includes it and the payments are complete.",
		sort_order=3,
	),
	item(
		"Terms",
		"Payments",
		"Invoices are due within 7 days. Work may pause while a payment is overdue.",
		sort_order=4,
	),
	item(
		"Terms",
		"Change requests",
		"Work outside the agreed scope is estimated and approved before it starts.",
		sort_order=5,
	),
	item(
		"Terms",
		"Suspension and termination",
		"Either party may end the engagement with 30 days' notice; work done until then is billed.",
		sort_order=6,
	),
	item(
		"Terms",
		"Hosting and third parties",
		"Third-party services (hosting, gateways, app stores) are governed by their own terms.",
		sort_order=7,
	),
)


# ---------- the project agreement's clauses (docs/client-documents.md §4) ----------

CLAUSE = "Agreement Clause"
AGREEMENT_CLAUSES = (
	item(
		CLAUSE,
		"Asking for a change",
		"Either party may ask for a change to the agreed scope in writing (an email is enough). TBO "
		"estimates its effort, cost and effect on the timeline within 3 business days.",
		group="Change Requests",
		sort_order=1,
	),
	item(
		CLAUSE,
		"Approving it",
		"Work on a change starts only after the Client approves the estimate in writing. Approved "
		"changes are billed with the next milestone and may move the dates that follow.",
		group="Change Requests",
		sort_order=2,
	),
	item(
		CLAUSE,
		"Your data",
		"The Client owns its data, its configuration and the documents made for it under this agreement.",
		group="Intellectual Property",
		sort_order=1,
	),
	item(
		CLAUSE,
		"The platform",
		"ERPNext and the Frappe Framework stay under their open-source licence (GNU GPL v3); no licence "
		"fee is due for them.",
		group="Intellectual Property",
		sort_order=2,
	),
	item(
		CLAUSE,
		"TBO's components",
		"TBO keeps the rights to its own reusable components and grants the Client a perpetual licence "
		"to use them in its business.",
		group="Intellectual Property",
		sort_order=3,
	),
	item(
		CLAUSE,
		"Source code",
		"The source code of the custom work is handed over to the Client once every payment under this "
		"agreement is complete.",
		group="Intellectual Property",
		condition="Source code",
		sort_order=4,
	),
	item(
		CLAUSE,
		"Confidential information",
		"Each party keeps the other's confidential information secret and uses it only for this "
		"project, during the agreement and for 3 years after it. This doesn't cover information that "
		"is public, or that the law requires to be shared.",
		group="Confidentiality",
		sort_order=1,
	),
	item(
		CLAUSE,
		"Personal data",
		"TBO handles personal data in the Client's system only to deliver this project and as the "
		"Client instructs.",
		group="Confidentiality",
		sort_order=2,
	),
	item(
		CLAUSE,
		"Notice",
		"Either party may end this agreement with 30 days' written notice.",
		group="Termination",
		sort_order=1,
	),
	item(
		CLAUSE,
		"Serious breach",
		"Either party may end it at once if the other seriously breaches it and doesn't put it right "
		"within 15 days of being told in writing.",
		group="Termination",
		sort_order=2,
	),
	item(
		CLAUSE,
		"Work done",
		"On termination the Client pays for the work done and the milestones reached until then, and "
		"TBO hands over the Client's data and the work paid for.",
		group="Termination",
		sort_order=3,
	),
	item(
		CLAUSE,
		"Payments",
		"Invoices are due within 7 days. Work may pause while a payment is overdue.",
		group="General",
		sort_order=1,
	),
	item(
		CLAUSE,
		"Liability",
		"Neither party is liable for indirect or consequential loss. TBO's total liability under this "
		"agreement is limited to the fees paid under it.",
		group="General",
		sort_order=2,
	),
	item(
		CLAUSE,
		"Governing law",
		"This agreement is governed by the laws of India; the courts of Kochi, Kerala have jurisdiction.",
		group="General",
		sort_order=3,
	),
	item(
		CLAUSE,
		"The whole agreement",
		"This agreement, with the accepted documents it names, is the whole agreement "
		"between the parties. Changes are valid only in writing.",
		group="General",
		sort_order=4,
	),
)


# ---------- the SRS: what each module adds to a functional specification (docs/client-documents.md §6) ----------


def srs(
	requirements: tuple,
	roles: tuple = (),
	screens: tuple = (),
	validations: tuple = (),
	notifications: tuple = (),
) -> dict:
	"""A module's SRS text: requirements as "requirement | acceptance criterion" lines."""
	return {
		"srs_requirements": "\n".join(requirements),
		"srs_roles": "\n".join(roles),
		"srs_screens": "\n".join(screens),
		"srs_validations": "\n".join(validations),
		"srs_notifications": "\n".join(notifications),
	}


SRS_TEXT = {
	"Accounts": srs(
		(
			"Keep the chart of accounts, cost centres and fiscal years | The client's chart of accounts and cost centres are in the system and the fiscal year is open",
			"Raise sales invoices with GST | A sales invoice for an intra-state and an inter-state customer shows CGST/SGST and IGST correctly",
			"Record purchase invoices with GST input | A purchase invoice posts the input tax to the right GST accounts",
			"Record payments and reconcile the bank | A payment against an invoice clears it, and a bank statement line matches it in reconciliation",
			"Prepare GSTR-1 and GSTR-3B | The month's GSTR-1 and GSTR-3B reports match the invoices entered",
			"Report profit and loss, balance sheet and cash flow | The statements for a test month agree with the trial balance",
		),
		(
			"Accounts User | Enters invoices, payments and journals",
			"Accounts Manager | Approves, closes periods and sees every report",
		),
		(
			"Sales Invoice: customer, posting date, items, tax template, payment terms",
			"Purchase Invoice: supplier, bill number, bill date, items, tax template",
			"Payment Entry: party, mode, amount, reference, allocation to invoices",
		),
		(
			"A closed period can't take new entries",
			"A GSTIN is checked for its format",
			"An invoice can't be cancelled once paid without cancelling the payment",
		),
		("The accounts manager is told of invoices overdue by 30 days",),
	),
	"HR & Payroll": srs(
		(
			"Keep the employee master with documents | An employee's record holds department, designation, joining date and documents",
			"Record attendance from check-in and the biometric device | A day's check-ins become attendance with the right status",
			"Apply for and approve leave against policy | A leave application within balance is approved by the manager and the balance drops",
			"Run monthly payroll with statutory deductions | A payroll run for a test month computes PF, ESI and professional tax as the salary structure says",
			"Issue salary slips | Each employee gets a salary slip by email after payroll is submitted",
			"Claim and approve expenses | An expense claim is approved by the manager and paid through accounts",
		),
		(
			"Employee | Applies for leave and expenses, sees own slips",
			"HR Manager | Keeps the masters and runs payroll",
			"Reporting Manager | Approves leave and expenses",
		),
		(
			"Employee: name, department, designation, joining date, reporting manager, bank account",
			"Leave Application: employee, leave type, from, to, reason",
			"Salary Slip: employee, period, earnings, deductions, net pay",
		),
		(
			"Leave can't exceed the balance unless the policy allows",
			"Payroll can't run twice for the same period",
		),
		(
			"The manager is told of each leave application",
			"The employee is told when leave is approved or rejected",
		),
	),
	"Selling & CRM": srs(
		(
			"Capture leads and opportunities | A new lead can be qualified into an opportunity with its source and owner",
			"Make quotations from the price list | A quotation picks the customer's price list and its taxes",
			"Turn a quotation into a sales order | An accepted quotation becomes a sales order with the same lines",
			"Follow the sales pipeline | The pipeline report shows opportunities by stage and owner",
		),
		(
			"Sales User | Works leads, quotations and orders",
			"Sales Manager | Sees the whole pipeline and approves discounts",
		),
		(
			"Lead: name, company, source, owner, status",
			"Quotation: party, items, price list, taxes, validity",
		),
		("A discount above the limit needs the manager's approval",),
		("The owner is told of quotations expiring this week",),
	),
	"Buying": srs(
		(
			"Raise material requests and purchase orders | A material request becomes a purchase order for the chosen supplier",
			"Compare supplier quotations | Two supplier quotations for the same items are compared side by side",
			"Receive goods against the order | A purchase receipt updates stock and the order's received quantity",
		),
		("Purchase User | Raises requests and orders", "Purchase Manager | Approves orders above the limit"),
		("Purchase Order: supplier, items, rates, schedule date, taxes",),
		("An order above the approval limit waits for the manager",),
		("The requester is told when the order is placed",),
	),
	"Stock & Inventory": srs(
		(
			"Keep items and warehouses | Items carry their unit, group and valuation, and each warehouse holds its own stock",
			"Move stock between warehouses | A stock transfer reduces one warehouse and increases the other",
			"Track batches and serial numbers where needed | A batch item can't be issued without its batch",
			"Report stock balance and ageing | The stock balance report agrees with a physical count for a test item",
		),
		(
			"Stock User | Receives, issues and moves stock",
			"Stock Manager | Approves adjustments and reconciliations",
		),
		(
			"Item: code, name, unit, group, valuation method",
			"Stock Entry: type, from warehouse, to warehouse, items",
		),
		("Stock can't go negative unless allowed", "A reconciliation needs the manager's approval"),
		("The stock manager is told when an item falls below its reorder level",),
	),
	"Manufacturing": srs(
		(
			"Keep bills of materials | A BOM lists the raw materials and operations of a finished item",
			"Plan production from sales orders | A production plan proposes work orders and material requests",
			"Run work orders and job cards | A work order consumes raw materials and produces the finished goods",
		),
		(
			"Manufacturing User | Runs work orders and job cards",
			"Manufacturing Manager | Keeps BOMs and plans",
		),
		("Work Order: item, BOM, quantity, planned start, warehouses",),
		("A work order can't finish more than planned without approval",),
		("The planner is told when materials for a work order are short",),
	),
	"Projects & Timesheets": srs(
		(
			"Plan projects with tasks and milestones | A project shows its tasks, owners and dates",
			"Record time on timesheets | A timesheet against a task adds to the project's hours",
			"Bill the time | Billable timesheets become a sales invoice",
		),
		("Projects User | Works tasks and timesheets", "Projects Manager | Plans and bills projects"),
		("Task: subject, project, assignee, expected dates", "Timesheet: employee, task, hours, billable"),
		("A timesheet can't be submitted for a closed project",),
		("The assignee is told of a new task",),
	),
	"Assets": srs(
		(
			"Register assets with their purchase details | An asset holds its category, location, cost and purchase date",
			"Depreciate assets on schedule | The depreciation schedule posts the month's entries",
			"Move and dispose of assets | A movement changes the location; a sale posts the gain or loss",
		),
		("Asset User | Registers and moves assets", "Accounts Manager | Runs depreciation and disposals"),
		("Asset: name, category, location, gross value, available for use date",),
		("An asset can't be depreciated before it is available for use",),
		(),
	),
	"Helpdesk": srs(
		(
			"Log tickets from email and the portal | An email to the support address becomes a ticket",
			"Assign and work tickets to an SLA | A ticket is assigned by the rules and shows its SLA due time",
			"Close tickets with the customer's feedback | A resolved ticket asks the customer for feedback",
		),
		("Agent | Works the tickets", "Support Manager | Sets rules and SLAs, sees every report"),
		("Ticket: subject, customer, priority, team, status",),
		("A ticket can't close without a resolution note",),
		("The customer is told at each reply and when the ticket closes",),
	),
	"Website & Portal": srs(
		(
			"Publish the website pages | The pages agreed are live on the client's domain",
			"Let customers sign in to the portal | A customer sees their orders and invoices on the portal",
		),
		("Website Manager | Edits pages", "Customer | Uses the portal"),
		("Web Page: title, route, content, published",),
		(),
		(),
	),
	"Flutter Mobile App": srs(
		(
			"Sign in securely | A user signs in and stays signed in until they sign out or are disabled",
			"Work offline and sync | Entries made offline sync when the phone is back online",
			"Use the agreed screens | Each screen in this specification works on Android and iOS",
		),
		("App User | Uses the app's screens",),
		("Sign-in: email or phone, password or one-time code",),
		("The app refuses an expired session",),
		("Push notifications for the events agreed",),
	),
	"Web Application": srs(
		(
			"Sign in with roles | Each role sees only its own screens",
			"Use the agreed screens and workflows | Each screen in this specification works in current browsers",
		),
		("User | Uses the screens of their role", "Administrator | Manages users and settings"),
		(),
		(),
		(),
	),
	"Admin Panel": srs(
		(
			"Manage users and roles | An administrator adds a user and gives a role",
			"Change the settings agreed | A setting changed in the panel applies at once",
		),
		("Administrator | Manages users, roles and settings",),
		(),
		(),
		(),
	),
	"Reports & Dashboards": srs(
		(
			"Give each role its dashboard | Each role's dashboard opens with its figures",
			"Email the scheduled reports | The scheduled reports arrive by email at the agreed time",
		),
		("Manager | Reads the dashboards and reports",),
		(),
		(),
		("The scheduled reports go to the people agreed",),
	),
	"Integrations": srs(
		(
			"Exchange data with the systems agreed | A test record passes both ways without re-typing",
			"Log and retry failures | A failed exchange is logged and retried, and an administrator can see it",
		),
		("Administrator | Sets up and watches the integrations",),
		(),
		("An exchange with a wrong key is refused",),
		("The administrator is told when an exchange keeps failing",),
	),
	"Data Migration": srs(
		(
			"Load the masters | Customers, suppliers, items and employees are in the system and checked by the client",
			"Load the opening balances | Opening balances agree with the client's books on the cut-over date",
		),
		("Client's key user | Checks the loaded data",),
		(),
		("Duplicates are removed before loading",),
		(),
	),
	"Training": srs(
		(
			"Train each role | Each role's users have had their session and can do their daily work",
			"Hand over user guides | The guides for each role are with the client",
		),
		(),
		(),
		(),
		(),
	),
}

NFR = "Non-functional Requirement"
NON_FUNCTIONAL = (
	item(
		NFR,
		"Performance",
		"Screens open within 3 seconds for up to the agreed number of users.",
		sort_order=1,
	),
	item(
		NFR,
		"Security",
		"Role-based access; every change is logged with who made it and when; passwords are never stored in plain text.",
		sort_order=2,
	),
	item(
		NFR,
		"Availability",
		"The production system is available during business hours, with planned maintenance announced in advance.",
		sort_order=3,
	),
	item(NFR, "Backup", "Daily backups, kept for 7 days, and a restore tested before go-live.", sort_order=4),
	item(
		NFR,
		"Browsers and devices",
		"Current Chrome, Edge, Safari and Firefox; the screens work on a tablet.",
		sort_order=5,
	),
	item(NFR, "Mobile", "Android 9 and later, iOS 15 and later.", condition="Mobile app", sort_order=6),
)


# ---------- the implementation plan: the client's part, dated by its phase (docs/client-documents.md §7) ----------

RESPONSIBILITY = "Client Responsibility"
CLIENT_RESPONSIBILITIES = (
	item(
		RESPONSIBILITY,
		"Name the project owner and the key user of each department",
		group="Analysis | start",
		sort_order=1,
	),
	item(
		RESPONSIBILITY,
		"Share the current documents, formats and sample data",
		group="Analysis | end",
		sort_order=2,
	),
	item(
		RESPONSIBILITY,
		"Review and approve the business requirements and the specification",
		group="Design | end",
		sort_order=3,
	),
	item(
		RESPONSIBILITY,
		"Fill the data migration templates with clean masters and opening balances",
		group="Development & Configuration | end",
		sort_order=4,
	),
	item(
		RESPONSIBILITY,
		"Make the key users available for user acceptance testing",
		group="Testing & UAT | start",
		sort_order=5,
	),
	item(RESPONSIBILITY, "Sign off the user acceptance tests", group="Testing & UAT | end", sort_order=6),
	item(
		RESPONSIBILITY,
		"Have every user attend their training session",
		group="Training & Go-live | start",
		sort_order=7,
	),
)
