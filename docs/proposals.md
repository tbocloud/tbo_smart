# Proposals to a lead (living spec, v2.5)

**Status:** built 2026-10-19 (Phases 3 and 4 of the sales flow the owner approved on 2026-10-16;
the Universal IT Proposal the owner set on 2026-10-19). Presales starts a proposal in the TBO
Support hub from the client's profile and requirements; the proposal is **assembled** from a
master structure, a pre-written module library and section libraries, with **one small AI call**
for the narrative; it is priced with the lead's quotation, shared or sent, and the client reads,
accepts or declines it on a web page on the ERP site, or as an A4 PDF.
This file is the living spec: every change to the feature updates it in the same pull request.
The documents after an accepted proposal (the quotation, the project agreement) are TBO Proposals
of their own type, on the same engine: `client-documents.md`. The hub's contract is
`hub-proposals-api.md`; the hub's own spec is tbocloud/helpdesk
`docs/proposals.md`.

- **Code:** `lead_pipeline/proposals.py` (the proposal, its page model, sending),
  `lead_pipeline/proposal_builder.py` (building sections from the libraries and modules, pricing
  from the modules, the AI calls), `lead_pipeline/proposal_profile.py` (the client profile and the
  guarded website read), `lead_pipeline/proposal_library.py` (the editors' library upkeep),
  `lead_pipeline/proposal_media.py` (images and the letter head), `lead_pipeline/proposal_templates.py`
  (what a site is seeded with), `api/hub_proposals.py` (the hub), `api/proposal_page.py` and
  `www/proposal.py` / `www/proposal.html` (the client's page and the PDF).
- **Related specs:** `lead-pipeline.md` (stages, quotations §9, the segment rate card §9.8, the
  deal at Closed – Won §6), `quotation-discount-control.md` (approvals), `smart-leads.md` §2 (AI
  providers, the usage log).

## 1. Switches (TBO Smart Settings → Proposals)

| Field | Default | Meaning |
| --- | --- | --- |
| `enable_proposals` | off | The whole feature. Needs Quotations on the lead (and so the lead pipeline). Off: the hub hides it, the API refuses, every link shows "not found". |
| `enable_proposal_ai` | off | The AI calls (§4.2). Also needs an AI provider. Off: presales types the profile and fills the form by hand; everything else still works. |
| `proposal_validity_days` | 30 | A new proposal's valid till. |
| `proposal_letter_head` | blank | The Letter Head every proposal uses unless it picks another (§2.2). Blank: the Letter Head marked default. Also set from the hub (`set_default_letter_head`, preset editors). |
| `proposal_service_item` | blank | The quotation line's Item for a module without an Item of its own (named by the module) and for customisation days. |
| `proposal_day_rate` | 0 | Customisation and change requests, a person-day (the Out of Scope section quotes it). |
| `proposal_ai_daily_limit` | 50 | At most this many AI proposal writes a day (whole drafts and single sections), so the cost stays capped. |

## 2. The proposal (TBO Proposal, `PROP-YYYY-#####`, track changes)

| Field | |
| --- | --- |
| `title`, `lead` (set once), `lead_name`, `template`, `solution_type`, `prepared_by` | `prepared_by` is the person who made it (the cover and our signatory) |
| `document_type`, `source_document` | Proposal for a proposal; the documents after it (`client-documents.md`) |
| `status` | Draft, Sent, Viewed, Accepted, Declined, Expired (§5) |
| `client_profile` | JSON: name, website, about, industry, size, locations, current software, key processes, pain points (§3.1). Required. |
| `requirements` | The requirements box, kept so a regenerate is one click (§3.1) |
| `scoping` | JSON: the scoping answers (§3.1) |
| `chat` | JSON: the assistant's transcript `[{role: presales / assistant, text, at}]`, the newest 200, kept on the proposal. The AI never reads it (§4.2). |
| `modules` | **TBO Proposal Module Line**: the proposal's own copy of each module (`module`, `module_name`, `fit` Standard / Configured / Customised, `description`, `features`, `effort_days`, `rate`, `rate_overridden`, `item`) |
| `sections` | Ordered **TBO Proposal Section** rows: `section_type` (the 23 master sections, Image, Custom), `enabled` (off: kept, not shown), `edited` (changed by hand: regenerating asks first), `heading`, `body` (§2.3), and `image`, `image_alt`, `image_caption`, `image_size` (§2.1). At most 30. |
| `quotation` | The lead's Quotation that prices it (§4.1) |
| `pricing_mode` | Itemised (every quotation line) or Consolidated (one project price with what it includes) |
| `theme` | **Ivory**, the one theme (§5.1); read only |
| `letter_head_override` | The proposal's own Letter Head; blank: the default (§2.2). Not called `letter_head`: Frappe fills a field of that name from the user's default letter head, which would pin every new proposal to it. |
| `valid_till` | After it the client can't answer, and the nightly job marks it Expired |
| `proposal_family`, `revision_no`, `is_latest` | Revisions (§4.4) |
| `public_token` | 32 random hex characters, new for every revision: the link's only key |
| `sent_on`, `sent_to`, `first_viewed_on`, `last_viewed_on`, `view_count`, `decided_on`, `decided_by_name`, `decided_by_email`, `decline_reason` | Tracking (§5, §6) |

**Permissions** (`has_permission`, `permission_query_conditions`): a proposal is open to whoever
may read its lead under the sales rules; changing it needs write on the lead; only a System
Manager deletes one.

**Editing:** only a Draft changes. A sent one is changed through a revision. The controller
enforces it for every path (REST, `set_value`): `status`, `quotation`, the revision fields, the
link token and the tracking fields change only through this module's workflow (a new proposal
starts as a fresh draft whatever a caller sends), a sent proposal's content not at all, and
`quotation` must be a quotation of the proposal's own lead. The Payment Terms milestones must add
up to 100% (417 otherwise).

### 2.1 The Universal IT Proposal: 23 sections in three parts

Every new proposal starts with **all 23 sections on, in this order**; presales turns off what
doesn't fit (off sections are kept, not shown). The part titles show in the contents and the PDF.

| # | Section | Part | Where its text comes from |
| --- | --- | --- | --- |
| 1 | Cover | – | Data: company and logo, title, client, prepared by, date, reference, version, valid till (a line under the title and a cover image optional) |
| 2 | Executive Summary | A — Business Proposal | **AI** (about one page) |
| 3 | Company Overview | A | Library **Company Overview** (set once in Settings; not regenerated per proposal) |
| 4 | Client Background & Business Understanding | A | **AI**, from the confirmed client profile, the requirements and the notes / minutes |
| 5 | Business Challenges | A | **AI**, only from the profile, requirements and notes; never invented (an empty answer is fine) |
| 6 | Proposed Solution | A | Data: the **Solution Component** table driven by the scoping form; then **AI** business benefits |
| 7 | Scope of Work (7.1 Business Analysis … 7.6 Training & Handover) | B — Technical & Implementation | Library **Scope of Work** + each module's scope lines, by subsection; then the customisations with what each means (§4.2) |
| 8 | Functional Modules & Features | B | The proposal's modules (drawn as the module map: a card a module) |
| 9 | Proposed Workflow | B | Each module's workflow chain + the library **Workflow** for the solution type (drawn as step diagrams) |
| 10 | Technical Architecture | B | Library **Architecture**; the scoping's Simple / Detailed choice adds the "Detailed architecture" rows |
| 11 | Project Deliverables | B | Each module's deliverables + library **Deliverable** (e.g. source code only when the scoping says so) |
| 12 | Implementation Plan | B | Library **Phase** (activities per phase) |
| 13 | Project Timeline | B | The picked **Timeline** template (45 / 60 / 90 / 120 days, phase split in days), drawn as a bar |
| 14 | Project Team & Responsibilities | B | Library **Role** (the "Large project" roles only above 40 person-days) |
| 15 | Investment | C — Commercial Proposal | Library **Investment Note** + the quotation (itemised or consolidated) |
| 16 | Payment Terms | C | The picked **Payment Terms** template ("milestone \| %", adding up to 100); amounts computed from the total |
| 17 | Warranty & Post-Implementation Support | C | Library **Warranty** |
| 18 | AMC | C | Library **AMC Plan** (Basic / Premium / Enterprise, features and yearly price): a comparison table, its price separate from the project; the recommended plan named |
| 19 | Assumptions & Dependencies | C | Library **Assumption** + the modules' |
| 20 | Out of Scope | C | Library **Out of Scope** + the modules', and the change-request day rate |
| 21 | Risks & Mitigation | C | Library **Risk** by rules (conditions; the first three on small projects) |
| 22 | Terms & Conditions | C | Library **Terms** |
| 23 | Acceptance & Approval | C | Data: signature boxes for the client and for us; accepting online fills name, email and date |

Image and Custom blocks can be added anywhere and stay in the part they are placed in.

### 2.2 Images

- Any section but the Investment and the Acceptance may carry an image (`image_size` Full /
  Half; half is full width on a phone); an **Image** block is a picture with its caption.
  Images are **optional**: a proposal looks complete without any. **No AI image generation.**
- `image` is a **File** (Link) attached to this proposal. Anything else (another proposal's image,
  any other file) is refused (403). `image_alt` is required with an image (417), up to 140
  characters; `image_caption` up to 280.
- **Upload** (`upload_proposal_image`, POST, write on the lead, Draft only): at most **5 MB**;
  **PNG, JPEG or WebP**, told by the bytes (Pillow), never by the name. **SVG is refused** (it can
  carry script), as is anything else. The file is private.
- **The image library** (**TBO Proposal Image**: title, image, alt text, the sections and solution
  types it suits, the modules it shows; public files, as they are for clients' eyes): a new
  proposal **picks** a fitting one by its tags for the Cover, Company Overview, Proposed Solution,
  Modules & Features (an image of one of its modules first) and Project Team, at no AI cost.
  `use_library_image` puts one on a section as its own File (the same file on disk).
- **Served to guests** by `proposal_page.image(token, file)` (§5): only a file one of this
  proposal's sections shows (and attached to it), or a private image of its letter head; the same
  "not found" for anything else. The **PDF embeds** the images as data URIs.
- **Revisions** copy the images as their own Files. **Sending** deletes uploads no section shows.

### 2.3 The letter head

The page, the PDF, the print and the hub's preview carry ERPNext's own **Letter Head**. Every
proposal has one:

1. the proposal's own pick (`letter_head_override`), else
2. TBO Smart Settings → **Proposal letter head**, else
3. the Letter Head marked **default** (and not disabled).
4. Only a site with **no** Letter Head at all falls back to the company's logo header; the hub
   warns editors then.

A disabled pick falls back to the next one; an unknown pick is refused. **HTML** headers and
footers are run through `frappe.utils.html_utils.sanitize_html` before a guest sees them; Jinja in
them is not rendered. **Image** ones show the image: a public `/files/` image by its URL, a
private one through the token-gated `proposal_page.image`, and in the PDF as a data URI. In the
**PDF** the header sits at the top of **every page**, scaled to the content's width (one taller
than 30 mm is narrowed instead, so it is never clipped). Its footer: a **footer picture** (an
Image footer, or an HTML footer around a picture — Frappe writes an attached footer image as HTML
at a small fixed width) spans the content's width on every page, the cover included, without the
HTML's text beside it (one taller than 30 mm, narrower than about 6:1, is narrowed instead); any
other footer is the footer line of every page but the cover (§5.1). The web page shows the same picture.

### 2.4 Section text

Plain text, laid out by `text_parts` (the page, the PDF and the hub's preview read it the same
way): a blank line starts a part; a line starting `## ` is a subheading (numbered 7.1, 7.2…);
lines starting `- ` are a list; lines with ` | ` are a table (the first line its header); a single
line with ` → ` is a chain of steps (a diagram). Everything is escaped.

## 3. Templates, the scoping form and the libraries

### 3.1 Starting a proposal (`create_proposal`)

In the hub a proposal starts as a **guided chat** (tbocloud/helpdesk `docs/proposals.md`): scripted
questions that cost no tokens, quick-reply chips wherever the choices are fixed (modules,
extras, users, hosting, timeline, payment terms, AMC), and a live summary of the same form beside
it that presales can edit at any time (or skip the chat and fill the form). The chat collects
exactly what the form holds; at the end *Generate proposal* calls `create_proposal` with it and
the transcript (`chat`). With the AI off the chat still works: chips and typed values go into the
fields as they are. The data, in order:

1. **Client profile (required):** typed, or **read from the website** (`read_client_website`,
   §3.5). `industry` and `pain_points` are required (417 "Fill the client profile first").
2. **Requirements box:** free text from presales (or the lead's notes and minutes in one click,
   `get_requirements_from_lead`, no AI) and the modules. With the AI on, **one structured call**
   (`extract_requirements`, feature **Proposal requirements**) reads it into the scoping form:
   the template, modules (only catalogue names), customisation bullets, mobile app, web app,
   integrations, data migration, users, hosting, AMC, target weeks, source code. Nothing is saved.
3. **Scoping form**, confirmed by presales: `modules`, `customisation` and `customisations`
   (`[{item, days, detail}]`: one-line bullets; `detail` is what the AI writes, §4.2),
   `mobile_app`, `web_app`, `integrations`, `data_migration`, `users` and `companies` (each a
   number, 0 when not said, or **"Unlimited"**; shown as "Unlimited users, up to 3 companies" in
   the Proposed Solution's table, the Investment notes and the Assumptions), `hosting` (Frappe Cloud,
   On-premise, Client's cloud, TBO hosting), `amc`, `timeline_weeks`, `source_code`,
   `architecture` (Simple / Detailed). The template decides which questions are asked.
4. **Templates:** `timeline_template` (suggested: the shortest one of at least 1.6 calendar days
   a person-day of module and customisation effort, or the target weeks if longer; presales
   picks), `payment_terms` (the first by default), `amc_plan` (the plan to recommend; blank: none).
5. **Generate:** the proposal is made with all 23 sections; data mobile app / integrations / data
   migration bring their module (Flutter Mobile App, Integrations, Data Migration); the modules are
   copied in and priced for the lead's segment; every data section is built (§2.1); library
   images are picked; the modules are priced into the quotation (when an Item or the service item
   allows; else a warning "Not priced yet"); with `ai_draft`, the narrative call (§4.2).

Without a template: the master sections, no default modules, every question.

### 3.2 Templates: presets on the master (TBO Proposal Template)

`template_name`, `solution_type`, `description`, `enabled`, `page_guide` (the length guide shown
in the builder: Small project 5–8, ERP 10–15, ERP + mobile 15–25, Enterprise 20–35 pages),
`questions` (the scoping keys it asks, one a line), `default_modules` (catalogue names ticked on a
new proposal), `sections` (**TBO Proposal Template Section**: `section_type`, `enabled`,
`heading`, `guidance` (the AI's brief and the builder's placeholder), `body`) and `packages`.

**Seeded** (§3.6): ERPNext Implementation, ERPNext + Flutter Mobile App, Flutter Mobile App, Web
Application, Custom Software Development, Frappe HR / HRMS, Helpdesk, CRM, Website / Digital, AMC
/ Support. Edited by the roles that edit presets and prices, in the hub or the desk.

### 3.3 The module catalogue (TBO Proposal Module), written once, reused everywhere

`module_name`, `solution_types` (one a line; blank: all), `enabled`, `sort_order`, `item` (an
ERPNext Item: priced from its rate on the lead segment's price list), `default_price` (without an
Item price), `effort_days`, `fit`, and what it adds to a proposal: `description`, `features` (a
line each), `scope` ("Subsection | line"), `deliverables`, `assumptions`, `out_of_scope`,
`workflow` ("Title: Step → Step"), `image_tag`.

Ticking modules pulls their text into Modules & Features, Scope of Work, Deliverables,
Assumptions, Out of Scope, Workflow, the Timeline (effort) and the Investment (price), at **zero
AI cost**. In a proposal the module lines are its own copy: editing one changes only that
proposal, and the hub's *Reset to library* copies the catalogue text back. A line whose price
wasn't set by hand (`rate_overridden`) takes the list price again on every save.

Seeded with ready Indian-context content (GST, PF / ESI / PT, INR): Accounts, HR & Payroll,
Selling & CRM, Buying, Stock & Inventory, Manufacturing, Projects & Timesheets, Assets, Helpdesk,
Website & Portal, Flutter Mobile App, Web Application, Admin Panel, Reports & Dashboards,
Integrations, Data Migration, Training. **Prices start at 0**: the owner sets them or links Items.

**Draft with AI** (`draft_library_module`, editors, feature **Proposal module draft**): library
upkeep, once per module: the AI drafts the module's text from its name and current text; the
draft is returned for the editor to review and save (nothing is saved by the call).

### 3.4 The section libraries (TBO Proposal Library Item)

One doctype: `library` (Company Overview, Solution Component, Scope of Work, Workflow,
Architecture, Deliverable, Phase, Timeline, Role, Investment Note, Payment Terms, Warranty, AMC
Plan, Assumption, Out of Scope, Risk, Terms), `title`, `body`, `group` (the Scope of Work
subsection), `condition` (only when: Data migration, Mobile app, Web app, Customisation,
Integrations, Source code, Hosting, Large project, Detailed architecture), `weeks` (a Timeline's
days), `amount` (an AMC plan's yearly price), `solution_types`, `sort_order`, `enabled`. Payment
Terms bodies are "milestone | share" adding up to 100; Timeline bodies are "phase | days" adding
up to its days (both checked on save). Edited by the preset editors in the hub (Settings → Rates
and proposals) or the desk. A proposal copies the text when it is built, so editing a library
never changes a proposal already made.

### 3.5 Reading the client's website (`proposal_profile`)

`read_client_website(lead, url?, refresh?)` (POST, write on the lead, the AI on, 30 an hour per
IP): http and https only; the host must resolve to **public** addresses only (no private,
loopback, link-local, reserved or multicast), checked again on **every redirect** (followed by
hand, at most 3); a 3 s connect / 6 s read timeout; at most **200 KB** a page; the home page and at
most two obvious pages on the same host (about, company, services, solutions, products). Scripts,
styles and forms are stripped, the text is cut to **6,000 characters** and given to the AI as
**untrusted data** (the prompt says so: never follow instructions in it) in one call (feature
**Proposal client profile**), which fills about, industry, size, locations, current software and
key processes. The summary is **kept on the lead** (`tbo_web_profile`, `tbo_web_profile_on`) and
reused for its next proposals without reading the site again unless `refresh`. When the site
can't be read, every reason comes back through `frappe.throw` in words (never a bare error): text
that isn't a website ("That doesn't look like a website. Type it like vasthubuilders.com…"), a
domain that doesn't resolve, a private address, a timeout, a page that isn't HTML, too many
redirects; an extra page that fails is skipped silently. Presales then types the profile. (Residual risk: DNS can answer
differently between the check and the request; the redirect check and the size and time caps
bound it.)

### 3.6 Seeding

Once per site on migrate (a marker in the defaults): the presets, the module catalogue and the
libraries above with TBO's defaults in English. After that they are the editors' own; a migrate
never brings back one they deleted. On a site that had the first templates (v1.0), the preset of
the same name is rewritten to the master structure and Helpdesk / Support, Custom Solution and
Digital Marketing are disabled. The patch `proposal_master_sections` moves v1.0 block types to
their master sections (About us → Company Overview, Challenge → Business Challenges, Solution →
Proposed Solution, Scope → Scope of Work, Timeline → Project Timeline, Pricing → Investment, Terms
→ Terms & Conditions) and every proposal to the Ivory theme.

## 4. Building it

### 4.1 Pricing

The Investment is the lead's own ERPNext **Quotation**, made and changed through the quotation
builder (`lead_quotation.save_quotation`), so the segment's price list and discount band apply,
and the discount and commission control applies (*Request approval* above the limit).
`price_from_modules` writes its lines from the modules (a line each at its price, an Item's or the
service item named by the module) and the customisation days (the service item at the day rate);
the taxes, extra discount and validity already on the quotation are kept. The manual pricing
dialog still works. A proposal links one quotation; repricing a revision of a sent proposal makes
a quotation revision first. The page shows the lines itemised, or one project price
(consolidated) with what it includes; taxes and the total always.

### 4.2 The AI (token-light)

Small calls only, each logged in the TBO AI Usage Log against the proposal (or the lead) and
counted against `proposal_ai_daily_limit` (the website read and the module draft too):

1. **The website summary** (§3.5), once per lead, kept.
2. **The requirements extraction** (§3.1), structured output only.
3. **The narrative** (`write_sections`, feature **Proposal draft**): one call writes the Executive
   Summary, the Client Background, the Business Challenges and the Proposed Solution's benefits,
   and expands each customisation bullet into one or two sentences (kept on the scoping as
   `detail`; effort and price stay manual). The brief is compact: the confirmed profile, the
   requirements (≤ 3,000 characters), the newest notes and call notes (≤ 1,500) and minutes
   (≤ 1,500), the module **names** (not their catalogue text), the scoping answers, each section's
   brief. No boilerplate is sent.

They stay separate (not merged into one call): each is small, the extraction must be confirmed
by presales before anything is generated, and the website summary is reused across a lead's
proposals. **A typical proposal makes two calls** (the extraction and the narrative), three for a
lead's first proposal with a website; none without the AI. The chat's other answers (chips,
numbers, customisation lines split one a line) are taken as they are, at no cost; only an answer
that doesn't fit its question costs a call (5 below).

4. **An edit in words** (`chat_edit`, feature **Proposal edit**), later, on a draft: "add Payroll",
   "make it 90 days", "remove AMC". One small call reads the message against the proposal's
   **structured state** (its modules and the catalogue's names, the customisations, the picked
   templates and their choices, users, hosting, the extras, the price shown, the sections off) —
   never the transcript or the text — into structured changes (`add_modules`, `remove_modules`,
   `timeline_template`, `payment_terms`, `amc`, `amc_plan`, `hosting`, `users`, `mobile_app`,
   `data_migration`, `integrations`, `add_customisations`, `remove_customisations`,
   `pricing_mode`, `sections_off`, `sections_on`). Only values from the choices are applied
   (`apply_edit`); the data sections are rebuilt (hand-edited ones kept), the modules repriced
   when they changed; it never rewrites the proposal. The reply is built from what was applied
   ("Done: Added HR & Payroll; Timeline: 90 days."), not written by the AI. The message and the
   reply go into `chat`. Everything factual (modules, prices, scope, timeline, terms) comes from data, never
the AI; a text with a number that isn't in the brief is left out with a warning
(`ai.invents_numbers`). The result is kept on the proposal; the AI's text is never rewritten on
save (§4.5 rebuilds only the data sections).

5. **An off-script chat answer** (`read_chat_answer`, feature **Proposal chat answer**), in the
   start chat, only when an answer doesn't fit its question: an intent typed at the website step
   ("they need payroll and accounts"), needs in place of a number, Malayalam or a Malayalam and
   English mix. One small structured call gets the question and the text (≤ 3,000 characters)
   with the templates, the catalogue's names and the hostings, and returns the text's language
   (English, Malayalam, Mixed), its English, and only the profile and scoping fields the text
   gives (checked against the choices like the extraction). Values are stored in English; the
   transcript keeps what was said, with "Translated from Malayalam" beside it.

The AI's text is tidied (`builder.tidy`) before it is kept: a code fence, and a few lowercase
letters left after the last sentence ("…growth.ss", or "ss" on a line of its own), are dropped.
A live draft showed such a tail; a domain's ending ("erpnext.com") is not touched.

**Regenerate** (`regenerate_section`): an AI section by one small call (feature **Proposal
section**), any other from the libraries at no AI cost (*Reset to library*). A section edited by
hand (`edited`, set on save when its text changed) comes back with a question (`confirm`) and is
written over only with `force`. **Regenerate proposal** (`regenerate_proposal`): every data section
again, and with the AI on the narrative and customisations in one call; edited sections are kept
(their headings returned) unless `force`.

Editors see the AI's runs, tokens and estimated cost on the proposal (from the usage log), and
about how many tokens a full regenerate costs (the brief at four characters a token plus about
250 a section).

### 4.3 Sharing

- **Copy link:** a sent proposal's `/proposal/<token>` link (Share and Send mark the draft ready,
  §4.5, when presales didn't). A **draft's link shows nothing**;
  `share_proposal` (the hub asks "Share this draft?" first) sends it as Send does but without an
  email: refused while anything blocks it (the pricing above the limit too; an outgoing email
  account isn't needed), its quotation goes out (`mark_sent(…, "Manual")`), the lead moves to
  Proposal Sent, the content is fixed. This is the safer choice: a client never sees a draft or
  an unapproved price. Opening the link records views as for Send.
- **Download PDF** and **Print** from the hub and the client's page (§5.1).
- **Send by email** (§6).

### 4.4 Revisions

*Create revision* copies the proposal (sections, modules, scoping, profile, images, pricing link)
as a new **Draft** with the next `revision_no`, a fresh validity and a **new link**; it becomes the
latest. Earlier versions keep their status and page ("A newer version exists"). Only the latest
is sent. A revision starts not marked ready.

### 4.5 Edits after Generate, and Mark ready

A save (`save_content`) that changes the **modules** (price, days, fit, added or removed) or the
**scoping** flows into the proposal, after the sections sent with it (the hub sends its copy):

- every data section not edited by hand is built again (Modules & Features, Scope of Work,
  Proposed Solution's table, Project Timeline, Investment, Payment Terms, Assumptions…); a section
  edited by hand is kept until *Reset to library* (`regenerate_section` with `force`);
- a timeline that was the suggestion follows the new effort (`suggest_timeline`); one picked by
  hand stays (picking the suggested one counts as following it);
- when the quotation's lines changed (a module's name, Item or price, or the customisation days)
  the draft's quotation is priced again (`price_new_proposal`), so the page's Investment and the
  payment amounts follow; what stops it comes back as `warnings` on the save. The AI's sections
  are never rewritten on save.

**Mark ready** (`finalise_proposal` with `ready` 1, `ready_on` stamped) is the step before Share
and Send: refused while anything would stop it going out (the share blockers), and then the
draft's content and numbers are fixed: every change (save, pricing, regenerate, the assistant,
images) is refused with "It is marked ready to send. Reopen it to change it." until **Reopen**
(`ready` 0). Share and Send mark it ready themselves when presales didn't, so the API stays as
it was.

## 5. The client's page (`/proposal/<token>`) and the PDF

`website_route_rules` → `www/proposal.py`. No login; `no_cache`; `noindex`.

- **Shown:** the letter head; the **cover** (§2.1 #1); the **contents** with the part titles; the
  numbered sections that are on and not empty (the Investment and the Acceptance always), each
  part's title before its first section, subheadings numbered n.1, n.2…; tables, workflow
  chains, the module cards, the timeline bar, payment amounts; the quotation's lines (itemised or
  consolidated), discount, taxes, total and total in words; the acceptance block. **Nothing
  else:** no emails (the accepted block shows the accepting client's email masked, e.g.
  a•••@client.example), owners, notes, lead or quotation names, internal statuses. Everything is escaped.
- **Tab and share card:** the icon is Website Settings' favicon, else its app logo or the company
  logo, else the bundled TBO icon (`/assets/tbo_smart/images/tbo-proposal-icon.svg`, a PNG for
  `apple-touch-icon`); the title "<proposal title> · <company>"; `og:title`, `og:description`
  ("Proposal for <client> from <company>") and `og:image` (the letter head image or the logo):
  **the title and the client's name only, never a price or the proposal's text**.
- **Not found** (the same 404 every time): a malformed or unknown token, a Draft without preview,
  Proposals switched off.
- **Preview** (`?preview=1`): any status including Draft, a "Preview" notice, no view recorded,
  no Accept or Decline. **Print** (`?print=1`): the browser's print dialog opens once loaded.
- **Viewed, Accept, Decline:** as v1.0 — `record_view` once loaded (visitors not signed in to the
  ERP site only, under the row lock); the first view moves Sent to Viewed and its quotation to
  Viewed / Acknowledged and tells the lead owner; Accept (name and email) moves the proposal and
  its quotation to Accepted, the lead to Closed – Won with its deal, and fills the acceptance
  block; Decline (optional reason) rejects the quotation and keeps the lead open; once decided it
  stays decided; only the latest revision, still with the client and within its validity (and
  its price), can be answered.
- **Download PDF** (`download_pdf`), **Images** (`image(token, file)`), **rate limits** per IP:
  views 60, accept and decline 10, PDF 30, images 600 an hour.

### 5.1 One theme: ivory, premium, print-ready

One theme, **Ivory**: warm ivory paper (#FBF8EF), dark ink (#1C1B19), the TBO teal (#3A748D)
only for section numbers, the headings' rule, the timeline bar and the module cards' top edge;
no other colours, no gradients; nothing depends on colour alone, so it reads in grayscale. The
colours are written out in the CSS (wkhtmltopdf's WebKit has no CSS variables).

**The PDF** (`render_pdf`), A4 on one grid:

- **The page frame** (`pdf_page`, `pdf_options`): 18 mm from every edge on every page. The top
  margin holds the letter head (18 mm + its height + 8 mm). The bottom one holds a footer picture
  under a 5 mm line for the page number (18 mm + its height + 5 mm + 3 mm), else the footer line
  (18 mm + 9 mm); the sides are full bleed with 18 mm of padding, so the ivory reaches the edges.
- **The cover** fills page 1: the kicker and the title in its upper third, then the facts grid
  (client, prepared for — the lead's contact when the client is a company —, prepared by, date,
  valid till, reference) and its own footer line (company, version). It has no running footer
  line or page number; a footer picture shows there as on every page.
- **The contents** on page 2, with dot leaders to each section's page. The PDF is rendered
  twice: `number_contents` finds each section's page in the first render (its number and heading,
  after the contents), and the second render prints them; the numbers move nothing. In the PDF
  the contents carry no links (they would open the web page).
- **Type:** one family, **Geist** (SIL OFL), shipped in `public/fonts` cut to Latin with
  **tabular figures as the default** (wkhtmltopdf can neither load Google Fonts at render time
  nor switch the `tnum` feature on), in Regular and SemiBold only (its WebKit takes 500 for 400,
  so a Medium face would replace Regular). The PDF embeds them as data URIs (`font_faces`;
  wkhtmltopdf runs with local file access off); the web page loads the same files from
  `/assets/tbo_smart/fonts/`. Body 10.5 pt at 1.45, the title 22 pt, section headings 14.5 pt,
  subheadings 12 pt, tables 9.5 pt; numbers right-aligned; section, subsection and contents
  numbers in one 12 mm column.
- **Breaks:** each part starts a new page; a heading is kept with the first thing under it;
  cards, rows and the signature block don't split; a long table breaks between rows and repeats
  its header.
- **After rendering**, `on_ivory` lays every page on an ivory sheet (wkhtmltopdf paints white
  below the content of the last page), paints over the cover's running footer line, and prints
  "n / N" in Geist (embedded) right-aligned at the 18 mm margin of every other page: on its own
  line above a footer picture, else at the right end of the footer line, on the same baseline as
  the letter head's footer on the left. The browser's print keeps the ivory
(`print-color-adjust: exact`) and the same page breaks. Responsive on a phone (one column), dark
mode follows the page's own ivory (the document is paper), keyboard reachable with a visible
focus ring, 44px targets.

## 6. Sending

`send_proposal` (the hub's *Send*):

1. **Refused** with the reasons (`send_blockers`, returned with every proposal): not the latest
   revision; Accepted, Declined or Expired; no sections; validity passed; its quotation
   cancelled, closed or past its own valid till; its pricing above the limit without an
   approval; no outgoing email account (`share_blockers` is the same list without that one).
2. Its quotation goes out with it (`quotations.mark_sent(…, "Email")`); without one the lead moves
   to Proposal Sent.
3. An email to the lead (or `recipients`) with the message and the link (and with `attach_pdf`
   the PDF), as a Communication on the lead's timeline.
4. The proposal becomes **Sent** with `sent_on` and `sent_to`; uploads it doesn't show are deleted.
   Sending again resends the same link.

## 7. Daily

`expire_proposals` (daily): Sent or Viewed proposals past their valid till become **Expired**.

## 8. What the owner sets up

1. Deploy and `bench migrate` (the doctypes, the presets, the catalogue and the libraries; the
   patch moves existing proposals to the master sections and the Ivory theme).
2. An **outgoing Email Account** on the ERP site, for Send.
3. TBO Smart Settings: Quotations on the lead and **Proposals** on; **AI proposal drafts** with an
   AI provider; the **Proposal service item** (an Item for module and customisation lines; e.g. a
   service Item "Implementation Services") and the **Day rate**.
4. **Prices:** in hub Settings → Rates and proposals → Module catalogue, set each module's default
   price or link it to an Item priced on the segment rate card; set the AMC plans' yearly prices;
   review the timeline, payment terms, terms and warranty templates.
5. A **Letter Head** (the TBO header image and footer), marked default (or picked in Settings).
6. Optionally the **image library** (team, office, product pictures, tagged by section).
7. Optionally Website Settings → favicon, for the client page's tab icon.

## 9. Tests

`tbo_smart/tests/test_proposals.py` (61 tests):

- **Building:** the template's 23 sections, in order and all on; the data sections built from the
  libraries and the template's modules; the profile kept; the timeline and payment terms picked;
  permissions, the switch, workflow fields, a fresh draft; editing sections (one theme), the text
  layout (headings, tables, chains); pricing with the band.
- **AI** (`TestAIDraft`, mocked): one call writes the narrative and the customisations from a
  compact brief (module names only, notes capped); an invented number left out; usage logged and
  shown to editors only; regenerate asks before writing over an edit and then does, a data
  section resets with no AI; regenerate all keeps hand edits; the requirements box fills the form
  (catalogue names only); an AI failure still makes the proposal; the daily limit; AI off.
- **Master** (`TestMaster`): modules price the quotation (named lines, customisation days at the
  day rate, a hand price kept, list price restored); timeline, payment and AMC templates (and
  their validation); the suggested timeline follows the effort; the profile is required; Share
  link (blocked until approved, no email needed, link then works); the page's numbering, parts,
  contents, cards, payment amounts, share card without prices, favicon, acceptance filled on
  accept; the ivory PDF with page numbers in Geist, its 18 mm frame, the contents' page numbers
  and dot leaders; libraries for editors only; Draft with AI saves
  nothing; library images picked by tag and served; the website read guarded (schemes, private
  addresses, a redirect to a private address) and kept on the lead; the v1.0 patch; the assistant
  (the transcript kept, the edit call reads the state not the transcript, only valid choices
  applied, AMC off, the timeline rebuilt, a hand edit kept, nothing matched, AI off).
- **Sending, the client's page, images, the letter head:** as v1.1.
- **Live feedback** (`TestLiveFeedback`): a module's price, days and fit flow into Modules &
  Features, the timeline and the quotation while a hand edit is kept (and reset on request); a
  timeline picked by hand stays; Mark ready refuses changes until reopened, Share marks it, a
  revision starts unmarked, blockers refuse it; the AI's tail trimmed; an intent at the website
  question and a Malayalam answer read (mocked).

## Changelog

- **v2.5 (2026-10-20):** a footer picture spans the content's width (at most 30 mm high) on
  every page and the cover, with the bottom margin taken from its height and "n / N" on a line
  above it; an HTML footer around a picture shows just the picture.
- **v2.4 (2026-10-19):** the premium A4 PDF layout: an 18 mm frame on every page, the letter
  head scaled to the content's width and never clipped, a designed cover with a facts grid and
  its own footer line, the contents with dot leaders and page numbers, one bundled font (Geist
  with tabular figures) on the PDF and the page, the type scale, and the footer line with
  "n / N" on every page but the cover.
- **v2.3 (2026-10-19):** edits after Generate flow into the data sections, the timeline and the
  quotation (§4.5); Mark ready / Reopen (`ready_on`); the AI's stray tail trimmed; an off-script
  chat answer read in one small call, Malayalam to English (§4.2, 5).
- **v2.2 (2026-10-19):** users and companies can be Unlimited (and companies are asked); every
  website-read failure comes back in words; the patch `proposal_scoping_companies` normalises
  existing scoping answers.
- **v2.1 (2026-10-19):** the engine serves every client document (`client-documents.md`): the
  type's sections, words and pricing rules; the owner's note reads "… opened the proposal …".
- **v2.0 (2026-10-19):** the Universal IT Proposal (23 sections in three parts), the scoping form
  and the start flow (client profile with the guarded website read, the requirements box, the
  templates), the module catalogue with pre-written text, the section libraries, pricing from the
  modules, the token-light AI (two small calls a typical proposal, section regenerate with edit
  protection, edits in words through one small structured call, the transcript kept), the image
  library, Share link, the Ivory premium page and A4 PDF with cover,
  contents, parts, diagrams, page numbers, the favicon and share card.
- **v1.1 (2026-10-19):** images and the letter head.
- **v1.0 (2026-10-19):** proposals, templates, pricing through the lead's quotation, AI draft,
  revisions, the client's page with views, accept and decline, sending.
