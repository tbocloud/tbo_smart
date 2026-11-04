# Lead Bank

**Status:** built 2026-11-04. Contract: `hub-leads-api.md` §2.21. The hub's side (the screens, the
photo reading) is in the hub repo's `docs/lead-bank.md`. Referral commission: `commission.md` §7.

Every staff member can add a lead from a photo of a shop board, visiting card, banner or ad, and
earns a referral commission when that lead becomes a client and pays. Most collectors are not
salespeople, so it has to be one big button and a photo.

## 1. How a collector uses it (staff, short)

1. Open **Add a lead** (one tap on the phone home screen) and take a photo, or pick one from the
   gallery. Several photos are fine.
2. The form fills itself from the photo. Fix anything that is wrong. India, UAE or KSA is picked
   from the phone number or your location; change it if it is wrong.
3. Press **Submit**. If we already have the business it says so ("This business is already in our
   leads"); the first person to add a business keeps the credit.
4. **My leads** lists what you added: its date, where it stands (New, Being called, Meeting, Won,
   Not interested) and what you have earned or will earn.

## 2. What happens to a lead (decisions)

- **Made by the system, not the collector.** A designer has no CRM permissions, so the lead is
  inserted by the integration user with `tbo_collected_by` set; the collector never gets access to
  the lead. The leads API stays closed to anyone without a sales role; the Lead Bank methods take
  any enabled System User (§2.21).
- **Region** from the phone prefix (+91 / +971 / +966, or 00 for the plus, or a full number; a
  10-digit mobile starting 6 to 9 is India), else from the GPS fix (rough boxes: the UAE first,
  since the KSA's box overlaps it), else the collector picks. The collector's choice wins.
- **Department per region** (TBO Smart Settings, "Lead Bank: department for India / UAE / KSA").
  India falls back to a department called **TBO**. A department that is missing or switched off
  counts as none: the lead waits in the **unassigned pool** with no owner and no department, and
  the Sales Managers and TBO Management get a notification. A round robin department hands the lead
  to its next member; any other department keeps it unowned for its heads.
- **Country and state:** the country follows the region; the state (or emirate) is whatever the
  photo and the collector give, cleaned like any lead's state.
- **Source:** "Staff referral" (made on migrate).
- **Business type** is kept as text (`tbo_business_type`), and also sets the Industry when it is
  one of the Industry Types. The **address** is `tbo_address`; the GPS fix is the lead's place
  (§2.11 of the API) with a Google Maps link.
- **Duplicates** (`sales/duplicates.py`, the matching `find_duplicate_leads` uses): email, phone
  (last 10 digits) or company, over every lead. A company match alone counts here too, since the
  collector cannot judge a branch. Nothing of the matching lead is returned, only the plain
  sentence. The repeat is a comment on the existing lead; its photos are not added.
- **Statuses for the collector:** New Lead is New; Meeting Scheduled, the three meetings, Proposal
  Sent and Negotiation are Meeting; Closed – Won is Won; Closed – Lost, Not Interested and Wrong
  Number are Not interested; every other stage is Being called.
- **What a collector never sees:** the CRM, other leads, the owner, notes, quotations or money. The
  only money is their own `earned` and `pending`.

## 3. Referral

See `commission.md` §7. Rates India 2%, UAE 3%, KSA 3%; cap 25,000 per lead in the company
currency; a salesperson who owns or is assigned the lead they collected earns none.

## 4. Management

`get_dashboard` (managers: Sales Manager, System Manager, TBO Management): leads collected in the
month, by region and by collector (most first), how many are Won (the conversion is of the month's
leads), the referral earned, paid and due (also per collector), and the pool (up to 20 leads, with
the count). `reassign_credit` moves a lead's credit with a reason; earlier statements stay as they
were and the cap still counts what was paid.

## 5. What the owner sets up

1. Settings → CRM → Commission plans → **Referral (Lead Bank)** in the hub: the three rates, the
   cap and the department of India, UAE and KSA.
2. Make the **UAE** and **KSA** departments in the hub (Settings → CRM → Lead departments) when
   those teams exist, and pick them there. Until then those leads wait in the pool and managers are
   told.
3. Make sure every collector has a user on this site (the hub's CRM user sync does it).
4. The AI key for reading photos is on the hub (`../helpdesk` `docs/lead-bank.md`).

## 6. Fields on the Lead

`tbo_collected_by` (User), `tbo_collected_on`, `tbo_collected_region` (India / UAE / KSA),
`tbo_business_type`, `tbo_address`. On the statement line: `kind` (Sales / Referral) and `lead`;
on the statement `referral_commission`. TBO Smart Settings: `referral_rate_india`, `referral_rate_uae`,
`referral_rate_ksa`, `referral_cap`, `lead_bank_department_india` / `_uae` / `_ksa`.

## 7. Code

`tbo_smart/lead_bank/`: `__init__.py` (regions, detection, rates, departments), `access.py` (who
may call), `intake.py` (submit, duplicates, the pool), `referral.py` (the calculation),
`reports.py` (my leads, the dashboard, credit, settings). `api/hub_lead_bank.py` and
`api/hub_commission.py` are the thin API. The statement merge is `commission.add_referral`.

## 8. Not built

No automatic payout, no referral on leads without a region, no push to the collector when a lead
is Won (the hub shows it in My leads).

## 9. Tests

`tbo_smart/tests/test_lead_bank.py` (`test_utils.make_lead_bank_*`, `make_collected_lead`,
`make_lead_receipt`): region detection (prefix, leading zero, GPS, phone over GPS); a staff member
with no sales role adds a lead (fields, photos, source, department, Maps link); business type to
industry; the region choice; the pool and the manager notification; a department per region; the
refusals (no photo, no phone, unknown fields, bad photo, too many photos, unknown users); the
duplicate keeps the first collector and adds a comment; a collector sees only their own leads and
the leads API stays closed to them; the five statuses; managers-only dashboard, credit and
settings; the dashboard figures; referral by region on collected money; the cap across receipts;
not before Won or payment; a cancelled receipt; a sales owner or assignee earns none; statements
Draft, Approved, Paid; an approved month untouched; the next month pays the rest up to the cap;
moved credit does not reset the cap; My leads' earned and pending; the dashboard's referral due.
