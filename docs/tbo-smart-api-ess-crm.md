# TBO Smart API: ESS, CRM and manager (tboindia side), v1.7

This is the contract for the TBO Smart mobile app's **tboindia.tbocloud.in** half: HR
self-service (HRMS), CRM (Frappe CRM) and the manager views. The hub (TBO Support) has its
own contract in `tbocloud/helpdesk`.

- **Code:** `tbo_smart/api/mobile/{auth,capabilities,ess,crm,manager,quotation,device}.py` and `tbo_smart/push.py`.
- **Fixtures:** `docs/fixtures/<module>.<method>.json`. Each one is the full HTTP body that
  Frappe returns (`{"message": ...}`).
- **Status:** v1.7, 2026-10-15. Any change gets a version note in the changelog at the end of
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
| Money | Numbers in the document's currency. Sales snapshot and sales insights values are in the CRM base currency. |
| Errors | HTTP 4xx/5xx with Frappe's body: `exc_type` plus `_server_messages`, a JSON string holding a list of JSON strings, each `{message, title}`. Map them as shown below. |

Error mapping for the app:

| `exc_type` | HTTP | App `code` | Meaning |
| --- | --- | --- | --- |
| `NoEmployeeError` | 404 | `no_employee` | No active Employee has `user_id` = this user. HR must link one. |
| `AuthenticationError` | 401 | `unauthenticated` | Single sign-in only (§2.5): the Microsoft ID token failed verification or was already used. |
| `PermissionError` | 403 | `forbidden` | Not your document, not your role, or a setting is off. |
| `DuplicateEntryError` | 409 | `duplicate` | For example, a second punch within 60 s. |
| `DuplicateLead` | 409 | `duplicate_lead` | `crm.create_lead` found possible duplicates. The body also has `matches` (§6.2). Show them, then resend with `force=1` if the user still wants the lead. |
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
   - **Allowed Roles:** add `Employee` so that only staff can sign in. Left empty, Frappe fills
     in `Desk User` on save, which allows every desk user.
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
  token from this PKCE flow. Tokens from single sign-in (§2.5) have **no** refresh token.
- On sign-out, call `unregister_device` (§7), then revoke the refresh token (or, after single
  sign-in, the access token).

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

### 2.5 Single sign-in with Microsoft: `tbo_smart.api.mobile.auth`

The app signs in **once**, with Microsoft, and gets its own tokens from each site. The hub
(TBO Support) has the same endpoint with the same request and response, so the app calls
both the same way. What a user may do on each site still comes from that site's roles
(§4): a CRM-only user signs in fine and simply has no `ess`.

**Flow**

1. The app runs Microsoft's Authorization Code + PKCE flow against
   `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize` with scopes
   `openid profile email offline_access` and a fresh `nonce`, and gets an **ID token**.
2. The app posts that ID token to each site (here and on the hub).
3. Each site verifies it and returns its own Frappe **access token only** (3600 s) for the
   matching user. There is **no refresh token**. The app makes Bearer calls as in §2.2 and
   calls `revoke_token` on sign-out.
4. When the access token is about to expire (or a call returns 401), the app gets a fresh ID
   token from Microsoft (silently, with its Microsoft refresh token, which never leaves the
   phone) and exchanges it again. `get_token` with `grant_type=refresh_token` is refused for
   these tokens because none was issued.
5. **Microsoft is the only login.** When TBO disables someone's Microsoft account, Microsoft
   refuses the silent refresh, so the app cannot get a new ID token. Their access to this site
   ends when the current access token expires, within an hour at most. Disabling the User
   here ends it at once (see the security notes).

**Endpoint**

| | |
| --- | --- |
| Method | `POST /api/method/tbo_smart.api.mobile.auth.exchange_microsoft_token` (JSON or form body, no `Authorization` header) |
| Body | `id_token` (required): the Microsoft ID token. `nonce` (optional): the nonce the app sent to Microsoft; when given, the token's `nonce` must equal it. |
| Rate limit | 30 calls per minute per IP, then 429 `RateLimitExceededError`. |
| Response | `{"v": 1, "data": {access_token, expires_in, token_type, scope, user, full_name}}` (no `refresh_token`) inside Frappe's `message`. See `fixtures/auth.exchange_microsoft_token.json`. Unlike the lists here (`{data, page, has_more}`), this method uses the hub's `{v, data}` envelope so one app function reads both sites. |

- `expires_in` is 3600 and `token_type` is `Bearer`; `scope` is `all openid`.
- `user` is the site's User id (its email) and `full_name` its full name.

