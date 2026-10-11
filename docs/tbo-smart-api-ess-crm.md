# TBO Smart API: ESS, CRM and manager (tboindia side), v1

This is the contract for the TBO Smart mobile app's **tboindia.tbocloud.in** half: HR
self-service (HRMS), CRM (Frappe CRM) and the manager views. The hub (TBO Support) has its
own contract in `tbocloud/helpdesk`.

- **Code:** `tbo_smart/api/mobile/{ess,crm,manager,device}.py` and `tbo_smart/push.py`.
- **Fixtures:** `docs/fixtures/<module>.<method>.json`. Each one is the full HTTP body that
  Frappe returns (`{"message": ...}`).
- **Status:** v1, 2026-10-10. Any change gets a version note in the changelog at the end of
  this file.

## 1. Conventions

| Item | Rule |
| --- | --- |
| Base URL | `https://tboindia.tbocloud.in/api/method/tbo_smart.api.mobile.<module>.<method>` |
| Auth | `Authorization: Bearer <access_token>` (OAuth, see §2). There are no API keys or passwords in the app. |
| Identity | Always the session user. No method accepts a user or employee id to act as. |
| Reads | `GET` with query parameters (POST also works). |
| Writes | `POST` with a JSON body. Writes are refused over GET. |
| Lists | `page` (1-based, default 1) and `page_length` (default 20, max 100). The response is `{"data": [...], "page": n, "has_more": bool}`. Short, bounded lists (options, holidays, approvals, team, follow-ups) return a bare array. |
| Dates | `YYYY-MM-DD`. Datetimes are `YYYY-MM-DD HH:MM:SS[.ffffff]` in the **site time zone** (Asia/Kolkata), with no offset. |
| Money | Numbers in the document's currency. Sales snapshot values are in the CRM base currency. |
| Errors | HTTP 4xx/5xx with Frappe's body: `exc_type` plus `_server_messages`, a JSON string holding a list of JSON strings, each `{message, title}`. Map them as shown below. |

Error mapping for the app:

| `exc_type` | HTTP | App `code` | Meaning |
| --- | --- | --- | --- |
| `NoEmployeeError` | 404 | `no_employee` | No active Employee has `user_id` = this user. HR must link one. |
| `PermissionError` | 403 | `forbidden` | Not your document, not your role, or a setting is off. |
| `DuplicateEntryError` | 409 | `duplicate` | For example, a second punch within 60 s. |
| `ValidationError` and subclasses (`CheckinRadiusExceededError`, `OverlapError`, `InsufficientLeaveBalanceError`, …) | 417 | `invalid` | Show the message. |
| `RateLimitExceededError` | 429 | `rate_limited` | Retry later. |
| `DoesNotExistError` | 404 | `not_found` | |

See `fixtures/error.no_employee.json` and `fixtures/error.permission.json`.

## 2. Authentication (OAuth 2.0 Authorization Code + PKCE)

### 2.1 What the owner creates on tboindia (once)

1. Go to **/app/oauth-client/new**.
2. Fill in the form:
   - **App Name:** `TBO Smart`
   - **Grant Type:** `Authorization Code`
   - **Response Type:** `Code`
   - **Skip Authorization:** ticked (staff app; no consent screen)
   - **Scopes:** `all openid` (space separated)
   - **Redirect URIs:** `tbosmart://oauth/callback`
   - **Default Redirect URI:** `tbosmart://oauth/callback`
   - **Allowed Roles:** add `Employee` so that only staff can sign in. Leave it empty to allow every user.
3. Save. Give the **Client ID** to the app build.
   - The **Client Secret** is not used: the app is a public client and must not embed it.
4. With the client's **Skip Authorization** ticked, there is no consent screen whatever **OAuth Provider Settings** says (`frappe/integrations/oauth2.py` `authorize`).

### 2.2 Endpoints

