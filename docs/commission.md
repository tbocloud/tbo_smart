# Sales commission

**Status:** built 2026-10-31. Contract: `hub-leads-api.md` §2.18. The hub shows it on Leads →
Commission, Settings → CRM → Commission plans and the project page.

Commission is a percentage on **money collected**, worked out per **project** and per month, with a
progressive slab scheme and a higher rate above a target. It is set up per project, or by rules.

## 1. What counts as collected

Customer receipts: Payment Entry, Receive, submitted, company currency. A receipt is followed to
the lead behind it by the Business overview's tracer (`BusinessOverview.traced_allocations`,
`hub-leads-api.md` §2.12): the Sales Order or Invoice it pays, then the deal or quotation line,
then the lead. This one tracer gives the lead, its owner, department and lead type. We do not trace
a second way.

- **Project of a receipt:** the `project` on the Sales Order or Sales Invoice it pays; failing that
  the hub project linked on the lead's documents (`TBO Proposal.hub_project`, set by
  `hub_proposals.link_project`; the latest if several).
- **Not allocated** to an order or invoice (an advance on account) is money with no lead: Unattributed.
- **Cancelled later:** a receipt counts when posted; if it is cancelled later it counts again,
  negatively, on the day it was cancelled. A refund (Payment Entry, Pay, to a customer) counts
  negatively on its posting date (negative again, positive, if cancelled). A past month is never
  rewritten; the correction lands in the month it happens. A month's net below zero earns nothing
  and is not carried forward.
- **Unattributed** (managers see it): money with no project under a project-basis plan ("No
  project"), or with no one to earn it ("No one earns": no lead owner and no project set-up).

## 2. Who earns, and by which scheme

1. **TBO Project Commission** (one per project, name = the project): earners with shares that add up
   to 100 (BD 70, head 30), and either a plan or the project's own slabs (`use_override`, with its
   own target and above-target rate). The project key is matched against the invoice's project and
   the lead's hub project, so a setup works whichever the invoices carry.
2. **No set-up, or no earners:** the owner of the lead earns 100%.
3. **No chosen plan or own slabs:** the **TBO Commission Plan** whose conditions all hold and that
   matches the most. Conditions are department (of the lead), lead type, project type and user; all
   optional. Weights: user 8, project type 4, lead type 2, department 1, so a plan for a person
   beats one for a project type, which beats one for a lead type, then a department. A plan with no
   conditions is the default. Two active plans cannot have identical conditions. With no plan the
   person earns nothing (the row says so).

## 3. The maths

Progressive, like tax brackets. Each slab's rate applies only to the part of the collection inside
that slab. Once the monthly target is reached, money above it earns the **above-target rate**
instead, where that is higher than its slab's rate (it never lowers a rate). A gap between slabs
earns nothing. A blank "to" means no upper limit.

**Basis** decides what goes through the slabs together:

- **Project collected** (default): each project's month on its own, and the target is per project.
  The project's commission is then split by the earners' shares.
- **Collected (all)**: the person's whole month (after shares, across projects) goes through once, and
  the target is per person. The commission is spread over the projects by what each credited.

**Worked example.** Slabs: 0 to 1,00,000 at 2%, 1,00,000 to 2,50,000 at 3%, over 2,50,000 at 4%.
Target 2,00,000, above-target rate 6%. A project collects 3,00,000 in the month:

| Part | Amount | Rate | Commission |
| --- | --- | --- | --- |
| 0 to 1,00,000 | 1,00,000 | 2% | 2,000 |
| 1,00,000 to 2,00,000 | 1,00,000 | 3% | 3,000 |
| 2,00,000 to 2,50,000 (above target; slab 3%, so 6%) | 50,000 | 6% | 3,000 |
| 2,50,000 to 3,00,000 (above target; slab 4%, so 6%) | 50,000 | 6% | 3,000 |
| **Total** | 3,00,000 | | **11,000** |

Without the target it is 2,000 + 4,500 + 2,000 = 8,500. With a 70/30 split the earners get 7,700
and 3,300.

## 4. Statements

One **TBO Commission Statement** per user per month: plans, credited collected, target, achievement
%, commission, status, a line per project (collected, share %, credited, commission), the slab
breakdown rows and the payments. Draft until a manager approves it (Sales Manager, System Manager,
TBO Management; nobody approves their own except a System Manager), then Approved, then Paid.

- `generate_statements(month)` creates or refreshes Drafts for everyone with money in the month and
  never touches Approved or Paid ones; a scheduler job does it on the 1st at 03:00 for the month
  that just ended.
- The live view (`get_commission`) always recomputes Drafts; an Approved or Paid statement is shown
  as approved. The record itself refuses edits to its figures and cannot be deleted once approved.

## 5. Decisions made (owner away)

Progressive slabs; marginal above-target rate; per-project-per-month basis by default; the month's
net floors at zero with no carry-forward; refunds count negatively; the lead's owner is the
default earner; plans match on the lead's department, not the earner's membership; a person with no
matching plan earns nothing; nobody but a System Manager approves their own; Drafts are only created
for people with money in the month. The sales activity targets in the daily report are unrelated to
this money target.

## 6. What the owner sets up

1. Settings → CRM → Commission plans: at least one default plan (no conditions) with slabs, a target and
   the above-target rate; add plans for a department, lead type, project type or person.
2. Set `project` on Sales Orders and Sales Invoices, or link the hub project to the lead's
   documents. Money with no project is Unattributed under project-basis plans.
3. On a project's page, set who earns and the split where it is not just the lead's owner.
