"""AI providers for tbo_smart (docs/smart-leads.md §2).

Modelled on the TBO Support hub's `helpdesk.ai_engine`: OpenAI Chat Completions first, an
optional Anthropic-compatible fallback (Kimi), failover on billing, key, model, rate-limit,
server and timeout failures, a 30-minute pause for a provider whose bill, key or model is
wrong, one alert per incident, a usage log with the estimated cost of every attempt, and a
number guard so text the AI writes never carries a count or date that wasn't in its facts.

Both providers are spoken through `requests`, so there is no SDK to depend on. Keys live only
in TBO Smart Settings Password fields; they never reach the browser, the log or an error.
"""

import json
import re
from dataclasses import dataclass, field
from time import monotonic

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt, format_datetime, now_datetime
from frappe.utils.password import get_decrypted_password

SETTINGS = "TBO Smart Settings"
OPENAI = "OpenAI"
ANTHROPIC = "Anthropic-compatible"
SLOTS = (OPENAI, ANTHROPIC)
OPENAI_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# a slow answer counts as a failure, so the fallback can still answer
REQUEST_TIMEOUT = 60
# every attempt of one call, both providers included, ends by then
CALL_SECONDS = 150
MIN_ATTEMPT_SECONDS = 5
# thinking models may spend the first budget thinking: room, then more once
TOKEN_BUDGETS = (2048, 4096)
TEST_TOKEN_BUDGET = 64

CIRCUIT_SECONDS = 30 * 60
ALERT_SECONDS = 6 * 60 * 60
STATUS_SECONDS = 24 * 60 * 60
CIRCUIT_KEY = "tbo_smart:ai:circuit:{}"
DOWN_KEY = "tbo_smart:ai:down:{}"
ALERT_KEY = "tbo_smart:ai:alerted:{}"

FAILOVER_KINDS = {"billing", "auth", "config", "rate_limit", "server", "timeout"}
CIRCUIT_KINDS = {"billing", "auth", "config"}
TRANSIENT_KINDS = {"rate_limit"}
REASONS = {
	"billing": "insufficient balance or quota",
	"auth": "the API key was refused",
	"config": "the model or base URL wasn't found",
	"rate_limit": "rate limited",
	"server": "the provider had an error",
	"timeout": "no answer in time",
	"invalid": "the request was rejected",
	"paused": "paused after a failure, tried again within 30 minutes",
}
BILLING_WORDS = ("balance", "quota", "billing", "credit", "payment", "insufficient", "suspended")
MODEL_MISSING_WORDS = ("model_not_found", "model not found", "does not exist", "unknown model")
# keys and their masked echoes (OpenAI's 401 repeats part of the key)
SECRET_PATTERN = re.compile(r"\b(sk|pk|key)-[\w*\-]{4,}", re.IGNORECASE)
MAX_ERROR_CHARS = 300


@dataclass
class AIProvider:
	"""One provider section of TBO Smart Settings, ready to call."""

	slot: str
	base_url: str
	model: str
	api_key: str = field(repr=False)
	input_price: float = 0.0
	output_price: float = 0.0

	@property
	def label(self) -> str:
		return f"{self.slot} ({self.model})"


class AINotConfigured(frappe.ValidationError):
	"""No provider has a key and a model."""


class AIProviderError(frappe.ValidationError):
	"""A provider's failure. `kind` (a REASONS key) decides whether to fail over; `detail` is
	the provider's own message with any key masked."""

	http_status_code = 503

	def __init__(self, provider: AIProvider, kind: str, detail: str = "", status: int | None = None):
		self.provider = provider
		self.kind = kind
		self.status = status
		self.detail = redact(detail, provider.api_key)
		super().__init__(f"{provider.label}: {self.summary}")

	@property
	def fails_over(self) -> bool:
		return self.kind in FAILOVER_KINDS

	@property
	def summary(self) -> str:
		status = f"HTTP {self.status}: " if self.status else ""
		reason = _(REASONS.get(self.kind, self.kind))
		return f"{reason} ({status}{self.detail})" if self.detail else reason


def redact(text: str, key: str | None = None) -> str:
	text = str(text or "")
	if key:
		text = text.replace(key, "…")
	return SECRET_PATTERN.sub(lambda m: f"{m.group(1)}-…", text)[:MAX_ERROR_CHARS]


# ---------- configuration ----------


def password(fieldname: str) -> str | None:
	return get_decrypted_password(SETTINGS, SETTINGS, fieldname, raise_exception=False)