| Step | Method | URL |
| --- | --- | --- |
| Authorize | GET | `/api/method/frappe.integrations.oauth2.authorize?client_id=…&response_type=code&redirect_uri=tbosmart://oauth/callback&scope=all%20openid&state=…&code_challenge=…&code_challenge_method=S256` |
| Token | POST form | `/api/method/frappe.integrations.oauth2.get_token` with `grant_type=authorization_code`, `code`, `redirect_uri`, `client_id`, `code_verifier` |
| Refresh | POST form | same URL with `grant_type=refresh_token`, `refresh_token`, `client_id` |
| Revoke (logout) | POST form | `/api/method/frappe.integrations.oauth2.revoke_token` with `token` (and `token_type_hint=refresh_token` for the refresh token) |
| Profile | GET | `/api/method/frappe.integrations.oauth2.openid_profile` (Bearer) |
| Discovery | GET | `/api/method/frappe.integrations.oauth2.openid_configuration` |

- Access tokens last **3600 s** (oauthlib default). A refresh token comes with each access
  token.
- On sign-out, call `unregister_device` (§6), then revoke the refresh token.

### 2.3 PKCE on Frappe v15: supported, not enforced

These findings come from reading `frappe/oauth.py` in Frappe 15.121.

- **Supported.**
  - `save_authorization_code` stores `code_challenge` and `code_challenge_method` (lowercased).
  - `validate_code` checks the verifier for `s256` (base64url SHA-256, no padding) and for `plain`.
  - If a challenge was sent and the token call has no `code_verifier`, the code is deleted
    and the exchange fails.
  - Send `code_challenge_method=S256`.
- **Not enforced.** No setting makes PKCE mandatory. A client that omits `code_challenge`
  still gets a token. The app must always send it.
- **Client secret not checked.** `authenticate_client` loads the client by `client_id`
  alone and never compares a secret. So the redirect URI plus PKCE are the real protection.
  Keep the redirect list to exactly `tbosmart://oauth/callback`.

### 2.4 Test with curl

```bash
# 1. Open the authorize URL in a browser signed in as a staff user; copy `code` from the redirect.
# 2. Exchange it:
curl -X POST https://tboindia.tbocloud.in/api/method/frappe.integrations.oauth2.get_token \
  -d grant_type=authorization_code -d code=$CODE -d client_id=$CLIENT_ID \
  -d redirect_uri=tbosmart://oauth/callback -d code_verifier=$VERIFIER
# 3. Call the API:
curl -H "Authorization: Bearer $ACCESS" \
  https://tboindia.tbocloud.in/api/method/tbo_smart.api.mobile.ess.get_me
```

## 3. ESS: `tbo_smart.api.mobile.ess`

Every ESS method needs an **active Employee linked to the user** (`Employee.user_id`).
Without one, it fails with `NoEmployeeError`. Reads of your own documents go through
`frappe.get_list` with an explicit "own employee" filter, so DocType and User Permissions
still apply.

