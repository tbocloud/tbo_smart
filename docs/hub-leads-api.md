# Hub leads API (`tbo_smart.api.hub_leads`), v1.1

**Status:** built 2026-10-16. The TBO Support hub's lead page (tbocloud/helpdesk) shows and edits
tboindia's leads through these methods. The leads are ERPNext's own **Lead**
(`docs/erpnext-lead-cutover.md`). This file is the contract: the hub builds against exactly this,
and every change to the API updates it in the same pull request.

## 1. Calling it

- **One API key:** the hub's integration user on tboindia, a System User with the role in
  TBO Smart Settings → **Hub integration role** (blank: **Hub Integration**, created on migrate).
  Give it System Manager as well, so ERPNext's own role permissions never stop a call the acting
  user may make.
- **`acting_user`** on every call: the hub user's email. It must be a user on tboindia that is
  **enabled**, a **System User**, and has a **sales role** (Sales User, Sales Manager or System
  Manager) and is not itself an integration user; otherwise the call fails with **403**
  (`PermissionError`). The integration user without `acting_user` also gets 403.
- **The call runs as that user** (`frappe.set_user` for the call, then back to the integration
  user): lists are their `frappe.get_list`, a lead opens only when they may read it, writes need
  their write permission, and every record written is theirs (Comment owner, the lead's
  `owner` and default `lead_owner`). Who sees which lead is TBO's sales rule: owner, assignee,
  their team in the sales hierarchy, Sales Managers outside the hierarchy and System Managers
  everything (`erpnext-lead-cutover.md` §3).