Errors (Frappe's error body, mapped as in §1):

| `exc_type` | HTTP | When |
| --- | --- | --- |
| `AuthenticationError` | 401 | The token fails any check below, was already used, or Microsoft's keys could not be fetched. Sign in with Microsoft again. |
| `PermissionError` | 403 | The switch is off or not set up; no enabled User matches; the user is disabled; or the user has none of the OAuth Client's Allowed Roles. The message says which. |
| `RateLimitExceededError` | 429 | Too many calls from this IP. |

**Validation, in order**

1. The switch **Single sign-in with Microsoft** is on and the tenant ID, mobile client ID and
   OAuth Client are set.
2. The token's `tid` is one of the configured tenants (checked before any key is fetched).
3. RS256 signature against that tenant's keys,
   `https://login.microsoftonline.com/<tid>/discovery/v2.0/keys`. PyJWT's `PyJWKClient`
   downloads the key set, and it is shared by every worker through Redis for an hour. An
   unknown `kid` downloads it again at most once per tenant every 5 minutes (a Redis `SET NX`
   cooldown). The new set replaces the shared one, so all workers see rotated keys at once,
   while made-up key ids cannot make the site hammer Microsoft. No other algorithm is
   accepted.
4. `iss` is exactly `https://login.microsoftonline.com/<tid>/v2.0`, `aud` is the mobile
   client ID, and `exp`, `iat`, `iss`, `aud`, `tid` are present.
5. `exp` and `nbf` hold, with 120 s of clock leeway.
6. `nonce` matches when the app sends one (constant-time compare).
7. Replay guard: the token's `uti` (or `jti`) is stored in Redis with `SET NX`. It stays there
   for 10 minutes, or until the token expires if that is later. A second exchange of the same
   token is refused, and a token with neither claim is refused.
8. The user is the first User whose **email** matches `email`, then `preferred_username`,
   then `upn`, ignoring case. A User's name (id) is never matched, because it can be an old
   address that now belongs to someone else. A disabled match is refused. Users are never
   created.
   `Administrator` and `Guest` never match.
9. The user must hold one of the OAuth Client's **Allowed Roles**, the same rule as the
   normal sign-in.
10. The token is made by oauthlib's `BearerToken` and saved by Frappe's own
    `OAuthWebRequestValidator.save_bearer_token`, the same code `get_token` uses. It is an
    **OAuth Bearer Token** for the configured OAuth Client with scopes `all openid` and a 3600 s
    expiry. It has **no refresh token**, so Frappe's `get_token` refresh finds nothing to
    refresh.

**Owner setup (once)**

1. **Entra ID** (portal.azure.com → Microsoft Entra ID → App registrations → New
   registration), in TBO's tenant:
   - **Name:** `TBO Smart`.
   - **Supported account types:** accounts in this organizational directory only.
   - **Platform:** *Mobile and desktop applications*. **Redirect URI:**
     `tbosmart://oauth/callback`.
   - **Authentication → Allow public client flows:** Yes. Create **no** client secret; the
     app is a public client and uses PKCE.
   - **API permissions:** Microsoft Graph delegated `openid`, `profile`, `email`,
     `offline_access`. Grant admin consent so staff are not prompted.
   - Copy the **Application (client) ID** and the **Directory (tenant) ID**. Give both to the
     app build too.
   - Optional: under **Token configuration**, add the `email` optional claim so `email` is
     always present (the site falls back to `preferred_username` otherwise).
2. **tboindia** → **/app/tbo-smart-settings**:
   - **Microsoft tenant ID:** the Directory (tenant) ID. Several tenants can be listed,
     separated by commas. `common` and `organizations` are refused.
   - **Mobile Entra client ID:** the Application (client) ID.
   - **OAuth Client:** the TBO Smart client from §2.1. Its **Allowed Roles** now decide who
     may use the app here. Add `Sales User` next to `Employee` if CRM-only staff should sign
     in.
   - Tick **Single sign-in with Microsoft**.
3. **Hub** → HD Settings: the same tenant ID, mobile client ID and the hub's own OAuth Client.

**Security notes**

- **Only this site's tokens leave the site.** Each site trusts the Microsoft ID token only
  long enough to pick the user, then issues its own token. A token stolen from one site does
  not work on the other.
- **The tenant is the trust boundary.** Only GUID tenants are accepted, so no outside
  Microsoft account (personal or another company's) can sign in. Inside the tenant, `email`,
  `preferred_username` and `upn` are controlled by TBO's own Entra admins.
- **No user is ever created.** Access stays with each site's User list, `enabled` flag,
  roles and the OAuth Client's Allowed Roles.
- **Leavers lose access everywhere.** Microsoft is the only login and the site issues no
  refresh token, so disabling the Microsoft account ends access within an hour at most.
  On this site, disabling the User ends it at once, in two ways:
  - Frappe refuses every Bearer call whose user is disabled
    (`OAuthWebRequestValidator.validate_bearer_token` checks `User.enabled`).
  - A `doc_events` User `on_update` hook (`auth.revoke_tokens_of_disabled_user`) sets
    every Active OAuth Bearer Token of that user to Revoked. This also covers tokens from the
    older PKCE sign-in, and tokens stay dead if the user is enabled again.
- **MFA and Conditional Access** are enforced by Microsoft at sign-in. Frappe's own password
  and two-factor checks do not run for this path.
- **Replay** is blocked by the one-time `uti`/`jti` and by `nonce` when sent. The audience
  check means an ID token issued to any other app is refused.
- **Nothing sensitive is logged.** The ID token and the issued tokens are never written to
  logs or error messages.
- **Rate-limited** per IP. Signature checks are cheap, and the limit keeps a misbehaving
  client from hammering the Microsoft key endpoint.
- **Turning it off** (untick the switch) refuses new exchanges at once. Access tokens
  already issued keep working until they expire (an hour at most) or are revoked in
  **/app/oauth-bearer-token**.

## 3. ESS: `tbo_smart.api.mobile.ess`

Every ESS method needs an **active Employee linked to the user** (`Employee.user_id`).
Without one, it fails with `NoEmployeeError`. Reads of your own documents go through
`frappe.get_list` with an explicit "own employee" filter, so DocType and User Permissions
still apply.

| Method | Verb | Params | Returns | Reuses |
| --- | --- | --- | --- | --- |
| `get_me` | GET | – | Employee, plus `capabilities` (§4) and the v1 aliases `is_manager` (= `team_today`), `is_approver` (= `approve_leave` or `approve_expense`), `is_sales_manager` (= `crm_manager`) | `capabilities.get_capabilities` |
| `check_in` | POST | `log_type` (`IN`/`OUT`), `latitude?`, `longitude?`, `accuracy?` (m), `place_name?` | the punch, with `place_name` and `outside_radius` (needs `checkin_allowed`) | HRMS `Employee Checkin` controller (shift) with TBO Smart's radius rule (§3.2) |
| `get_checkin_locations` | GET | – | `{enforced, exempt, max_accuracy, tolerance_cap, locations[{name, location_name, latitude, longitude, radius}]}` | `tbo_smart.checkin.geofence.get_policy` (§3.2) |
| `get_today` | GET | – | `{date, server_time, shift_name, shift_start, shift_end, status, attendance, checkins[]}` | `hrms…shift_assignment.get_employee_shift` |
| `get_attendance` | GET | `year`, `month` (1–12) | `[{date, status}]` | `hrms.api.get_attendance_calendar_events` |
| `get_holidays` | GET | `year?` (default: this year) | `[{date, description}]` (weekly offs excluded) | `hrms.api.get_holidays_for_employee` |
| `get_leave_balances` | GET | – | `[{leave_type, allocated, used, pending, balance}]` | `hrms…leave_application.get_leave_details` |
| `get_leave_types` | GET | – | `["Casual Leave", …]` (allocated types plus LWP) | `hrms.api.get_leave_types` |
| `get_leave_approver` | GET | – | `{leave_approver, leave_approver_name, is_mandatory}` | `hrms.api.get_leave_approval_details` |
| `get_leave_applications` | GET | `page`, `page_length` | page of LeaveApplication | `frappe.get_list` |
| `get_leave_days` | GET | `leave_type`, `from_date`, `to_date`, `half_day?`, `half_day_date?` | `{days, half_day_date}` | `hrms…leave_application.get_number_of_leave_days` |
| `apply_leave` | POST | `leave_type`, `from_date`, `to_date`, `half_day?`, `reason?`, `half_day_date?` | the new LeaveApplication, with `approver` | Leave Application controller |
| `cancel_leave` | POST | `name` | `{name, status: "Cancelled", withdrawn}` | Leave Application controller |
| `get_expense_types` | GET | – | `["Travel", …]` | `hrms.api.get_expense_claim_types` |
| `get_expense_claims` | GET | `page`, `page_length` | page of ExpenseClaim | `frappe.get_list` |
| `create_expense_claim` | POST | `expense_type`, `amount`, `expense_date`, `description?`, `receipt_filename?`, `receipt_base64?` | the new ExpenseClaim | `hrms.api.get_expense_approval_details`, `get_company_cost_center_and_expense_account`, `upload_base64_file` |
| `get_advances` | GET | `page`, `page_length` | page of EmployeeAdvance (`advances`) | `frappe.get_list` |
| `create_advance` | POST | `purpose`, `advance_amount`, `posting_date?` (default today), `currency?` (default company currency), `advance_account?` (default the company's) | the new EmployeeAdvance, a draft (`advances`) | `hrms.api.get_advance_account`, `get_expense_approval_details`, `erpnext.setup.utils.get_exchange_rate`, Employee Advance controller |
| `cancel_advance` | POST | `name` | `{name, status: "Cancelled", withdrawn}` | Employee Advance controller |
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
  - When **HR Settings → Allow Geolocation Tracking** is on, HRMS requires latitude and
    longitude, and stores them plus a GeoJSON `geolocation`.
  - The radius rule is in §3.2. A refusal is `CheckinOutsideRadiusError` (a subclass of
    HRMS's `CheckinRadiusExceededError`) or `CheckinAccuracyError`, both HTTP 417 with a
    message to show as is.
  - The accuracy stays readable in **Location / Device ID** as `TBO Smart ±12 m`, returned as
    `location`, and is also stored in `tbo_location_accuracy`.
  - `place_name` is the phone's reverse-geocoded address (at most 140 characters), stored in
    `tbo_location_name` ("Place"). Without it the server names the place in the background
    (§3.2), so a punch's `place_name` can be `null` at first and filled in by `get_today`
    later.
  - `outside_radius` is true for an exempt employee's punch outside every allowed location.
- **`get_today.checkins[]`** have the same shape as the `check_in` result.
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
  - `half_day_date` picks the half day of a leave of several days. It defaults to
    `from_date`, must be between `from_date` and `to_date` (else `ValidationError` "The half
    day must be between …"), and is ignored without `half_day`. A one-day leave's half day is
    that day.
- **`get_leave_days`** previews the total with HRMS's own calculation (the same as the saved
  `total_leave_days`): a half day counts 0.5 on its date, and holidays and weekly offs are left
  out unless the leave type includes holidays. It validates `half_day_date` like `apply_leave`.
- **`cancel_leave`** works on your own application only.
  - **Open draft:** it is withdrawn, which means deleted. This needs the **Delete** permission
    on Leave Application, which the Employee role does **not** have by default. See §8.
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
- **Employee advances** (`get_advances`, `create_advance`, `cancel_advance`; flag `advances`).
  - An EmployeeAdvance is `{name, posting_date, purpose, currency, advance_amount,
    paid_amount, claimed_amount, return_amount, pending_amount, balance_amount, status,
    docstatus}`. `status` is HRMS's own: `Draft`, `Unpaid` (submitted, not yet paid),
    `Paid`, `Claimed`, `Returned`, `Partly Claimed and Returned`, or `Cancelled`.
  - `pending_amount` = advance − claimed − returned: what is still open against the advance.
    `balance_amount` = paid − claimed − returned: cash the employee holds and still has to
    claim or return (the HRMS PWA's "balance"). Both are 0 once cancelled.
  - `create_advance` creates a **draft**, defaulted as the HRMS PWA form does: the currency is
    the company currency (HRMS warns that an advance in another currency cannot be settled
    through an Expense Claim; another `currency` gets ERPNext's exchange rate, or is refused
    when there is none); the advance account is **Company → Default Employee Advance Account**
    (`hrms.api.get_advance_account`). An `advance_account` of another company is refused.
    `purpose` is required and `advance_amount` must be more than zero, each with its own
    message.
  - **Routing.** HRMS's Employee Advance has no approver field. The draft is **shared with
    the employee's expense approver** (Employee → Expense Approver, else the first
    Department expense approver, `hrms.api.get_expense_approval_details`) with Write and
    Submit, the way HRMS shares an Expense Claim with its approver. Without the share a line
    manager could not read a subordinate's advance under the Employee User Permission.
  - Approval is submitting it (§5). Paying, claiming and returning stay in ERPNext with
    Accounts.
  - `cancel_advance` works on your own advance only (others get `PermissionError`).
    - **Draft:** withdrawn, which means deleted. The Employee role has no Delete on Employee
      Advance, so the endpoint deletes after checking the advance is your own draft.
    - **Submitted with nothing paid:** cancelled through `doc.cancel()`, which needs
      **Cancel** (Expense Approver by default, not Employee).
    - Anything paid is refused.
- **Salary slips** are **own only**.
  - The list is filtered to the session employee and `docstatus = 1`.
  - `download_salary_slip` checks that the slip's employee is the session employee
    **before** rendering. Any other name gets `PermissionError`, the same as a missing one.
  - It then uses Frappe's `download_pdf`, which checks read/print permission again.
  - We do **not** reuse `hrms.api.download_salary_slip`, because it has no ownership check.

### 3.2 Check-in location: place name and radius (v1.5)

Full spec and owner setup: `docs/checkin-location.md`.

- **Which locations:** the Shift Location on the employee's active Shift Assignment for the
  day, else **TBO Smart Settings → Allowed check-in locations**. Inside any of them is allowed.
- **Rule:** distance (HRMS's haversine) ≤ `radius` + min(`accuracy`, `tolerance_cap` = 50 m).
  A reported accuracy worse than `max_accuracy` (default 100 m) is refused first. Without
  coordinates the punch is refused.
- **Enforced** only when **Enforce check-in radius** is on and at least one usable location
  applies (`enforced` in `get_checkin_locations`). With nothing configured nothing is blocked.
- **Exempt** (`exempt` = `"role"` or `"remote"`): a role listed in **Roles that may check in
  anywhere** (default HR Manager), or **Employee → Allow remote check-in**. Their punches are
  accepted anywhere and flagged `outside_radius` when outside.
- **Every channel:** the rule is an Employee Checkin `validate` document event, for the
  employee's own punches from the app, the HRMS PWA, desk or `/api/resource`. Logs entered for
  someone else (HR, biometric devices) are left to HRMS.
- **HR Settings → Allow Geolocation Tracking** on adds HRMS's own strict check for Shift
  Assignment locations (no tolerance, no exemptions); leave it off with this rule unless needed.
- **App:** call `get_checkin_locations` before the punch, measure the same way, show the
  distance and disable the button when `enforced`, not `exempt`, and outside or too vague. The
  server stays authoritative.
- **Place name fallback:** Nominatim (configurable URL, identifying User-Agent and contact
  email, at most 1 request/s, cached by 4-decimal coordinates for 90 days) in a background
  job after commit. Failures leave the name blank and are logged.

Fixtures: `ess.check_in.json`, `ess.get_today.json`, `ess.get_checkin_locations.json`,
`error.checkin_outside_radius.json`.

## 4. Capabilities: what the app may show

Sign-in is per user. The app shows exactly what that user's **tboindia roles and settings**
allow, and nothing is configured in the app. One function,
`tbo_smart.api.mobile.capabilities.get_capabilities`, computes the flags. They come back:

- in `ess.get_me` as `capabilities`;
- from `tbo_smart.api.mobile.capabilities.get_my_capabilities` (GET, no params). This also
  works for a user **without an Employee**, for example a CRM-only user, so call it when
  `get_me` returns `no_employee`.

Every endpoint checks the **same flag** it advertises, so the app never offers something the
server would refuse. Flags are computed on every call, not cached, so a role changed in the
ERP takes effect on the next request.

| Flag | Granted when | Endpoints that check it |
| --- | --- | --- |
| `ess` | The user has an **active Employee** (`Employee.user_id`). | every `ess.*` method (else `NoEmployeeError`) |
| `checkin_allowed` | `ess` and **HR Settings → Allow Employee Checkin from Mobile App**. | `ess.check_in` |
| `geolocation_required` | `checkin_allowed` and **HR Settings → Allow Geolocation Tracking**. The app must send latitude and longitude; HRMS refuses a punch without them. The radius rule is reported separately by `ess.get_checkin_locations` (§3.2). | `ess.check_in` (HRMS) |
| `approve_leave` | **HR Manager** or **HR User**; or the user is the **Leave Approver** of an active Employee or a Department (Department Approver, Leave Approvers); or a **Workflow** on Leave Application has an approve or reject transition (action or next state containing "approv" / "reject") allowed to one of their own roles (not All or Guest). The **Leave Approver** role alone, with nobody to approve, is not enough. | `manager.get_approvals` (leave part), `manager.decide_approval(kind="leave")` |
| `approve_expense` | The same rule for Expense Claim: HR Manager / HR User, named **Expense Approver** of an Employee or Department, or a workflow role. | `manager.get_approvals` (expense part), `manager.decide_approval(kind="expense")` |
| `advances` | `ess` and the user's roles can create an Employee Advance (the Employee role can). | `ess.get_advances`, `ess.create_advance` |
| `approve_advance` | HRMS has no approver or approval status on Employee Advance: approving it is **submitting** it. **With a Workflow** on Employee Advance: one of their own roles has an approve or reject transition. **Without one:** **HR Manager / HR User** whose roles can submit Employee Advance (by default only **Expense Approver** can), or the named **Expense Approver** of an active Employee or a Department, to whom `create_advance` routes the drafts. | `manager.get_approvals` (advance part), `manager.decide_approval(kind="advance")` |
| `team_today` | `ess` and at least one **active direct report** (`Employee.reports_to`). | `manager.get_team_today` |
| `team_insights` | `team_today`, or the **HR Manager** role. | `manager.get_team_insights` |
| `team_scope` | `company` for an HR Manager (every active employee of their company; every company if they have no Employee), `direct_reports` for other managers, else `null`. Never includes the caller. | `manager.get_team_insights` |
| `crm` | **Sales User**, **Sales Manager** or **System Manager**: the roles Frappe CRM itself lets into the CRM app (`crm.api.check_app_permission`). | every `crm.*` method |
| `crm_manager` | `crm` and **Sales Manager** or **System Manager**, as CRM's dashboard decides "manager". | – |
| `crm_scope` | The widest CRM visibility, the rule in `crm.permissions.org_hierarchy`: `all` for System Manager; with **FCRM Settings → Sales hierarchy** on and the user in it, `team` if anyone is below them, else `own` (this limits even a Sales Manager); a Sales Manager outside the hierarchy `all`; everyone else `own`. `null` without `crm`. | `crm.get_leads`, `get_deals`, `get_my_follow_ups`, `get_sales_insights` |
| `crm_scopes` | The `scope` values the user may pass, narrowest first: `own` → `["mine"]`; `team` → `["mine", "team"]`; `all` → `["mine", "team", "all"]`, without `team` when they have no sales team. Show a switcher only when there is more than one. | same |
| `sales_insights` | `crm_manager`. | `crm.get_sales_insights`, `manager.get_sales_snapshot` |
| `approve_quotations` | A quotation approval level that requests can wait at: the user's highest level role ranks above the lowest level (BDM, Sales & Marketing Head, Director, CEO with the default levels; never a BDE or someone with no level). The same rule as `is_approver` in `get_adjustment_limits` ([quotation-discount-control.md](quotation-discount-control.md) §2). | `quotation.get_approvals(tab="waiting")`, `quotation.decide` |

### 4.1 CRM scope

| `scope` | Leads and deals (`get_leads`, `get_deals`) | Follow-ups (`get_my_follow_ups`) | Sales insights |
| --- | --- | --- | --- |
| `mine` | Owned by me. At my widest scope (staff always), no extra filter: CRM's own rule already gives "owned by me or assigned to me". | Assigned to me (the default) | `deal_owner` / `lead_owner` = me |
| `team` | Owner in my CRM sales team (me and everyone below me). At my widest scope, CRM's rule (owned by or assigned to the team). | Assigned to my sales team | owner in my sales team |
| `all` | No owner filter: everything CRM lets me read. | Any open task I can read | no owner filter |

- `get_leads` and `get_deals` default to the widest scope, which is exactly the v1
  behaviour. `get_my_follow_ups` defaults to `mine`.
- A scope that is not in `crm_scopes` is refused with `PermissionError`
  (`fixtures/error.scope.json`). Staff are therefore always `mine`.
- Lists still go through `frappe.get_list`, so CRM's permission rules apply on top of the
  scope: a scope can only narrow what CRM shows, never widen it.
- Sales insights read aggregates with query-builder queries, as CRM's dashboard does, and
  filter on the owner, as the dashboard filters a Sales User to their own records.

Fixtures: `capabilities.get_my_capabilities.json` (a sales head who manages a team) and
`ess.get_me.json` (a developer who is also a Sales User).

## 5. Manager: `tbo_smart.api.mobile.manager`

| Method | Verb | Params | Returns | Who may call |
| --- | --- | --- | --- | --- |
| `get_team_today` | GET | – | `[{id, employee, employee_name, image, department, designation, attendance, checked_in_at, leave_type}]` | `team_today`. Others get `PermissionError`. |
| `get_approvals` | GET | – | `[Approval]`, newest first | `approve_leave`, `approve_expense` or `approve_advance`; each kind only with its flag. Others get `PermissionError`. |
| `decide_approval` | POST | `kind` (`leave`/`expense`/`advance`), `name`, `approve` (bool), `reason?` (required to reject) | `{kind, name, status, docstatus}` | `approve_<kind>`, and then the document's named approver, an HR Manager / HR User, or a workflow role (below) |
| `get_team_insights` | GET | `period` (`week`/`month`/`quarter`/`year`, default `month`) | Team insights (§5.1) | `team_insights` |
| `get_sales_snapshot` | GET | – | `{currency, by_stage[], pipeline, weighted, won_this_month{count, amount}, closing_this_week[]}` | `sales_insights`. Covers the caller's widest `crm_scope`. Prefer `crm.get_sales_insights`. |

- **`get_team_today`**
  - `attendance` is `leave` (an approved, submitted Leave Application covers today), `in`
    (an IN punch today) or `not_yet`.
  - It reads other employees' check-ins, so it is limited to the caller's own direct reports.
  - It returns only the first IN time, never coordinates.
  - `id` is the member's **user email**, the cross-site identity (Employee name if no user).
- **`get_approvals`** reuses the HRMS PWA queries: `hrms.api.get_leave_applications` and
  `get_expense_claims` with `for_approval=True`.
  - **No workflow:** drafts where I am the approver (`leave_approver` or `expense_approver`),
    Open leave and Draft claims, never my own. An **HR Manager / HR User** gets every open
    one they can read, as in HRMS.
  - **With a Workflow on the doctype:** documents in the states my roles can move.
  - **Advances** (`kind: "advance"`, through `hrms.api.get_filters`): draft Employee
    Advances, never my own. An HR Manager / HR User gets every draft they can read; anyone
    else the drafts shared with them by `ess.create_advance`. `title` is "Employee Advance",
    `detail` the purpose, `amount` the advance amount, plus `currency`.
  - Every row has `can_reject`. It is `false` only for an advance without a workflow: HRMS
    has no rejected state for an advance, so hide Reject there.
- **`decide_approval`** follows the HRMS path. It never writes the status directly.
  - **No workflow:**
    - Only the named approver, or an HR Manager / HR User (who can submit any of these in
      HRMS), may decide; others get `PermissionError`.
    - **Leave:** sets `status` to Approved or Rejected, then `submit()`. HRMS then creates the
      leave ledger, updates attendance and notifies the employee.
    - **Expense:** sets `approval_status`, fills `sanctioned_amount` = `amount` on approved
      lines, then `submit()`.
      - An approved submit **posts GL entries**, so the claim needs a payable account.
      - Rejected claims post nothing.
    - **Advance:** only an HR Manager / HR User, or the approver it was routed (shared) to,
      may decide; approving calls `submit()` (status `Unpaid` until Accounts pays it, which
      needs Company → Default Employee Advance Account, a **Receivable** ledger). Rejecting
      without a workflow is refused with a `ValidationError`: ask the employee to withdraw
      it, or add a workflow.
    - Nobody decides their own advance (`PermissionError`).
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
  - It covers the caller's widest `crm_scope`: every deal for a Sales Manager outside the
    sales hierarchy, their team's deals inside it.

### 5.1 Team insights (`get_team_insights`)

Read-only aggregates for **management by insight, not surveillance**. Fixture:
`manager.get_team_insights.json`.

- **Team:** `team_scope` (§4): active direct reports, or the HR Manager's company.
- **Period:** the current `period` **to date** (`from_date`…`to_date` = today). `previous` is
  the same number of days from the start of the previous period, so a half-finished month is
  compared fairly. Weeks start on the site's **first day of the week**.
- **`attendance`** counts submitted Attendance records of the team in the period:
  - `present_pct` = (Present + Work From Home + ½ × Half Day) ÷ all records × 100;
  - `late` = records with **Late Entry** ticked; `absent` = Absent records;
    `on_leave_days` = On Leave records; `marked_days` = all records;
  - `*_delta` = current minus previous (percentage points for `present_pct`);
  - `on_leave_today` = people with approved, submitted leave covering today;
    `checked_in_today` = people with an IN punch today (a count only).
- **`trend`**: 8 periods, oldest first, the last one the current period. `label` is
  `YYYY-MM` (month), `YYYY-Qn` (quarter), `YYYY` (year) or the week's first day
  `YYYY-MM-DD` (week). Each has the same
  KPIs as `attendance` (without the today counts).
- **`pending`**: the team's Open leave applications and Draft expense claims: `count`,
  `oldest_days` (age of the oldest by posting date) and, for expenses, `amount`.
- **`people`**: names appear only here, and only as counts: up to 10 people with late
  check-ins or absences in the period, most first. Nobody else is named.
- **Never returned:** check-in times, coordinates, the device / location text, or any
  per-day record of a person.
- Computed with a fixed set of grouped queries (no per-person queries), and cached for **5 minutes** per
  (user, day, period, team scope); `generated_at` says when.

### 5.2 Quotation approvals: `tbo_smart.api.mobile.quotation`

Thin wrappers over `tbo_smart.api.quotation_adjustment` (the functions the CRM's Approvals
page calls), so the app and the CRM never disagree. The rules, levels and statuses are in
[quotation-discount-control.md](quotation-discount-control.md).

| Method | Verb | Params | Returns | Who may call |
| --- | --- | --- | --- | --- |
| `get_limits` | GET | – | `get_adjustment_limits()` unchanged: `{enabled, applies_to, kinds, level, limits, limit_labels, is_approver, can_view_all, can_override, can_forward, open_requests}` | anyone |
| `get_approvals` | GET | `tab` (`waiting` default, `mine`) | bare array (at most 200) of requests: `waiting` = `get_pending_approvals()` (open requests waiting for me, oldest first, with `can_approve_in_full`); `mine` = `get_adjustment_requests("mine")` (mine, newest first) | `waiting`: `approve_quotations`; `mine`: anyone (only their own) |
| `get_trail` | GET | `name` | the request (`get_adjustment_trail`) with `steps`, `currency` and `actions` | anyone who may read the request (a BDE only their own) |
| `decide` | POST | `name`, `action` (`approve`, `reject`, `forward`, `override`), `percent?` (approve or override; default the requested %), `remarks?` (required to reject and override) | as `get_trail`, after the decision | `approve_quotations`, then `decide_adjustment`'s own checks (the level it waits for, the limit, never the requester) |

- A request: `name`, `quotation`, `customer`, `deal` (CRM Deal or null), `kind` (Discount,
  Commission), `status` (Pending, Escalated, Approved, Rejected, Overridden), `voided`,
  `within_limit`, `previous_percent`, `requested_percent`, `approved_percent`,
  `quotation_value` (list value), `currency`, `requested_by`, `requested_by_name`,
  `approved_by`, `current_level`, `current_rank`, `reason`, `remarks`, `creation`, `modified`.
- A step: `{action (Requested, Applied, Approved, Rejected, Forwarded, Overridden, Voided),
  by_user, by_name, level, percent, at, remarks}`.
- **`actions`** say what the caller may do now, worked out with the same checks
  `decide_adjustment` makes (`check_can_decide`, the limit, the next level), so the app shows
  only buttons the server accepts: `{approve, approve_max (the highest % I may approve: the
  requested % or my limit, whichever is lower; null when I can't approve), reject, forward,
  forward_to (the next level's name), override}`.
- Fixtures: `quotation.get_approvals.json`, `quotation.get_trail.json`.
- Push: every request, forward and decision is a Notification Log on `TBO Quotation
  Adjustment`, so it reaches the phone with the route `/quotations/<name>` (§7).
- Tests: `tbo_smart/tests/test_mobile_quotation.py` (the flag per level, `waiting` refused
  to a BDE, `mine` lists my own, the actions per level and limit, decide returns the trail,
  CEO override needs a reason, the push route).

## 6. CRM: `tbo_smart.api.mobile.crm`

Every method needs the `crm` capability (§4). Lists use `frappe.get_list`, so Frappe CRM's
own rules decide visibility, and `scope` (§4.1) narrows it:

- a **Sales User** sees the leads and deals they own or that are assigned to them;
- the **sales hierarchy** extends that to their team when it is on;
- **Sales Manager** and **System Manager** see everything.

Writes are normal inserts and saves under the session user.

| Method | Verb | Params | Returns |
| --- | --- | --- | --- |
| `get_options` | GET | – | `{lead_sources[], lead_statuses[{name,type}], deal_stages[{name,type,probability}], lost_reasons[]}` |
| `get_leads` | GET | `status?`, `scope?` (§4.1), `search?`, `include_converted?` (default false), `page`, `page_length` | page of Lead |
| `get_lead` | GET | `name` | Lead plus `notes[]`, `tasks[]`, `activities[]` |
| `create_lead` | POST | `lead_name`, `organization?`, `email?`, `mobile_no?`, `source?`, `note?`, and for lead capture (§6.2) `follow_up_on?`, `follow_up_note?`, `assign_to?`, `source_channel?`, `card_image?`, `force?` | the new Lead (owner = me), as `get_lead`. Possible duplicates without `force`: 409 `DuplicateLead` |
| `check_duplicates` | GET | `phone?`, `email?`, `organization?` | `{data: {matches: [Match]}}` (§6.2) |
| `get_assignable_users` | GET | – | `{data: [{user, full_name, image}]}`: who I may pass as `assign_to` (§6.2) |
| `set_lead_status` | POST | `name`, `status` | `{name, status}` |
| `convert_lead` | POST | `name` | the Deal (an already-converted lead returns its existing deal) |
| `get_deals` | GET | `stage?`, `scope?` (§4.1), `mine?` (v1 alias of `scope=mine`), `search?`, `page`, `page_length` | page of Deal |
| `get_deal` | GET | `name` | Deal plus `erpnext_customer`, `stage_history[]`, `notes[]`, `tasks[]`, `activities[]` |
| `move_deal_stage` | POST | `name`, `stage`, `lost_reason?` (required for a Lost-type stage) | the Deal |
| `get_organizations` | GET | `search?`, `page`, `page_length` | page of Organization |
| `get_organization` | GET | `name` | Organization plus `contacts[]` and `deals[]` |
| `get_call_today` | GET | `user?` (a teammate, within my team or crm_scope all) | Call today (§6.3); needs TBO Smart Settings → Lead scoring on, else a ValidationError |
| `get_my_follow_ups` | GET | `scope?` (§4.1, default `mine`) | `[FollowUp]`: open CRM Tasks due today or overdue, oldest first, with `assigned_to` and `assigned_to_name` |
| `get_sales_insights` | GET | `period` (`week`/`month`/`quarter`/`year`, default `month`), `scope?` (§4.1, default widest), `stalled_days?` (default 14, 1–365) | Sales insights (§6.1); needs `sales_insights` |
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
- **Follow-ups** are CRM Tasks assigned to the scope's people (me by default), a status
  other than Done or Canceled, and `due_date` ≤ end of today. `kind` is always `"task"`,
  since CRM tasks have no call/meeting type.

### 6.2 Lead capture (v1.3)

Business card scan, picking from phone contacts and WhatsApp share all happen **on the
device**. They end in the same two calls: `check_duplicates` while the form is filled in,
then `create_lead`. Fixtures: `crm.check_duplicates.json`, `crm.create_lead.json`,
`crm.get_assignable_users.json`, `error.duplicate_lead.json`.

**`check_duplicates(phone?, email?, organization?)`**, needs `crm`:

- **Normalising.**
  - `phone`: digits only, then the **last 9 digits**, so `+971 50 418 2276`,
    `00971-50-418-2276`, `050 418 2276` and `504182276` are the same number, with or
    without the country code or trunk 0. A phone with fewer than 7 digits is ignored.
    Stored numbers are normalised the same way in SQL (`REGEXP_REPLACE`, `RIGHT`).
  - `email`: trimmed and compared case-insensitively.
  - `organization`: outer whitespace trimmed and compared case-insensitively.
  - No usable key: `matches` is empty.
- **Where it looks**, in this order, with one grouped query per doctype:

| `kind` | DocType | phone | email | organization | `status` |
| --- | --- | --- | --- | --- | --- |
| `lead` | CRM Lead, not converted (a converted lead is found through its deal) | `mobile_no`, `phone` | `email` | `organization` | lead status |
| `deal` | CRM Deal and its contacts (`CRM Contacts` rows) | deal `mobile_no`, `phone`; contact rows `mobile_no`, `phone` | deal `email`; contact rows `email` | `organization` | deal stage |
| `organization` | CRM Organization | – | – | `organization_name` | `null` |
| `contact` | Contact | `phone_nos[].phone`, `mobile_no`, `phone` | `email_ids[].email_id`, `email_id` | – | contact status |
| `customer` | ERPNext Customer | – | – | `customer_name` | `Active` / `Disabled` |

- At most **10** matches in all, newest first within each kind.
- **Permissions.** One `frappe.get_list` per doctype finds which matches the caller may read,
  so CRM's rules (owner, assignment, sales hierarchy, shares) apply.
  - Readable: full details.
  - Not readable: `name: null`, `title: "Someone on the team already has this <kind>"`,
    `status: null`, `can_open: false`. Only `owner_name` and `match_on` are shown.
    `owner_name` is then the owner's full name only, never their user id; it is `null` if
    they have no full name.
- **Match:** `{kind, name, title, owner_name, status, match_on: ["phone"|"email"|"organization"], can_open}`.
  - `owner_name` is the full name of the lead or deal owner, the customer's account manager
    (else its creator), or the creator of an organization or contact.
  - `can_open` is true for readable leads, deals and organizations, the kinds with an app
    screen. Readable contacts and customers show their details, but `can_open` is false.

**`create_lead`, new optional params.** Everything is checked **before** the insert, because
Frappe CRM's lead insert commits; a refused request leaves no lead.

| Param | Behaviour |
| --- | --- |
| `force` (bool, default false) | Runs the same duplicate check on `mobile_no`, `email` and `organization`. Matches and no `force`: HTTP **409**, `exc_type: "DuplicateLead"`, `matches: [Match]` at the top level of the body, nothing created. With `force`: the lead is created with a note `Created despite possible duplicate of lead CRM-LEAD-… (Faisal); a lead owned by Deepa`. Hidden matches are named only by kind and owner there too. |
| `assign_to` (user email) | Allowed when `crm_scopes` has `all` (any CRM user), or has `team` and the assignee is in my sales team (`get_sales_team`). Otherwise `PermissionError`. The assignee must be an **enabled system user with a CRM role** (Sales User, Sales Manager or System Manager), else `ValidationError`. My own email is always allowed and changes nothing. The lead stays **owned by me**; the assignee is added with Frappe's `assign_to.add`, the call CRM's own Assign To dialog makes, so it shows under Assigned To in CRM, the assignee can read the lead, and CRM and Frappe notify them. |
| `follow_up_on` (date or datetime) | A **CRM Task** "Follow up with {lead name}", Todo, due then (a bare date means 00:00), assigned to the assignee if `assign_to` was given, else to me. It shows as the lead's `next_follow_up`. |
| `follow_up_note` (str) | The task's description, and also an FCRM Note on the lead. Without `follow_up_on` it is just the note. |
| `source_channel` | `card_scan`, `contacts`, `whatsapp_share` or `manual`; anything else is a `ValidationError`. Always stored as an FCRM Note `Captured in TBO Smart: Business card scan (card_scan)` (labels: Business card scan, Phone contacts, WhatsApp share, Typed in). For reports, filter FCRM Note titles starting with `Captured in TBO Smart:`; the code is in brackets. CRM Lead has no source-detail field. If `source` is not sent, the lead's **Source** is set only when a CRM Lead Source already exists with one of these names (never created): `card_scan` → Business Card, Card Scan, Visiting Card; `contacts` → Phone Contacts, Phone Contact, Contacts; `whatsapp_share` → WhatsApp, WhatsApp Share; `manual` → none. |
| `card_image` (base64 JPEG) | At most **2 MB** decoded; a `data:image/jpeg;base64,` prefix and line breaks are allowed. Not base64, too large or not a JPEG: `ValidationError`. Saved through HRMS `upload_base64_file` (as for expense receipts) as a **private** File `<lead>-card.jpg` attached to the lead, auto-rotated by its EXIF tag. |

The order of notes on a new lead: `note`, `follow_up_note`, the capture note, then the
duplicate note.

**`get_assignable_users()`**, needs `crm`: `{data: [{user, full_name, image}]}`, ordered by
name. Enabled system users with a CRM role: everyone for `crm_scopes` with `all`; me and my
sales team for `team`; an **empty list** for staff (`own`). Show the "Assign to" picker only
when it is not empty.

**`get_options`** already returns `lead_sources` (since v1); unchanged.

### 6.3 Call today (v1.7)

`get_call_today(user=None)` returns today's list of leads to call for me (or a teammate), the
same as the CRM's `/crm/call-today` page (`docs/smart-leads.md` §4.3). Fixture:
`docs/fixtures/crm.get_call_today.json`.

```
{user, user_name, date, size, more, data: [CallTodayRow]}
CallTodayRow = {id, lead_name, organization, status, source, score (0-100), band ("Hot" | "Warm" | "Cold"),
  reason, why ("overdue" | "due_today" | "best_score"), phone, phone_e164 (international E.164, e.g.
  "+919847012345" or "+971504182276"), tel ("tel:" + phone_e164), whatsapp ("https://wa.me/<digits>"), email, last_touch: {at, kind, days} | null,
  follow_up: {task, title, due, overdue} | null}
```

- Order: overdue follow-ups (oldest first), due today, then leads with no follow-up and no touch
  today by score. Leads with a follow-up on a later day are not listed. `size` rows (12 by
  default), `more` = how many did not fit.
- Visibility follows CRM's own rules (`frappe.get_list`); `user` other than me is refused with
  403 unless they are in my sales team, or I have crm_scope `all` or am a sales head.
- `phone_e164`, `tel` and `whatsapp` are null when the lead has no usable number. A number
  with + or 00 keeps its country code; a local 10-digit number gets +91 (the default).
- To act on a row the app uses the existing `log_activity`, `add_follow_up` and
  `set_lead_status` on `doctype="lead"`.

### 6.1 Sales insights (`get_sales_insights`)

Read-only aggregates for the scope. Fixture: `crm.get_sales_insights.json`.

- **Value** is `deal_value × exchange_rate` in the FCRM base currency (`currency`), Frappe
  CRM's dashboard formula. **Weighted** is value × probability ÷ 100.
- **Period** and **previous** work as in team insights (§5.1). Deltas are current minus
  previous (percentage points for rates).
- **`pipeline`**: every open stage (type not Won or Lost) in CRM order, also empty ones:
  `count`, `value`, `weighted`, `avg_age_days` (days since the deal was created) and
  `avg_days_in_stage` (days since it entered its current stage, from CRM's status change
  log). Totals at the top.
- **`won` / `lost`**: deals closed in the period, `count` and `value` with deltas.
  - Won counts by `closed_date`, as CRM's dashboard does.
  - CRM keeps no date for a loss, so a lost deal closes on the day it entered its Lost stage
    (status change log), else on its last edit.
  - `win_rate` = won ÷ (won + lost) × 100 by count.
- **`stalled`**: open deals with **no activity for `stalled_days`**: not edited, and no call
  log, note, task change or comment on them since then. `count`, `value`, and the 10
  biggest with `idle_since` (last edit).
- **`leads_funnel`**: leads created in the period (`new`); `qualified` = a Won-type CRM Lead
  Status (defaults: Qualified, Converted) or converted; `converted` = became a deal. With
  percentages of `new` and deltas.
- **`activity`**: per person in the scope, `calls` (CRM Call Logs: the receiver for
  incoming, the caller otherwise), `notes` (FCRM Notes written) and `tasks_done` (CRM Tasks
  set to Done in the period, by assignee). Most active first.
- **`trend`**: 8 periods, oldest first: `won_value`, `won_count` and `new_leads`.
- **`closing`**: the 10 biggest open deals expected to close in the **whole current period**.
- Computed with a fixed set of grouped queries (no per-deal or per-person queries), and cached for **5 minutes** per
  (user, day, period, scope, stalled days); `generated_at` says when.

## 7. Devices and push: `tbo_smart.api.mobile.device` and `tbo_smart.push`

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
| TBO Quotation Adjustment (requests, forwards and decisions) | `/quotations/<name>` |
| Anything else | `/alerts` |

## 8. Roles and permissions to check on tboindia before launch

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
   - **Allow Geolocation Tracking:** on to require and store coordinates (GeoJSON).
   - **Check-in radius:** Shift Locations with coordinates and a radius, on Shift Assignments
     or in **TBO Smart Settings → Allowed check-in locations**; roles that may check in
     anywhere; **Employee → Allow remote check-in** (§3.2, `docs/checkin-location.md` §3).
   - Check **Leave Approver Mandatory** and **Expense Approver Mandatory**.
   - Team insights count late check-ins from Attendance **Late Entry**, which HRMS sets when
     the shift type's late-entry grace period is configured and attendance is marked from
     check-ins.
4. **Approvers.** Set **Leave Approver** and **Expense Approver** on each Employee or
   Department.
   - HRMS gives those users the **Leave Approver** and **Expense Approver** roles; check them.
   - Managers need **Employee → Reports To** set on their team for `get_team_today` and
     team insights.
   - Approvers are recognised by these Employee and Department fields (§4), not by the
     Leave Approver / Expense Approver role alone.
   - Give **HR Manager** only to people who should see company-wide team insights.
5. **Withdrawing leave from the app.** The Employee role has no Delete or Cancel on Leave
   Application, so `cancel_leave` returns 403. Pick one:
   - grant **Employee → Delete, If Owner** on Leave Application (Role Permission Manager), or
   - keep it HR-only and hide the button in the app.
6. **Expense approval posts GL entries on submit.** Each company needs **Default Expense
   Claim Payable Account** and a cost center, and each Expense Claim Type needs a default
   account. If approvers should approve without posting, add a **Workflow**;
   `decide_approval` follows it.
7. **Employee advances.**
   - **Company → Default Employee Advance Account** must be a **Receivable** ledger; HRMS
     refuses any other type on save and needs one to submit.
   - Set **Expense Approver** on each Employee (or Department): new advances from the app
     are shared with that user. Advances created in the desk are not shared, so only HR
     sees those in the app.
   - Only **Expense Approver** has Submit and Cancel on Employee Advance by default. Give HR
     users that role (or Submit on the doctype) if HR should approve advances.
   - Add a **Workflow** on Employee Advance if approvers should be able to reject.
8. **CRM.**
   - Sales staff need **Sales User**; heads need **Sales Manager** for sales insights.
   - Turn on **FCRM Settings → Sales hierarchy** if team leads should see their team's deals.
     Inside the hierarchy a Sales Manager is limited to their subtree (`crm_scope = team`);
     outside it they see everything (`all`).
   - Win rates count lost deals by the day they entered a Lost stage, so move deals to Lost
     in CRM (with a reason) rather than leaving them open.
   - Set **FCRM Settings → Currency** for the snapshot.
   - Fill in **User → Mobile No** for anyone who logs calls.
9. **OAuth Client** as in §2.1. Restrict **Allowed Roles** to `Employee`.
10. **Push.** Tick **TBO Smart Settings → Mobile push** only when the app build with Expo
   push ships. The workers (`short` queue) must be running.

## 9. Mismatches with the app (`tbo_smart_mobile/src/api`)

| # | App today | Backend | Who changes |
| --- | --- | --- | --- |
| 1 | Method names `tbo_smart.api.get_me`, `hrms.api.*`, `crm.api.*` | Everything is under `tbo_smart.api.mobile.{ess,crm,manager,device}`. Façades keep the shapes stable even when HRMS or CRM change. | **App** (comments and `client.ts`) |
| 2 | Lists are bare arrays (`Lead[]`, `LeaveApplication[]`, …) | Paged lists return `{data, page, has_more}` | **App**: unwrap `data` in the client and add paging |
| 3 | `checkIn` → `{log_type, time, location}` | Also `name`, `latitude`, `longitude`, `place_name`, `outside_radius`. `location` is `TBO Smart ±12 m` | **App** (done in 0.6.1: sends and shows `place_name`) |
| 4 | `Today.shift_*` are strings | Can be `null` with no shift. Extra `date`, `server_time`, `attendance` | **App**: handle null |
| 5 | `AttendanceStatus` lacks `Half Day`, `Work From Home` | HRMS returns them | **App**: widen the type |
| 6 | `LeaveBalance` has `allocated, used, balance` | Also `pending` | **App** (optional field) |
| 7 | `LeaveApplication.approver` is a name | `approver` (full name) plus `approver_id` (email), `half_day_date`, `posting_date` | **App** (additive) |
| 8 | `cancelLeave` sets status Cancelled | Open drafts are **withdrawn (deleted)**, approved leave is cancelled; both need permission (§8.5) | **App** handles 403 and `withdrawn`; **owner** decides on permission |
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
| 27 | `Organization` = ERPNext Customer with `health`, `health_reason`, `amc`, `contacts` | CRM Organization with `erpnext_customers[]`, `open_deals`, `contacts[]` and `deals[]` (detail). **No `health` or `amc`** (§6) | **App**: drop health; take AMC from the hub |
| 28 | `SalesSnapshot` camelCase (`byStage`, `wonThisMonth`, `closingThisWeek: Deal[]`) | snake_case (`by_stage`, `won_this_month`, `closing_this_week` with slim rows), plus `currency` | **App** |
| 29 | ESS `getNotifications` / `markNotificationsRead` | Not part of this backend. The push payload carries the route. A notification list for tboindia is a follow-up. | **Backend** (v1.1) if the app keeps an ESS notification list |
| 30 | Screens shown by role or a "Preview as" switch | `get_me().capabilities` (or `capabilities.get_my_capabilities` without an Employee) decides every screen, and `crm_scopes` decides the Mine / Team / All switcher | **App** |
| 31 | Manager dashboards built from per-person lists | `manager.get_team_insights` and `crm.get_sales_insights` return ready aggregates, deltas and 8-period trends; render them as they come | **App** |

## 10. Tests

The tests are in `tbo_smart/tests/test_mobile_{ess,advance,crm,lead_capture,manager,quotation,push,sso}.py` and `test_checkin_location.py`, with helpers in
`tbo_smart/test_utils.py`. They cover:

- **ESS:** own-only data and server-time check-in; a half day inside a multi-day leave (2.5
  days), the default first day, a half-day date outside the range refused, a one-day half day.
- **Check-in location** (`test_checkin_location.py`): inside, outside (message names the
  nearest location and distance), any of several locations, accuracy tolerance capped at
  50 m, poor accuracy refused, shift location over the fallback list, exempt role, remote
  employee, switch off and nothing configured block nothing, the
  controller blocking a desk insert with and without coordinates, HR entries for others
  unaffected, the settings refusing a location without a radius; place name from the device,
  the background lookup queued after commit and off without a URL, the Nominatim answer
  parsed and cached, a failure logged and not cached, one request per second. HTTP is
  mocked; no test reaches Nominatim.
- **Approvals:** a leave approval shows its `half_day_date`.
- **Manager:** endpoints refuse non-managers and non-approvers; only the named approver can
  decide.
- **CRM:** visibility follows CRM rules. Call today (`test_smart_leads.py`): order, team permission, the off switch and the shape against `docs/fixtures/crm.get_call_today.json`.
- **Lead capture** (`test_mobile_lead_capture.py`): phone formats (`+971 50…`, `050…`,
  `00971`, spaces and brackets), email and organization case, matches across leads,
  organizations, contacts and customers; hidden matches without details; 409 without
  `force`, then creation with the duplicate note; the follow-up task's due date, assignee
  and description; `assign_to` refused for staff and outside the team, allowed for a manager
  and a team lead within scope, refused for a non-CRM assignee; the private card image; the
  source channel note and Lead Source mapping.
- **Capabilities** (`test_mobile_capabilities.py`): each role combination (plain employee,
  leave approver, expense approver, Sales User, Sales Manager, HR Manager, manager with
  reports, user without an Employee), the HR Settings flags, and scope enforcement (staff
  cannot ask for `team` or `all`; a manager switches; the sales hierarchy gives `team`).
- **Insights** (`test_mobile_insights.py`): period windows, figures on fixtures, refusal
  without the capability or outside the scope, and no location or punch time in team
  insights.
- **Employee advances** (`test_mobile_advance.py`): create with the company currency and
  default advance account; amount above zero and purpose required; a foreign advance
  account refused; own-only list with HRMS's paid, claimed, returned, pending and balance
  figures; withdrawing an own draft, refusing someone else's and a paid one; the `advances`
  and `approve_advance` flags (the Expense Approver role alone is not enough); approvals
  refused to non-approvers; the routed approver sees and approves (submits) only their
  drafts; rejection refused without a workflow; HR sees every draft; no deciding your own;
  and with a workflow, approve and reject through it.
- **Push:** off by default, payload, dead-token cleanup, never raises.
- **Single sign-in** (`test_mobile_sso.py`): a locally generated RSA key signs the ID tokens
  and the JWKS download is mocked. A valid token issues an access token linked to the OAuth Client, with no refresh token;
  wrong `aud`, `iss` or `tid`, expired or not-yet-valid, bad signature, nonce mismatch and
  replay are refused with 401; unknown, disabled and role-less users get 403; the switch
  off refuses; `common` is refused as a tenant; a refresh through Frappe's `get_token` is
  refused; a disabled user's existing token is rejected by Frappe's Bearer check; and
  disabling a User revokes their tokens.

They need a site with **erpnext, hrms and crm** installed and `allow_tests` on:
`bench --site <site> run-tests --app tbo_smart`. CI (`.github/workflows/ci.yml`) installs
those apps on Frappe version-15.

## Changelog

- **v1 (2026-10-10):** first contract. ESS, manager, CRM, devices and Expo push.
- **v1.1 (2026-10-11):** capabilities from real roles (§4) in `get_me` and
  `capabilities.get_my_capabilities`; every endpoint checks the flag it advertises (CRM now
  needs `crm`; approvals follow named approvers and HR roles, not the approver roles alone).
  `scope` on `get_leads`, `get_deals` and `get_my_follow_ups`. New read-only
  `manager.get_team_insights` and `crm.get_sales_insights`. `get_sales_snapshot` follows the
  caller's `crm_scope`. Follow-ups add `assigned_to` and `assigned_to_name`.
- **v1.2 (2026-10-11):** single sign-in with Microsoft (§2.5):
  `auth.exchange_microsoft_token` trades an Entra ID token for this site's own access token
  (no refresh token; the app re-exchanges a fresh ID token). Disabling a User revokes their
  OAuth Bearer Tokens.
  New TBO Smart Settings fields: Single sign-in with Microsoft, Microsoft tenant ID, Mobile
  Entra client ID and OAuth Client.
- **v1.3 (2026-10-11):** lead capture (§6.2). New `crm.check_duplicates` and
  `crm.get_assignable_users`. `crm.create_lead` adds `follow_up_on`, `follow_up_note`,
  `assign_to`, `source_channel`, `card_image` and `force`, and refuses likely duplicates with
  HTTP 409 `DuplicateLead` unless forced. `get_options` already had `lead_sources`.
- **v1.4 (2026-10-12):** quotation approvals in the app (§5.2). New
  `tbo_smart.api.mobile.quotation` (`get_limits`, `get_approvals`, `get_trail`, `decide`)
  over `tbo_smart.api.quotation_adjustment`, with `actions` on the trail. New capability
  `approve_quotations` (§4). Push routes `TBO Quotation Adjustment` to `/quotations/<name>`.
- **v1.5 (2026-10-13):** check-in location (§3.2, `docs/checkin-location.md`). `check_in`
  takes `place_name`; punches return `place_name` and `outside_radius`. New
  `ess.get_checkin_locations`. Self check-ins outside the allowed Shift Locations are refused
  on every channel (`CheckinOutsideRadiusError`, `CheckinAccuracyError`). New TBO Smart
  Settings section **Check-in location**, Employee **Allow remote check-in** and Employee
  Checkin fields Place, Outside radius, GPS accuracy, Nearest allowed location and Distance.
  Half day on a leave of several days: `apply_leave` validates `half_day_date` within the
  range, new `ess.get_leave_days` previews HRMS's total, and leave approvals
  (`manager.get_approvals`) return `half_day_date`.
- **v1.7 (2026-10-15):** `crm.get_call_today` (§6.3), the smart leads Call today list
  (`docs/smart-leads.md`). Needs TBO Smart Settings → Lead scoring and Call today.
- **v1.6 (2026-10-13):** employee advances. New `ess.get_advances`, `ess.create_advance` and
  `ess.cancel_advance` (§3.1); new drafts are shared with the employee's expense approver.
  New capabilities `advances` and `approve_advance` (§4). `manager.get_approvals` includes
  advances (`kind: "advance"`, with `currency`) and every row gains `can_reject`;
  `manager.decide_approval` accepts `kind="advance"` (approve submits; reject needs a
  workflow).