def configured_providers(settings=None) -> dict[str, AIProvider | None]:
	"""Each provider section, or None where it lacks a key or a model."""
	settings = settings or frappe.get_cached_doc(SETTINGS)
	providers: dict[str, AIProvider | None] = {}
	for slot, prefix, default_url in (
		(OPENAI, "openai", OPENAI_BASE_URL),
		(ANTHROPIC, "anthropic", ANTHROPIC_BASE_URL),
	):
		key = password(f"{prefix}_api_key")
		model = (settings.get(f"{prefix}_model") or "").strip()
		base_url = (settings.get(f"{prefix}_base_url") or "").strip().rstrip("/") or default_url
		providers[slot] = (
			AIProvider(
				slot,
				base_url,
				model,
				key,
				flt(settings.get(f"{prefix}_input_price")),
				flt(settings.get(f"{prefix}_output_price")),
			)
			if key and model
			else None
		)
	return providers


def provider_order(settings=None) -> list[str]:
	"""The primary section, then the other one when failover is on."""
	settings = settings or frappe.get_cached_doc(SETTINGS)
	primary = settings.get("ai_primary_provider") or OPENAI
	if not cint(settings.get("ai_failover")):
		return [primary]
	return [primary, *(slot for slot in SLOTS if slot != primary)]


def get_providers() -> list[AIProvider]:
	settings = frappe.get_cached_doc(SETTINGS)
	providers = configured_providers(settings)
	return [providers[slot] for slot in provider_order(settings) if providers[slot]]


def ai_ready() -> bool:
	"""At least one provider has a key and a model."""
	return bool(get_providers())


# ---------- one request ----------


def classify(status: int, detail: str) -> str:
	"""The kind of failure behind an HTTP status and the provider's message."""
	text = (detail or "").lower()
	if status == 402 or (status in (400, 403, 429) and any(word in text for word in BILLING_WORDS)):
		return "billing"
	if status in (401, 403):
		return "auth"
	if status == 404 or (status == 400 and any(word in text for word in MODEL_MISSING_WORDS)):
		return "config"
	if status == 429:
		return "rate_limit"
	if status in (408, 504):
		return "timeout"
	if status >= 500:
		return "server"
	return "invalid"


def error_detail(body) -> str:
	"""The message (and type or code) in an OpenAI- or Anthropic-shaped error body."""
	error = body.get("error") if isinstance(body, dict) else None
	if isinstance(error, dict):
		message = str(error.get("message") or "")
		code = error.get("code") or error.get("type")
		return f"{message} ({code})" if message and code else message or str(code or "")
	if isinstance(error, str):
		return error
	return str(body.get("message") or "") if isinstance(body, dict) else ""


def post(
	provider: AIProvider, url: str, body: dict, headers: dict, timeout: float, files: dict | None = None
) -> dict:
	"""POST JSON (or, with `files`, a multipart form of `body` and the files); the JSON answer."""
	try:
		if files:
			response = requests.post(url, data=body, files=files, headers=headers, timeout=timeout)
		else:
			response = requests.post(url, json=body, headers=headers, timeout=timeout)
	except requests.RequestException as e:
		# only the kind: the text can hold the request
		raise AIProviderError(provider, "timeout", type(e).__name__) from None
	if not response.ok:
		try:
			detail = error_detail(response.json())
		except ValueError:
			detail = ""
		raise AIProviderError(
			provider, classify(response.status_code, detail), detail or response.reason, response.status_code
		)
	try:
		return response.json()
	except ValueError:
		raise AIProviderError(provider, "server", "the answer wasn't JSON") from None


def openai_chat(provider: AIProvider, system: str, user: str, max_tokens: int, timeout: float):
	"""One /chat/completions call. Returns (text, input_tokens, output_tokens, ran_out).

	No temperature: newer OpenAI models refuse anything but the default. JSON mode needs the
	word "JSON" in the prompt, which every caller here has."""
	body = {
		"model": provider.model,
		"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
		"max_completion_tokens": max_tokens,
	}
	if "json" in system.lower():
		body["response_format"] = {"type": "json_object"}
	data = post(
		provider,
		f"{provider.base_url}/chat/completions",
		body,
		{"Authorization": f"Bearer {provider.api_key}", "Content-Type": "application/json"},
		timeout,
	)
	try:
		choice = data["choices"][0]
		text = choice["message"].get("content") or ""
	except (KeyError, IndexError, TypeError, AttributeError):
		raise AIProviderError(provider, "server", "the answer had no choices or message") from None
	usage = data.get("usage") or {}
	return (
		text,
		cint(usage.get("prompt_tokens")),
		cint(usage.get("completion_tokens")),
		choice.get("finish_reason") == "length",
	)


