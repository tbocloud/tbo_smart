# Lead departments (living spec, v1.1, Phase 1 and 2)

Leads belong to a department (GrowthX and the like) that admins create. The department's
**heads** see every lead of it; the people who follow a lead (its owner, or anyone it is
assigned to) see theirs. Each lead is also one of two types: **ERP / Software** or
**Content / Digital Marketing**. Phase 1 is the data, the visibility rule and the hub API; the
hub's screens come after.

## 1. Data

- **TBO Lead Department** (`tbo_smart/tbo_smart/doctype/tbo_lead_department`, named by
  `department_name`, unique): `lead_type` (required), `is_active` (default on), `heads` and
  `members` (tables of **TBO Lead Department User**: `user`, `full_name`). System Manager and
  Sales Manager: full; Sales User: read. A person is listed once per table; no rename.
- **Lead** custom fields (`lead_pipeline/setup.py`, after `tbo_segment`, ERPNext sites only):
  `tbo_department` (Link, in list and standard filter) and `tbo_lead_type` (Select; fetched
  from the department's type only while empty, so it can be set to the other type).

## 2. Who sees a lead (`sales/permissions.py`, `sales/departments.py`)

Every existing rule stays (docs/erpnext-lead-cutover.md §3). On top of the owner/assignee/
hierarchy rule, **a head of an active department also sees every lead whose `tbo_department` is
one of theirs**, in the list (`permission_query_conditions`, one subquery, no per-row query) and
in `has_permission`. Members who are not heads get nothing extra. A lead without a department,
and a department switched off, behave as before. System Manager, Administrator and a Sales
Manager outside the hierarchy still see everything.

## 3. Validation (`sales.departments.validate_lead_department`, Lead validate)

- A lead may be put in a department only while it is active (checked when the department
  changes, so a lead stays editable after its department is switched off).
- When the department has any heads or members, `lead_owner` must be one of them (checked when
  the department or the owner changes).

## 4. Hub API (`tbo_smart.api.hub_leads`, same `acting_user` rules)

- `get_options`: adds `departments` (active only: `{name, lead_type, is_active, is_head,
  is_member}` for the acting user) and `lead_types`.
- `list_leads(department=...)`: filters to the department; rows carry `tbo_department` and
  `tbo_lead_type`. `get_lead`'s `lead` carries the same two.
- `create_lead` / `update_lead`: `data` takes `department` (or `tbo_department`) and `lead_type`
  (or `tbo_lead_type`).
- `get_lead_departments` -> `[{name, department_name, lead_type, is_active, heads, members}]`
  for any lead user (heads and members are `[{user, full_name}]`, inactive departments included).
- `save_lead_department(values, name=None)` (POST): `values` has `department_name` (new only),
  `lead_type`, `is_active`, `heads`, `members` (lists of emails, replacing the old list).
  Returns the department. `set_lead_department_active(name, active)` (POST). Both need Sales
  Manager or System Manager (PermissionError otherwise).

Not in Phase 1: the overview, daily report and mobile scopes still follow the owner scope, not
the department.

## 4a. Phase 2: the type decides the documents

An ERP / Software lead (or one without a type) keeps the full chain: Proposal, Quotation,
Project Agreement, BRD, SRS, Implementation Plan, then the project. A **Content / Digital
Marketing** lead goes Lead -> a 6-month **strategy proposal** (the preset "Digital Marketing –
6-month strategy", used by default for a new proposal on such a lead) -> once the client accepts,
the project (the hub links it with `link_project` on the proposal and reads
`get_strategy_project`). The credentials handover and the content calendar approvals are the
hub's. Details and the data-driven table: `lead-pipeline.md` §13.

## 5. Tests

`tbo_smart/tests/test_lead_departments.py`: a head sees the department's leads they do not
own, a member only their own, a lead without a department is unchanged, a department switched
off opens nothing, the lead type default and override, an inactive department is refused, the
owner must be one of the department's people, the options, the list filter, create through the
hub, and who may save departments.

`tbo_smart/tests/test_client_documents.py` (`TestContentLeadChain`): a content lead gets the strategy preset by default, its chain after the accepted proposal is the project, an ERP lead's chain is unchanged, and `get_strategy_project` / `link_project` on the proposal.