| Method | Verb | Params | Returns | Reuses |
| --- | --- | --- | --- | --- |
| `get_me` | GET | – | Employee, plus `is_manager` (has active direct reports), `is_approver`, `is_sales_manager` | – |
| `check_in` | POST | `log_type` (`IN`/`OUT`), `latitude?`, `longitude?`, `accuracy?` (m) | the punch | HRMS `Employee Checkin` controller (shift, geofence) |
| `get_today` | GET | – | `{date, server_time, shift_name, shift_start, shift_end, status, attendance, checkins[]}` | `hrms…shift_assignment.get_employee_shift` |
| `get_attendance` | GET | `year`, `month` (1–12) | `[{date, status}]` | `hrms.api.get_attendance_calendar_events` |
| `get_holidays` | GET | `year?` (default: this year) | `[{date, description}]` (weekly offs excluded) | `hrms.api.get_holidays_for_employee` |
| `get_leave_balances` | GET | – | `[{leave_type, allocated, used, pending, balance}]` | `hrms…leave_application.get_leave_details` |
| `get_leave_types` | GET | – | `["Casual Leave", …]` (allocated types plus LWP) | `hrms.api.get_leave_types` |
| `get_leave_approver` | GET | – | `{leave_approver, leave_approver_name, is_mandatory}` | `hrms.api.get_leave_approval_details` |
| `get_leave_applications` | GET | `page`, `page_length` | page of LeaveApplication | `frappe.get_list` |
| `apply_leave` | POST | `leave_type`, `from_date`, `to_date`, `half_day?`, `reason?`, `half_day_date?` | the new LeaveApplication, with `approver` | Leave Application controller |
| `cancel_leave` | POST | `name` | `{name, status: "Cancelled", withdrawn}` | Leave Application controller |
| `get_expense_types` | GET | – | `["Travel", …]` | `hrms.api.get_expense_claim_types` |
| `get_expense_claims` | GET | `page`, `page_length` | page of ExpenseClaim | `frappe.get_list` |
| `create_expense_claim` | POST | `expense_type`, `amount`, `expense_date`, `description?`, `receipt_filename?`, `receipt_base64?` | the new ExpenseClaim | `hrms.api.get_expense_approval_details`, `get_company_cost_center_and_expense_account`, `upload_base64_file` |
| `get_salary_slips` | GET | `page`, `page_length` (default 12) | page of SalarySlip, each with `pdf_url` | `frappe.get_list` |
| `download_salary_slip` | GET | `name` | the PDF file (`application/pdf`) | `frappe.utils.print_format.download_pdf` |

Fixtures: `ess.<method>.json`.

### 3.1 Details

- **`check_in`**
  - The server records `time = now` in site time. The app never sends a time.
    - For the Employee role, HRMS also resets `time` (permlevel 1) to its "Now" default.
  - It is refused (`PermissionError`) unless **HR Settings → Allow Employee Checkin from Mobile App** is ticked.
  - It is refused (`DuplicateEntryError`) if the employee already punched in the last 60 s.
  - The rate limit is 60 calls a minute per IP. It is generous because an office shares one IP.
  - When **HR Settings → Allow Geolocation Tracking** is on:
    - HRMS requires latitude and longitude, and stores them plus a GeoJSON `geolocation`.
    - If the employee's active Shift Assignment has a **Shift Location** with a radius, HRMS
      refuses punches outside it (`CheckinRadiusExceededError`). That is the geofence; set it
      per shift location, not in this app.
  - HRMS has no accuracy field. The accuracy is kept readable in **Location / Device ID**
    as `TBO Smart ±12 m`, and returned as `location`.
- **`get_today.status`** is `not_checked_in`, `checked_in` or `checked_out`, from the last punch.
  `attendance` is today's submitted Attendance status, or `null`. Use `server_time` to show
  clock drift.
- **`get_attendance`** returns only the days that have a submitted Attendance record or a
  holiday.
  - Status is one of `Present`, `Absent`, `On Leave`, `Half Day`, `Work From Home`,
    `Holiday`, `Weekly off`.
  - `Weekly off` is a holiday-list row with **Weekly Off** ticked.
- **`apply_leave`**
  - Creates an **Open** draft for the session employee.
  - `leave_approver` comes from Employee → Leave Approver, falling back to the first Department
    Leave Approver, the same rule as HRMS.
  - HRMS validates balance, overlap, block days and the rest; its message comes back as a
    `ValidationError`.
  - `half_day_date` defaults to `from_date`.
- **`cancel_leave`** works on your own application only.
  - **Open draft:** it is withdrawn, which means deleted. This needs the **Delete** permission
    on Leave Application, which the Employee role does **not** have by default. See §7.
  - **Approved (submitted):** it is cancelled through `doc.cancel()`. This needs **Cancel**,
    which Employee also lacks by default.
  - Anything else is refused.
