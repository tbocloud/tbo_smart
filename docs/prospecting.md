# Find prospects (v1.0)

**Status:** built 2026-10-30. TBO sells mainly by phone. A manager or department head picks an
industry and districts ("Resorts in Idukki", "Ayurvedic hospitals in Malappuram"); the businesses
found are dropped into the department's **unassigned pool** and the call batches
(`call-batches.md`) hand them out. Each one is read for a reason to call. The hub's Leads → Find
prospects screen drives it through `hub-leads-api.md` §2.15.

Ideas and parts of the providers and the website reading come from the open-source sales agent
Harvey (MIT, Copyright (c) 2026 Ethan Rogers); see `NOTICE`. Harvey is not a dependency.

## 1. Settings

TBO Smart Settings → **Prospecting**:

| Field | Meaning |
|---|---|
| Enable Find prospects | Off: every method refuses and `get_options` says `can_find_prospects: false`. |
| OpenStreetMap / DataForSEO / Serper | Which providers may be chosen. A paid one can be switched on only with its login or key. |
| DataForSEO login, password; Serper API key | Stored as Password fields (encrypted). |
| Monthly spend cap (USD) | A paid run is refused (at estimate and again at start) when this month's spend (finished and failed runs' actual cost, running runs' estimate) plus its estimate would pass it. 0: no paid runs. Default 20. |
| Default department | Used when a run names none. Must be active. |
| Max results per run | 1 to 1,000, default 100. |
| Industry to provider categories | Table that overrides the built-in mapping (§3). |

## 2. Providers

One module each under `tbo_smart/prospecting/providers/`; each prices a run before it runs and
returns plain business dicts `{name, phone, website, address, city, latitude, longitude, ref,
maps_url, category}`. `ref` is the provider's id for the place and keeps the same place from being
imported twice.

| Provider | Cost | What it is | Coverage |
|---|---|---|---|
| **OpenStreetMap** (`osm.py`) | free | Nominatim finds the district (one request, remembered 30 days); Overpass lists the places by tag inside the district's area. | **Thin.** OSM maps places, not phone numbers. Idukki, 30 Oct 2026: `tourism=resort` has 3 named places; resorts, hotels, guest houses and chalets together 345 named, of which 39 (11%) have a phone and 32 a website; hospitals and clinics 360 named, 27 with a phone. Use it to try the flow and to cross-check, not to fill a pool. |
| **DataForSEO Business Listings** (`dataforseo.py`) | USD 0.012 a request plus USD 0.00036 a business (about USD 0.05 per 100), as Harvey documents it in October 2026; check DataForSEO's price list. Charged on what it returns; the answer's `cost` is stored as the run's actual cost. | Google Business Profile data: phone, website, rating, place id. A district is a point and radius around its centre, cut afterwards to the district's box. | The real source for a phone pool. A radius spills into neighbouring districts; the box cut removes most of it, but their businesses are paid for. |
| **Serper Google Maps** (`serper.py`) | about USD 0.003 a page of up to 20 places (estimate; Serper bills in credits, check its price list). | The places Google Maps shows for "<phrase> in <district>". | Good for small runs; capped at 10 pages (200 places) a district. |

OpenStreetMap policy: Nominatim at most one request a second with an identifying User-Agent, no
bulk geocoding (a run looks up each district once and caches it); Overpass one request at a time
with a pause, the public mirrors tried in turn when one is busy, and a run that finds every mirror
busy **fails** instead of reporting an empty district. A heavy use of OpenStreetMap should move to
a paid provider rather than hammer the volunteer servers.

## 3. Industry mapping

`prospecting/mapping.py` holds a built-in table for the starter Industry Types (OSM tags, a
search phrase, a DataForSEO category). A row in TBO Smart Settings → "Industry to provider
categories" replaces it field by field. OSM tags are one per line, `key=value`, optionally
`;name~word|word` to also require a name match (Ayurvedic hospitals are told from other hospitals
by name). Tags are checked character by character before they reach an Overpass query. An industry
with no tags cannot be searched on OpenStreetMap (the estimate says so); it can on the paid
providers, by phrase. The DataForSEO category ids in the built-in table are best guesses: check
them against DataForSEO's free categories list and correct them in the settings table, or clear
the field to match on the phrase.

## 4. Runs and the import

**TBO Prospect Run** (`industry`, `state`, `districts`, `provider`, `max_results`, `department`,
`require_phone`, `status`, `estimated_cost`, `expected_count`, `estimate_note`, `actual_cost`,
`count_found/duplicates/skipped/created`, `started_at`, `finished_at`, `log`). Flow:
`estimate_prospect_run` (creates it, Estimated) → `start_prospect_run` (cap check, Running, queued
on the `long` queue) → the job searches each district in turn, imports, saves the counts after
each district, and ends Done or Failed (the log says why; the cost so far is kept) → on Done the
website reading is queued.

Who: System Manager and Sales Manager for any department; a department head for their own.

The import (`prospecting/importer.py`) for each business:
1. skipped when it has no name, no phone while `require_phone` is on (the call pool only takes
   leads with a phone), or lies outside the district's box;