def anthropic_messages(provider: AIProvider, system: str, user: str, max_tokens: int, timeout: float):
	"""One Messages API call (Anthropic or a compatible endpoint such as Kimi)."""
	data = post(
		provider,
		f"{provider.base_url}/v1/messages",
		{
			"model": provider.model,
			"max_tokens": max_tokens,
			"system": system,
			"messages": [{"role": "user", "content": user}],
		},
		{
			"x-api-key": provider.api_key,
			"anthropic-version": ANTHROPIC_VERSION,
			"Content-Type": "application/json",
		},
		timeout,
	)
	blocks = data.get("content")
	if not isinstance(blocks, list):
		raise AIProviderError(provider, "server", "the answer had no content")
	# thinking-capable models may put a thinking block first
	text = "".join(block.get("text") or "" for block in blocks if block.get("type", "text") == "text")
	usage = data.get("usage") or {}
	return (
		text,
		cint(usage.get("input_tokens")),
		cint(usage.get("output_tokens")),
		data.get("stop_reason") == "max_tokens",
	)


def complete(
	provider: AIProvider,
	system: str,
	user: str,
	feature: str,
	reference: tuple | None = None,
	budgets: tuple[int, ...] = TOKEN_BUDGETS,
	deadline: float | None = None,
) -> str:
	"""One answer from one provider, each attempt logged; AIProviderError if it fails."""
	call = openai_chat if provider.slot == OPENAI else anthropic_messages
	deadline = deadline or monotonic() + CALL_SECONDS
	text = ""
	for max_tokens in budgets:
		timeout = min(REQUEST_TIMEOUT, deadline - monotonic())
		if timeout < MIN_ATTEMPT_SECONDS:
			raise AIProviderError(provider, "timeout", "no time left for this call")
		started = monotonic()
		try:
			text, input_tokens, output_tokens, ran_out = call(provider, system, user, max_tokens, timeout)
		except AIProviderError as e:
			log_usage(provider, feature, reference, 0, 0, started, error=e.kind)
			raise
		log_usage(provider, feature, reference, input_tokens, output_tokens, started)
		if text.strip() or not ran_out:
			break
	return text


# ---------- speech to text ----------

TRANSCRIBE_TIMEOUT = 180
TRANSCRIBE_PROMPT = "A sales phone call in Kerala, India. It may be in Malayalam, English or both."


def transcribe(
	audio: bytes,
	filename: str,
	model: str,
	minutes: float,
	price_per_minute: float,
	feature: str,
	reference: tuple | None = None,
) -> str:
	"""The text of a recording from the OpenAI section's /audio/transcriptions (the model detects
	the language), logged at `price_per_minute` for `minutes`. Raises AINotConfigured without an
	OpenAI key and AIProviderError when it fails or is paused."""
	provider = configured_providers().get(OPENAI)
	if not provider:
		frappe.throw(_("Transcription needs the OpenAI section in TBO Smart Settings."), AINotConfigured)
	provider = AIProvider(OPENAI, provider.base_url, model, provider.api_key)
	if circuit_open(provider):
		raise AIProviderError(provider, "paused")
	started = monotonic()
	cost = round(flt(minutes) * flt(price_per_minute), 6)
	try:
		data = post(
			provider,
			f"{provider.base_url}/audio/transcriptions",
			{"model": model, "response_format": "json", "prompt": TRANSCRIBE_PROMPT},
			{"Authorization": f"Bearer {provider.api_key}"},
			TRANSCRIBE_TIMEOUT,
			files={"file": (filename, audio, "audio/mpeg")},
		)
	except AIProviderError as e:
		log_usage(provider, feature, reference, 0, 0, started, error=e.kind)
		# a wrong speech model must not pause the OpenAI section's chat features
		if e.kind != "config":
			record_failure(e, None)
		raise
	usage = data.get("usage") or {}
	log_usage(
		provider,
		feature,
		reference,
		cint(usage.get("input_tokens")),
		cint(usage.get("output_tokens")),
		started,
		cost=cost,
	)
	return str(data.get("text") or "").strip()


# ---------- failover state (Redis) ----------


def circuit_open(provider: AIProvider) -> bool:
	return bool(frappe.cache.get_value(CIRCUIT_KEY.format(provider.slot), expires=True))


def provider_down(slot: str) -> dict | None:
	return frappe.cache.get_value(DOWN_KEY.format(slot), expires=True)