- **`create_expense_claim`**
  - Creates a **draft** claim with one line.
  - `expense_approver` comes from Employee or Department. The payable account and cost
    center come from the company defaults.
  - The receipt is base64 and goes through HRMS `upload_base64_file`:
    - the file is **private**;
    - only JPG, PNG, PDF, TXT and Office files are allowed;
    - JPEGs are auto-rotated;
    - the site's maximum file size applies.
  - The claim and the file are saved in one request, so a failed upload leaves no claim.
- **Expense `status`**:
  - `Paid` when the status is Paid;
  - `Approved` or `Rejected` from the approval status;
  - otherwise `Pending`.
- **Salary slips** are **own only**.
  - The list is filtered to the session employee and `docstatus = 1`.
  - `download_salary_slip` checks that the slip's employee is the session employee
    **before** rendering. Any other name gets `PermissionError`, the same as a missing one.
  - It then uses Frappe's `download_pdf`, which checks read/print permission again.
  - We do **not** reuse `hrms.api.download_salary_slip`, because it has no ownership check.

## 4. Manager: `tbo_smart.api.mobile.manager`

| Method | Verb | Params | Returns | Who may call |
| --- | --- | --- | --- | --- |
| `get_team_today` | GET | – | `[{id, employee, employee_name, image, department, designation, attendance, checked_in_at, leave_type}]` | Employees with at least one **active direct report** (`Employee.reports_to`). Others get `PermissionError`. |
| `get_approvals` | GET | – | `[Approval]`, newest first | Users with **Leave Approver** or **Expense Approver**. Others get `PermissionError`. |
| `decide_approval` | POST | `kind` (`leave`/`expense`), `name`, `approve` (bool), `reason?` (required to reject) | `{kind, name, status, docstatus}` | The document's named approver, or a workflow role (below) |
| `get_sales_snapshot` | GET | – | `{currency, by_stage[], pipeline, weighted, won_this_month{count, amount}, closing_this_week[]}` | **Sales Manager** or **System Manager**. Others get `PermissionError`. |

- **`get_team_today`**
  - `attendance` is `leave` (an approved, submitted Leave Application covers today), `in`
    (an IN punch today) or `not_yet`.
  - It reads other employees' check-ins, so it is limited to the caller's own direct reports.
  - It returns only the first IN time, never coordinates.
  - `id` is the member's **user email**, the cross-site identity (Employee name if no user).
- **`get_approvals`** reuses the HRMS PWA queries: `hrms.api.get_leave_applications` and
  `get_expense_claims` with `for_approval=True`.
  - **No workflow:** drafts where I am the approver (`leave_approver` or `expense_approver`),
    Open leave and Draft claims, never my own.
  - **With a Workflow on the doctype:** documents in the states my roles can move.
- **`decide_approval`** follows the HRMS path. It never writes the status directly.
  - **No workflow:**
    - Only the named approver may decide; others get `PermissionError`.
    - **Leave:** sets `status` to Approved or Rejected, then `submit()`. HRMS then creates the
      leave ledger, updates attendance and notifies the employee.
    - **Expense:** sets `approval_status`, fills `sanctioned_amount` = `amount` on approved
      lines, then `submit()`.
      - An approved submit **posts GL entries**, so the claim needs a payable account.
      - Rejected claims post nothing.
  - **Workflow active:** it applies the transition open to me whose action or next state
    contains "approv" or "reject". If there is none, `PermissionError`.
  - A `reason` is added to the document as a comment: `Approved: …` or `Rejected: …`.
- **`get_sales_snapshot`**
  - Values are `deal_value × exchange_rate` in the FCRM base currency, the same formula as
    Frappe CRM's dashboard.
  - Open stages are every CRM Deal Status whose type is not Won or Lost, in CRM position order.
  - `weighted` sums value × probability.
  - `won_this_month` counts Won-type deals with `closed_date` in this calendar month.
  - `closing_this_week` lists open deals with an expected closure on or before today + 7.
    That includes overdue ones (`overdue: true`), up to 50.
  - A Sales Manager sees every deal, as in CRM.

## 5. CRM: `tbo_smart.api.mobile.crm`

