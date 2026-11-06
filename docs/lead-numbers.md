# Contact numbers of a lead

Leads arrive with one number. On a call the person often gives another (the decision-maker's, the
office's). A lead keeps all of them; nothing is overwritten. API: `hub-leads-api.md` §2.24.

## How to use (for telecallers and BDs)

- **They give you a new number on a call:** open the lead, tap **+ Add number**, type it, **Save**.
  The old number stays in the list below. Nothing else to do.
- **It is the number to call from now on:** tick **Make this the main number** before saving. The
  old number moves to **Old numbers**; it is never deleted.
- **After a call**, the result screen has **Got another number?**: the same form.
- **A number is wrong:** on the call's result pick **Wrong number**. The number is greyed out and
  the next number becomes the main one. The lead only becomes a Wrong Number lead when no number is
  left to try.
- **Which number to call:** each number has its own **Call** button. On Today's calls the main
  number is shown; **+1 more** lists the others.
- **Same number on another lead?** The form warns you and shows whose lead it is. You can still
  save (the decision-maker may have two businesses).
- Only a manager can remove a number. Everyone else marks it Old or Wrong number.

## What is stored

Each number is a row of the Lead's table `tbo_numbers` (child DocType **TBO Lead Number**):

| Field | Meaning |
|---|---|
| `number` | as typed; `digits` is the normalised international form (`normalise_phone`) |
| `label` | Mobile, Office, WhatsApp, Decision-maker, Other |
| `person`, `designation` | optional |
| `is_primary` | the main number: exactly one while the lead has any number |
| `status` | Active, Not reachable, Wrong number, Old. Active and Not reachable are *usable* |
| `source` | Import, Lead Bank, Added on a call, Manual |
| `added_by`, `added_on`, `note` | who, when, and why |

`mobile_no` on the Lead is always the main number, copied on every save, so lists, search,
reports and older code keep working. `phone` and `whatsapp_no` keep what they hold; a number typed
into either (or into `mobile_no`) becomes a row on the next save. Typing a different `mobile_no`
into a lead that has rows makes it the main number and keeps the old one as Old.

The leads search (`list_leads(search=…)`) also finds a lead by any of its rows, whatever the
status: when the search reads as a phone number (digits with spaces, `+`, `-` or brackets, at least
5 digits), it matches rows whose `digits` hold those digits, with a leading trunk 0 dropped
(`numbers.leads_with_digits`, at most 500 leads).

Code: `tbo_smart/sales/numbers.py`. Lead `validate` runs `numbers.sync`, `on_update` runs
`numbers.write_log` (the timeline notes).

## Rules

- **Never lost.** A new main number turns the old one into an **Old** row (a Wrong number stays
  Wrong number). Only a System Manager or Sales Manager removes a row (`remove_lead_number`).
- **Timeline.** Each add, change of main number, status change and removal is a note on the lead.
- **One main number.** If the main number becomes Wrong number or Old, the next usable number (in
  the order added) becomes the main one. If none is usable the lead keeps the last as main.
- **Wrong number outcome.** A call outcome Wrong number (Log call, a Today's calls item) marks the
  number called (default the main one). The lead becomes the Wrong Number stage (and a batch item
  flags it, `call-batches.md`) only when no usable number is left.
- **Duplicates.** `duplicates.find_duplicates` compares the last ten digits with every number row
  of every lead, whatever its status, besides the three fields.
- **Lookups by number** (MyOperator incoming calls `telephony.calls.find_lead`, WhatsApp
  `outreach.whatsapp.find_lead`, click-to-call, the Lead Bank's duplicate check) match any number
  of a lead.
- **Permissions.** Adding or changing a number needs write on the lead (a BD: their own lead).
  The rows are part of the lead, so they follow its permissions.

## Decisions

- **A table on the Lead, not ERPNext's Contact.** ERPNext makes one Contact per lead and its
  `phone_nos` rows have only a number and a main flag. We need status, label, source, who added it
  and a note per number, the duplicate check and call matching need one indexed place to read, and
  a Contact is a separate document with its own permissions (a BD who may write their lead may
  not write its Contact; the hub acts as that BD). A child table inherits the lead's permission
  and saves with it. The Contact is left as it is.
- **Old numbers stay.** The owner asked that the old number is still seen after a change; a
  Wrong number is kept so nobody adds it again unwarned.
- **Duplicates warn, they do not block, when adding a second number**: the same decision-maker may
  own two businesses. A new lead with a known number is still refused (`create_lead`).

## Migration

Patch `tbo_smart.patches.v1.lead_contact_numbers` (run by `bench migrate`) gives every lead without
rows one row per different number in its mobile, phone and WhatsApp fields (source Import, the
mobile first and main). It skips leads that have rows, so running it again changes nothing.

## Tests

`tbo_smart/tests/test_lead_numbers.py`: rows for new leads, the idempotent patch, adding, changing
the main number (old kept, logged), direct `mobile_no` edits, duplicates across all numbers,
lookups by any number, a BD cannot remove, wrong-number logic, a call on another number.
