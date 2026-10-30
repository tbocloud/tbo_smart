"""Query helpers that work on Frappe v15 and v16."""

import frappe


def aggregate(function: str, field: str, alias: str):
	"""An aggregate for a `fields` list, e.g. aggregate("count", "name", "count").

	Frappe v16 accepts only {"COUNT": "name", "as": "count"} and rejects SQL functions written as
	strings; v15 accepts only "count(name) as count".
	"""
	if int(frappe.__version__.split(".")[0]) >= 16:
		return {function.upper(): field, "as": alias}
	return f"{function}({field}) as {alias}"
