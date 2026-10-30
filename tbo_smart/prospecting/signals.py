"""Why call: what a prospect's own website says (docs/prospecting.md §5).

Ported in spirit from Harvey's website profiling (https://github.com/ethanplusai/harvey, MIT,
Copyright (c) 2026 Ethan Rogers): at most three public requests per site (the home page, one
about page and one contact page), code stripped before any text rule, bot-challenge pages
recognised, no model call. Fetching is the guarded `proposal_profile.fetch` (http(s) only, public
addresses only and again on every redirect, size and time limits).

The page is untrusted. Nothing from it is ever run, followed or given to a model; the signals are
yes/no findings plus a few short values (an email, a phone, a social link, a technology name)
matched by fixed patterns and cut to length. The "Why call" line is built here from the findings,
never copied from the page.
"""

import json
import re
import time
from urllib.parse import urljoin, urlparse

import frappe
from frappe.utils import getdate, now_datetime

from tbo_smart.lead_pipeline import proposal_profile as web
from tbo_smart.prospecting import USER_AGENT

MAX_REQUESTS = 3
PAUSE = 1.5  # between sites, so one run does not hammer anyone
ABOUT_PATHS = re.compile(r"about|who-we-are|our-story|company", re.I)
CONTACT_PATHS = re.compile(r"contact|reach-us|enquir|get-in-touch|book", re.I)
CAREER_LINK = re.compile(
	r"^(careers?|jobs?|we['\u2019]?re hiring|join (us|our team)|vacanc(y|ies)|work with us)$", re.I
)
CAREER_PATH = re.compile(r"/(careers?|jobs?|vacanc(y|ies)|join-us|work-with-us)(/|$)", re.I)

BOOKING = {
	"Calendly": r"calendly\.com",
	"Booking engine": r"(bookingengine|booking-engine|book[-_ ]?now|check[-_ ]?availability)",
	"eZee": r"ezee(absolute|centrix|reservation)",
	"Cloudbeds": r"cloudbeds\.com",
	"Beds24": r"beds24\.com",
	"HotelRunner": r"hotelrunner\.com",
	"Staah": r"staah\.(net|com)",
	"Booking.com": r"booking\.com/hotel",
	"Practo": r"practo\.com",
	"Appointments": r"(book|schedule)[-_ ]?(an?[-_ ])?(appointment|consultation|visit|table)",
}
TECH = {
	"WordPress": r"wp-content|wp-includes",
	"Wix": r"wixstatic\.com|wix\.com",
	"Shopify": r"cdn\.shopify\.com",
	"Squarespace": r"squarespace\.com|squarespace-cdn",
	"Webflow": r"webflow\.(com|io)",
	"Joomla": r"/media/jui/|joomla",
}
SOFTWARE = {  # systems a business names in its own text: a reason to talk about what runs behind
	"Tally": r"\btally(prime| erp)?\b",
	"SAP": r"\bsap (business one|s/4|erp)\b",
	"Odoo": r"\bodoo\b",
	"ERPNext": r"\berpnext\b",
	"Zoho": r"\bzoho\b",
	"Oracle": r"\boracle (netsuite|erp|fusion)\b",
}
SOCIAL = {
	"facebook": r"facebook\.com/(?!sharer|share|tr\b|plugins)[\w.\-]+",
	"instagram": r"instagram\.com/[\w.\-]+",
	"youtube": r"youtube\.com/(c/|channel/|@|user/)[\w.\-]+",
	"linkedin": r"linkedin\.com/company/[\w.\-]+",
	"x": r"(twitter|x)\.com/(?!intent|share)[\w]+",
}
WHATSAPP = re.compile(r"(wa\.me/|api\.whatsapp\.com/send|web\.whatsapp\.com/send|whatsapp://send)", re.I)
EMAIL = re.compile(r"mailto:([A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,120}\.[A-Za-z]{2,24})", re.I)
PHONE = re.compile(r"tel:(\+?[\d\s\-().]{7,20})", re.I)
YEAR = re.compile(r"(?:©|&copy;|copyright)[^0-9<]{0,40}((?:19|20)\d{2})(?:\s*[-–]\s*((?:19|20)\d{2}))?", re.I)
BLOCK_TITLES = ("just a moment", "attention required", "access denied", "are you a robot", "security check")
BOOKING_INDUSTRIES = re.compile(
	r"resort|hotel|restaurant|hospital|clinic|ayurved|travel|tourism|school", re.I
)


