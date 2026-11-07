"""Handing out a lead (Assign leads) or pulling it into a batch dated its ToDo today, Frappe's
default for an assignment, and the batches read a dated open ToDo as a follow-up due: those leads
jumped every later batch, ahead of the product filter (docs/call-batches.md §3). Their open
assignment ToDos lose the date; real follow-ups keep theirs."""

import frappe

from tbo_smart.sales.call_batches.fill import PULLED_NOTE
from tbo_smart.sales.call_batches.handout import HANDED_NOTE


def execute():
	ToDo = frappe.qb.DocType("ToDo")
	notes = ToDo.description.like(f"%{PULLED_NOTE}%") | ToDo.description.like(f"%{HANDED_NOTE}%")
	(
		frappe.qb.update(ToDo)
		.set(ToDo.date, None)
		.where((ToDo.reference_type == "Lead") & (ToDo.status == "Open") & ToDo.date.isnotnull() & notes)
	).run()