def record_failure(error: AIProviderError, fallback: AIProvider | None) -> None:
	"""Pause a provider whose bill, key or model is wrong, show it as failing and alert the
	System Managers once; a rejected request is only logged."""
	provider = error.provider
	if not error.fails_over:
		frappe.log_error(title=f"AI request rejected by {provider.label}", message=str(error))
		return
	if error.kind in CIRCUIT_KINDS:
		frappe.cache.set_value(CIRCUIT_KEY.format(provider.slot), error.kind, expires_in_sec=CIRCUIT_SECONDS)
	if error.kind in TRANSIENT_KINDS:
		return
	since = (provider_down(provider.slot) or {}).get("since") or str(now_datetime())
	frappe.cache.set_value(
		DOWN_KEY.format(provider.slot),
		{"kind": error.kind, "reason": error.summary, "since": since, "model": provider.model},
		expires_in_sec=STATUS_SECONDS,
	)
	alert_once(error, fallback, since)


def alert_once(error: AIProviderError, fallback: AIProvider | None, since: str) -> None:
	"""One alert per provider in ALERT_SECONDS, from a job so a rollback doesn't lose it."""
	provider = error.provider
	first = frappe.cache.set(  # site-prefixed via make_key; set_value can't do NX - nosemgrep
		frappe.cache.make_key(ALERT_KEY.format(provider.slot)), 1, nx=True, ex=ALERT_SECONDS
	)
	if not first:
		return
	when = format_datetime(since, "d MMM, HH:mm")
	reason = _(REASONS[error.kind])
	if fallback:
		subject = _("AI provider {0} failing since {1}: {2}. Using {3}.").format(
			provider.label, when, reason, fallback.label
		)
	else:
		subject = _(
			"AI provider {0} failing since {1}: {2}. AI lead features are paused until it is fixed in TBO Smart Settings."
		).format(provider.label, when, reason)
	frappe.enqueue("tbo_smart.ai.send_alert", subject=subject, queue="short", now=frappe.flags.in_test)


def send_alert(subject: str) -> None:
	"""In-app (and mobile push) to the enabled System Managers."""
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	from tbo_smart.smart_leads.utils import users_with_role

	users = users_with_role("System Manager")
	if users:
		enqueue_create_notification(
			users,
			{
				"type": "Alert",
				"document_type": SETTINGS,
				"document_name": SETTINGS,
				"subject": subject,
				"from_user": "Administrator",
			},
		)


def record_success(provider: AIProvider) -> None:
	frappe.cache.delete_value([CIRCUIT_KEY.format(provider.slot), DOWN_KEY.format(provider.slot)])


def reset_provider_state() -> None:
	"""Forget pauses and failures, e.g. after the settings changed."""
	frappe.cache.delete_value([key.format(slot) for slot in SLOTS for key in (CIRCUIT_KEY, DOWN_KEY)])


def provider_status() -> dict:
	"""The order, each section's state and which one answers now. Never a key."""
	settings = frappe.get_cached_doc(SETTINGS)
	providers = configured_providers(settings)
	order = provider_order(settings)
	sections = {
		slot: {
			"configured": bool(providers[slot]),
			"model": providers[slot].model if providers[slot] else None,
			"paused": bool(providers[slot] and circuit_open(providers[slot])),
			"down": provider_down(slot),
		}
		for slot in SLOTS
	}
	in_use = next(
		(slot for slot in order if sections[slot]["configured"] and not sections[slot]["paused"]), None
	)
	return {
		"primary": order[0],
		"failover": len(order) > 1,
		"order": order,
		"in_use": in_use,
		"providers": sections,
	}


# ---------- the call every feature uses ----------


def call_ai(
	system: str,
	user: str,
	feature: str,
	reference: tuple | None = None,
	budgets: tuple[int, ...] = TOKEN_BUDGETS,
) -> dict:
	"""A JSON answer from the first provider that gives one, in failover order.

	Returns {"response": dict, "provider": slot, "model": str}. Raises AINotConfigured when no
	provider is set up and AIProviderError when every provider failed or is paused."""
	providers = get_providers()
	if not providers:
		frappe.throw(_("AI isn't set up. Add an API key in TBO Smart Settings."), AINotConfigured)
	ready = [provider for provider in providers if not circuit_open(provider)]
	if not ready:
		raise AIProviderError(providers[0], "paused")
	error = None
	deadline = monotonic() + CALL_SECONDS
	for index, provider in enumerate(ready):
		try:
			text = complete(provider, system, user, feature, reference, budgets, deadline)
		except AIProviderError as e:
			error = e
			record_failure(e, ready[index + 1] if index + 1 < len(ready) else None)
			if not e.fails_over:
				raise
			continue
		record_success(provider)
		return {"response": parse_json_answer(text), "provider": provider.slot, "model": provider.model}
	raise error