def strip_code(html: str) -> str:
	"""Scripts, styles and comments out first, so minified code is never read as prose."""
	html = re.sub(r"(?is)<script\b.*?</script>", " ", html)
	html = re.sub(r"(?is)<style\b.*?</style>", " ", html)
	return re.sub(r"(?s)<!--.*?-->", " ", html)


def looks_blocked(html: str) -> bool:
	"""A bot-challenge or error page, which says nothing about the business."""
	head = html[:6000].lower()
	title = re.search(r"(?is)<title[^>]*>(.*?)</title>", head)
	return bool(title and any(word in title.group(1) for word in BLOCK_TITLES))


def find_pages(final_url: str, html: str) -> dict[str, str]:
	"""{about: url, contact: url, careers: url} of the links on the home page that stay on the site."""
	host = web.safe_host(final_url)
	found = {}
	for match in re.finditer(r'(?is)<a\b[^>]*?href=["\']([^"\'#]{1,300})["\'][^>]*>(.*?)</a>', html):
		try:
			target = urljoin(final_url, match.group(1))
			parsed = urlparse(target)
		except ValueError:
			continue
		if parsed.scheme not in ("http", "https") or web.safe_host(target) != host:
			continue
		text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(2))).strip()
		if "careers" not in found and (CAREER_LINK.match(text) or CAREER_PATH.search(parsed.path)):
			found["careers"] = target
		elif "about" not in found and ABOUT_PATHS.search(parsed.path):
			found["about"] = target
		elif "contact" not in found and CONTACT_PATHS.search(parsed.path):
			found["contact"] = target
	return found


def analyse(pages: list[tuple[str, str]], has_careers_link: bool = False) -> dict:
	"""The signals in the pages read, [(url, html)]. Pure: no network, no database."""
	raw = "\n".join(html for _url, html in pages)
	code = raw.lower()
	body = strip_code(raw)
	text = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", body)).lower()
	booking = next((name for name, pattern in BOOKING.items() if re.search(pattern, code)), None)
	years = [int(y) for match in YEAR.finditer(body) for y in match.groups() if y]
	email = EMAIL.search(body)
	phone = PHONE.search(body)
	hiring = has_careers_link or bool(
		re.search(r"we['\u2019]?re hiring|we are hiring|current openings|job openings", text)
	)
	return {
		"has_website": True,
		"https": pages[0][0].startswith("https://") if pages else False,
		"online_booking": bool(booking),
		"booking_tool": booking if booking and booking != "Appointments" else None,
		"whatsapp": bool(WHATSAPP.search(raw)),
		"email": email.group(1).lower()[:120] if email else None,
		"phone": re.sub(r"\s+", " ", phone.group(1)).strip()[:30] if phone else None,
		"social": [name for name, pattern in SOCIAL.items() if re.search(pattern, code)],
		"hiring": hiring,
		"tech": [name for name, pattern in TECH.items() if re.search(pattern, code)][:3],
		"software": [name for name, pattern in SOFTWARE.items() if re.search(pattern, text)][:3],
		"last_updated_year": max((y for y in years if 2000 <= y <= getdate().year), default=None),
	}


def why_call(signals: dict, industry: str | None = None) -> str:
	"""One short line a caller can open with. Built from the findings only."""
	if not signals.get("has_website"):
		return "No website. A site and an online presence is the way in."
	reasons = []
	if not signals.get("online_booking") and BOOKING_INDUSTRIES.search(industry or ""):
		reasons.append("no online booking on the site")
	if signals.get("hiring"):
		reasons.append("hiring now, so growing")
	year = signals.get("last_updated_year")
	if year and year <= getdate().year - 3:
		reasons.append(f"site last updated {year}")
	if signals.get("software"):
		reasons.append("mentions " + ", ".join(signals["software"]))
	if not signals.get("whatsapp"):
		reasons.append("no WhatsApp contact")
	if signals.get("tech") and "WordPress" in signals["tech"] and not reasons:
		reasons.append("small WordPress site")
	if not reasons:
		return "Has a working website. Ask what they use to run bookings and billing."
	line = "; ".join(reasons[:3])
	return line[0].upper() + line[1:] + "."


