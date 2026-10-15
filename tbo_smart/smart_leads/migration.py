"""The migration note each lead brought from the old ERPNext site (docs/smart-leads.md §3.2).

On 2026-10-14 every lead got an FCRM Note titled "Migrated from tboindia-old <id>" holding the
original status, source, owner, created date, remarks, priority and project type. The note's
exact layout was not available offline, so the parser accepts "Label: value" lines, "Label -
value" lines and two-cell table rows, in any order, with or without HTML, and ignores labels it
doesn't know. **[verify on site]** against a real note before switching scoring on.
"""

import re

import frappe
from frappe.utils import strip_html

NOTE_TITLE_PREFIX = "Migrated from tboindia-old"
# label words → key; the first match wins, so "lead owner" is checked before "lead"
LABELS = (
	("project type", "project_type"),
	("type of project", "project_type"),
	("original status", "status"),
	("lead status", "status"),
	("status", "status"),
	("lead source", "source"),
	("source", "source"),
	("lead owner", "owner"),
	("owner", "owner"),
	("created on", "created"),
	("created", "created"),
	("creation", "created"),
	("remarks", "remarks"),
	("remark", "remarks"),
	("notes", "remarks"),
	("priority", "priority"),
)
LINE = re.compile(r"^\s*[-*•]?\s*([A-Za-z][A-Za-z /_]{1,40}?)\s*(?::|=|\s-\s|\t)\s*(.*)$")
# the old ERPNext Lead statuses, best first; anything else counts as neutral
ORIGINAL_STATUS_POINTS = {
	"interested": 10,
	"opportunity": 10,
	"quotation": 10,
	"replied": 6,
	"open": 3,
	"lead": 1,
	"lost quotation": -5,
	"do not contact": -20,
}
PRIORITY_POINTS = {"high": 5, "urgent": 5, "medium": 2}


def note_text(html: str | None) -> str:
	"""Plain lines from the note's HTML: one per paragraph, line break or table row."""
	text = html or ""
	text = re.sub(r"(?i)</t[dh]>\s*<t[dh][^>]*>", ": ", text)
	text = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</div>|</li>", "\n", text)
	return strip_html(text)


def parse_migration_note(html: str | None) -> dict:
	"""{status, source, owner, created, remarks, priority, project_type} found in a note; a
	remarks value may run over several lines until the next known label."""
	found: dict[str, str] = {}
	current = None
	for raw in note_text(html).splitlines():
		line = raw.strip()
		if not line:
			continue
		match = LINE.match(line)
		key = label_key(match.group(1)) if match else None
		if key:
			current = key
			if key not in found:
				found[key] = match.group(2).strip()
			continue
		if current == "remarks":
			found["remarks"] = f"{found.get('remarks', '')}\n{line}".strip()
	return {key: value for key, value in found.items() if value}


def label_key(label: str) -> str | None:
	label = " ".join(label.lower().replace("_", " ").split())
	for words, key in LABELS:
		if label == words:
			return key
	return None


def get_migration_notes(leads: list[str]) -> dict[str, dict]:
	"""{lead: parsed note} for the leads that have a migration note (one query)."""
	if not leads:
		return {}
	Note = frappe.qb.DocType("FCRM Note")
	rows = (
		frappe.qb.from_(Note)
		.select(Note.reference_docname, Note.content)
		.where(
			(Note.reference_doctype == "CRM Lead")
			& (Note.reference_docname.isin(leads))
			& (Note.title.like(f"{NOTE_TITLE_PREFIX}%"))
		)
		.orderby(Note.creation)
	).run(as_dict=True)
	return {row.reference_docname: parse_migration_note(row.content) for row in rows}


def original_status_points(status: str | None) -> int:
	return ORIGINAL_STATUS_POINTS.get((status or "").strip().lower(), 0)


def priority_points(priority: str | None) -> int:
	return PRIORITY_POINTS.get((priority or "").strip().lower(), 0)