def test_provider(slot: str) -> dict:
	"""One tiny call with a section's saved settings: {ok, message, details}."""
	provider = configured_providers().get(slot)
	if not provider:
		return {"ok": False, "message": _("Add the API key and model, save, then test again.")}
	started = monotonic()
	try:
		complete(
			provider,
			"Reply with the single word OK.",
			"Connection test",
			"Test connection",
			budgets=(TEST_TOKEN_BUDGET,),
		)
	except AIProviderError as e:
		return {"ok": False, "message": _("{0} answered: {1}").format(provider.slot, e.summary)}
	record_success(provider)
	return {
		"ok": True,
		"message": _("{0} answered. The lead features can use it.").format(provider.slot),
		"details": [
			{"label": _("Model"), "value": provider.model},
			{"label": _("Endpoint"), "value": provider.base_url.split("/")[2]},
			{"label": _("Time"), "value": f"{round((monotonic() - started) * 1000)} ms"},
		],
	}


# ---------- usage log ----------


def estimate_cost(provider: AIProvider, input_tokens: int, output_tokens: int) -> float:
	"""USD from the prices per 1M tokens in TBO Smart Settings (0 until the owner enters them)."""
	return round(
		input_tokens / 1_000_000 * provider.input_price + output_tokens / 1_000_000 * provider.output_price,
		6,
	)


def log_usage(
	provider: AIProvider,
	feature: str,
	reference: tuple | None,
	input_tokens: int,
	output_tokens: int,
	started: float,
	error: str | None = None,
	cost: float | None = None,
) -> None:
	"""One TBO AI Usage Log row per attempt, written by a job queued at once (not after the
	commit): a request that fails after a billed attempt rolls back, and the row must stay.
	`cost` overrides the token estimate (speech is priced by the minute). Never breaks the
	caller."""
	reference_doctype, reference_name = reference or (None, None)
	try:
		frappe.enqueue(
			"tbo_smart.ai.write_usage_log",
			queue="short",
			now=frappe.flags.in_test,
			values={
				"provider": provider.slot,
				"model": provider.model,
				"feature": feature,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
				"user": frappe.session.user,
				"input_tokens": input_tokens,
				"output_tokens": output_tokens,
				"estimated_cost_usd": estimate_cost(provider, input_tokens, output_tokens)
				if cost is None
				else (0 if error else cost),
				"duration_ms": round((monotonic() - started) * 1000),
				"ok": 0 if error else 1,
				"error_kind": error,
			},
		)
	except Exception:
		frappe.log_error(title="TBO AI: usage not logged")


def write_usage_log(values: dict) -> None:
	"""Background job of log_usage."""
	frappe.get_doc({"doctype": "TBO AI Usage Log", **values}).insert(
		ignore_permissions=True, ignore_links=True
	)


# ---------- answers and the number guard ----------


def parse_json_answer(text: str) -> dict:
	"""The JSON object in a model's answer, which may come in a code fence or with prose."""
	candidates = [text]
	if "```" in text:
		fenced = text.split("```")[1]
		candidates.append(fenced[4:] if fenced.startswith("json") else fenced)
	start, end = text.find("{"), text.rfind("}")
	if 0 <= start < end:
		candidates.append(text[start : end + 1])
	for candidate in candidates:
		try:
			parsed = json.loads(candidate.strip())
		except (json.JSONDecodeError, ValueError):
			continue
		if isinstance(parsed, dict):
			return parsed
	return {}


NUMBER = re.compile(r"\d+(?:\.\d+)?")


def normalise_number(value: str) -> str:
	"""07 and 7, 3.50 and 3.5 are the same number."""
	if "." in value:
		value = value.rstrip("0").rstrip(".")
	return value.lstrip("0") or "0"


def fact_numbers(*facts) -> set[str]:
	"""Every number in the facts given to the model (dates give their year, month and day)."""
	text = " ".join(json.dumps(fact, default=str, ensure_ascii=False) for fact in facts)
	return {normalise_number(number) for number in NUMBER.findall(text)}


def invents_numbers(text: str, known: set[str]) -> bool:
	"""The model's text has a number that isn't in its facts, so it can't be trusted."""
	return bool({normalise_number(number) for number in NUMBER.findall(text or "")} - known)


def guarded(text, known: set[str]) -> str | None:
	"""The text when every number in it is a fact, else None."""
	text = " ".join(str(text or "").split())
	return text if text and not invents_numbers(text, known) else None