Lists use `frappe.get_list`, so Frappe CRM's own rules decide visibility:

- a **Sales User** sees the leads and deals they own or that are assigned to them;
- the **sales hierarchy** extends that to their team when it is on;
- **Sales Manager** and **System Manager** see everything.

Writes are normal inserts and saves under the session user.

| Method | Verb | Params | Returns |
| --- | --- | --- | --- |
| `get_options` | GET | – | `{lead_sources[], lead_statuses[{name,type}], deal_stages[{name,type,probability}], lost_reasons[]}` |
| `get_leads` | GET | `status?`, `search?`, `include_converted?` (default false), `page`, `page_length` | page of Lead |
| `get_lead` | GET | `name` | Lead plus `notes[]`, `tasks[]`, `activities[]` |
| `create_lead` | POST | `lead_name`, `organization?`, `email?`, `mobile_no?`, `source?`, `note?` | the new Lead (owner = me) |
| `set_lead_status` | POST | `name`, `status` | `{name, status}` |
| `convert_lead` | POST | `name` | the Deal (an already-converted lead returns its existing deal) |
| `get_deals` | GET | `stage?`, `mine?`, `search?`, `page`, `page_length` | page of Deal |
| `get_deal` | GET | `name` | Deal plus `erpnext_customer`, `stage_history[]`, `notes[]`, `tasks[]`, `activities[]` |
| `move_deal_stage` | POST | `name`, `stage`, `lost_reason?` (required for a Lost-type stage) | the Deal |
| `get_organizations` | GET | `search?`, `page`, `page_length` | page of Organization |
| `get_organization` | GET | `name` | Organization plus `contacts[]` and `deals[]` |
| `get_my_follow_ups` | GET | – | `[FollowUp]`: my open CRM Tasks due today or overdue, oldest first |
| `log_activity` | POST | `doctype` (`lead`/`deal`), `name`, `kind` (`call`/`note`/`meeting`/`email`/`whatsapp`), `text`, `next_follow_up?`, `phone?` | the record's `{notes, tasks, activities}` |
| `add_follow_up` | POST | `doctype`, `name`, `due`, `title?` | `{id, subject, due, status}` |
| `set_task_done` | POST | `name` (CRM Task), `done` (bool) | `{id, status, done}` |

- **Reuse:**
  - `convert_lead` uses CRM's `convert_to_deal`, after a write-permission check and an
    existing-deal check.
  - Timelines use CRM's `get_linked_notes` and `get_linked_tasks`.
  - Meeting, email and WhatsApp entries use CRM's `crm.api.comment.add_comment`.
- **`log_activity`** maps each kind to CRM's own records:
  - `call` → a **CRM Call Log** (Manual, Outgoing, Completed) with an FCRM Note holding the text.
    - CRM requires both numbers. `to` is `phone`, or else the record's mobile.
    - `from` is the caller's **User → Mobile No** (or else Employee → Cell Number).
    - If either is missing, it fails with a clear message.
  - `note` → **FCRM Note**.
  - `meeting` / `email` / `whatsapp` → a CRM **comment** `Meeting: …`. Frappe CRM has no
    doctype for these, and they come back as `kind: "comment"`.
  - A `call` or `meeting` marks my open tasks on that record that are due by now as **Done**,
    because the follow-up was answered.
  - `next_follow_up` (a date or datetime) adds a **CRM Task** assigned to me.
- **`move_deal_stage` with a Lost-type stage:**
  - a reason equal to a **CRM Lost Reason** is used as is;
  - free text is saved as `lost_reason = "Other"` with the text in `lost_notes`, which is what
    CRM validation requires.
  - The response's `lost_reason` shows the notes when set.
- **Activities** are calls, notes not attached to a call, and comments, merged newest first.
  - For a deal converted from a lead, the lead's own history is **not** merged in (v1).
