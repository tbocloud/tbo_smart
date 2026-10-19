# Client documents after the proposal (living spec, v1.0)

**Status:** built 2026-10-19 (the owner's sales-to-delivery chain of 2026-10-19), step 1 of 5:
the **Quotation** and the **Project Agreement / Work Order**. The BRD, the SRS, the
implementation plan and the UAT sign-off follow in their own pull requests and are added here
when they are built. The proposal itself is `proposals.md`; the hub's contract is
`hub-proposals-api.md`; the hub's side is tbocloud/helpdesk `docs/proposals.md` §Documents.

The chain, one document after the next, each made from the accepted one before it:

**Proposal → Quotation → Project Agreement (Work Order)** → (BRD → SRS → Plan → Project → UAT)

- **Code:** `lead_pipeline/document_types.py` (the types: sections, headings, builders, words),
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

| | Proposal | Quotation | Work Order |
| --- | --- | --- | --- |
| Name | `PROP-YYYY-#####` | `QUOT-YYYY-#####` | `WO-YYYY-#####` |
| Switch (TBO Smart Settings) | Proposals | **Quotations** (`enable_quotation_documents`) | **Project agreements** (`enable_work_orders`, needs Quotations) |
| Made from | the lead (`proposals.md` §3) | the accepted proposal | the accepted quotation |
| Its price | its own Quotation | its own Quotation | the accepted quotation's, read only |
| AI | the narrative (`proposals.md` §4.2) | none | none |
| Contents page | yes | no | yes |
| The client | accepts / declines | accepts / declines | **signs** (name, designation, email) / asks for changes |

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

## 5. The chain on the lead (`client_documents.chain`)

`hub_leads.get_lead` returns `documents` (and `hub_proposals.get_document_chain(lead)` the same):

```
{steps: [{document_type, label, name, title, status, status_label (Signed for an accepted
  agreement), done, view_count, quotation_status (the ERPNext Quotation's, for a priced one)}],
 next: null | {action: "create_proposal" | "create" | "open", document_type, label,
  source (create: the accepted document to make it from), name (open)}}
```

One step per switched-on type, in order, each the lead's accepted document of that type, else its
newest latest revision. `next` is the one primary action: the first step in progress (open it),
else make the next document from the accepted one, else none (the chain is done).

## 6. What the client sees

The same page and PDF (`proposals.md` §5) with the type's own words (`document_types.words`):
the cover's kicker (Quotation, Project Agreement / Work Order), the decision panel (Accept
quotation / Sign the agreement, Decline / Ask for changes), the notices (Accepted / Signed /
Changes asked for), the acceptance sentence, the share card ("Agreement with <client> from
<company>"), the email's subject, greeting and link. Nothing else changes: no emails, owners,
notes or internal names; the token is the only key; preview, print and PDF as for a proposal.

## 7. What the owner sets up

1. Deploy and `bench migrate` (the fields, the section types, the Agreement Clause library).
2. TBO Smart Settings → **Client documents after the proposal**: **Quotations**, then **Project
   agreements (work orders)**. Both are off by default.
3. On the ERP site: a **default Sales Taxes and Charges Template** for the company (GST), and in
   TBO Smart Settings the quotation **terms** and **payment terms template**, so every quotation
   gets them.
4. Have the **Agreement Clause** texts reviewed by TBO's lawyer (hub Settings → Rates and
   proposals → library, or the desk).
5. The company's **address and GSTIN** on the Company (the agreement's Parties).

## 8. Tests

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
the source-code clause follows the scoping; a revision stays an agreement.

## Changelog

- **v1.0 (2026-10-19):** one engine for every client document; the Quotation and the Project
  Agreement / Work Order; the chain on the lead.