2. **duplicate** (counted, left alone) when a lead already has its `tbo_source_ref`, its phone
   (last ten digits) or its company name (`sales/duplicates.find_duplicates`), or its website's
   domain. Shared hosts (Facebook, Google, Wix sites, ...) never count as a domain. A company-name
   match counts here, unlike in `create_lead`, because calling the same business twice is the
   cost; a chain's branches with the same name are the price of that;
3. otherwise a Lead: first name and company = the business, `mobile_no`, `website`, `city`,
   `industry`, `state` and `tbo_district` (when the lead categories are installed), `tbo_department`,
   source **Prospecting – <provider>**, `tbo_maps_url`, `tbo_latitude/longitude`, `tbo_location_note`
   (the address), `tbo_source_ref`, `tbo_prospect_run`. **No owner**: `lead.flags.unassigned_pool`
   makes `sales.assignment.claim_owner` leave it unowned even in a round-robin department, so the
   call batches' unassigned-pool step hands it out.

Two hidden Lead fields are added on migrate: `tbo_source_ref` and `tbo_prospect_run`.

## 5. Website signals ("Why call")

`prospecting/signals.py`. Background job, one site at a time with a 1.5 s pause, a failure on one
lead never stops the rest. At most **three** public requests a site: the home page, then one about
and one contact page found among its links. The fetch is the proposals' guarded
`proposal_profile.fetch`: http(s) only, no credentials in the URL, every address the host resolves
to must be public (checked again on every redirect, at most 3), 200 KB and a few seconds a page,
HTML only. Known limit: the address is checked and then fetched by name, so a hostile DNS server
could rebind between the two (Harvey pins the checked IP; this app does not yet).

The page is untrusted: scripts and styles are stripped before any text rule, a bot-challenge page
is recognised and read as nothing, and no model is called. Only fixed patterns run over it, and the
result is yes/no findings plus a few short values: `has_website`, `https`, `online_booking` (and
`booking_tool`), `whatsapp`, `email`, `phone`, `social` (facebook, instagram, youtube, linkedin,
x), `hiring` (a careers link or "we're hiring"), `tech` (WordPress, Wix, Shopify, ...),
`software` (Tally, SAP, Odoo, ERPNext, Zoho, Oracle named in the text), `last_updated_year` (the
newest copyright year). The **Why call** line is built from these, never copied from the page: for
example "No online booking on the site; hiring now, so growing; site last updated 2019." A lead
with no website gets "No website. A site and an online presence is the way in."

The findings are kept in `Lead.tbo_web_profile` under the key `prospect`
(`{signals, why_call, read_on}`), beside the proposal summary (`about`, `industry`, ...) that
`proposal_profile.read_website` writes; each leaves the other's part alone. `tbo_web_profile_on`
still means the AI read.

## 6. Cost

| Run | OpenStreetMap | DataForSEO | Serper |
|---|---|---|---|
| 100 businesses, 1 district | 0 | about USD 0.05 | about USD 0.015 |
| 1,000, 5 districts | 0 | about USD 0.42 | about USD 0.15 |

The website reading costs nothing (three requests, no model). A lead's later AI read is the
existing smart-leads setting, not part of this.

## 7. Legal notes

- **Public business data only**: business name, address, phone and website as the business
  published them on OpenStreetMap or Google Business Profile, and what its own website says about
  itself. No personal data is collected about people, no contact is scraped from a social network.
- **No LinkedIn, no Justdial** (or any directory whose terms forbid scraping): none is a provider
  and none is fetched. A listing's website that is a Facebook, Google or Justdial page is ignored.
- Respect OpenStreetMap's usage policies (§2) and credit the data: OpenStreetMap data is
  (c) OpenStreetMap contributors, ODbL. A lead made from it carries the source
  "Prospecting – OpenStreetMap".
- **DPDP Act 2023 (India)**: a business's published phone is still personal data when it is a
  person's own number. Prospect leads are called for B2B reasons relevant to the business; a person
  who says not to call again must not be called again: record the call outcome
  (Wrong number flags the lead) and move the lead to a closed stage so it leaves the call pool; a
  deletion request is met by deleting the lead. Do not import into marketing lists or send bulk messages from this data. Calls follow
  TRAI's DND rules for the number class used.
- DataForSEO and Serper terms apply to their data; keep the keys in the settings only.

## 8. What the owner sets up

1. OpenStreetMap needs nothing: switch Find prospects on, choose a default department, try it.
2. For a real phone pool, open a DataForSEO account (app.dataforseo.com), top up (their minimum
   deposit applies), put the login and API password in the settings and switch DataForSEO on.
   Set the monthly cap.
3. Serper is optional: an API key from serper.dev.
4. Check the DataForSEO category ids for the industries that matter (§3).

## 9. Tests

`tbo_smart/tests/test_prospecting.py` (HTTP is mocked; nothing paid or on the network is called):
the mapping and its override, tags that could alter a query refused; the OpenStreetMap query, the
element reader, mirrors that throttle; DataForSEO's price, reading and refusals; Serper's paging
and cost; the import (unassigned even in a round-robin department, source and place fields, the
four kinds of duplicate, no phone, shared hosts); the run (estimate, who may, the cap, start once,
the job's counts, failure keeps the cost, listing and opening, the options flag); the signals (what
is found, booking tools, the Why call line, at most three requests, unreachable and blocked sites,
private addresses never fetched, the findings beside the proposal summary, one failing lead not
stopping the rest, the hub's `lead.prospect`).
