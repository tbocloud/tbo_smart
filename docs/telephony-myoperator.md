# Telephony through MyOperator (living spec, v1.0)

**Status:** built 2026-10-19, behind TBO Smart Settings → **Telephony (MyOperator)**, off by
default. A salesperson's calls with leads go through MyOperator's cloud telephony. Each call
lands on the lead by itself, with its recording. A missed call becomes a "Call back" task, and
the AI can sum the call up. The hub's lead page and the TBO Smart app place calls with **Call**,
which rings the salesperson's phone first and then the lead.

Code: `tbo_smart/telephony/` (`myoperator.py` holds everything shaped like MyOperator,
`calls.py` the webhook and the calls, `summary.py` the AI summary), `tbo_smart/api/telephony.py`
(the API), doctypes **TBO Call** and **TBO Telephony Agent**. Tests:
`tbo_smart/tests/test_telephony.py`.

## 1. What MyOperator's documentation says

Sources:

- MyOperator's API reference (Postman):
  <https://documenter.getpostman.com/view/38426694/2sAXqy3evq>. "PM" below refers to it.
- The support index that links to it:
  <https://support.myoperator.com/portal/en/kb/articles/myoperator-api-reference-postman-documentation-links>.

Read on 2026-10-19.

### 1.1 Confirmed

