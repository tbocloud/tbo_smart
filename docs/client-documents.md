# Client documents after the proposal (living spec, v1.6)

**Status:** built 2026-10-19/20 (the owner's sales-to-delivery chain of 2026-10-19) in five
steps: (1) the **Quotation** and the **Project Agreement / Work Order**, (2) the **BRD** with its
client questionnaire, (3) the **SRS**, (4) the **Implementation Plan** with the hub's project, and
(5) the **UAT sign-off** on that project. The proposal itself is `proposals.md`; the hub's
contract is `hub-proposals-api.md`; the hub's side is tbocloud/helpdesk `docs/proposals.md`
§Documents.

The chain, one document after the next, each made from the accepted one before it:

**Proposal → Project Agreement (Work Order) → BRD → SRS → Plan → Project → UAT**

while **the proposal is the quotation** (the default, §1.1); with that switched off, as before:

**Proposal → Quotation → Project Agreement (Work Order) → BRD → SRS → Plan → Project → UAT**

- **Code:** `lead_pipeline/document_types.py` (the types: sections, headings, builders, words),
  `lead_pipeline/brd.py` (the BRD's sections, its AI call and the questionnaire),
  `lead_pipeline/srs.py` (the SRS's sections a module, the FR numbering, its AI calls),
  `lead_pipeline/implementation_plan.py` (the plan's dated sections, the hub's project and UAT),
  `www/questions.py` / `www/questions.html` (the questionnaire page),
  `lead_pipeline/client_documents.py` (making one, the quotation's ERPNext Quotation, the chain,
  the next document made when one is accepted), `lead_pipeline/document_steps.py` (steps skipped
  or not used, the project made straight from the lead, who may steer the chain),
  and the proposal engine they share (`proposals.py`, `www/proposal.html`, `api/proposal_page.py`,
  `api/hub_proposals.py`).

## 1. One engine, one doctype (the decision)

Every client document is a **TBO Proposal** with a `document_type` (Proposal, Quotation, Work
Order). Considered: a sibling doctype per document sharing the engine. Chosen: the type field,
because every part of the engine is the same for all of them — the blocks and their text layout,
the libraries, the ivory page and the A4 PDF, the letter head, the token link, Share / Send /
email on the lead's timeline, views, accept / sign, revisions, permissions (through the lead),
the nightly expiry — and a sibling doctype would have meant a second copy of the controller, the
child tables, the permission hooks and the page, or an abstraction layer over both. With the
field, the differences are data in one registry (`document_types.TYPES`):

| | Proposal | Quotation | Work Order | BRD | SRS | Implementation Plan |
| --- | --- | --- | --- | --- | --- | --- |
| Name | `PROP-YYYY-#####` | `QUOT-YYYY-#####` | `WO-YYYY-#####` | `BRD-YYYY-#####` | `SRS-YYYY-#####` | `PIP-YYYY-#####` |
| Switch (TBO Smart Settings) | Proposals | **Quotations** (`enable_quotation_documents`; never while the proposal is the quotation) | **Project agreements** (`enable_work_orders`, needs Quotations unless the proposal is the quotation) | **BRDs** (`enable_brds`, needs Project agreements) | **SRS** (`enable_srs`, needs BRDs) | **Implementation plans** (`enable_implementation_plans`, needs SRS) |
| Made from | the lead (`proposals.md` §3) | the accepted proposal | the accepted proposal while the proposal is the quotation, else the accepted quotation | the signed agreement | the approved BRD | the approved SRS |
| Its price | its own Quotation (the final price while the proposal is the quotation) | its own Quotation | the accepted proposal's or quotation's, read only | none | none | none |
| AI | the narrative (`proposals.md` §4.2) | none | none | the narrative, from the client's words only (§5) | at most one call a module group (§6) | none |
| Contents page | yes | no | yes | yes | yes | yes |
| The client | accepts / declines | accepts / declines | **signs** (name, designation, email) / asks for changes | answers the questionnaire, **approves** / asks for changes | **approves** / asks for changes | **approves** / asks for changes |

### 1.1 The proposal is the quotation (`proposal_is_quotation`, on by default)

The owner's decision of 2026-10-20: one document carries the solution and the final value, so
there is no separate Quotation step after an accepted proposal. TBO Smart Settings → Client
documents → **The proposal is the quotation** (Check, default on; the patch
`proposal_is_quotation` switches it on for existing sites). While it is on:

- **The proposal's commercials are final.** Its own ERPNext Quotation (`proposals.md` §4.1)
  stays the single price record (rate card, manual prices, discount approval as today). A new
  one starts with the commercial defaults (`proposals.commercial_defaults`): the company's
  **default Sales Taxes and Charges Template** (GST), TBO Smart Settings' quotation **terms** and
  **payment terms template**, valid as long as the proposal. The Investment section shows the
  lines, the discount, the GST, the grand total and the total in words; the Payment Terms
  section each milestone's amount of the grand total. Defaults apply however it is priced (from
  the modules, or by hand through `save_pricing`; values given win), and **Send and Share need a
  price** ("Price the proposal first."), since the client accepts it.
- **Accepting the proposal** accepts its Quotation and moves the lead to Closed – Won, as
  before; the Sales Order is made from that accepted quotation as before.
- **The Quotation type is off** whatever `enable_quotation_documents` says (`document_enabled`):
  hidden from the options and the chain, `create_document` refuses it (417 "The proposal is the
  quotation: make the project agreement from the accepted proposal."). The **Project
  Agreement is made from the accepted proposal** (`document_types.made_from`), showing that
  proposal's quotation's lines and taxes read only; its Price section reads "The price is the one
  in the proposal PROP-… the Client accepted."; Send needs that quotation still Accepted ("Revise
  the proposal and have it accepted first."); repricing it is refused ("Change it through a
  revised proposal."). The BRD, SRS and plan read their references through `lineage`, so they
  follow either chain.
- **The ERPNext Quotation is internal** (accounts, the Sales Order, approvals). The client's page,
  the PDF and the emails never show its number (they never did; a test guards it): the client's
  reference is the proposal's own (`PROP-…`) and version. Staff see it in the hub (the builder's
  pricing block and the lead's Quotations, each with a desk link).
- **Tracing:** the Quotation's `tbo_proposal` names the proposal it prices; ERPNext's mapper
  carries it to the Sales Order and the Sales Invoice (`lead-pipeline.md` §9.5).

Off: everything as below (the Quotation document after the proposal, the agreement from the
accepted quotation), unchanged.

Switching it on mid-way: a lead whose Quotation document is already made keeps it, but the chain
no longer shows it and the agreement is made from the accepted proposal with **the proposal's**
price. For such a lead, make the agreement before switching on, or revise the proposal to the
agreed price first.

`document_type` and `source_document` ("Made from") are set only when a document is made
(`client_documents.create_document`); the controller resets them on any other insert (before
naming, so a REST insert can't name itself `WO-…`) and refuses changes to them after
(`LOCKED_FIELDS`). A revision keeps both.

## 2. Making the next document (`create_document(source, document_type, lead)`)

Refused (417) unless: the type and every type before it are switched on; the lead isn't lost
(§9.1); every document before it in the chain is accepted, skipped or not used (one still a
draft or with the client: "…Make this once it is accepted, or skip that step first.");
`source` is the step's `source` in the chain (§9): the **latest accepted document before it**, or
none, and then `lead` names the lead and the document is made from the lead; the person may write
its lead. A step that isn't the chain's next one (out of order: further on, or in between) needs
the lead's owner, a head of its department or a manager (§9.1). Refused (409) while the lead
already has one of that type in play (Draft, Sent, Viewed or Accepted, the latest revision): open
it or revise it. Everything is read under the lead's row lock, so two requests at once can't
both make one, and a revision made meanwhile is seen. Making a skipped step makes it wanted
again, and the lead's timeline says who made what from what.

It copies from the source: the client profile, the requirements, the scoping answers, the
modules (the proposal's own lines and prices), the template, solution type, letter head and price
shown; it is titled "<Type>: <the proposal's title>", valid for `proposal_validity_days`, every
section of its type on (the AMC section off when the client didn't choose AMC), and every data
section built at **zero AI cost** from that data and the libraries. Presales reviews and edits
it like a proposal (only a draft changes; a sent one through a revision).

Without an accepted document before it, it is filled from the step's `prefill` (the latest
document before it of any status, such as a skipped draft: copied the same way, but not named as
its source, so nothing reads it as accepted), else from the lead: its profile
(`proposal_profile.profile_defaults`), its notes and minutes as the requirements, and the lead
type's template and default modules. An agreement made with no priced document before it sets
no price ("This agreement sets no price: the price is agreed separately.") and Send doesn't wait
for an accepted one.

### 2.1 When the client answers (`client_documents.after_decision`)

Queued after the client's answer is saved (`proposals.decide`), run as the lead's owner:

- **Accepted / signed / approved:** the chain's next step, when it is a client document made from
  this one, is made at once as a draft (`prepare_next`), so the hub shows one button, *Review and
  send*. While the proposal isn't the quotation that is the **Quotation**, made from the accepted
  proposal with its ERPNext Quotation (a revision of the proposal's, §3): the owner is told
  "Quotation SAL-QTN-… created from the accepted proposal PROP-…: review and send it". While the
  proposal is the quotation nothing more is priced and the agreement is prepared. A step skipped
  or already made is left alone; the project is the hub's (*Create project*).
- **Ask for changes** on an agreement, a BRD, an SRS or a plan (`REVISED_ON_CHANGES`): its
  revision is made as a draft (once), the owner told with the reason, and the builder shows
  "Changes asked" with it (`changes_asked`). The client's page says the updated document will
  follow.
- A failure (the AI down, a price that can't be made) rolls back, is logged, and the owner is told
  to make it from the lead's Documents card. A document presales made by hand meanwhile is left
  as it is, with nothing said.

## 3. The Quotation

Only while the proposal is **not** the quotation (§1.1). The detailed commercial offer, made in the hub (*Create quotation*) as soon as the proposal is
accepted. Its price is **its own ERPNext Quotation** on the ERP site:

- the accepted proposal's quotation is **revised** (`quotations.create_revision`: the same lines,
  discount and commission, a fresh validity; the accepted one stays Accepted); with no quotation
  (an unpriced proposal) a new one is priced from the modules (`proposal_builder.pricing_values`),
  or the document is made unpriced with the warning "Not priced yet: …" and Send says "Price the
  quotation first." until *Price from modules* or the pricing dialog prices it;
- wherever it has none: the company's **default tax template**, TBO Smart Settings' **terms**
  and **payment terms template**, and the **validity** (`lead_quotation.new_quotation_defaults`,
  the same defaults the quotation builder starts with);
- through `lead_quotation.save_quotation`, so the segment's price list and band and the
  **discount approval** apply: above the limit, Send is blocked until *Request approval* is
  granted, as for a proposal.

Sections: Cover, What is included (the module cards), Quotation (the lines, discount, taxes,
total and total in words), Payment Terms (the proposal's milestones with amounts), AMC (the
plans, when chosen), Terms & Conditions (its validity, then the Quotation's own terms, else the
terms library without the proposal's validity line), Acceptance.

Sending and accepting work as for a proposal (`proposals.md` §5, §6): its quotation is submitted
and goes out with it (Sent to Client, the follow-up task), the first view marks it Viewed /
Acknowledged, accepting marks it **Accepted** (the lead is already Closed – Won; the Sales Order
is made from this accepted quotation), declining marks it Rejected. The ERPNext print (TBO
Quotation print format) stays available on the ERP site; the client gets the ivory page and PDF
like every document in the chain.

## 4. The Project Agreement / Work Order

Formally confirms the business; made from the accepted quotation, or from the accepted proposal
while the proposal is the quotation (§1.1) (*Create project agreement*).

| # | Section | From |
| --- | --- | --- |
| 1 | Cover | data |
| 2 | Parties | our company (address, GSTIN) and the client (city, state, contact), and who is "TBO" and "the Client" |
| 3 | Purpose of this Agreement | the references of the accepted proposal (and quotation, when there is one) and their dates, the start date |
| 4 | Scope of Work | the scope library, the modules' scope lines and the customisations (as the proposal) |
| 5 | Modules & Customisations | the module cards |
| 6 | Deliverables | the modules' and the library's |
| 7 | Project Timeline | the proposal's timeline template, **dated from the start date**: each phase from / to (calendar days, each phase starting the day after the one before), drawn as bars with the table kept |
| 8 | Price | the accepted quotation's (or proposal's) lines, taxes and total (read only), named by the client document's reference, and the investment notes |
| 9 | Payment Milestones | the proposal's payment terms with each amount |
| 10 | Change Request Process | the clause library + the day rate |
| 11 | Warranty & Support | the Warranty library |
| 12 | AMC | the plan the client chose, its features and yearly fee (off when none) |
| 13 | Intellectual Property & Source Code | the clause library (the source-code clause only when the scoping includes source code) |
| 14 | Confidentiality | the clause library |
| 15 | Termination | the clause library |
| 16 | General Terms | the clause library |
| 17 | Signatures | both sides: the client's signer (name, designation, date, "Signed online" with the masked email) and ours (prepared by) |

- **The clause library**: library **Agreement Clause** (TBO Proposal Library Item), `group` naming
  its section (Change Requests, Intellectual Property, Confidentiality, Termination, General),
  with `condition` and `sort_order` as the other libraries. One clause shows as its text, several
  as numbered subsections. Seeded once per site on migrate (`setup.seed_agreement_clauses`, the
  marker `tbo_agreement_clauses_seeded`) when the library has no clause; after that the editors'
  own (hub Settings → Rates and proposals, or the desk). **The seeded text is a starting point
  for TBO's lawyer to review.**
- **Start date** (`start_date`): set by presales before sending ("Set the project's start date"
  blocks Send otherwise). A new start date rebuilds the Purpose and the Project Timeline with
  their dates when they weren't edited by hand (an edited one is kept; *Regenerate* rebuilds it).
- **Price:** never changed here (`require_priced`: "Change it through a new quotation."); Send
  is blocked unless its quotation is still Accepted.
- **Signing:** the page asks for name, **designation** and email ("Sign the agreement"); the
  designation is kept (`decided_by_designation`) and printed in the signature block. "Ask for
  changes" (the decline, with a reason) leaves it for a revision. Signing moves nothing on the
  quotation or the lead; the owner is told ("Asha Menon signed the agreement …").

## 5. The BRD and the client questionnaire

The client's business requirements, made from the signed agreement (*Create brd* in the hub's
strip). **It never invents a requirement**: everything comes from what the client told us, and
anything a BRD needs without evidence reads "To confirm with client: …" and becomes a question.

| # | Section | From |
| --- | --- | --- |
| 1 | Business Objectives | AI; else "To confirm with client" |
| 2 | Stakeholders | the lead's contact, each module's owner (to confirm), our analyst; AI |
| 3 | Current Process (As-Is) | the profile's current software and key processes, a subheading a module; AI |
| 4 | Pain Points | the profile's pain points; AI |
| 5 | Desired Process (To-Be) | each module's workflow from the module library; AI |
| 6 | Business Rules | the customisations (with what each means); AI |
| 7 | Reports Needed | the modules' report and dashboard features; AI |
| 8 | Integrations | the scoping's systems to connect, else "none in scope" |
| 9 | Data Migration | the Data Migration module's scope, else "none in scope" |
| 10 | Success Criteria | AI; else "To confirm with client" |
| 11 | Constraints | the agreed timeline, users, hosting, the agreed price |
| 12 | Assumptions | the assumption library and the modules' |
| 13 | Open Questions | the questionnaire: each question with the answer, or "To confirm with client" |
| 14 | Approval | the client approves |

The data sections quote the client's answers for them word for word ("From the client").

- **The AI** (with *AI proposal drafts* on and a provider; the same daily limit, features
  **BRD draft** and **BRD section** in the usage log): **one call** on creation writes the eight
  narrative sections from a compact brief (the profile, the requirements, the newest notes and
  minutes, the proposal's Executive Summary, Client Background and Business Challenges, the
  module names, the customisations, the client's answers) and returns at most 15 questions for the
  gaps (added as "AI" questions). A text with a number that isn't in the brief is left as the data
  built it, with a warning (`ai.invents_numbers`). Without the AI, or when every provider fails,
  the BRD is made from the data alone with a warning. Regenerate per section as for proposals.
- **The questionnaire** (`questions` on the BRD, **TBO Document Question**: section, question,
  answer, answered on, applied, asked by Library / AI / Presales): a new BRD starts with the
  library's questions (objectives, stakeholders, success, each module's current process and
  reports, the integrations and data migration when in scope, the rules). Presales edits them on a
  draft (`save_questions`; answered ones are kept as the client gave them; at most 60).
- **The client's page** `/questions/<token>`: its **own token** (`questions_token`, 32 hex, new for
  every revision; never the BRD's page token), no login, `noindex`, no referrer. It shows the
  client's name, the questions by section and their own answers, **never the draft BRD**. The
  client saves answers (`proposal_page.save_answers`, 60 an hour per IP; under the row lock; a
  blank answer clears one); the lead owner is told. Open while the BRD is a draft or with the
  client and the latest revision; closed once it is approved, declined or revised. *Copy link*
  (`share_questionnaire`) or *Send* (`send_questionnaire`, an email on the lead's timeline).
- **Update from answers** (`apply_answers`): only the sections the **new** answers belong to are
  rebuilt from data (with the answers quoted) and, with the AI on, the narrative ones among them
  rewritten in **one small call** naming only them; sections edited by hand are kept; the answers
  are marked applied and the Open Questions refreshed. While new answers wait, Send is blocked
  ("The client sent new answers. Update the document from them first.").
- **Approving** (the decision panel "Approve the BRD"; "Ask for changes" declines with a reason)
  shows "Approved" in the chain and closes the questionnaire.

## 6. The SRS (functional specification)

Made from the approved BRD (*Create srs*). Sections: Cover, Introduction (purpose, the scope's
modules, the references of the BRD, agreement and proposal), User Roles & Permissions (every
module's roles), **one Module Specification section a module** (its heading names the module; a
**Customisations** one when the scoping has customisations), Non-functional Requirements (library
**Non-functional Requirement**: performance, security, availability, backup, browsers, mobile when
in scope), Approval.

A module's section, from the **module library's SRS text** (TBO Proposal Module: `srs_requirements`
"requirement | how the client accepts it", `srs_roles` "role | what they may do", `srs_screens`
"screen: fields", `srs_validations`, `srs_notifications`; seeded once per site for the 17 catalogue
modules where blank, `setup.seed_srs_library`, editable in the hub's module library):

- **Functional requirements**: a table "ID | Requirement | Acceptance criteria", numbered
  **FR-<module code>-NN** (ACC for Accounts, HP for HR & Payroll, CUS for the customisations; a
  requirement without a criterion reads "To confirm with client");
- user roles and permissions, the workflow and states (the module's workflow), screens and
  fields, validations, reports (its report features), notifications.

**The AI** (AI proposal drafts on; features **SRS draft** and **SRS module**; the daily limit):
**at most one small call a module group**, and none for a module the BRD says nothing confirmed
about (lines "To confirm with client" are no evidence). The call gets the module, the
requirements its library already has, and only what the approved BRD says about it (its own
subsections of the current and desired process, the business rules, the client's answers that
name it; for the customisations, the customisations agreed), and returns at most 8 more
requirements with their acceptance criteria (for the customisations, a criterion each). They are
numbered after the library's. A requirement with a number the BRD doesn't have is left out with
a warning. Regenerating one module section is one call for that module; *Reset to library*
rebuilds it with no AI; a section edited by hand is kept (asked first), also on a full regenerate.

`srs.functional_requirements(doc)` reads every FR back (`{id, module, requirement, acceptance}`)
for the UAT checklist (step 5).

## 7. The Project Implementation Plan and the hub's project

Made from the approved SRS (*Create implementation plan*), **all data, no AI**. It starts on the
agreement's start date (`start_date`, editable; Send needs it; a new date rebuilds the dated
sections that weren't edited by hand).

| Section | From |
| --- | --- |
| Plan Overview | the start, the go-live, the days and phases |
| Phases & Milestones | the proposal's timeline template dated from the start (each phase from / to, calendar days), a milestone "<phase> complete" at each phase's end; drawn as bars with the table kept |
| Deliverables by Phase | the Phase library's activities and each module's scope lines, each in the phase its subsection belongs to (Business Analysis → Analysis, System Design → Design, Development & Configuration, Testing → Testing & UAT, Deployment, Training & Handover → Training & Go-live) |
| RACI Matrix | the Role library's roles and the Client, against the phases: the project manager accountable; analysts responsible for analysis, design and training; developers for development; QA for testing; DevOps for deployment; the client responsible for UAT and training, consulted on analysis, design and deployment |
| Client Responsibilities | library **Client Responsibility** (its `group` "<phase> \| start" or "\| end"), each with its date; seeded once (`setup.seed_plan_library`) |
| Risks & Mitigation | the Risk library, as the proposal |
| Approval | the client approves |

**The hub's project** (one way, once): with the plan approved, the chain's next action is *Create
project*. The hub reads `get_project_plan` (the name "<client> - <solution>", the client, the
start and go-live, each phase with a task for each of its deliverables and its milestone, dated)
and makes its own project and tasks with them (read from the plan's **approved** Phases &
Milestones table and Deliverables by Phase section, so an edit the client approved stands), then
calls `link_project` (an approved, latest plan only) (the project's name and
link, kept on the plan in `hub_project` / `hub_project_url`, once; another name is refused; the
lead's timeline says so). After that the project is where the work is planned: changing the plan
never changes the project. `record_uat` (the hub's UAT sign-off on that project: its state, and
when **Signed** who signed and **the warranty's start**, the day after; `uat_status`,
`uat_signed_on`, `uat_signed_by`, `warranty_starts_on`) is what the UAT step (§8) records. The
same state again changes nothing (a signed UAT keeps its date and warranty); a later state after
Signed (Reopened) withdraws the signature and the warranty date.

A timeline template may name its phases its own way: the modules' work goes to the standard
phase of its subsection by name, else to the phase at the same position.

## 8. The UAT sign-off

The client's acceptance tests, on the hub's **existing project sign-off** (tbocloud/helpdesk
`docs/project-signoff.md`, UAT), extended rather than duplicated: the token link with the email
code, the answers, the issues as tasks, the signature, the signed PDF on the project. What the
ERP site gives it:

- `get_uat_cases(name)` (an **approved**, latest plan; 417 otherwise): from the plan's **approved
  SRS** (read permission on it checked),
  `srs.uat_cases`: **each FR becomes a test case**: `{fr, module, title ("FR-ACC-01: <the
  requirement>"), steps ("1. Sign in as a user who works in <module>. 2. <the requirement>.
  3. Check the result against the expected result."), expected (its acceptance criterion)}`. 417
  while the SRS isn't approved.
- `record_uat(name, status, signed_by)` (§7): the hub records Signed (or Reopened); Signed starts
  the warranty the day after, shown on the plan and in the chain's UAT step.

## 9. The chain on the lead (`client_documents.chain`)

`hub_leads.get_lead` returns `documents` (and `hub_proposals.get_document_chain(lead)` the same),
for the acting user:

```
{steps: [{document_type, label, name, title, document_status, status, status_label (Signed for
  an accepted agreement; Skipped / Not used), done, view_count, quotation_status, url? (the
  project), skipped: null | {status, reason, by, by_name, on}, implicit?, wanted,
  actions: [open, create, skip, bring_back], source, prefill, in_order, create_refused}],
 next: null | {action: "create_proposal" | "create" | "open" | "create_project" | "uat",
  document_type, label, source (create: the accepted document to make it from, null: the lead;
  create_project: the plan or proposal, null: the lead), source_type, name (open, uat)},
 blocked: null | text, can_steer, note: null | text}
```

One step per switched-on type, in order (no Quotation step while the proposal is the quotation),
each the lead's accepted document of that type, else its newest latest revision; after the plan,
the **Project** (done once the hub made it, `url` its link) and the **UAT** sign-off (done once
Signed). The project is the plan's (`hub_project`) or, made straight from the lead, the lead's
Project step's (§9.1).

`next` is the one primary action: the first step that isn't done, skipped or not used. With its
document a draft it is *Review and send* (open it); with the client, *Open the …*; declined or
expired, *Revise the …*; without one, *Create …* from the latest accepted document before it (or
from the lead), *New proposal*, *Create project* or the *UAT sign-off*. None when every step is
done, skipped or not used, or while the lead is lost (`blocked`); `note` says why when the UAT
waits for a project that was skipped.

### 9.1 A flexible chain: skip, start anywhere, make one in between, bring back

The chain suggests the next step; nothing is hard-locked.

- **Skip** (`skip_steps(lead, steps, reason)`): any step (a document type, the Project, the UAT)
  this client doesn't need, with a short reason (required, up to 500 characters). It shows as
  **Skipped** with who, when and why, and the chain moves on. A step whose document is with the
  client or decided can't be skipped, only revised; nor a made project or a signed UAT. A draft
  left on a skipped step stays (openable), and comes back with it.
- **Start anywhere:** a step further on can be made first (`create_document` with the step's
  `source`, or from the lead). The steps before the furthest one under way that have nothing show
  as **Not used** (`implicit`, nothing recorded) and can be marked so in one go with one reason
  (`skip_steps(…, status="Not used")`), which records them.
- **Make one in between:** a step skipped or not used can still be made later (the BRD after the
  SRS started): it is filled from whatever exists and takes its place in the chain; making it
  clears its skip.
- **Bring back** (`bring_back_step`): a skipped or not-used step is wanted again (`wanted`; it
  is no longer taken as not used) and becomes the next step when it is the first left.
- **Who:** the lead's owner, a head of its department, or a manager (Sales Manager, System
  Manager) may skip, bring back and work out of order (`can_steer`); anyone else who may write
  the lead (a BD assigned to it) makes only the next step. A BD steers only their own leads.
- **A lost lead** (Closed – Lost, Not Interested, Wrong Number, Small Business) moves nothing:
  `blocked` says so in words, `next` is none and no step offers create, skip or bring back;
  making a proposal or a document, skipping and bringing back are refused (417). A won lead
  carries on (accepting the proposal is what wins it).
- **Stored with the chain:** each skipped, not-used or brought-back step is a **TBO Lead Document
  Step** (`lead`, `document_type` (a type, Project, UAT), `status` Skipped / Not used / blank,
  `reason`, `set_by`, `set_on`), one per lead and step, read by the mobile app and the reports
  (its lead decides who reads it, as for proposals; it is written only through this API, and
  changing one is a change in the hub's lead feed). Its Project row also carries the **project
  made straight from the lead** (`hub_project`, `hub_project_url`) and its UAT (`uat_status`,
  `uat_signed_on`, `uat_signed_by`, `warranty_starts_on`), recorded as on a plan
  (`link_project(lead, project)` with neither a plan nor a proposal, `record_uat(lead=…)`). A
  project is made from the lead only while no plan (or, for a content lead, no accepted strategy
  proposal) makes it: with one approved, `link_project` without it is refused ("This lead's
  project is made from its implementation plan PIP-…"). Its UAT has no SRS to test against, so
  the chain doesn't point at it: `note` says so, and the UAT step can be skipped. A project or
  UAT that goes ahead clears its skip record.
- **A proposal out of order:** while the chain has moved on without one (the Proposal step
  skipped or not used), making a proposal needs the lead's owner, a head or a manager too.
- **The lead's timeline** has a line for every step: made ("<who> made the BRD BRD-… from
  PROP-…" or "from the lead"), sent ("…sent the agreement … to …") or shared, opened, accepted,
  signed or approved, changes asked (with the reason), skipped ("Skipped BRD, SRS: <reason>"),
  marked not used, brought back, prepared after an acceptance, the project made, the UAT signed.
  Each also notifies the lead's owner, unless the owner did it.

## 10. What the client sees

The same page and PDF (`proposals.md` §5) with the type's own words (`document_types.words`):
the cover's kicker (Quotation, Project Agreement / Work Order), the decision panel (Accept
quotation / Sign the agreement, Decline / Ask for changes), the notices (Accepted / Signed /
Changes asked for), the acceptance sentence, the share card ("Agreement with <client> from
<company>"), the email's subject, greeting and link. Nothing else changes: no emails, owners,
notes or internal names; the token is the only key; preview, print and PDF as for a proposal.

## 11. What the owner sets up

1. Deploy and `bench migrate` (the fields, the section types, the Agreement Clause library).
2. TBO Smart Settings → **Client documents after the proposal**: **The proposal is the quotation**
   (on after the migrate; leave it on for Proposal → Agreement), then **Project agreements (work
   orders)**, then **BRDs**, then **SRS**, then **Implementation plans** (all off by default).
   **Quotations** only when the proposal is not the quotation. The BRD's AI uses **AI proposal
   drafts** and its daily limit.
3. On the ERP site: a **default Sales Taxes and Charges Template** for the company (GST; tick
   *Default* on it), and in TBO Smart Settings the quotation **terms** and **payment terms
   template**, so every proposal's quotation gets them. For the **proposal service item** use a
   UOM that takes fractions (ERPNext's **Day**, *Must be Whole Number* off), so 3.5 customisation
   days stay 3.5 × the day rate on one line (`proposals.md` §4.1).
4. Have the **Agreement Clause** texts reviewed by TBO's lawyer (hub Settings → Rates and
   proposals → library, or the desk).
5. The company's **address and GSTIN** on the Company (the agreement's Parties).

## 12. Tests

`tbo_smart/tests/test_client_documents.py` (helpers `enable_client_documents`,
`make_accepted_proposal`, `accept_as_client`, `make_hub_document` in `test_utils.py`): each type
off by default and the agreement needing the quotation's switch; a REST insert can't choose the
type; the quotation from the accepted proposal (a revision of its quotation with the default
taxes, the sections, one in play at a time, sent with its quotation, accepted → quotation
Accepted, the chain and its next action at each step); only from an accepted proposal; an unpriced
proposal priced from its modules with the defaults; a discount above the limit blocks Send; the
agreement (sections, Parties, Purpose with the references, clauses, price read only, the start
date blocks Send and dates the timeline and the purpose (a hand-edited purpose kept), the page asks for the designation, the PDF, signing
needs the designation and leaves the quotation Accepted, the chain done, `get_lead` carries it);
the source-code clause follows the scoping; a revision stays an agreement; the BRD (built from
the agreement without AI with the profile's words and "to confirm", the library questions, its own
questionnaire token; only from a signed agreement; one AI call from the client's words with an
invented number left out and the AI's questions added, usage logged; the client's page shows the
questions and not the draft, answers saved as a guest, unknown questions refused, Send blocked
until the answers are applied, *Update from answers* rewrites only the touched section in one call
naming only it; approving closes the questionnaire, a bad token is not found; presales edits the
questions); the SRS (a section a module with numbered FRs from the library, the roles, the
non-functional requirements and the references; read back for the UAT; a module rebuilt with no
AI; only from an approved BRD; one AI call a module the BRD speaks of, its requirements numbered
after the library's, usage logged; an invented number left out); the plan (dated from the
agreement's start, milestones, deliverables by phase, the RACI, the client's dated part; no
project from a draft; approved: the project plan with its phases, tasks and milestones, linked
once, the chain's Project and UAT steps and their actions, the UAT recorded with the warranty's
start); the UAT's test cases (a case an FR of the approved SRS, its steps and expected result;
refused for anything but a plan). `TestProposalIsQuotation`: the options and the chain without
the Quotation step; the proposal's quotation with the default GST, the proposal's validity and
`tbo_proposal`, the page's GST and grand total; the quotation number absent from the guest page,
the PDF's HTML and the email (staff get it with its desk link); the agreement made from the
accepted proposal with its quotation read only, its Price naming the proposal, signed, then the
BRD; the Sales Order and the Sales Invoice carry the proposal; off keeps the Quotation step and the
proposal's quotation as before. `TestFractionalDays`: 3.5 days on Nos priced as one unit at 3.5 ×
the day rate (the owner's "Create quotation" case), and kept as 3.5 on Day.

`TestFlexibleChain` (helpers `skip_hub_steps`, `make_hub_document_from_lead`, `decline_as_client`,
`client_page_html`): three steps skipped in a row (each Skipped with its reason and who, stored,
on the timeline), the SRS made next from the lead filled from the skipped draft, the BRD brought
back and made in between; a chain started at the plan (the earlier steps Not used, then marked so
in one go), approved, then *Create project* from it; a project straight from the lead and its UAT
signed (the warranty on the lead's Project step; its UAT waits with a note and can be skipped);
the approved plan's project not made from the lead, a proposal out of order refused for a BD who
isn't the owner; no project (every step skipped, nothing next; a
UAT brought back without a project waits with a note); a step with the client can't be skipped,
a reason is required, a skipped draft comes back with its step, nothing is made past a document
with the client; a BD assigned to the lead can't skip or work out of order, a manager can; a lost
lead moves nothing; the Quotation made as a draft when the proposal is accepted (once), the
agreement prepared while the proposal is the quotation; changes asked make the revision with the
reason, and the client's page says the update follows.

## Changelog

- **v1.6 (2026-11-14):** a flexible chain (§9.1): skip a step with a reason, start anywhere
  (earlier steps Not used), make one in between, bring back; out of order for the lead's owner, a
  head or a manager; nothing moves on a lost lead; **TBO Lead Document Step**; a project (and its
  UAT) straight from the lead; the next document made as a draft when one is accepted (the
  automatic quotation), the revision when the client asks for changes (§2.1); a timeline line and
  the owner told for every step.

- **v1.5 (2026-10-20):** the proposal is the quotation (§1.1), on by default: Proposal → Project
  Agreement, the proposal's quotation with the default GST, terms and validity, `tbo_proposal`
  on the quotation, the Sales Order and the Sales Invoice.

- **v1.4 (2026-10-19):** the UAT test cases from the SRS for the hub's sign-off.
- **v1.3 (2026-10-19):** the Project Implementation Plan and the hub's project from it.
- **v1.2 (2026-10-19):** the SRS: a section a module, numbered FRs and their acceptance criteria
  from the module library, at most one AI call a module group.
- **v1.1 (2026-10-19):** the BRD and the client questionnaire.

- **v1.0 (2026-10-19):** one engine for every client document; the Quotation and the Project
  Agreement / Work Order; the chain on the lead.
