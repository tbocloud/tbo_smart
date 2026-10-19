# Hub proposals API (`tbo_smart.api.hub_proposals`), v1.0

**Status:** built 2026-10-19. The TBO Support hub's proposal builder (tbocloud/helpdesk
`docs/proposals.md`) calls these. What proposals are and do: `proposals.md`. This file is the
contract: the hub builds against exactly this, and every change updates it in the same pull
request.

## 1. Calling it

Exactly as `hub-leads-api.md` §1: the hub's integration user's API key, **`acting_user`** on every
call (an enabled System User with a sales role, not an integration user; 403 otherwise), the call
runs as that person, writes are POST, errors are Frappe's (`PermissionError` 403,
`ValidationError` 417, `DoesNotExistError` 404). Every method but `get_proposal_options` needs TBO
Smart Settings → Proposals (417 "Proposals are not switched on." otherwise). Reading a proposal
needs read on its lead; changing it needs write on its lead.

## 2. Methods

| Method | Verb | Arguments | Returns |
| --- | --- | --- | --- |
| `get_proposal_options` | GET | `acting_user` | `{enabled: false}` while off, else `Options` |
| `list_proposals` | GET | `lead`, `acting_user` | `[Row]` |
| `create_proposal` | POST | `lead`, `template?`, `ai_draft` (0/1), `acting_user` | `{proposal: Proposal, warnings: [text]}` |
| `get_proposal` | GET | `name`, `acting_user` | `Proposal` |
| `save_proposal` | POST | `name`, `data` (JSON: any of `title`, `theme`, `valid_till`, `sections`), `acting_user` | `Proposal` |
| `get_pricing_form` | GET | `name`, `acting_user` | the quotation builder: `lead_quotation.get_quotation_form(lead, quotation)` (`lead-pipeline.md` §9.7), with `catalogue` priced for the segment and `pricing` |
| `save_pricing` | POST | `name`, `values` (JSON, `lead_quotation.save_quotation`'s values: `items` [{item_code, qty, price_list_rate, discount_percentage, description?}], `additional_discount_percentage`, `commission`, `taxes_and_charges`, `valid_till`, `package`, `tc_name`, `payment_terms_template`), `acting_user` | `Proposal` |
| `request_approval` | POST | `name`, `reason`, `acting_user` | `Proposal` (each kind above the limit requested from the next level up) |
| `send_proposal` | POST | `name`, `recipients?` (blank: the lead's email), `message?`, `attach_pdf` (0/1), `acting_user` | `Proposal` |
| `create_revision` | POST | `name`, `acting_user` | the new `Proposal` |
| `save_template` | POST | `data` (JSON `Template`, `name` blank for a new one), `acting_user` | `Options` |

`hub_leads.get_lead` also returns `proposals: [Row]` (empty while Proposals is off) and timeline
items of `kind: "proposal"` (`text` "<title>: <status>", `ref` the proposal, `at` its latest
decision, view, send or change).

### 2.1 Shapes

```
Options = {enabled: true, ai (AI drafts on and a provider set up), can_edit_templates,
  templates: [Template] (enabled ones; editors also get disabled ones),
  packages: [enabled TBO Quotation Package names, for a template's default packages],
  section_types: ["Cover", "About us", "Challenge", "Solution", "Scope", "Timeline", "Pricing",
    "Terms", "Custom"],
  themes: ["TBO Teal", "Graphite"]}

Template = {name, template_name, solution_type, description, enabled,
  sections: [{section_type, heading, guidance, body}], packages: [TBO Quotation Package name]}

Row = {name, title, status, revision_no, is_latest, proposal_family, quotation, sent_on,
  view_count, last_viewed_on, decided_on, modified}

Proposal = {name, title, lead, lead_name, client (the lead's company, else its name: what the
  page calls the client), lead_email, template, status (Draft / Sent / Viewed /
  Accepted / Declined / Expired), theme, valid_till,
  sections: [{section_type, heading, body}],
  quotation: null | {…lead_quotation serialize (name, docstatus, currency, items [{item_code,
    item_name, qty, price_list_rate, discount_percentage, description, amount}], total,
    discount_amount, net_total, total_taxes_and_charges, grand_total,
    additional_discount_percentage, commission, taxes_and_charges, valid_till, quote_status,
    revision, family, is_latest, …), needs_approval: [{kind, percent, limit}] (drafts only)},
  pricing: {segment, price_list, discount_band (null: none)},
  public_url (null while Draft), preview_url (…?preview=1),
  revision_no, is_latest, revisions: [{name, revision_no, status, is_latest, sent_on, modified}],
  tracking: {sent_on, sent_to, first_viewed_on, last_viewed_on, view_count, decided_on,
    decided_by_name, decided_by_email, decline_reason},
  can_write, send_blockers: [text] (empty: Send is allowed; always empty without can_write)}
```

### 2.2 Rules

- `create_proposal`: `template` an enabled template's name (404 otherwise; blank: Cover, Solution,
  Pricing, Terms). `ai_draft=1` while the AI is off is 417. `warnings` lists blocks the AI wrote
  with numbers that aren't in the lead's facts (left as the template had them).
- `save_proposal` and `save_pricing` change only a Draft (417 "Create a revision instead.").
  `sections` replaces all blocks (at most 30; each `{section_type, heading?, body?}`, an unknown
  type is 417; headings up to 140 characters, bodies 8,000). `valid_till` can't be past; `theme`
  one of `themes`.
- `save_pricing` creates the lead's quotation on the first call and updates the same draft after;
  when the linked quotation was already submitted, it revises it first and links the revision.
- `send_proposal` is refused (417, the `send_blockers` joined) while any blocker stands, and when
  there is no address. Pricing above the limit is one: request approval first.
- `create_revision` works on any proposal the person may write; the new one is the latest.
- `save_template`: the roles in "Roles that edit presets and prices" only (403). A name already
  used by another template is 409 (`DuplicateEntryError`).

## 3. The client's side (no hub involvement)

`/proposal/<token>` on the ERP site and the guest methods `tbo_smart.api.proposal_page.record_view`,
`accept(token, name, email)`, `decline(token, reason?, name?, email?)` (POST) and
`download_pdf(token, preview?)` (GET), all rate limited: `proposals.md` §5.

## 4. Tests

`tbo_smart/tests/test_proposals.py` (`proposals.md` §9) calls these methods as the integration
user with `acting_user`.

## Changelog

- **v1.0 (2026-10-19):** first version.