| Topic | What the docs say |
| --- | --- |
| Keys | Panel → APIs & Webhook → Developer API → Calling APIs → Endpoint details: the **x-api-key**, **secret token**, **company ID**, **public IVR ID**. The **API token** for `developers.myoperator.co` (users, call logs, recordings) is on the same page. |
| Click-to-call (OBD API) | `POST https://obd-api.myoperator.co/obd-api-v1`, header `x-api-key`, JSON body `{company_id, secret_token, type, number, public_ivr_id, user_id \| number_2, reference_id?, caller_id?, call_hold?, max_call_duration?}`. Type **"1"** is the *User dialer* when `user_id` (the agent's UUID) is sent, and the *Anonymous dialer* when `number_2` (the agent's number) is sent. Numbers include the country code. `reference_id` must be unique and is echoed back. Answer: `{"details": "Request accepted successfully", "status": "success", "code": "200", "unique_id": "<request_id>", "reference_id": "…"}`. Errors use the same shape with `"status": "error"`. A **403** means one of: IP allowlist, usage threshold, duplicate `reference_id`, billing expiry. |
| Webhooks v2 | A POST of an envelope `{event_id, company_id, event_type, event_sequence, timestamp, channel, direction, session_id, customer_identifier, payload}`. Call events are `call.initiated`, `call.dial_begin`, `call.answered`, `call.end`, `call.summary` and `disposition`. `call.summary` is "the final event … should be used for CRM updates". |
| Call payload | `{id, direction (incoming \| outgoing), customer_number (E.164), status (bridged \| missed \| voicemail), started_at, ended_at (ISO 8601), duration (s), recording_filename, ref_id, client_ref_id (the reference_id we sent), legs: [{type (agent \| customer), result (answered \| not_answered \| connected), talk_duration, phone_number, agent: {uuid, name, email, extension, contact}}]}` |
| Webhook auth | Set in the panel. It can be **custom headers** (the docs' example is `X-Webhook-Token: your-secret-token`), Basic Auth or "API Key Auth" (a header). The docs mention no signature. |
| Retries | "Webhook retries may occur … use `event_id` to detect duplicates". "Events may arrive out of order". "Always return HTTP 200 OK immediately … queue the event". |
| Recordings | Webhooks v2 carry only `recording_filename`. Get a link with `GET https://developers.myoperator.co/search/recordings/link?token=<API token>&file=<recording_filename>`. "Recording links are valid for 24 hours only", so we download the file and keep it privately. |
| Users | `GET https://developers.myoperator.co/search/user?token=…&page-size=100`. Each row is `{uuid, name, email, extension, contact_number, …}`. The `uuid` is the agent's ID in `legs[].agent.uuid` and the OBD `user_id`. |
| Webhooks v1 | Deprecated: the `_ai`/`_cl`/`_su`/`_fu` keys, sometimes form-encoded as `myoperator=…`. Since 28 Nov 2025 new webhooks are POST + JSON, so **we read v2 only**. |

### 1.2 To confirm with MyOperator support

1. **The OBD API's second header.** The support index lists a `secret-key` header as well as
   `x-api-key`. PM sends only `x-api-key` plus `secret_token` in the body. We send what PM shows.
   Ask whether `secret-key` is needed and whether its value is the same as the secret token.
2. **IP allowlist for the OBD API.** A 403 "Unauthorised source IP" suggests the ERP server's
   public IP must be allowlisted (Panel → Network access). Ask them, and give them the server's
   IP.
3. **The recording link answer.** PM gives no example response. We take the first `https://`
   URL anywhere in the answer (`myoperator.find_url`). Confirm the key, and whether the link
   needs the token too.
4. **`call.summary` for missed calls.** PM says the summary's legs are "never empty", while
   `call.end` covers missed calls. We take both events: whichever comes first makes the call, and
   a later `call.summary` completes it. Confirm that both are sent when both are subscribed.
5. **Who rings first.** PM's leg samples show agent-first and customer-first OBD calls, and no
   request field picks the order. Ask them to set the click-to-call IVR to **agent first**.
6. **Webhook source IPs and retry count/backoff.** Not documented.
7. **Signing.** A support-article snippet mentions an `X-MyOperator-Signature` HMAC when a
   "signing secret" is enabled. It isn't in PM. If MyOperator offers it, it can be added next to
   the header check.

If a field turns out to be named differently, the only place to change is `myoperator.py`:
`parse_event`, `click_to_call_body`, `find_url` and `list_users`.

## 2. Settings (TBO Smart Settings → Telephony)

| Field | Meaning |
| --- | --- |
| Enable telephony | Off by default. While off, the webhook refuses everything (403) and the Call buttons are hidden. |
| MyOperator company ID, Public IVR ID, Caller ID (optional) | Click-to-call. |
| Calling API key (x-api-key), Calling secret token | Password fields: click-to-call. |
| API token (users and recordings) | Password field: recordings and Test connection. |
| Webhook secret | Read only. Made by **Regenerate webhook secret** (desk button or hub). It is shown once, and from then on only "set" is shown. |
| MyOperator users (TBO Telephony Agent rows) | `user` ↔ MyOperator user ID (UUID) / extension / mobile. One row per person, each with a user ID or a mobile. |
| Create a lead for an unknown caller | On by default. |
| Owner of new caller leads | Used when nobody here answered. Blank: the smart leads fallback owner. |
| Keep recordings (days) | Blank: 180. Every night, older recordings and transcripts are deleted; the call and its summary stay. |
| Recording notice | The text to set as the IVR greeting (see §7). |
| AI call summary | Off by default. Shortest call to sum up (blank: 30 s), transcription model (default `gpt-4o-transcribe`), price per minute (default $0.006, for the usage log). |

Validation: no person twice, every row has a user ID or a mobile, and no negative numbers.
A Single's defaults apply only to a new document, so the migrate gives an existing site these
defaults once, for any field that was never saved (`telephony/setup.py`): create leads on, 180
days, the notice, 30 s, the model and its price.

## 3. A call arriving (`tbo_smart.api.telephony.myoperator_webhook`)

1. **Guest POST**, rate limited (600 a minute per IP). Telephony must be on.
2. **Authentication:** the `X-Webhook-Token` header must equal the webhook secret
   (`hmac.compare_digest`). Anything else gets a 403. No field in the payload decides who may
   call this.
3. Only `call.end` and `call.summary` are read. Any other event gets `{"ok": true, "ignored": true}`.
4. **Once per call:** a **TBO Call** keyed by MyOperator's call ID (`payload.id`, unique) holds
   the call and its last payload. A repeat of an event already seen returns
   `{"ok": true, "duplicate": true}`. A `call.summary` after a `call.end` completes the stored
   call. Two requests for the same call at once hit the unique key: the loser is a duplicate,
   unless it is the `call.summary`, which then completes the stored call.
5. `process_call` runs in the background, short queue. It runs again safely for the same call
   because it locks the call's row.
   - **Agent:** the click-to-call that started the call (its `reference_id`, remembered 6 hours).
     Otherwise the answered agent leg, matched through the mapping by user ID, extension or
     mobile, else an enabled user with the agent's email.
   - **Lead:** the click-to-call's lead. Otherwise the lead whose mobile, phone or WhatsApp
     number is the caller's. Spaces, dashes, brackets, +91 and a trunk 0 are ignored, and an open
     lead comes before a closed one, then the newest.
   - **Unknown incoming caller:** a new Lead named by the number, with mobile = the number,
     source **Phone call** (made on migrate) and owner = the agent, else the fallback owner.
     Outgoing calls to unknown numbers stay unlinked TBO Calls.
   - **The call Event** (category Call, Completed, at the call's start in site time) is created
     as the agent (else the lead owner, else Administrator). Its subject is "Call from +91… (2m
     05s)", "Call to …", "Missed call from …" or "Unanswered call to …".
   - **A later `call.summary`** (after `call.end` already made them) keeps the one Event and
     brings it up to date: its subject, start and owner (the agent). A Call back made for a
     call that turned out answered is cancelled, unless another missed call shares it; the task
     stays linked on the TBO Call (`call_back`).
   - **Missed incoming call:** one high-priority "Call back: +91…" follow-up due today for the
     lead owner (else the agent, else the fallback owner). It is skipped when one is already
     open for them today.
   - **Recording:** for an answered call with `recording_filename`, a long-queue job fetches the
     24-hour link and downloads the file (https only, at most 50 MB). It becomes a **private
     File** attached to the TBO Call. A failure is logged in the Error Log.

TBO Call is readable by System Managers only, so the transcript and the raw payload stay there.

## 4. API (`tbo_smart.api.telephony`)

| Method | Verb | Arguments | Returns |
| --- | --- | --- | --- |
| `myoperator_webhook` | POST (guest) | the v2 JSON body, header `X-Webhook-Token` | `{ok, ignored? , duplicate?}` |
| `click_to_call` | POST | `lead`, `number?` (one of the lead's own numbers, default its first: mobile, phone, WhatsApp), `acting_user?` | `{ok, message: "MyOperator is calling your phone…", number, reference_id, request_id}` |
| `call_recording` | GET | `call` (TBO Call name, from the timeline's `recording`), `acting_user?` | the audio (audio/mpeg, inline) |
| `get_settings` | GET | `acting_user?` | `{settings, agents, secrets: {field: {set, masked (the last 4 characters)}}, webhook: {url, header, events}, users: [{email, full_name}]}` |
| `save_settings` | POST | `values` (fields, secrets, `agents` replaces the mapping), `acting_user?` | as `get_settings` |
| `regenerate_webhook_secret` | POST | `acting_user?` | `{secret, header, url}`: the only time the secret is shown |
| `test_connection` | POST | `acting_user?` | `{ok, message, missing: [label], users: [{uuid, name, email, extension, contact}]}`: no call is placed |

- `acting_user` works as in `hub-leads-api.md` §1: the hub's integration user names the person.
  The settings methods need **System Manager** (the acting user's). `click_to_call` needs read
  on the lead and a mapping row, and is limited to one call every 15 seconds per person. Errors
  come back as messages people can act on (not set up, the lead has no number, MyOperator
  refused the key, IP or limit, MyOperator unreachable).
- `call_recording` streams only to someone who may read the call's lead. Anyone else gets 403.
  A missing recording gets 404.
- The app: `tbo_smart.api.mobile.leads.click_to_call(name, number?)` and
  `call_recording(call)` behind the `crm` capability. Capability `click_to_call` is true when
  telephony is on and the person has a sales role and is mapped (`tbo-smart-api-ess-crm.md` §6.6).
- The hub: proxies in tbocloud/helpdesk `helpdesk/api/leads.py`, and Settings → Telephony
  (`docs/leads.md`).

### 4.1 Calls on the timeline

`hub_leads.get_lead` timeline items of kind `call`, and the app's `activities` of kind `call`
(`crm.get_lead`), gain these fields when the call came through MyOperator:

```
{call (TBO Call name), duration (talk seconds), direction ("incoming" | "outgoing"),
 status ("answered" | "missed" | "voicemail"), recording (the call's name when there is one, else null),
 summary: {text, language, interest, sentiment, intent, objections: [text],
   next_step: {what, channel, due} | null, by: "TBO AI", at} | null}
```

`get_lead`'s `lead` and `get_options` gain `can_call`: the acting user may use click-to-call. The transcript is
never in any answer.

### 4.2 The Sales scoreboard

`get_sales_activity` rows gain `calls` (answered MyOperator calls the person took in the range)
and `talk_seconds` (their talk time), counted by the agent (`hub-leads-api.md` §2.5).

## 5. AI call summary (`telephony/summary.py`)

It runs for an answered call on a lead, at least the shortest length (talk time), with a
recording, while the AI call summary is on and an AI provider is set up. `process_call` marks
the call **Pending**, and the summary job runs after the recording download.

1. It takes one of the day's AI lead reads (`ai_daily_lead_limit`, the autopilot's counter). With
   none left, the call is marked **Skipped**.
2. **Transcription:** `POST {OpenAI base URL}/audio/transcriptions` with the OpenAI section's key
   (`ai.transcribe`). The model is the transcription setting, and no language is sent, so the
   model detects Malayalam, English or both. A short prompt names the languages. The usage log
   row is "Call transcription", priced as minutes × price per minute. A paused or failing
   OpenAI section marks the call **Failed**. A wrong speech model (`config`) doesn't pause the
   OpenAI section's chat features.
3. **One chat call** through the usual provider order and failover (`ai.call_ai`, "Call summary").
   It returns `{summary, language, interest, sentiment, intent, objections, next_step}`. Every
   text goes through the number guard against the transcript, today and the call's length. A
   summary that fails the guard marks the call **Failed** but keeps the transcript.
4. **Written:**
   - the summary on the TBO Call, and the transcript (private);
   - the call Event's description: the summary, concerns and next step, which is the timeline's
     text;
   - a "Summed up the call: … Next: … (by …)." Info comment by **TBO AI** on the lead.
5. **The autopilot:** with AI runs leads on, the summary becomes the lead's stored AI read:
   sentiment, intent, the next step as its next action, and its summary when it had none. It is
   stored with the current read hash, and the next step becomes the owner's Sales follow-up when
   none is planned (`smart-leads.md` §11). The call's Event is inserted with
   `flags.skip_ai_read`, so the autopilot does not read the lead a second time for this call.
   Calls without a summary are read by the autopilot as before.

## 6. Retention

`tbo_smart.telephony.calls.purge_old_recordings` runs daily, with telephony on or off. It
deletes the recording Files and clears the transcripts of calls older than **Keep recordings**
days, counted from the call's start (or the day it was stored, when MyOperator sent no start).
The TBO Call, the Event and the summary stay.

## 7. What the owner does in MyOperator

1. **Get a number:** Panel → Get number. Buy the virtual number, set it up as the IVR, and point
   its call flow at the sales users or the department.
2. **Recording notice (DPDP):** in the IVR greeting of the number and of the click-to-call IVR,
   play the text in **Recording notice** ("This call may be recorded for quality and training
   purposes."). Under the Digital Personal Data Protection Act 2023, callers must be told before
   a call is recorded. Turn call recording on in MyOperator only after the notice plays.
3. **Developer API:** Panel → APIs & Webhook → Developer API → Calling APIs → Endpoint details.
   Copy:
   - the **x-api-key** → Calling API key;
   - the **secret token** → Calling secret token;
   - the **company ID**;
   - the **public IVR ID** of the IVR that click-to-call should use;
   - the **API token** → API token.

   Paste them into hub Settings → Telephony (or desk TBO Smart Settings → Telephony). Ask
   MyOperator to allowlist the ERP server's IP for the OBD API, if they require it.
4. **Webhooks v2:**
   - Press **Regenerate webhook secret** and copy the secret.
   - In Panel → Webhooks v2 → New webhook:
     - URL: `https://india.teambackoffice.com/api/method/tbo_smart.api.telephony.myoperator_webhook`
     - Method: POST
     - Content-Type: application/json
     - Header `X-Webhook-Token` = the secret
     - Events: **call.end** and **call.summary**
   - Save. Then place a test call: it shows on the lead.
5. **Users:** in MyOperator, add each telecaller as a user with their mobile. In hub Settings →
   Telephony, press **Test connection**. It lists MyOperator's users with their UUIDs. Map each
   salesperson to their UUID (or extension or mobile). People not mapped can't use Call, and
   their calls land without an agent (the lead owner gets the call back).
6. **The customer-care mobile:** on the current customer-care SIM, set unconditional call
   forwarding to the MyOperator number. Callers keep dialling the same number and MyOperator
   handles the call. On most Indian networks dial `**21*<MyOperator number>#`; cancel with
   `##21#`. Forwarded calls are billed to the SIM by the operator, so check the SIM's plan.
7. **AI call summary (optional):** set up the AI providers (OpenAI with a key) and turn on **AI
   call summary**. Check the AI usage log for the cost.

## 8. Tests (`tbo_smart/tests/test_telephony.py`)

- **Webhook:**
  - a wrong, empty or missing secret is refused, and a forged company ID doesn't help;
  - while switched off, even the right secret is refused;
  - the same call lands once;
  - `call.end` followed by `call.summary` makes one Event, and the summary corrects its
    subject, owner and Call back;
  - a `call.summary` that loses the insert race still completes the call;
  - other events are ignored;
  - the call lands as the agent, in site time.
- **Matching:**
  - number formats;
  - phone and WhatsApp numbers, with an open lead first;
  - agents by user ID, extension, mobile and email;
  - an unknown caller becomes a lead, or doesn't (switched off, outgoing);
  - a missed call gives one Call back task.
- **Recording:**
  - downloaded privately through the link API;
  - only someone who may read the lead hears it (another salesperson, the hub acting for them
    and Guest are refused);
  - the timeline shows call fields without the transcript;
  - the retention purge.
- **Click-to-call:**
  - the request body and header;
  - the reference lands the call on its lead and agent;
  - the anonymous dialer for a mobile-only agent;
  - the app's capability and method;
  - refusals: not the lead's number, no read, MyOperator's 403, not mapped.
- **Settings:**
  - Test connection lists users without placing a call;
  - no secret ever comes back;
  - regenerating replaces the secret;
  - not available to non-managers.
- **AI summary** (transcription and chat mocked):
  - the requests;
  - the number guard;
  - the Event and the TBO AI note;
  - the autopilot's next action and follow-up;
  - usage and cost logged;
  - no "Lead insight" second call;
  - short calls make no AI call.
- **Scoreboard:** answered calls and talk time per agent.
- **Migrate:** the defaults reach an existing site once and never overwrite a saved value.

## Changelog

- **v1.0 (2026-10-19):** first version: webhook, click-to-call, recordings, AI call summary,
  timeline fields, scoreboard figures, settings API.
