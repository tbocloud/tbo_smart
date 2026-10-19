# Hub proposals API (`tbo_smart.api.hub_proposals`), v2.2

**Status:** built 2026-10-19. The TBO Support hub's proposal builder (tbocloud/helpdesk
`docs/proposals.md`) calls these. What proposals are and do: `proposals.md`; the documents after
them (quotation, project agreement): `client-documents.md`, through the same methods. This file is the
contract: the hub builds against exactly this, and every change updates it in the same pull
request.

## 1. Calling it

Exactly as `hub-leads-api.md` §1: the hub's integration user's API key, **`acting_user`** on every
call (an enabled System User with a sales role, not an integration user; 403 otherwise), the call
runs as that person, writes are POST, errors are Frappe's (`PermissionError` 403,
`ValidationError` 417, `DoesNotExistError` 404). Every method but `get_proposal_options` needs TBO
Smart Settings → Proposals (417 "Proposals are not switched on." otherwise). Reading a proposal
needs read on its lead; changing it needs write on its lead. The AI methods need the AI on (417
otherwise) and count against the day's limit (417 "Today's AI proposal runs are used up.").

## 2. Methods

| Method | Verb | Arguments | Returns |
| --- | --- | --- | --- |
| `get_proposal_options` | GET | `acting_user` | `{enabled: false}` while off, else `Options` |
| `list_proposals` | GET | `lead`, `acting_user` | `[Row]` |
| `get_client_profile` | GET | `lead`, `acting_user` | `{profile: Profile, web_read_on, from_web}`: what the lead says and the website summary kept on it |
| `read_client_website` | POST | `lead`, `url?` (blank: the lead's website), `refresh` (0/1), `acting_user` | `{profile}` (about, industry, size, locations, current_software, key_processes, website); kept on the lead; 30 an hour per IP |
| `get_requirements_from_lead` | GET | `lead`, `acting_user` | `{text}`: the notes and minutes, newest first (no AI) |
| `extract_requirements` | POST | `lead`, `text`, `acting_user` | `{template, scoping: Scoping}` for presales to confirm; nothing saved |
| `create_proposal` | POST | `lead`, `template?`, `ai_draft` (0/1), `scoping?` (JSON `Scoping`), `requirements?`, `client_profile` (JSON `Profile`, required: industry and pain_points), `chat?` (JSON `[ChatLine]`), `acting_user` | `{proposal: Proposal, warnings: [text]}` |
| `create_document` | POST | `source` (the accepted document), `document_type` (`Quotation`, `Work Order`, `BRD`), `acting_user` | `{proposal: Proposal, warnings: [text]}` (`client-documents.md` §2) |
| `get_document_chain` | GET | `lead`, `acting_user` | `Chain` |
| `save_questions` | POST | `name` (a draft BRD), `questions` (JSON `[{section_type, question}]`), `acting_user` | `Proposal` (answered questions kept) |
| `share_questionnaire` | POST | `name`, `acting_user` | `{proposal, url}`: the client's `/questions/<token>` link |
| `send_questionnaire` | POST | `name`, `recipients?`, `message?`, `acting_user` | `{proposal, url}` (an email on the lead) |
| `apply_answers` | POST | `name`, `acting_user` | `{proposal, sections: [headings rebuilt], warnings}` (417 with no new answers) |
| `get_proposal` | GET | `name`, `acting_user` | `Proposal` |
| `save_proposal` | POST | `name`, `data` (JSON: any of `title`, `letter_head` (blank: the default), `pricing_mode`, `valid_till`, `requirements`, `client_profile`, `scoping`, `modules`, `sections`, `start_date` (an agreement's); `theme` is ignored), `acting_user` | `Proposal` |
| `regenerate_section` | POST | `name`, `index`, `force` (0/1), `acting_user` | `{proposal, warnings, confirm}`: `confirm` (a question) when the section was edited by hand and `force` wasn't given; nothing changed then |
| `regenerate_proposal` | POST | `name`, `force` (0/1), `acting_user` | `{proposal, warnings, kept: [headings kept because edited by hand]}` |
| `chat_edit` | POST | `name`, `message`, `acting_user` | `{proposal, reply, changes: [text], warnings}`: one small structured call; the transcript kept |
| `price_from_modules` | POST | `name`, `acting_user` | `Proposal` (the quotation's lines from the modules and the customisation days) |
| `share_proposal` | POST | `name`, `acting_user` | `{proposal, url}`: a draft goes out as Send does but without an email (refused with the reasons while anything blocks it); a sent one just gives its link |
| `upload_proposal_image` | POST | `name`, `filename`, `content` (base64), `acting_user` | `{file, url}`: put `file` in a section's `image`, then `save_proposal` |
| `use_library_image` | POST | `name`, `image` (a TBO Proposal Image), `acting_user` | `{file, url, alt}` |
| `get_pricing_form` | GET | `name`, `acting_user` | the quotation builder (`lead_quotation.get_quotation_form`) |
| `save_pricing` | POST | `name`, `values` (JSON, `lead_quotation.save_quotation`'s values), `acting_user` | `Proposal` |
| `request_approval` | POST | `name`, `reason`, `acting_user` | `Proposal` |
| `send_proposal` | POST | `name`, `recipients?`, `message?`, `attach_pdf` (0/1), `acting_user` | `Proposal` |
| `create_revision` | POST | `name`, `acting_user` | the new `Proposal` |
| `save_template` | POST | `data` (JSON `Template`, `name` blank for a new one), `acting_user` | `Options` |
| `set_default_letter_head` | POST | `letter_head?`, `acting_user` | `Options` |
| `get_proposal_library` | GET | `acting_user` | `Library` |
| `save_library_record` | POST | `kind` (`module`, `item`, `image`), `data` (JSON; `name` blank for a new module or item; an image is created only by upload), `acting_user` | `Library` (editors only, 403) |
| `delete_library_record` | POST | `kind`, `name`, `acting_user` | `Library` (editors only) |
| `upload_library_image` | POST | `filename`, `content` (base64), `data` (JSON: title, alt_text, sections, solution_types, modules), `acting_user` | `Library` (editors only; PNG / JPEG / WebP up to 5 MB; a public file) |
| `draft_library_module` | POST | `module`, `acting_user` | `{draft}`: the AI's draft of the module's text for the editor to review (editors only; nothing saved) |

`hub_leads.get_lead` also returns `proposals: [Row]` (every type), `documents: Chain` (null while
Proposals is off) and timeline items of `kind: "proposal"`.

### 2.1 Shapes

```
Options = {enabled: true, ai, can_edit_templates,
  templates: [Template], packages: [names], section_types: [the 23 master sections, "Image",
  "Custom"], default_headings: {type: heading}, ai_sections: [the AI's section types],
  themes: ["Ivory"], pricing_modes: ["Itemised", "Consolidated"], questions: [scoping keys],
  hosting: ["Frappe Cloud", "On-premise", "Client's cloud", "TBO hosting"],
  profile_required: ["industry", "pain_points"],
  timelines: [{title, days}], payment_terms: [titles], amc_plans: [titles],
  catalogue: [Module], images: [{name, title, alt_text, sections, solution_types, url}],
  letter_heads: [{name, is_default}], default_letter_head, proposal_letter_head,
  document_types: [DocumentType] (the switched-on ones, in the chain's order)}

DocumentType = {name ("Proposal" / "Quotation" / "Work Order"), label ("Project Agreement" for
  the work order), accepted_label ("Accepted", "Signed"), follows, section_types,
  default_headings: {type: heading}, ai_sections, priced (its quotation is its price), signature
  (the client signs with a designation), questionnaire (the BRD)}

Questionnaire = {questions: [{name, section_type, question, answer, answered_on, applied,
  asked_by}], url (null until shared), open, sent_on, answered_on, new_answers}

Chain = {steps: [{document_type, label, name, title, status, status_label, done, view_count,
  quotation_status}], next: null | {action ("create_proposal" / "create" / "open"),
  document_type, label, source?, name?}}

Template = {name, template_name, solution_type, description, enabled, page_guide,
  questions: [keys], default_modules: [names],
  sections: [{section_type, enabled, heading, guidance, body}], packages: [names]}

Module = {name, module_name, solution_types (one a line), description, features, effort_days,
  default_price, item, fit, scope, deliverables, assumptions, out_of_scope, workflow,
  image_tag}

Profile = {name, website, about, industry, size, locations, current_software, key_processes,
  pain_points}

Scoping = {modules: [names], customisation, customisations: [{item, days, detail}], mobile_app,
  web_app, integrations, data_migration, users, hosting, amc, timeline_weeks, source_code,
  architecture ("Simple" / "Detailed"), timeline_template, payment_terms, amc_plan}

ChatLine = {role: "presales" | "assistant", text, at?}

Row = {name, document_type, source_document, title, status, revision_no, is_latest, proposal_family, quotation, sent_on,
  view_count, last_viewed_on, decided_on, modified}

Proposal = {name, document_type, document_label, accepted_label, source_document, can_price,
  signature, start_date, questionnaire: null | Questionnaire, title, lead, lead_name, client, lead_email, template, solution_type,
  page_guide, prepared_by (full name), status, theme ("Ivory"), pricing_mode, scoping: Scoping,
  requirements, client_profile: Profile, chat: [ChatLine],
  modules: [{module, module_name, fit, features, description, effort_days, rate,
    rate_overridden, item}],
  letter_head, letter_head_preview: null | {name, header, footer}, valid_till,
  sections: [{section_type, enabled, edited, ai (written by the AI), rebuildable, heading,
    body, image, image_url, image_alt, image_caption, image_size}],
  quotation: null | {…lead_quotation serialize, needs_approval}, pricing: {segment,
    price_list, discount_band}, public_url (null while Draft), preview_url,
  revision_no, is_latest, revisions, tracking, can_write,
  send_blockers: [text], share_blockers: [text] (the same without the email account),
  ai: null (not an editor) | {runs, input_tokens, output_tokens, cost_usd, estimate_tokens,
    daily_limit}}

Library = {can_edit, modules: [Module + enabled, sort_order], items: [{name, library, title,
  body, group, condition, weeks, amount, solution_types, sort_order, enabled}],
  images: [{name, image, url, title, alt_text, sections, solution_types, modules, enabled}],
  libraries: [kinds], conditions: [conditions], fits, hosting, scope_groups,
  clause_groups (an Agreement Clause's `group`: Change Requests, Intellectual Property,
  Confidentiality, Termination, General)}
```

### 2.2 Rules

- `create_proposal`: `template` an enabled template's name (404 otherwise; blank: the master
  sections, all on). Without the profile's industry and pain points, 417. All 23 sections start
  on. `warnings` lists what the AI left out and "Not priced yet: …" when the modules can't be
  priced (no Item and no service item).
- `save_proposal`, `save_pricing`, `regenerate_*`, `chat_edit`, `price_from_modules`,
  `upload_proposal_image` and `use_library_image` change only a Draft (417 "Create a revision
  instead."). `sections` replaces all sections (at most 30; an unknown type is 417; a section
  whose text changed is marked `edited`). Payment Terms milestones must add up to 100 (417).
  `modules` replaces the module lines (at most 40); a line whose price wasn't set by hand takes
  the list price again. `letter_head` must be an enabled Letter Head.
- `create_document`: 417 while the type (or one before it) is off, the source isn't of the type
  it follows, or isn't accepted and the latest; 409 while the lead has one of that type in play.
  `warnings` has "Not priced yet: …" when a quotation can't be priced from the modules.
- `save_pricing`, `price_from_modules`: a proposal or a quotation only (417 for an agreement: its
  price is the accepted quotation's). `chat_edit`: a proposal only.
- `chat_edit`: values outside the choices are ignored; `changes` empty when nothing matched (the
  reply says so).
- `share_proposal` and `send_proposal` are refused (417, the blockers joined) while any blocker
  stands; Share doesn't need an outgoing email account.
- `read_client_website`: http(s) public addresses only, checked on every redirect; a site that
  can't be read is 417 with the reason.
- Library methods: the roles in "Roles that edit presets and prices" only (403). A Timeline's
  phases must add up to its days; a Payment Terms template's milestones to 100 (417).

## 3. The client's side (no hub involvement)

`/proposal/<token>` (`?preview=1` for drafts, `?print=1` opens the print dialog) and the guest
methods `tbo_smart.api.proposal_page.record_view`, `accept(token, name, email)`,
`decline(token, reason?, name?, email?)` (POST; `accept` takes `designation` too, required to sign
an agreement) and `save_answers(token, answers)` (POST, a BRD's questionnaire, JSON `{question row:
text}`, 60 an hour), `download_pdf(token, preview?)` and
`image(token, file)` (GET), all rate limited: `proposals.md` §5. The hub builds the PDF link from
`preview_url`'s token: `/api/method/tbo_smart.api.proposal_page.download_pdf?token=…` (with
`&preview=1` for a draft).

## 4. Tests

`tbo_smart/tests/test_proposals.py` (`proposals.md` §9) and `test_client_documents.py`
(`client-documents.md` §8) call these methods as the integration
user with `acting_user`.

## Changelog

- **v2.2 (2026-10-19):** the BRD: `save_questions`, `share_questionnaire`, `send_questionnaire`,
  `apply_answers`, `questionnaire` on `Proposal`, `save_answers` on the client's side.
- **v2.1 (2026-10-19):** `create_document`, `get_document_chain`, `documents` on the lead,
  `document_types` in the options, `clause_groups` in the library, the type fields on `Proposal` and `Row`, `start_date`, and
  `accept`'s `designation`.
- **v2.0 (2026-10-19):** the Universal IT Proposal: `get_client_profile`, `read_client_website`,
  `get_requirements_from_lead`, `extract_requirements`, `regenerate_section`,
  `regenerate_proposal`, `chat_edit`, `price_from_modules`, `share_proposal`,
  `use_library_image`, the library methods and `draft_library_module`; `create_proposal` takes
  the scoping, requirements, client profile and chat; `Proposal` gains the profile, scoping,
  requirements, chat, modules, pricing mode, section flags, `share_blockers` and `ai`; one theme
  (Ivory).
- **v1.1 (2026-10-19):** images and the letter head.
- **v1.0 (2026-10-19):** first version.
