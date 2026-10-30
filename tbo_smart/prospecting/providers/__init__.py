"""Where businesses come from. One module per provider; each says what a run would cost before it
runs and returns plain business dicts (docs/prospecting.md §2).

A business: {name, phone, website, address, city, latitude, longitude, ref, maps_url, category}.
`ref` is the provider's own id for the place (osm:node/1, gplace:..., dfs:...) and is what stops the
same place being imported twice.
"""

import re

import frappe
from frappe import _

OSM, DATAFORSEO, SERPER = "OpenStreetMap", "DataForSEO", "Serper"
MAPS_PIN = "https://www.google.com/maps/search/?api=1&query={lat},{lng}"
MAPS_CID = "https://www.google.com/maps?cid={cid}"


class ProviderError(Exception):
	"""The provider could not answer; the message is for the run's log."""


class Provider:
	key = ""
	label = ""
	paid = False
	# the setting that switches it on, and the secrets it needs
	enabled_field = ""
	secret_fields: tuple[str, ...] = ()
	coverage = ""

	def configured(self) -> bool:
		return all(self.secret(field) for field in self.secret_fields)

	@staticmethod
	def secret(fieldname: str) -> str:
		"""A setting's value; a Password one decrypted."""
		settings = frappe.get_single("TBO Smart Settings")
		if settings.meta.get_field(fieldname).fieldtype == "Password":
			return settings.get_password(fieldname, raise_exception=False) or ""
		return settings.get(fieldname) or ""

	def available(self) -> bool:
		from tbo_smart.prospecting import setting

		return bool(setting(self.enabled_field)) and self.configured()

	def estimate(self, spec: dict, places: list[dict], limit: int, require_phone: bool) -> dict:
		"""{cost (USD), expected (a count or None when unknowable), note}. No paid call."""
		raise NotImplementedError

	def search(self, spec: dict, place: dict, limit: int, require_phone: bool) -> tuple[list[dict], float]:
		"""(businesses, cost in USD) for one district. Raises ProviderError."""
		raise NotImplementedError

	def describe(self) -> dict:
		return {
			"key": self.key,
			"label": self.label,
			"paid": self.paid,
			"available": self.available(),
			"coverage": self.coverage,
		}


def registry() -> dict[str, Provider]:
	from tbo_smart.prospecting.providers.dataforseo import DataForSEO
	from tbo_smart.prospecting.providers.osm import OpenStreetMap
	from tbo_smart.prospecting.providers.serper import Serper

	return {provider.key: provider for provider in (OpenStreetMap(), DataForSEO(), Serper())}


def get_provider(key: str) -> Provider:
	provider = registry().get(key)
	if not provider:
		frappe.throw(_("{0} is not a provider.").format(key))
	return provider


def first_phone(value) -> str:
	"""The first number of "+91 484 123456; 9847012345": providers put several in one string."""
	for part in str(value or "").replace("/", ";").replace(",", ";").split(";"):
		digits = "".join(ch for ch in part if ch.isdigit())
		if len(digits) >= 7:
			return re.sub(r"[^\d+\-() ]", "", part).strip()[:40]
	return ""
