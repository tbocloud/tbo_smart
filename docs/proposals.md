# Proposals to a lead (living spec, v1.0)

**Status:** built 2026-10-19 (Phases 3 and 4 of the sales flow the owner approved on 2026-10-16).
A salesperson builds a proposal for a lead in the TBO Support hub, prices it with the lead's
quotation, sends it, and the client reads, accepts or declines it on a web page on the ERP site.
This file is the living spec: every change to the feature updates it in the same pull request.
The hub's contract is `hub-proposals-api.md`; the hub's own spec is tbocloud/helpdesk
`docs/proposals.md`.

- **Code:** `lead_pipeline/proposals.py` (the logic), `lead_pipeline/proposal_templates.py` (the
  seeded templates), `api/hub_proposals.py` (the hub), `api/proposal_page.py` and
  `www/proposal.py` / `www/proposal.html` (the client's page).
- **Related specs:** `lead-pipeline.md` (stages, quotations §9, the segment rate card §9.8, the
  deal at Closed – Won §6), `quotation-discount-control.md` (approvals).

## 1. Switches (TBO Smart Settings → Proposals)

| Field | Default | Meaning |
| --- | --- | --- |
| `enable_proposals` | off | The whole feature. Needs Quotations on the lead (and so the lead pipeline). Off: the hub hides it, the API refuses, every link shows "not found". |
| `enable_proposal_ai` | off | The AI draft of a new proposal. Also needs an AI provider (`smart-leads.md` §2). |
| `proposal_validity_days` | 30 | A new proposal's valid till. |

## 2. The proposal (TBO Proposal, `PROP-YYYY-#####`, track changes)

| Field | |
| --- | --- |
| `title`, `lead` (set once), `lead_name`, `template` | |
| `status` | Draft, Sent, Viewed, Accepted, Declined, Expired (§5) |
| `sections` | Ordered blocks (**TBO Proposal Section**): `section_type` (Cover, About us, Challenge, Solution, Scope, Timeline, Pricing, Terms, Custom), `heading`, `body` (plain text: a blank line starts a paragraph, lines starting "- " are a list). At most 30. |
| `quotation` | The lead's Quotation that prices it (§4) |
| `theme` | TBO Teal or Graphite: the page's accent, both within the TBO brand |
| `valid_till` | After it the client can't answer, and the nightly job marks it Expired |
| `proposal_family`, `revision_no`, `is_latest` | Revisions (§4.3) |
| `public_token` | 32 random hex characters, new for every revision: the link's only key |
| `sent_on`, `sent_to`, `first_viewed_on`, `last_viewed_on`, `view_count`, `decided_on`, `decided_by_name`, `decided_by_email`, `decline_reason` | Tracking (§5, §6) |

**Permissions** (`has_permission`, `permission_query_conditions`): like TBO Lead Meeting, a
proposal is open to whoever may read its lead under the sales rules; changing it needs write on
the lead; only a System Manager deletes one.

**Editing:** only a Draft changes (title, theme, validity, blocks, pricing). A sent one is
changed through a revision. The controller enforces it for every path (REST, `set_value`):
`status`, `quotation`, the revision fields, the link token and the tracking fields change only
through this module's workflow (a new proposal starts as a fresh draft whatever a caller sends),
a sent proposal's content not at all, and `quotation` must be a quotation of the proposal's own
lead (the client's page shows it).

## 3. Templates (TBO Proposal Template)

`template_name` (the name), `solution_type`, `description`, `enabled`, `sections` (**TBO Proposal
Template Section**: `section_type`, `heading`, `guidance` (what the block should say: the
builder's placeholder and the AI's brief), `body` (default text)) and `packages` (default TBO
Quotation Packages, **TBO Proposal Template Package**).

- A new proposal copies the template's blocks with their default text and is titled
  "<solution type> for <company>". Without a template: Cover, Solution, Pricing, Terms.
- **Seeded** once per site on install / migrate (a marker in the site's defaults), when it has no
  template yet: ERPNext Implementation,
  Helpdesk / Support, Custom Solution, Digital Marketing, each with an About us and Terms text and
  guidance for the rest, and no packages (packages are the site's own). After that they are the
  editors' to change or delete; a migrate doesn't bring them back, even when all were deleted.
- Edited by the roles that edit presets and prices (`presets.can_edit_presets`), from the hub
  (`save_template`) or the desk.

## 4. Building it

### 4.1 Pricing

The pricing block is the lead's own ERPNext **Quotation**, made and changed through the
quotation builder (`lead_quotation.save_quotation`), so:

- the catalogue and packages are priced for the lead's segment and the segment's discount band
  applies (`lead-pipeline.md` §9.8);
- the discount and commission control applies; above the limit the hub's *Request approval*
  (`request_approval`) asks the next level up, as everywhere else;
- the proposal shows the quotation's lines, discount, taxes, total in words and payment
  schedule, read live from the quotation, so the price lives in one place.

A proposal links one quotation. Repricing a draft proposal whose quotation was already sent
(a revision of a sent proposal) makes a **quotation revision** first (`quotations.create_revision`),
so the quotation the client had stays as it was.

### 4.2 AI draft

"Draft with AI" on a new proposal (`create_proposal(..., ai_draft=1)`) sends the lead's facts and
notes (`smart_leads.facts.LeadFacts`), the minutes of its meetings with MOM Submitted, and each
block's type, heading, guidance and default text to `tbo_smart.ai` (feature **Proposal draft** in
the TBO AI Usage Log against the proposal, which is saved first; with the providers'
failover). The pricing block is never written by the
AI. A block whose text has a number that isn't in those facts is left as the template had it,
with a warning (the number guard, `ai.invents_numbers`). When every provider fails, the proposal
is still made with the template's text and a warning saying why. The proposal is a draft: the
salesperson reads and edits every block before sending.

### 4.3 Revisions

*Create revision* copies the proposal (blocks, pricing link, theme) as a new **Draft** with the
next `revision_no` in its family, a fresh validity and a **new link**; it becomes the latest
(`is_latest`) and the others don't. Earlier versions keep their status and their page, which says
"A newer version exists" and can no longer be accepted or declined. Only the latest is sent.

## 5. The client's page (`/proposal/<token>`)

`website_route_rules` → `www/proposal.py`. No login; `no_cache`; `noindex`.

- **Shown** (line descriptions as plain text, left out when they only repeat the item's name):
  the company's name and logo, "Proposal for <client company or name>", the title, valid
  till, version, the blocks (empty ones left out), the pricing block's quotation lines, discount,
  taxes, total, total in words and payment schedule. **Nothing else:** no emails, owners, notes,
  lead or quotation names, internal statuses. Everything is escaped.
- **Not found** (the same 404 every time): a malformed or unknown token, a Draft without preview,
  Proposals switched off.
- **Preview** (`?preview=1`, the hub's *Preview link*): any status including Draft, a "Preview"
  notice, no view recorded, no Accept or Decline.
- **Viewed:** the page posts `record_view` once loaded (so a mail scanner fetching the link counts
  nothing); only a visitor who isn't signed in to the ERP site counts (staff checking the link
  don't), and the count is taken under the row lock. Each view adds to `view_count` and sets `last_viewed_on`; the first moves Sent to
  **Viewed** with `first_viewed_on`, moves its quotation from Sent to Client to Viewed /
  Acknowledged, comments on the lead and tells the lead owner (in-app and push).
- **Accept:** name and email required. The proposal becomes **Accepted** with who and when; its
  quotation becomes **Accepted** (`quotations.set_status`), which moves the lead to Closed – Won
  and makes its deal (`lead-pipeline.md` §6, §9.3); without a quotation the lead moves to Closed –
  Won itself (`transitions.advance_lead`). Refused while its quotation is no longer with the
  client or its valid till has passed (even before the nightly job marks it Expired), or it was
  closed or revised away: the price has gone, so it can't win the lead. Decline is refused the
  same way, and the page then shows "This price is no longer valid" instead of the decision
  panel. The lead owner is told.
- **Decline:** an optional reason. The proposal becomes **Declined**; its quotation **Rejected**
  with the reason; the lead stays open for the salesperson to decide. The owner is told.
- **Once decided it stays decided:** the same answer again changes nothing; the other answer is
  refused. Only the latest revision, still Sent or Viewed and within its validity, can be
  answered. The status is read under a row lock, so two answers at once can't both win.
- These moves run as a system action (the client is Guest), after the token was checked.
- **Download PDF** (`download_pdf`): the same page, rendered by wkhtmltopdf, with its status
  (accepted, declined, superseded, price gone, expired) but not the decision panel.
- **Rate limits** per IP: views 60, accept and decline 10, PDF 30 an hour.
- Responsive (one column on a phone), print-friendly (the decision panel and links hidden), dark
  mode, keyboard reachable with a visible focus ring, 44px targets.

## 6. Sending

`send_proposal` (the hub's *Send*; **nothing goes out without it**):

1. **Refused** with the reasons (`send_blockers`, also returned with every proposal so the hub
   can say why Send is off): not the latest revision; Accepted, Declined or Expired; no blocks;
   validity passed; its quotation cancelled, closed or past its own valid till; or **its pricing above the limit without
   an approval** ("The pricing's discount of 12% is above your limit (5%). Request approval…" /
   "…is waiting for approval").
2. Its quotation goes out with it (`quotations.mark_sent(…, "Email")`): submitted (the discount
   control checks it once more), Sent to Client, the lead to **Proposal Sent**, and the quotation
   follow-up task. Without a quotation the lead moves to Proposal Sent.
3. An email from the ERP site to the lead's email (or `recipients`) with the message and the link
   (and with `attach_pdf` the PDF), through Frappe's `communication.email.make` on the **Lead**,
   so it is a **Communication on the lead's timeline**. It needs an outgoing Email Account on the
   ERP site.
4. The proposal becomes **Sent** with `sent_on` and `sent_to`. Sending again (Sent or Viewed)
   resends the same link.

The hub's lead timeline shows each proposal (`kind: "proposal"`, "<title>: <status>").

## 7. Daily

`expire_proposals` (daily): Sent or Viewed proposals past their valid till become **Expired**.

## 8. What the owner sets up

1. Deploy and `bench migrate` (the doctypes and the four templates).
2. An **outgoing Email Account** on the ERP site (Settings → Email Account, default outgoing), so
   Send can email the client.
3. TBO Smart Settings: Quotations on the lead on (with its masters, `lead-pipeline.md` §8), then
   **Proposals** on; optionally **AI proposal drafts** (with an AI provider).
4. Review the four templates (hub Settings → Proposals, or the desk), add each template's default
   packages, and set the segment rate card (`lead-pipeline.md` §9.8).
5. The company's logo on the Company (a public file) for the page's header.

## 9. Tests

`tbo_smart/tests/test_proposals.py`: a proposal from its template (blocks, title, token, family),
only people who may work the lead (acting user, list query), the switch; editing blocks and the
text layout; pricing through the lead's quotation with the band; the AI draft (pricing left out,
an invented number dropped, usage logged) and off by default; Send blocked until the pricing is
approved (then emailed on the Lead, quotation sent, lead at Proposal Sent), Send without pricing
with the PDF, a sent proposal changed only by revision; revisions (new link, previous kept,
repricing revises the sent quotation, the old one can't be sent); the page: a draft or bad link
shows nothing, preview, no private data, views counted with the first marking it Viewed,
accept once (quotation Accepted, lead Closed – Won, deal made; repeated accept unchanged, decline
refused), decline once (quotation Rejected, lead open), expired and superseded versions can't be
answered, the nightly expiry, the PDF.

## Changelog

- **v1.0 (2026-10-19):** proposals, templates, pricing through the lead's quotation, AI draft,
  revisions, the client's page with views, accept and decline, sending.