- **Anyone else** calling these (a user's own session or key) acts as themselves; passing
  another user's `acting_user` is 403.
- **Writes are POST**, reads GET. Arguments are form or JSON fields; `data` is a JSON object
  (or its string). Errors are Frappe's: `exc_type` `PermissionError` (403), `ValidationError`
  (417), `DoesNotExistError` (404).

```
curl -s -X POST https://india.teambackoffice.com/api/method/tbo_smart.api.hub_leads.create_lead \
  -H "Authorization: token <api key>:<api secret>" -H "Content-Type: application/json" \
  -d '{"acting_user": "faisal@teambackoffice.com", "data": {"lead_name": "Ravi Kumar", "mobile_no": "9847012345"}}'
```

## 2. Methods

| Method | Verb | Arguments | Returns |
| --- | --- | --- | --- |
| `get_options` | GET | `acting_user` | `{stages: [{name, label, type, color}], sources: [], segments: [], owners: [{email, full_name}], territories: [], industries: []}` |
| `list_leads` | GET | `search?`, `stage?`, `owner?`, `segment?`, `view` (`all` / `mine` / `call_today` / `follow_up`, default `all`), `start` (0), `page_length` (20, at most 100), `acting_user` | `{rows: [Row], total}` |
| `get_lead` | GET | `name`, `acting_user` | `{lead: Lead, timeline: [Item], quotations: [Quotation], allowed_stages: [stage name]}` |
| `create_lead` | POST | `data`, `acting_user` | as `get_lead` |
| `update_lead` | POST | `name`, `data`, `acting_user` | as `get_lead` |
| `set_stage` | POST | `name`, `stage`, `note?`, `acting_user` | as `get_lead` |
| `add_note` | POST | `name`, `note`, `acting_user` | the new timeline `Item` |
| `get_rate_card` | GET | `acting_user` | `RateCard` (§2.3) |
| `save_rate_card` | POST | `rows` (JSON list of `{segment, price_list, max_discount}`), `acting_user` | `RateCard` |
| `set_segment_rate` | POST | `item_code`, `rate`, `segment?` (blank: the selling price list), `acting_user` | `{item_code, segment, rate}` |

### 2.1 Shapes

```
Row = {name, lead_name, company_name, email_id, mobile_no, tbo_stage, segment, lead_owner,
  owner_name, score (0-100), next_follow_up (date | null), modified}

Lead = {name, lead_name, first_name, last_name, company_name, email_id, mobile_no, phone,
  whatsapp_no, job_title, website, source, territory, industry, segment, tbo_stage,
  status (ERPNext's own, read only), lead_owner, owner_name, tbo_score, tbo_temperature
  (Hot / Warm / Cold), tbo_score_reason, tbo_lost_reason, tbo_lost_notes, creation, modified,
  next_follow_up (date | null), deal (the Opportunity once won | null), can_write}

Item = {kind: "note" | "call" | "activity" | "meeting" | "stage" | "quotation", at, who (user | null),
  who_name, text, ref (the record's name)}

Quotation = {name, docstatus, tbo_quote_status, transaction_date, valid_till, currency,
  grand_total, tbo_revision_no, tbo_is_latest}
```

- **Stages** are TBO's 13 pipeline stages in order (`lead-pipeline.md` §2), each with its type
  (Open, Ongoing, On Hold, Won, Lost) and colour name. `allowed_stages` is every other stage when
  the acting user may write the lead, else `[]`. Automatic moves (meetings, quotations) still
  happen on the server.
- **Segments:** Retail, Small, Medium, Large, Premium (`Lead.tbo_segment`); each has a price
  list and a discount band on the rate card (§2.3).
- **Views:** `mine` = the acting user owns it; `call_today` = their Call today list, in its
  order (`smart-leads.md` §4.3; empty while lead scoring is off); `follow_up` = leads with an
  open follow-up due today or earlier, earliest first. `total` counts the whole view; `start` /
  `page_length` page it. Other filters apply in every view.
- **Timeline:** notes (Comments), calls and logged meetings, emails and WhatsApp (Events), the
  pipeline meetings (TBO Lead Meeting, `text` "1st meeting: MOM Submitted"), stage changes
  (`text` "Contacted → Proposal Sent") and quotations, newest first.

### 2.2 Writing

`data` keys (anything else is refused with 417):

| Key | Field |
| --- | --- |
| `lead_name` | split into `first_name` / `last_name` when those are not sent |
| `first_name`, `last_name`, `company_name`, `email_id`, `mobile_no`, `phone`, `whatsapp_no`, `job_title`, `website` | the Lead's own |
| `source` (a Lead Source), `territory`, `industry` (Industry Type) | links; `get_options` lists them |
| `segment` | `tbo_segment`; moving a lead from one segment to another needs a preset editor (417 otherwise; `lead-pipeline.md` §9.8) |
| `lead_owner` | another sales user: only within the acting user's reach (anyone for a Sales Manager outside the hierarchy, their team for a team lead, nobody for a salesperson); 403 otherwise |
| `note` | `create_lead` only: the lead's first note |
| `next_follow_up` | `YYYY-MM-DD`: a Sales follow-up ToDo for the lead owner that day, unless one is already open for it; an empty value adds nothing (it never closes one) |

- `create_lead` needs a name or a company. ERPNext checks the email (unique unless CRM Settings
  allow duplicates, and never the owner's own).
- `set_stage` to **Closed – Lost** needs `note`: a lost reason's name, or free text saved as
  "Other" with the text as `tbo_lost_notes`. For any other stage `note` is added as a note.
  Closed – Won makes the deal (with the lead pipeline on).
- `add_note` needs read on the lead (as the app's notes do).

### 2.3 The segment rate card

```
RateCard = {segments: [{segment, price_list (null: the selling list), max_discount (0: none)}],
  default_price_list, price_lists: [selling price list names], can_edit,
  services: [{item_code, item_name, item_group, description, uom, rate (selling list),
    rates: {segment: rate | null}}]}
```

- Every segment is listed, in order, whether or not it is on the card. `rates` has a key for
  each segment with its own price list; `null` where that list has no price for the service.
- Needs Quotations on the lead (417 otherwise). Writes need a role in "Roles that edit presets
  and prices" (403 otherwise); `can_edit` tells the hub whether to show the edit controls.
- `save_rate_card` replaces the card: a row with neither a list nor a band is dropped. Refused
  (417): an unknown segment, a segment twice, a band outside 0-100, a list that isn't selling.
- `set_segment_rate` with a segment that has no list of its own is refused (417).
- What the card does to quotations: `lead-pipeline.md` §9.8.

## 3. Links back to the hub

Teams messages, emails and the morning digest link to the lead page when TBO Smart Settings →
**Lead page URL** is set (the hub's page, e.g. `https://support.tbocloud.in/helpdesk/leads`):
a lead opens at `<url>/<lead name>`, Call today at `<url>?view=call_today`. Blank: the desk.

## 4. Tests

`tbo_smart/tests/test_hub_leads.py`: the integration user must name an enabled sales user
(unknown, disabled and non-sales users refused); anyone else acts only as themselves; lists and
reads follow the acting user; writes are theirs (owner, lead owner, note author) and the session
returns to the integration user; field, owner and lost-reason rules; the follow-up view; the
options. `tests/test_segment_rate_card.py`: the rate card methods (acting user, editors only).

## Changelog

- **v1.0 (2026-10-16):** first version.
- **v1.1 (2026-10-19):** `get_rate_card`, `save_rate_card`, `set_segment_rate`.