- **Organizations** are **CRM Organizations**.
  - **ERPNext Customer link:** Frappe CRM links a Customer to a **Deal**
    (`CRM Deal.erpnext_customer` / `Customer.crm_deal`) when the ERPNext integration creates
    it, which happens on a won deal. So `erpnext_customers` lists the customers on the
    organization's deals that you can read. There is no direct Organization → Customer link
    in CRM.
  - **AMC / support hours are not included.** No AMC data exists on tboindia (ERPNext has no
    AMC doctype and none is installed locally). The AMC hours live on the hub. The app
    should take them from the hub's customer API.
  - **Customer health is not included** either. It has no source on tboindia.
- **Follow-ups** are CRM Tasks with `assigned_to` = me, a status other than Done or Canceled,
  and `due_date` ≤ end of today. `kind` is always `"task"`, since CRM tasks have no
  call/meeting type.

## 6. Devices and push: `tbo_smart.api.mobile.device` and `tbo_smart.push`

| Method | Verb | Params | Returns |
| --- | --- | --- | --- |
| `register_device` | POST | `token` (Expo push token), `platform` (`android`/`ios`), `app_version?` | `{name, platform, last_seen}` |
| `unregister_device` | POST | `token` | `{removed}` |

- **DocType `TBO Mobile Device`**
  - Fields: `user`, `platform`, `expo_push_token` (unique), `app_version`, `last_seen`.
  - Permissions: System Manager full; **Desk User** (every system user, never website users) **if owner**.
  - Registering is idempotent and refreshes `last_seen`. Call it on every app start.
  - A token already registered to another user moves to the new user, because the phone
    changed hands.
  - The rate limit is 60 calls a minute per IP.
- **Single `TBO Smart Settings` → Mobile push** (`enable_mobile_push`) defaults to **off**.
  - While it is off, nothing is sent.
- **Triggers** (`hooks.py` `doc_events`, `after_insert`):
  - **Notification Log**: Frappe assignments, mentions, shares, alerts and CRM notifications.
  - **PWA Notification**: HRMS writes leave and expense approval requests and decisions here,
    **not** to Notification Log, so this hook is what delivers "leave approved/rejected" and
    "new leave to approve".
- **Sending**
  - The hook only enqueues: queue `short`, after commit.
  - The background job makes one POST to `https://exp.host/--/api/v2/push/send` with one
    message per device, and a 10 s timeout.
  - Tickets with `DeviceNotRegistered` delete that device.
  - Every failure goes to **Error Log** ("TBO Smart push: …") and never breaks the insert.
- **Payload** `data`: `{"site": "ess", "doctype", "name", "route"}` (`fixtures/push.expo_message.json`).
  - `title` is the notification subject without HTML; `body` is its text, cut to 180 characters.
- **Routes:**

| Document | Route |
| --- | --- |
| Leave Application (to the employee) | `/hr/leave` |
| Leave Application (to the approver) | `/team/approvals?kind=leave` |
| Expense Claim (to the employee) | `/hr/expenses` |
| Expense Claim (to the approver) | `/team/approvals?kind=expense` |
| Salary Slip | `/hr/salary` |
| Employee Checkin, Attendance | `/hr/attendance` |
| CRM Lead | `/crm/lead/<name>` |
| CRM Deal | `/crm/deal/<name>` |
| CRM Organization | `/crm/org/<name>` |
| Anything else | `/alerts` |

## 7. Roles and permissions to check on tboindia before launch

1. **HRMS and CRM are installed.** `tbo_smart` declares
   `required_apps = ["erpnext", "hrms", "crm"]`. Confirm with
   `/api/method/frappe.utils.change_log.get_versions`.
2. **Employee ↔ User.** Every staff user needs an **active Employee** with `user_id` set.
   - Keep **Create User Permission** ticked on each Employee.
   - The Employee role can read **every** Salary Slip, Leave Application, Expense Claim and
     Employee Checkin unless a **User Permission on Employee** narrows it.
   - The app's own endpoints filter to the session employee anyway, but `/api/resource`
     would not.