def read_site(url: str, industry: str | None = None) -> dict | None:
	"""Read a site with at most MAX_REQUESTS requests and return its signals and Why call line,
	or None when the site cannot be read."""
	if not re.match(r"^https?://", url, re.I):
		url = f"https://{url}"
	pages: list[tuple[str, str]] = []
	try:
		final, html = web.fetch(url, USER_AGENT)
	except web.SiteUnreachable:
		frappe.clear_last_message()
		return None
	if looks_blocked(html):
		return None
	pages.append((final, html))
	links = find_pages(final, html)
	for kind in ("about", "contact"):
		if kind in links and len(pages) < MAX_REQUESTS:
			try:
				pages.append(web.fetch(links[kind], USER_AGENT))
			except web.SiteUnreachable:
				frappe.clear_last_message()
	signals = analyse(pages, has_careers_link="careers" in links)
	return {"signals": signals, "why_call": why_call(signals, industry)}


def store(lead: str, result: dict | None, has_website: bool = True, industry: str | None = None) -> dict:
	"""Merge the findings into the lead's website profile (beside the proposal summary) and
	return the block kept. A lead without a website gets the "no website" finding."""
	row = frappe.db.get_value("Lead", lead, ["tbo_web_profile"], as_dict=True)
	profile = web.stored_profile(row.tbo_web_profile)
	if not has_website:
		result = {"signals": {"has_website": False}, "why_call": why_call({"has_website": False})}
	elif result is None:
		result = {"signals": {"has_website": True, "unreadable": True}, "why_call": ""}
	block = {**result, "read_on": str(now_datetime())}
	profile[web.PROSPECT_KEY] = block
	frappe.db.set_value("Lead", lead, "tbo_web_profile", json.dumps(profile), update_modified=False)
	return block


def read_lead(lead: str) -> dict:
	"""Read one lead's website (if it has one) and keep the findings on it."""
	row = frappe.db.get_value("Lead", lead, ["website", "industry"], as_dict=True)
	url = (row.website or "").strip()
	result = read_site(url, row.industry) if url else None
	return store(lead, result, bool(url), row.industry)


def read_leads(leads: list[str]) -> int:
	"""Background job: read each lead's site in turn, one at a time with a pause, and carry on past
	any single failure. Returns how many were read."""
	done = 0
	for index, lead in enumerate(leads):
		if index:
			time.sleep(PAUSE)
		try:
			read_lead(lead)
			frappe.db.commit()  # nosemgrep
			done += 1
		except Exception:
			frappe.log_error(title=f"Find prospects: reading the website of {lead}")
	return done


def prospect_of(profile_json) -> dict | None:
	"""The block for the hub: {why_call, signals, read_on} or None while nothing was read."""
	block = web.prospect_block(profile_json)
	if not block:
		return None
	return {
		"why_call": block.get("why_call") or None,
		"signals": block.get("signals") or {},
		"read_on": block.get("read_on"),
	}


# what a first message may be written from: findings, never the page's own words (the email and
# phone are on the lead already)
MESSAGE_SIGNALS = (
	"has_website",
	"online_booking",
	"booking_tool",
	"whatsapp",
	"hiring",
	"tech",
	"software",
	"last_updated_year",
	"https",
)
VALUE_CHARS = 40


def first_message_context(lead: str) -> dict | None:
	"""{why_call, signals} for a lead Find prospects created that nobody has written to yet, else
	None. The AI writes the first message from this and the lead's own facts only."""
	row = frappe.db.get_value(
		"Lead", lead, ["tbo_source_ref", "tbo_prospect_run", "tbo_web_profile"], as_dict=True
	)
	if not row or not (row.tbo_source_ref or row.tbo_prospect_run) or written_to(lead):
		return None
	block = prospect_of(row.tbo_web_profile) or {}
	signals = block.get("signals") or {}
	return {
		"why_call": block.get("why_call"),
		"signals": {
			key: value[:VALUE_CHARS] if isinstance(value, str) else value
			for key, value in signals.items()
			if key in MESSAGE_SIGNALS and isinstance(value, str | int | float | bool)
		},
	}


def written_to(lead: str) -> bool:
	"""We already sent the lead a mail, or an outreach message went to them."""
	return bool(
		frappe.db.exists(
			"Communication",
			{
				"reference_doctype": "Lead",
				"reference_name": lead,
				"communication_medium": "Email",
				"sent_or_received": "Sent",
			},
		)
		or frappe.db.exists("TBO Outreach Message", {"lead": lead, "status": "Sent"})
	)


def unread_leads(run: str) -> list[str]:
	"""The leads a run created that have no website reading yet."""
	rows = frappe.get_all(
		"Lead", filters={"tbo_prospect_run": run}, fields=["name", "tbo_web_profile"], order_by="creation asc"
	)
	return [row.name for row in rows if not web.prospect_block(row.tbo_web_profile)]
