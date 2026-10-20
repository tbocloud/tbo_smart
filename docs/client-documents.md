# Client documents after the proposal (living spec, v1.4)

**Status:** built 2026-10-19 (the owner's sales-to-delivery chain of 2026-10-19), all 5 steps:
the **Quotation**, the **Project Agreement / Work Order**, the **BRD** with its client
questionnaire, the **SRS**, the **Implementation Plan** with the hub's project, and the **UAT
sign-off** on that project. The proposal itself is `proposals.md`; the hub's
contract is `hub-proposals-api.md`; the hub's side is tbocloud/helpdesk `docs/proposals.md`
§Documents.

The chain, one document after the next, each made from the accepted one before it:

**Proposal → Quotation → Project Agreement (Work Order) → BRD → SRS → Plan → Project → UAT**

- **Code:** `lead_pipeline/document_types.py` (the types: sections, headings, builders, words),
  `lead_pipeline/brd.py` (the BRD's sections, its AI call and the questionnaire),
  `lead_pipeline/srs.py` (the SRS's sections a module, the FR numbering, its AI calls),
  `lead_pipeline/implementation_plan.py` (the plan's dated sections, the hub's project and UAT),
  `www/questions.py` / `www/questions.html` (the questionnaire page),
  `lead_pipeline/client_documents.py` (making one, the quotation's ERPNext Quotation, the chain),
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
| Switch (TBO Smart Settings) | Proposals | **Quotations** (`enable_quotation_documents`) | **Project agreements** (`enable_work_orders`, needs Quotations) | **BRDs** (`enable_brds`, needs Project agreements) | **SRS** (`enable_srs`, needs BRDs) | **Implementation plans** (`enable_implementation_plans`, needs SRS) |
| Made from | the lead (`proposals.md` §3) | the accepted proposal | the accepted quotation | the signed agreement | the approved BRD | the approved SRS |
| Its price | its own Quotation | its own Quotation | the accepted quotation's, read only | none | none | none |
| AI | the narrative (`proposals.md` §4.2) | none | none | the narrative, from the client's words only (§5) | at most one call a module group (§6) | none |
| Contents page | yes | no | yes | yes | yes | yes |
| The client | accepts / declines | accepts / declines | **signs** (name, designation, email) / asks for changes | answers the questionnaire, **approves** / asks for changes | **approves** / asks for changes | **approves** / asks for changes |

`document_type` and `source_document` ("Made from") are set only when a document is made
(`client_documents.create_document`); the controller resets them on any other insert (before
naming, so a REST insert can't name itself `WO-…`) and refuses changes to them after
(`LOCKED_FIELDS`). A revision keeps both.

## 2. Making the next document (`create_document(source, document_type)`)

Refused (417) unless: the type and every type before it are switched on; `source` is of the type
it follows, **Accepted** and the latest revision; the person may write its lead. Refused (409)
while the lead already has one of that type in play (Draft, Sent, Viewed or Accepted, the latest
revision): open it or revise it. The source's status is read under its row lock, so two
requests at once can't both make one, and a revision made meanwhile is seen.

It copies from the source: the client profile, the requirements, the scoping answers, the
modules (the proposal's own lines and prices), the template, solution type, letter head and price
shown; it is titled "<Type>: <the proposal's title>", valid for `proposal_validity_days`, every
section of its type on (the AMC section off when the client didn't choose AMC), and every data
section built at **zero AI cost** from that data and the libraries. Presales reviews and edits
it like a proposal (only a draft changes; a sent one through a revision).

## 3. The Quotation

The detailed commercial offer, made in the hub (*Create quotation*) as soon as the proposal is
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

Formally confirms the business; made from the accepted quotation (*Create project agreement*).

| # | Section | From |
| --- | --- | --- |
| 1 | Cover | data |
| 2 | Parties | our company (address, GSTIN) and the client (city, state, contact), and who is "TBO" and "the Client" |
| 3 | Purpose of this Agreement | the references of the accepted proposal and quotation and their dates, the start date |
| 4 | Scope of Work | the scope library, the modules' scope lines and the customisations (as the proposal) |
| 5 | Modules & Customisations | the module cards |
| 6 | Deliverables | the modules' and the library's |
| 7 | Project Timeline | the proposal's timeline template, **dated from the start date**: each phase from / to (calendar days, each phase starting the day after the one before), drawn as bars with the table kept |
| 8 | Price | the accepted quotation's lines, taxes and total (read only) and the investment notes |
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

- `get_uat_cases(name)` (the approved plan): from the plan's **approved SRS**,
  `srs.uat_cases`: **each FR becomes a test case**: `{fr, module, title ("FR-ACC-01: <the
  requirement>"), steps ("1. Sign in as a user who works in <module>. 2. <the requirement>.
  3. Check the result against the expected result."), expected (its acceptance criterion)}`. 417
  while the SRS isn't approved.
- `record_uat(name, status, signed_by)` (§7): the hub records Signed (or Reopened); Signed starts
  the warranty the day after, shown on the plan and in the chain's UAT step.

## 9. The chain on the lead (`client_documents.chain`)

`hub_leads.get_lead` returns `documents` (and `hub_proposals.get_document_chain(lead)` the same):

```
{steps: [{document_type, label, name, title, status, status_label (Signed for an accepted
  agreement), done, view_count, quotation_status (the ERPNext Quotation's, for a priced one)}],
 next: null | {action: "create_proposal" | "create" | "open", document_type, label,
  source (create: the accepted document to make it from), name (open)}}
```

One step per switched-on type, in order, each the lead's accepted document of that type, else its
newest latest revision; after the plan, the **Project** (done once the hub made it, `url` its
link) and the **UAT** sign-off (done once Signed). The next actions add `create_project`
(`source` the approved plan) and `uat` (`name` the hub project). `next` is the one primary action: the first step in progress (open it),
else make the next document from the accepted one, else none (the chain is done).

## 10. What the client sees

The same page and PDF (`proposals.md` §5) with the type's own words (`document_types.words`):
the cover's kicker (Quotation, Project Agreement / Work Order), the decision panel (Accept
quotation / Sign the agreement, Decline / Ask for changes), the notices (Accepted / Signed /
Changes asked for), the acceptance sentence, the share card ("Agreement with <client> from
<company>"), the email's subject, greeting and link. Nothing else changes: no emails, owners,
notes or internal names; the token is the only key; preview, print and PDF as for a proposal.

## 11. What the owner sets up

1. Deploy and `bench migrate` (the fields, the section types, the Agreement Clause library).
2. TBO Smart Settings → **Client documents after the proposal**: **Quotations**, then **Project
   agreements (work orders)**, then **BRDs**, then **SRS**, then **Implementation plans**. All are off by default. The BRD's AI uses **AI
   proposal drafts** and its daily limit.
3. On the ERP site: a **default Sales Taxes and Charges Template** for the company (GST), and in
   TBO Smart Settings the quotation **terms** and **payment terms template**, so every quotation
   gets them.
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
refused for anything but a plan).

## Changelog

- **v1.4 (2026-10-19):** the UAT test cases from the SRS for the hub's sign-off.
- **v1.3 (2026-10-19):** the Project Implementation Plan and the hub's project from it.
- **v1.2 (2026-10-19):** the SRS: a section a module, numbered FRs and their acceptance criteria
  from the module library, at most one AI call a module group.
- **v1.1 (2026-10-19):** the BRD and the client questionnaire.

- **v1.0 (2026-10-19):** one engine for every client document; the Quotation and the Project
  Agreement / Work Order; the chain on the lead.