3. **HR Settings:**
   - **Allow Employee Checkin from Mobile App:** on, or `check_in` refuses.
   - **Allow Geolocation Tracking:** on to store coordinates and enforce shift-location radii.
   - Check **Leave Approver Mandatory** and **Expense Approver Mandatory**.
4. **Approvers.** Set **Leave Approver** and **Expense Approver** on each Employee or
   Department.
   - HRMS gives those users the **Leave Approver** and **Expense Approver** roles; check them.
   - Managers need **Employee → Reports To** set on their team for `get_team_today`.
5. **Withdrawing leave from the app.** The Employee role has no Delete or Cancel on Leave
   Application, so `cancel_leave` returns 403. Pick one:
   - grant **Employee → Delete, If Owner** on Leave Application (Role Permission Manager), or
   - keep it HR-only and hide the button in the app.
6. **Expense approval posts GL entries on submit.** Each company needs **Default Expense
   Claim Payable Account** and a cost center, and each Expense Claim Type needs a default
   account. If approvers should approve without posting, add a **Workflow**;
   `decide_approval` follows it.
7. **CRM.**
   - Sales staff need **Sales User**; heads need **Sales Manager** for `get_sales_snapshot`.
   - Turn on **FCRM Settings → Sales hierarchy** if team leads should see their team's deals.
   - Set **FCRM Settings → Currency** for the snapshot.
   - Fill in **User → Mobile No** for anyone who logs calls.
8. **OAuth Client** as in §2.1. Restrict **Allowed Roles** to `Employee`.
9. **Push.** Tick **TBO Smart Settings → Mobile push** only when the app build with Expo
   push ships. The workers (`short` queue) must be running.

## 8. Mismatches with the app (`tbo_smart_mobile/src/api`)

