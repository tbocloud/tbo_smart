"""The lead stages of TBO's pipeline (docs/lead-pipeline.md §2).

A lead's stage is its `tbo_stage` (a Select of these names, in this order); ERPNext's own
`status` is left to ERPNext. Each stage has a type (Open, Ongoing, On Hold, Won, Lost, as
Frappe CRM's lead statuses had) and a colour for the screens. The code refers to them by name,
here only.
"""

NEW_LEAD = "New Lead"
CONTACTED = "Contacted"
NOT_RESPONDING = "Not Responding"
MEETING_SCHEDULED = "Meeting Scheduled"
FIRST_MEETING = "First Meeting Completed"
SECOND_MEETING = "Second Meeting Completed"
THIRD_MEETING = "Third Meeting Completed"
PROPOSAL_SENT = "Proposal Sent"
NEGOTIATION = "Negotiation"
POSITIVE = "Positive / Interested"
FOLLOW_UP_REQUIRED = "Follow-up Required"
ON_HOLD = "On Hold"
CLOSED_WON = "Closed – Won"
CLOSED_LOST = "Closed – Lost"
NOT_INTERESTED = "Not Interested"
WRONG_NUMBER = "Wrong Number"

# (name, type, colour), in pipeline order
STATUSES = (
	(NEW_LEAD, "Open", "gray"),
	(CONTACTED, "Ongoing", "blue"),
	# called but not answered, busy or switched off: call again
	(NOT_RESPONDING, "Ongoing", "orange"),
	(MEETING_SCHEDULED, "Ongoing", "cyan"),
	(FIRST_MEETING, "Ongoing", "teal"),
	(SECOND_MEETING, "Ongoing", "teal"),
	(THIRD_MEETING, "Ongoing", "teal"),
	(PROPOSAL_SENT, "Ongoing", "violet"),
	(NEGOTIATION, "Ongoing", "orange"),
	(POSITIVE, "Ongoing", "green"),
	(FOLLOW_UP_REQUIRED, "Ongoing", "amber"),
	(ON_HOLD, "On Hold", "gray"),
	(CLOSED_WON, "Won", "green"),
	(CLOSED_LOST, "Lost", "red"),
	# picked up and declined / an invalid number: lost without a reason to type
	(NOT_INTERESTED, "Lost", "red"),
	(WRONG_NUMBER, "Lost", "gray"),
)
STATUS_NAMES = tuple(name for name, _type, _color in STATUSES)
# a salesperson still works on these: scored, listed on Call today, followed up and triaged
OPEN_STATUSES = tuple(name for name, kind, _color in STATUSES if kind not in ("Won", "Lost"))
WON_STATUSES = (CLOSED_WON,)
LOST_STATUSES = tuple(name for name, kind, _color in STATUSES if kind == "Lost")
# the lost reason these stages fill in themselves (Opportunity Lost Reason names)
AUTO_LOST_REASONS = {NOT_INTERESTED: "Not interested", WRONG_NUMBER: "Wrong number"}

# the stage the Nth meeting's MOM completes
MEETING_COMPLETED = {1: FIRST_MEETING, 2: SECOND_MEETING, 3: THIRD_MEETING}

# How far along the pipeline a status is, for the automatic moves: they only ever go forward.
# Side statuses (Contacted, Not Responding, Positive / Interested, Follow-up Required, On Hold)
# sit together before the first meeting, so scheduling one moves any of them on.
PROGRESS = {
	NEW_LEAD: 0,
	CONTACTED: 1,
	NOT_RESPONDING: 1,
	POSITIVE: 1,
	FOLLOW_UP_REQUIRED: 1,
	ON_HOLD: 1,
	MEETING_SCHEDULED: 2,
	FIRST_MEETING: 3,
	SECOND_MEETING: 4,
	THIRD_MEETING: 5,
	PROPOSAL_SENT: 6,
	NEGOTIATION: 7,
	CLOSED_WON: 8,
}


STATUS_TYPES = {name: kind for name, kind, _color in STATUSES}


def status_type(status: str | None) -> str | None:
	return STATUS_TYPES.get(status) if status else None


def needs_lost_reason(status: str | None) -> bool:
	"""A Lost-type stage that makes the person say why (Closed – Lost); Not Interested and Wrong
	Number say it themselves."""
	return status_type(status) == "Lost" and status not in AUTO_LOST_REASONS


def is_closed(status: str | None) -> bool:
	return status_type(status) in ("Won", "Lost")


def stage_options() -> list[dict]:
	"""The stages for the screens: [{name, label, type, color}], in pipeline order."""
	return [{"name": name, "label": name, "type": kind, "color": color} for name, kind, color in STATUSES]
