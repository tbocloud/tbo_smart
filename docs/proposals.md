# Proposals to a lead (living spec, v1.1)

**Status:** built 2026-10-19 (Phases 3 and 4 of the sales flow the owner approved on 2026-10-16).
A salesperson builds a proposal for a lead in the TBO Support hub, prices it with the lead's
quotation, sends it, and the client reads, accepts or declines it on a web page on the ERP site.
This file is the living spec: every change to the feature updates it in the same pull request.
The hub's contract is `hub-proposals-api.md`; the hub's own spec is tbocloud/helpdesk
`docs/proposals.md`.

- **Code:** `lead_pipeline/proposals.py` (the logic), `lead_pipeline/proposal_media.py` (images
  and the letter head), `lead_pipeline/proposal_templates.py` (the
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
| `proposal_letter_head` | blank | The Letter Head every proposal uses unless it picks another (§2.2). Blank: the Letter Head marked default. Also set from the hub (`set_default_letter_head`, preset editors). |

## 2. The proposal (TBO Proposal, `PROP-YYYY-#####`, track changes)

| Field | |
| --- | --- |
| `title`, `lead` (set once), `lead_name`, `template` | |
| `status` | Draft, Sent, Viewed, Accepted, Declined, Expired (§5) |
| `sections` | Ordered blocks (**TBO Proposal Section**): `section_type` (Cover, About us, Challenge, Solution, Scope, Timeline, Pricing, Terms, Image, Custom), `heading`, `body` (plain text: a blank line starts a paragraph, lines starting "- " are a list), and on a Cover or Image block `image`, `image_alt`, `image_caption`, `image_size` (§2.1). At most 30. |
| `quotation` | The lead's Quotation that prices it (§4) |
| `theme` | TBO Teal or Graphite: the page's accent, both within the TBO brand |
| `letter_head_override` | The proposal's own Letter Head; blank: the default (§2.2). Not called `letter_head`: Frappe fills a field of that name from the user's default letter head, which would pin every new proposal to it. |
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

### 2.1 Images

- An **Image** block shows one picture with its caption, at full or half width (`image_size`
  Full / Half; half is full width on a phone). A **Cover** block can carry a cover image the
  same way. Other blocks carry none (an image sent with them is dropped).
- `image` is a **File** (Link): a **private** file attached to this proposal. Anything else
  (another proposal's image, any other file) is refused (403). `image_alt` is required with an
  image (417), up to 140 characters; `image_caption` up to 280.
- **Upload** (`hub_proposals.upload_proposal_image`, POST, write on the lead, Draft only): at
  most **5 MB**; **PNG, JPEG or WebP**, told by the bytes (Pillow), never by the name, so the
  file is named with the true extension. **SVG is refused** (it can carry script), as is
  anything else. The builder then puts the returned `file` in a block and saves.
- An Image block without an image shows nothing, like an empty text block.
- **Served to guests** by `proposal_page.image(token, file)` (§5): only a file one of this
  proposal's blocks shows (and attached to it), or a private image of its letter head; the same
  "not found" for anything else, including another proposal's image and any other private file.
  Private files are never made public. The **PDF embeds** the images as data URIs, so
  wkhtmltopdf fetches nothing.
- **Revisions** copy the images: each image gets its own File on the revision (the same file on
  disk), so a revision never depends on its predecessor's attachments.
- **Sending** deletes the proposal's uploads that no block shows (its content is fixed from
  then on).

### 2.2 The letter head

The page, the PDF and the hub's preview carry ERPNext's own **Letter Head** (header and footer,
image or HTML; no store of our own). Every proposal has one:

1. the proposal's own pick (`letter_head_override`, the builder's picker), else
2. TBO Smart Settings → **Proposal letter head**, else
3. the Letter Head marked **default** (and not disabled).
4. Only a site with **no** Letter Head at all falls back to the company's logo header; the hub
   warns editors then.

A disabled pick falls back to the next one; an unknown pick is refused. **HTML** headers and
footers are run through `frappe.utils.html_utils.sanitize_html` before a guest sees them
(script, event handlers and the like removed); Jinja in them is not rendered. **Image** ones
show the image: a public `/files/` image by its URL, a private one through the token-gated
`proposal_page.image`, and in the PDF as a data URI. A private image inside an HTML header is
not rewritten: use the Image source for a private image. In the **PDF** the header sits at the
top and the footer at the bottom of **every page** (Frappe's `get_pdf` lifts `#header-html` /
`#footer-html` into wkhtmltopdf's header and footer); the page shows them above and below the
proposal.

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
  the letter head (§2.2; without one the company's name and logo), "Proposal for <client
  company or name>", the title, valid till, version, the blocks (empty ones left out) with
  their images (§2.1), the pricing block's quotation lines, discount, taxes, total, total in
  words and payment schedule. **Nothing else:** no emails, owners, notes,
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
- **Download PDF** (`download_pdf`): the same page, rendered by wkhtmltopdf on A4, with its
  status (accepted, declined, superseded, price gone, expired) but not the decision panel, the
  letter head on every page and the images embedded.
- **Images** (`image(token, file)`, GET): §2.1; inline, with the image's own type (PNG, JPEG,
  WebP, or a letter head's GIF; never SVG). Drafts' images too, as the preview shows them.
- **Rate limits** per IP: views 60, accept and decline 10, PDF 30, images 600 an hour.
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
5. A **Letter Head** (Settings → Letter Head on the ERP site): the header (an image of the TBO
   letter head, or HTML) and the footer (address, website); mark it **default**, or pick it in
   TBO Smart Settings → Proposal letter head (or hub Settings → Rates and proposals). Without
   any Letter Head the page falls back to the company's logo (a public file on the Company).

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
answered, the nightly expiry, the PDF. Images (`TestImages`): upload size, type (SVG, GIF and
junk refused, the extension from the bytes) and permission (write on the lead, Draft only);
blocks keep only their own images with alt text; the guest endpoint serves only this proposal's
images (not another proposal's, another revision's, a loose private file or an unknown one);
images on the page, embedded in the PDF, copied to a revision, and unused uploads deleted on
send. The letter head (`TestLetterHead`): the fallback order, the proposal's override and back
to the default, a disabled pick, the logo with none at all, only editors set the default,
sanitised HTML on the page and in the PDF's header and footer, and a private letter head image
served through the token only while the proposal uses it.

## Changelog

- **v1.1 (2026-10-19):** images (Image block, cover image, private uploads served by token,
  embedded in the PDF, copied to revisions) and the letter head (ERPNext's Letter Head, the
  setting, the per-proposal override, sanitised, on every PDF page).
- **v1.0 (2026-10-19):** proposals, templates, pricing through the lead's quotation, AI draft,
  revisions, the client's page with views, accept and decline, sending.
