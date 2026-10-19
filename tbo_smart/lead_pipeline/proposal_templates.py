"""The proposal templates seeded on a site that has none (docs/proposals.md §3). Kept short on
purpose: each block says what it should cover, and the preset editors change them in the hub."""

ABOUT_US = (
	"TBO (Team Back Office) builds and runs business software for growing companies in Kerala and "
	"beyond: ERPNext implementations, support desks and custom solutions, with a team that stays "
	"with you after go-live."
)
TERMS = (
	"This proposal is valid until the date above. Prices are as in the pricing block, with taxes "
	"shown separately.\n\n- Work starts once the proposal is accepted.\n- Anything outside the scope "
	"above is quoted separately.\n- Payments follow the schedule in the pricing block."
)


def block(section_type: str, heading: str, guidance: str, body: str | None = None) -> dict:
	return {"section_type": section_type, "heading": heading, "guidance": guidance, "body": body}


def common_start(challenge: str) -> list[dict]:
	return [
		block(
			"Cover", "Introduction", "One short paragraph: who it is for and what it will change for them."
		),
		block("About us", "About TBO", "Who TBO is, in two or three sentences.", ABOUT_US),
		block("Challenge", "Where you are today", challenge),
	]


def common_end() -> list[dict]:
	return [
		block("Pricing", "Investment", "A line on what the price covers; the quotation's lines follow."),
		block("Terms", "Terms", "Validity, what is outside the scope, how payments run.", TERMS),
	]


TEMPLATES = (
	{
		"template_name": "ERPNext Implementation",
		"solution_type": "ERPNext Implementation",
		"description": "ERPNext set up for the client's company: modules, data, training and go-live.",
		"sections": [
			*common_start("The processes and tools they use now, and what slows them down, in their words."),
			block(
				"Solution", "What we'll set up", "The ERPNext modules and how each fixes a pain point above."
			),
			block(
				"Scope", "Scope", "Modules, data migration, integrations, training, and what is not included."
			),
			block("Timeline", "How we'll get there", "The phases from kick-off to go-live and support."),
			*common_end(),
		],
	},
	{
		"template_name": "Helpdesk / Support",
		"solution_type": "Helpdesk / Support",
		"description": "A helpdesk or an ongoing support contract.",
		"sections": [
			*common_start("How their customers or staff ask for help today, and what gets lost."),
			block(
				"Solution", "How support will work", "Channels, response times, who handles what, reporting."
			),
			block("Scope", "What's covered", "Systems, hours and kinds of requests covered, and what isn't."),
			*common_end(),
		],
	},
	{
		"template_name": "Custom Solution",
		"solution_type": "Custom Solution",
		"description": "Software built for a need no product covers.",
		"sections": [
			*common_start("The problem to solve and why off-the-shelf tools haven't solved it."),
			block(
				"Solution", "What we'll build", "The solution in plain words: who uses it and what it does."
			),
			block("Scope", "Scope", "Features in this phase, integrations, and what comes later."),
			block("Timeline", "How we'll build it", "Discovery, build, testing and launch."),
			*common_end(),
		],
	},
	{
		"template_name": "Digital Marketing",
		"solution_type": "Digital Marketing",
		"description": "Campaigns, content and social media for the client's brand.",
		"sections": [
			*common_start("Their audience, channels and goals, and what isn't working yet."),
			block("Solution", "The plan", "Channels, content themes and how results will be measured."),
			block("Scope", "Deliverables", "What is produced each month, and approvals."),
			block("Timeline", "The first months", "Onboarding, the first campaigns, the first review."),
			*common_end(),
		],
	},
)
