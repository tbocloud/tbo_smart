# Quotation discount and commission control

When someone prepares a quotation, they can adjust its discount or commission only up to the
limit of their designation. Anything above the limit needs approval from a higher level
before the quotation can be submitted. Every adjustment, approval and override is recorded.

- **Code:** `tbo_smart/quotation_control.py` (the Quotation hooks),
  `tbo_smart/api/quotation_adjustment.py` (the APIs), the doctypes
  `TBO Quotation Approval Settings`, `TBO Quotation Approval Level`,
  `TBO Quotation Adjustment` and `TBO Quotation Adjustment Step`, `tbo_smart/install.py`
  (roles, a field and default levels), and `tbo_smart/public/js/quotation_adjustment.js`
  (the Quotation form).
- **Tests:** `tbo_smart/tests/test_quotation_adjustment.py`.
- **Status:** v1.1, 2026-10-12. Enforcement is **off** until management switches it on (§9).

## 1. The flow

```
Quotation created → discount / commission set → limit check on save and submit
   ├─ within the saving user's limit → recorded as "Applied", can be submitted
   └─ above it → can be saved as a draft, cannot be submitted
          → Request approval (reason required) → next level up is notified
          → that approver approves (in full or less) / rejects / forwards up
          → requester is notified → quotation can be submitted
```

Because a quotation can only become a Sales Order or Sales Invoice after it is submitted,
blocking the submit also blocks both.

## 2. Levels

| Level | Role (given by HR) | Rank | Approves directly | Limit |
| --- | --- | --- | --- | --- |
| BDE | Business Development Executive | 10 | no | not set |
| BDM | Business Development Manager | 20 | no | not set |
| Sales & Marketing Head | Sales & Marketing Head | 30 | no | not set |
| Director | Director | 40 | yes | not set |
| CEO | CEO | 50 | yes | **no limit** |

- **A person's level** is the highest-ranked level whose role they hold. ERPNext's
  Designation is a label, not a permission, so each designation maps to a role. HR gives a
  new BDE the role *Business Development Executive*, alongside *Sales User* so that they can
  open quotations, and so on up the table. Approvers also need *Sales User* or *Sales
  Manager* to read the quotation behind a request.
- **No default limits.** The levels ship with no limit set. A limit that is blank or 0
  means "not set", and then every non-zero adjustment by that level needs approval, so
  nothing slips through before management decides the numbers. The forms say "Your limit:
  not set yet".
- **Approves directly:** Director and CEO can decide a request while it is still waiting at
  a lower level, without waiting for it to be forwarded to them. Every level, Director
  included, applies adjustments within its own limit with no approval.