| # | App today | Backend | Who changes |
| --- | --- | --- | --- |
| 1 | Method names `tbo_smart.api.get_me`, `hrms.api.*`, `crm.api.*` | Everything is under `tbo_smart.api.mobile.{ess,crm,manager,device}`. Façades keep the shapes stable even when HRMS or CRM change. | **App** (comments and `client.ts`) |
| 2 | Lists are bare arrays (`Lead[]`, `LeaveApplication[]`, …) | Paged lists return `{data, page, has_more}` | **App**: unwrap `data` in the client and add paging |
| 3 | `checkIn` → `{log_type, time, location}` | Also `name`, `latitude`, `longitude`. `location` is `TBO Smart ±12 m` (no office name) | **App**: format the location; optionally reverse-geocode |
| 4 | `Today.shift_*` are strings | Can be `null` with no shift. Extra `date`, `server_time`, `attendance` | **App**: handle null |
| 5 | `AttendanceStatus` lacks `Half Day`, `Work From Home` | HRMS returns them | **App**: widen the type |
| 6 | `LeaveBalance` has `allocated, used, balance` | Also `pending` | **App** (optional field) |
| 7 | `LeaveApplication.approver` is a name | `approver` (full name) plus `approver_id` (email), `half_day_date`, `posting_date` | **App** (additive) |
| 8 | `cancelLeave` sets status Cancelled | Open drafts are **withdrawn (deleted)**, approved leave is cancelled; both need permission (§7.5) | **App** handles 403 and `withdrawn`; **owner** decides on permission |
| 9 | `getLeaveApprover()` → string | `{leave_approver, leave_approver_name, is_mandatory}` | **App** |
| 10 | `createClaim({…, date, receipt})` with a file name | Params `expense_date`, `receipt_filename`, `receipt_base64` | **App** |
| 11 | `ExpenseClaim` | Also `sanctioned_amount`, `approver_id` | **App** (additive) |
| 12 | `getExpenseTypes` → `string[]` | Same | – |
| 13 | `SalarySlip` | Also `start_date`, `end_date`, `currency`, `pdf_url` (Bearer-authenticated GET) | **App** (additive) |
| 14 | `Employee` has no `image` or flags | Adds `reports_to_id`, `image`, `is_manager`, `is_approver`, `is_sales_manager` | **App** (additive; use the flags instead of "Preview as") |
| 15 | `TeamAttendance.id` is a short id (`"najath"`) | `id` = user email, plus `employee` and names | **App**: join on email with the hub |
| 16 | `Approval` has `title`/`detail` preformatted ("Casual Leave · 26–28 Oct") | `title` = leave or expense type, `detail` = reason. Structured `from_date`, `to_date`, `days`, `half_day`, `amount`, `employee`. `submitted` is a datetime | **App** formats |
| 17 | `decideHrApproval(id, approve)` | `decide_approval(kind, name, approve, reason)`, with a reason required to reject | **App**: add a reason sheet for rejections |
| 18 | `DealStage` is a fixed union (`Demo`, `Proposal`, …) | Stages are the site's **CRM Deal Status** names (defaults `Demo/Making`, `Proposal/Quotation`, `Ready to Close`, …) plus `stage_type` | **App**: load `get_options().deal_stages`; use `stage_type` for Won and Lost |
| 19 | `LeadStatus` union | Site's CRM Lead Status names (defaults add `Nurture`, `Junk`) | **App**: load from `get_options` |
| 20 | `Deal.title` | CRM Deal has no title. `title` = organization, else contact, else name | – (documented) |
| 21 | `Deal.tasks[].done`; `toggleDealTask` | `tasks[]` have `status` and `done`; use `set_task_done(name, done)` | **App** |
| 22 | `Lead.whatsapp`, `Lead.territory` free text | No WhatsApp field. `territory` is a CRM Territory link | **App**: use `phone` for WhatsApp |
| 23 | `CrmActivity.kind` includes `email`/`meeting`/`whatsapp`/`stage` | Returns `call`, `note`, `comment` (meeting/email/WhatsApp are comments with a `Meeting:`-style prefix). Stage changes are in `stage_history` | **App**: widen the type to `comment` |
| 24 | `logActivity(ref, {kind, text, nextFollowUp})` | `log_activity(doctype, name, kind, text, next_follow_up, phone)`. Returns the timeline | **App** (rename) |
| 25 | `setLeadFollowUp(name, date)` | `add_follow_up(doctype, name, due, title?)` creates a CRM Task | **App** |
| 26 | `FollowUp.kind` is `call`/`meeting`; `owner` | `kind` is always `task`. Adds `status`, `priority`, `overdue`; no `owner` (always me) | **App** |
| 27 | `Organization` = ERPNext Customer with `health`, `health_reason`, `amc`, `contacts` | CRM Organization with `erpnext_customers[]`, `open_deals`, `contacts[]` and `deals[]` (detail). **No `health` or `amc`** (§5) | **App**: drop health; take AMC from the hub |
| 28 | `SalesSnapshot` camelCase (`byStage`, `wonThisMonth`, `closingThisWeek: Deal[]`) | snake_case (`by_stage`, `won_this_month`, `closing_this_week` with slim rows), plus `currency` | **App** |
| 29 | ESS `getNotifications` / `markNotificationsRead` | Not part of this backend. The push payload carries the route. A notification list for tboindia is a follow-up. | **Backend** (v1.1) if the app keeps an ESS notification list |

## 9. Tests

The tests are in `tbo_smart/tests/test_mobile_{ess,crm,manager,push}.py`, with helpers in
`tbo_smart/test_utils.py`. They cover:

- **ESS:** own-only data and server-time check-in.
- **Manager:** endpoints refuse non-managers and non-approvers; only the named approver can
  decide.
- **CRM:** visibility follows CRM rules.
- **Push:** off by default, payload, dead-token cleanup, never raises.

They need a site with **erpnext, hrms and crm** installed and `allow_tests` on:
`bench --site <site> run-tests --app tbo_smart`. CI (`.github/workflows/ci.yml`) installs
those apps on Frappe version-15.

## Changelog

- **v1 (2026-10-10):** first contract. ESS, manager, CRM, devices and Expo push.