- **No limit (CEO):** any percentage, and the CEO can override any decision (approve above
  everyone's limit, or reverse a rejection). An override always needs a reason.
- Someone with none of the five roles has no level: their limit is 0 and they cannot
  request approvals. Their discounted quotations wait until someone with enough authority
  submits them.

## 3. Settings: TBO Quotation Approval Settings

`/app/tbo-quotation-approval-settings`, editable by System Manager and CEO. It has
`track_changes` on, so every change to a limit has a Version entry with who changed it and
when.

| Field | Meaning |
| --- | --- |
| Enforce adjustment limits | Master switch. Off by default so that the limits can be set before sales staff are blocked. |
| Limits apply to | `Discount`, `Commission` or `Both` (default `Both`). This is management's open decision, made as a setting. |
| Levels | One row per designation: level, role, rank, max discount %, max commission %, approves directly, no limit. Role, rank and level name must be unique, and limits run from 0 to 100. |

## 4. What "discount" and "commission" are on a Quotation

**Discount: the effective discount on the list value.** It is not one ERPNext field but all
of them together:

```
list value = Σ qty × max(price_list_rate, rate_with_margin, rate)     (alternative items left out)
discount % = (list value − net_total) ÷ list value × 100
```

- Item `discount_percentage` and `discount_amount`, a rate typed below the price list rate,
  and the header `additional_discount_percentage` or `discount_amount` all count. Moving a
  discount from the header to the items (or typing a lower rate) changes nothing, so the
  limit cannot be got around that way.
- `net_total` is after the header discount, before taxes. With *Apply Additional Discount
  On = Grand Total*, ERPNext spreads the discount over the net amounts, so this still holds.
- On the form, the hint on *Additional Discount Percentage* reads "Your limit: 5%" and
  notes that item discounts count too.

**Commission: a new Quotation field, `tbo_commission_rate` ("Commission %").** ERPNext v15
Quotation has no commission field. `commission_rate`, `total_commission` and `sales_team`
exist only on Sales Order and Sales Invoice, and Sales Partner `commission_rate` is a
partner master value, not a per-quotation term. The field sits after *Referral Sales
Partner* and is created by the install hook and patch. It has a `tbo_` prefix so that it
never clashes with an ERPNext field.

## 5. Enforcement (server)

`doc_events` on Quotation, in `tbo_smart/quotation_control.py` (each hook only calls
`QuotationControl` methods):

| Event | What happens |
| --- | --- |
| `validate` (draft save) | For each limited kind above the saver's limit and not covered by an approval, an orange alert says it needs approval before submit. The save goes through. |
| `validate` (submit) | Raises `AdjustmentApprovalRequired` (a ValidationError) unless every limited kind is 0, within the submitter's limit, or covered by a granted adjustment. |
| `on_update` / `on_submit` | Voids granted adjustments that no longer cover the quotation, then records an **Applied** adjustment when the saver set a percentage within their own limit that nothing covers yet. |

**When an approval covers the quotation:** the adjustment's status is Approved or
Overridden, it is not voided, the quotation's percentage is **at or below** the approved
percentage, and the list value is the same as when it was decided.

**Voiding:** after an approval, any change that takes the percentage above the approved one
or changes the list value (items, quantities, prices) voids it. A "Voided" step is logged,
and lowering the percentage afterwards does not bring it back: a new request is needed.
Lowering the percentage under a live approval keeps it valid. An approval belongs to one
quotation name, so an amended quotation (`Q-0001-1`) needs a fresh approval.

**Open requests** are voided too, once the quotation goes above the percentage they ask
for, because they could never cover it. The requester can then ask again. Nobody can
decide or override a voided row, not even the CEO: the quotation needs a new request. A
change in list value does not void an open request, because the approver's decision
records the list value at the moment they decide.

## 6. Approval record: TBO Quotation Adjustment

One row per adjustment (`QADJ-YYYY-#####`).

| Field | |
| --- | --- |
| quotation, customer, deal | The quotation, its customer name, and its CRM Deal (Frappe CRM's `crm_deal` field, or the party when *Quotation To* is CRM Deal). |
| kind | Discount or Commission. |
| status | Pending, Escalated (forwarded, still open), Approved, Rejected, Overridden. |
| within_limit | Set on **Applied** rows: the person applied it inside their own limit. |
| voided | The quotation changed after the decision. |
| previous %, requested %, approved % | Previous is the last granted percentage for this quotation and kind. |
| quotation_value | The list value when it was requested and when it was decided. |
| requested_by, approved_by (Decided by), current_level | |
| reason, remarks | The requester's reason and the approver's remarks or override reason. |
| steps | The audit trail (below). |

**Escalation chain:** a request goes to the level directly above the requester
(BDE → BDM → Head → Director → CEO).

- An approver whose limit covers the request may **approve** it (or approve a **lower** %)
  or **reject** it with remarks.
- An approver whose limit does not cover it may **forward** it to the next level, or approve
  a lower % within their own limit. Forwarding is always allowed while a higher level
  exists.
- Director and CEO can act at any lower stage (*approves directly*).
- The CEO can **override** anything, with a reason.
- The requester can never decide their own request, and a level can never decide a request
  that is not waiting for it. Only one request per quotation and kind can be open at a
  time.

**Audit:**

- Every action appends a row to `steps`: Requested, Applied, Approved, Rejected, Forwarded,
  Overridden or Voided, with the user, level, %, remarks and time. The controller refuses
  edits to or removal of existing steps, and changes to the request fields (quotation,
  kind, requester, requested %, previous %, reason).
- Each step is also written to the Quotation's timeline as an Info comment.
- `track_changes` keeps Versions.
- Nobody has write, create or delete permission on the doctype; rows are written only by
  the server code above. A draft quotation can still be deleted (`ignore_links_on_delete`),
  and its adjustment rows stay.

Read access:

- System Manager, Sales Manager, BDM, Head, Director and CEO see all rows.
- A BDE sees the rows they created.

## 7. APIs (`tbo_smart.api.quotation_adjustment`)

All of them act as the session user, and permissions are checked on the server.

| Method | HTTP | Returns / does |
| --- | --- | --- |
| `get_adjustment_limits()` | GET | `{enabled, applies_to, kinds, level, limits, limit_labels, is_approver, can_view_all, can_override, can_forward, open_requests}`. In `limits`, `null` means no limit and `0` means not set. `is_approver`: some level ranks below mine, so requests can wait for me. `can_view_all`: my level approves directly (Director, CEO). `can_override`: my level has no limit. `can_forward`: a higher level exists. `open_requests`: how many of my requests are still open. The CRM uses these only to hide what a user cannot do. |
| `get_quotation_adjustment_status(quotation)` | GET | For each limited kind: `percent`, `limit`, `limit_label`, `within_limit`, `approved` and the `latest` adjustment. Needs read on the quotation. When the user cannot read that adjustment (a BDE looking at another BDE's request), `latest` carries only its state (status, level, percentages, voided) with `name` set to null, never the requester, reason or remarks. |
| `get_quotations_adjustment_status(quotations)` | GET | The same for up to 100 quotations at once (a JSON list of names), keyed by quotation. Quotations the user cannot read, or that do not exist, are left out. It runs a fixed number of queries however many quotations there are. Used by the CRM deal's Quotations tab. |
| `get_adjustment_requests(scope)` | GET | Requests (not within-limit Applied rows), newest first, up to 200, with `requested_by_name` and the quotation `currency` (which `get_pending_approvals` now returns too). `scope="mine"`: the ones I made. `scope="all"`: every request, only for a level that approves directly (Director, CEO), otherwise 403. |
| `get_adjustment_trail(name)` | GET | One adjustment with its `steps` (action, by_user, by_name, level, percent, at, remarks), oldest first. Needs read on the adjustment, so a BDE sees only the trails of their own requests. |
| `request_adjustment(quotation, kind, percent, reason)` | POST | Creates a Pending request at the next level up and notifies that level. It is refused when: the requester cannot write the quotation; the quotation is not a draft; the percentage is within the requester's own limit; the reason is empty; the requester has no level; or a request is already open. |
| `decide_adjustment(name, action, percent=None, remarks=None)` | POST | `action` is `approve`, `reject`, `forward` or `override`; `percent` (approve or override) defaults to the requested %; remarks are required for reject and override. It notifies the requester, or on forward the next level. |
| `get_pending_approvals()` | GET | Open requests waiting for me (my level, or lower levels when I approve directly), oldest first, excluding my own. Each has `can_approve_in_full`. |

Errors follow the TBO Smart API convention: `PermissionError` (403) when it is not yours to
decide, and `ValidationError` (417) for a bad request.

**Notifications** are Frappe Notification Logs (type Alert). They show in the desk bell,
are emailed per the user's notification settings, and reach the TBO Smart phone app through
`tbo_smart.push`. Recipients:

- On a request or forward, every enabled system user who holds the waiting level's role,
  except the requester.
- On a decision, the requester.

## 8. UI: ERPNext desk and the CRM

**ERPNext desk:**

- **Quotation form:**
  - The discount and commission fields show "Your limit: …".
  - A headline shows each limited kind with a chip: Approved, Needs approval, Waiting for
    BDM, or Rejected.
  - *Adjustment → Request discount approval* opens a dialog with the % and a required
    reason.
  - *View request* opens the request while one is open.
- **TBO Quotation Adjustment form:**
  - *Decide → Approve / Reject / Forward / Override* appears only for the person the request
    is waiting for, with an intro that says whether it is within their limit.
  - *Open quotation*.
- **TBO Quotation Adjustment list:** status indicators (Waiting for BDM, Approved, Voided,
  …) and a **Waiting for me** button, which is the approvals inbox for BDM, Head, Director
  and CEO.

**In the CRM (tbocloud/crm#2, `docs/tbo-quotation-approvals.md` there):** daily users never
open `/app` for this.

1. **Deal → Quotations tab** (`QuotationsList.vue`): each quotation shows a chip per limited
   kind (Within limit, Needs approval, Pending with BDM, Approved 8%, Rejected, Overridden
   30%) and "Your limit: 5%" beside it, from `get_quotations_adjustment_status`. *Request
   approval* (draft quotations only) opens a dialog with the kind, the % and a required
   reason, and posts `request_adjustment`. *Trail* opens a side panel with the request's
   steps from `get_adjustment_trail`. Opening a quotation still goes to the ERPNext form,
   since creating and editing quotations in the CRM belongs to the finance plan's Phase 1.
2. **Approvals page** at `/crm/approvals`, in the sidebar only for approvers
   (`is_approver`) or users with open requests (`open_requests`). Tabs: *Waiting for me*
   (`get_pending_approvals`), *I requested* (`get_adjustment_requests("mine")`) and *All*
   (`get_adjustment_requests("all")`, Director and CEO only). Row actions post
   `decide_adjustment`: Approve (in full or a lower %), Reject and Forward with remarks,
   and Override (CEO, reason required).
3. **Refresh:** both screens reload on Frappe's `list_update` event for TBO Quotation
   Adjustment and when the window regains focus; the tab also reloads on the CRM's
   `crm_quotation_update` event. No CRM backend change was needed, and when tbo_smart is
   not installed the CRM hides all of it.

**In the TBO Smart app:** the same decisions on the phone for BDM, Sales & Marketing Head,
Director and CEO (capability `approve_quotations`), through the thin wrappers in
`tbo_smart/api/mobile/quotation.py` (contract `docs/tbo-smart-api-ess-crm.md` §5.2): *Waiting
for me* and *I requested*, approve in full or a lower %, reject, forward, CEO override with a
reason, and the trail. A notification about a request opens it in the app.

**Quotations on the lead** (`lead-pipeline.md` §9) use the same control: the lead's
Quotations tab shows the same chips and Request approval, a draft with an open request shows
*Pending Approval*, and *Mark sent* refuses it until the approval is granted.

## 9. What management must do after deploy

1. `bench --site tboindia.tbocloud.in migrate`, which runs
   `tbo_smart.patches.v1.setup_quotation_approval`. It creates the five roles, the
   Commission % field and the five default levels.
2. HR gives each salesperson their level's role (plus Sales User), and gives approvers
   their role.
3. In **TBO Quotation Approval Settings**:
   - choose **Limits apply to** (Discount / Commission / Both);
   - enter each level's max discount % and max commission %;
   - tick **Enforce adjustment limits**.
4. Tell the team: above your limit, use *Request approval* on the deal's Quotations tab in
   the CRM (or *Adjustment → Request approval* on the quotation); approvers use the CRM's
   **Approvals** page (or the **Waiting for me** list in the desk).

## 10. Open questions for management

1. **Exact limits** for BDE, BDM, Head and Director, for discount and for commission.
2. **Discount, commission or both?** The default is Both; change it in settings.
3. **What is commission based on?** Today it is a percentage on the quotation (Commission
   %). It is not yet copied to the Sales Order's `commission_rate`, which ERPNext fills
   from the Sales Partner. Should an approved commission carry over to the Sales Order?
4. **Who exactly approves:** anyone holding the next level's role (today), or the
   requester's own manager from the CRM Sales Hierarchy?
5. **Should the CEO's own adjustments** also be logged for a board review? They are logged
   as Applied today.
6. **Amended quotations:** should an approval carry over to an amendment with the same
   values? Today it does not.

## Changelog

- v1.1 (2026-10-12): APIs for the CRM screens (`get_quotations_adjustment_status`,
  `get_adjustment_requests`, `get_adjustment_trail`, access flags on
  `get_adjustment_limits`); §8 now describes the CRM screens.
- v1 (2026-10-12): levels and settings, Quotation enforcement, the approval record with its
  audit trail, the APIs, the desk UI and notifications.
