# Telephony through CallerDesk (living spec, v1.3)

**Status:** built 2026-11-14, behind TBO Smart Settings → **Telephony → Telephony provider =
CallerDesk**, off by default (MyOperator stays the default provider). Telecallers' calls through
CallerDesk (app.callerdesk.io) land on the lead by themselves, with their recordings, exactly as
the MyOperator calls do (`telephony-myoperator.md` §3, §5, §6): the same TBO Call, the same call
Event, the same "Call back" task for a missed incoming call, the same AI summary, retention and
call batches. The hub's lead page and the TBO Smart app place calls with **Call**, which rings the
telecaller's phone first and then the lead. No screen changed.

Code: `tbo_smart/telephony/callerdesk.py` (everything shaped like CallerDesk: `parse_report`,
`click_to_call_params`, `place_call`), the provider switch in `tbo_smart/telephony/calls.py`
(`parse_call`, `click_to_call`, `fetch_recording`, `map_agent`, `can_call`) and the webhook
`tbo_smart.api.telephony.callerdesk_webhook`. Tests: `tbo_smart/tests/test_callerdesk.py`.

## 1. What CallerDesk's documentation says

Sources: CallerDesk's full API reference (its Postman collection, read in full on 2026-11-14: webhook payloads, click-to-call variants, call report, live call, members, break control), CallerDesk support's answers of 2026-11-14, <https://docs.callerdesk.io/sample-payload>, <https://docs.callerdesk.io/setup-live-webhook>,
<https://docs.callerdesk.io/what-is-webhook-event>, the API reference
<https://api.callerdesk.io/view/metadata/2sA3kPp4vp>, and CallerDesk's own LeadSquared guide
(<https://docs.callerdesk.io/leadsquard-integration/>), the only public page that shows the
click-to-call URL. Read on 2026-11-14.

### 1.1 Confirmed

| Topic | What CallerDesk says (source) |
| --- | --- |
| Auth | The API key goes in the `authcode` parameter; HTTPS only. Most APIs are POST form (`authcode`); click-to-call and live_call are GET (docs). |
| Call Report webhook | JSON with `"type": "call_report"`, `SourceNumber` (caller), `DestinationNumber` (virtual number), `DialWhomNumber` ("agent number on which the call has been answered"), `CallDuration` ("total call duration from initiation to end"; the sample is 26 for StartTime to EndTime 26 s), `coins`, `Status`, `StartTime`, `EndTime` (`yyyy-mm-dd hh:mm:ss`), `CallSid`, `CallRecordingUrl`, `Direction` (`IVR` incoming, `WEBOBD` outgoing), `campid` (outgoing only), `TalkDuration` ("if the call was answered", sample 2), `call_group`, `receiver_name`, `error_code`, `LegA_Picked_time`, `LegB_Start_time`, `LegB_Picked_time`, `key_press`, `hangup_cause` (sample `ANSWER(16-customer)`), `Uniqueid` (docs, Post Webhook payload). |
| Who is who on WEBOBD | The sample is an outgoing call: `receiver_name` "Aniket" answered on `DialWhomNumber` 0765208xxx, the member number the click-to-call docs use as the agent; `SourceNumber` is the customer. So customer = `SourceNumber`, agent = `DialWhomNumber` (docs; confirm with a real call, §1.3). |
| Durations | `CallDuration` is the whole call (ringing included), `TalkDuration` the conversation (docs). |
| Report or live | A Call Report carries `type` `call_report`. The Live Call sample lists only `SourceNumber`, `DestinationNumber`, `DialWhomNumber`, `Status`, `StartTime`, `CallSid`, `Direction`, `call_group` and documents no `type` (docs). |
| Status values | Besides `ANSWER`: `cancel-Agent`, `cancel-Customer`, `CANCEL`, `Congestion`, `AgentEngaged`, `AbandonedCall`, `Agent-Unregistered`, `Unavailable`, `NOANSWER`, `Busy`. A separate `CallStatus` returns only "Answered" or "No Answer" (support, 2026-11-14). |
| Delivery | Call Report and Live Call webhooks are enabled on the account; delivered by POST in JSON (support, 2026-11-14). |
| Recordings | Downloadable from the recording URL without login; how long CallerDesk keeps them is not confirmed (support, 2026-11-14). |
| Coins | Click-to-call does not use click-to-call coins; OBD coins are deducted (support, 2026-11-14). |
| Browser calling | WebRTC calling in the browser is supported; no details given (support, 2026-11-14). |
| Webhook setup | Dashboard → API & Integration → Webhooks → Create Webhook: a public HTTPS URL, an alert email, the events. Up to five URLs. A premium feature. URLs containing CallerDesk's domain are refused. |
| Click-to-call request | `GET https://app.callerdesk.io/api/click_to_call_v2` with `calling_party_a` (the agent, called first), `calling_party_b` (the customer), `deskphone`, `call_from_did=1` (mandatory), `authcode`; the "with call group" variant adds `group_name` (party A must be in the group); `call_start_time` is optional (docs). |
| Click-to-call answer | Success: `{"type": "success", "message": "Call to Customer Initiate Successfully..", "campid": <int>, "callerid": "…"}`, `campid` at the top level (docs). A refusal is HTTP 200 `{"type": "error", "message": "…"}` (seen live, 2026-11-14). With no member in the account CallerDesk says "Agent on break/Inactive. Call not allowed" (seen live). |
| Account | Deskphone `08065117042` (did_num `8065117042`); the member list endpoints need POST (seen live). |

### 1.2 Still assumed

1. **No signature, no header auth.** The docs say nothing about either, so the webhook's
   address carries a long random token (`?token=…`), checked in constant time.
2. **Times are India time.** The docs give the format only, not the zone.
3. **Live Call `type`.** Not documented; any `type` other than `call_report` is taken as live,
   and with no `type` the old rule (a CallSid but no EndTime and no CallDuration) decides.
4. **What Live Call `ANSWER` means on an outgoing call**, and whether Live Call fires again
   when the second leg picks up (§3.1).
5. **Customer numbers.** Indian numbers are sent as 10 digits; any other country's with its code.
6. **Recordings** are WAV files; we keep the format. `CallRecordingUrl` must be https.

### 1.3 To confirm with a real test call

- On a WEBOBD report, that `SourceNumber` is the customer and `DialWhomNumber` the telecaller
  (the code still swaps them when only `SourceNumber` is a mapped telecaller's number).
- The Live Call payload: its `type`, and the order of its events for one click-to-call.
- That `Status` of an unanswered click-to-call is one of the listed values.
- The time zone of the times.
- Questions for CallerDesk: is there a **hang-up API** (end a running call by `sid_id`)? Their
  Postman collection (read 2026-11-14) has none, only `hangup_cause` in reports. Is a webhook's
  `CallSid` the same as `call_list_v2`'s `sid_id` (the first real call's recording file name ends
  in its `sid_id`, 59884394)? Does `call_list_v2` take `current_page` and `start_date` /
  `end_date` as the docs say, and does a row carry `campid` or `channel`?

## 2. Settings (TBO Smart Settings → Telephony)

Enable telephony, then choose **Telephony provider: CallerDesk**. MyOperator's fields hide; the
shared ones (create leads, owner of new caller leads, retention, AI summary) stay.

| Field | Meaning |
| --- | --- |
| Virtual number (deskphone) | The CallerDesk number calls go through, without +91. Required for Call. |
| Call group | Optional. Sent as `group_name` when set. |
| API key (authcode) | Password field. Required for Call. |
| Webhook token | Read only, made by itself when CallerDesk is chosen and saved. The full webhook address to paste into CallerDesk is shown under it (System Managers). **Regenerate webhook token** makes a new one; the old address stops working. |
| Telephony users (TBO Telephony Agent rows) | The same table as MyOperator's, with a new column **CallerDesk number**: the number CallerDesk rings for the person. A row needs a MyOperator user ID, a CallerDesk number or a mobile. A call's agent is found by the CallerDesk number or the mobile (last 10 digits, so +91 and a trunk 0 don't matter). |

The secret of CallerDesk's Basic-auth APIs isn't a field: nothing here calls those.

## 3. A call arriving (`callerdesk_webhook`)

1. **Guest POST**, JSON body, rate limited (600 a minute per IP). Telephony must be on and the
   provider CallerDesk, else 403.
2. **Authentication:** the `token` in the address must equal the webhook token
   (`hmac.compare_digest`). Anything else gets a 403. Nothing in the payload decides who may call.
3. `calls.receive` stores one **TBO Call** per `CallSid` (a repeat returns
   `{"ok": true, "duplicate": true}`) and queues `process_call`, the same job as MyOperator's.
   The report is normalised by `callerdesk.parse_report` into the same shape
   `myoperator.parse_event` makes:

   | TBO Call | From |
   | --- | --- |
   | call_id | `CallSid` |
   | direction | `Direction` `WEBOBD`/`OBD` → Outgoing, anything else Incoming |
   | status | `Status` `ANSWER` → Answered, every other value Missed (list in §1.1); when `Status` is missing or not in that list, `CallStatus` ("Answered" / "No Answer") decides |
   | provider_status | the `Status` as CallerDesk wrote it (or the `CallStatus` stand-in) |
   | customer_number | `SourceNumber` |
   | agent | `DialWhomNumber`, mapped to a user through the table (swapped with `SourceNumber` on an outgoing call only when `SourceNumber` is the mapped telecaller's) |
   | started_at, ended_at | `StartTime`, `EndTime` (India time, converted to the site's time zone) |
   | duration, talk_seconds | `CallDuration` (whole call), `TalkDuration` (answered calls only; a missing one, not a 0, falls back to `CallDuration`) |
   | recording_filename | `CallRecordingUrl`, answered calls only |
   | reference_id | `campid` |

   **What the status means for the lead and the batches** (`calls.call_outcome`, `outcomes.sync_calls`):

   | `Status` | TBO Call | Lead's last call / batch item |
   | --- | --- | --- |
   | `ANSWER` | Answered | Connected |
   | `Busy`, `AgentEngaged` | Missed | **Busy** (retried like Not answered) |
   | `NOANSWER`, `Unavailable`, `Congestion`, `AbandonedCall`, `CANCEL`, `cancel-Customer` | Missed | Not answered |
   | `cancel-Agent`, `Agent-Unregistered` | Missed | nothing: the telecaller's own side failed, the lead was never tried (no lead result, not counted on a batch) |

4. `process_call` is otherwise unchanged: the click-to-call's lead and agent first (by `campid`), else the
   lead with the customer's number among all its numbers, else a new lead for an unknown incoming
   caller (when "Create a lead for an unknown caller" is on), the call Event on the lead, a "Call
   back" task for a missed incoming call, and the call's outcome for call batches.
5. **Recording:** the long-queue job `fetch_recording` downloads `CallRecordingUrl` (https, at
   most 50 MB) into a private File on the call. The daily purge keeps to the retention days.

## 4. Click-to-call

`click_to_call` (hub and app, unchanged) checks the person may call: telephony on, a sales role,
a CallerDesk number or a mobile in the table. With the provider CallerDesk it sends:

```
GET https://app.callerdesk.io/api/click_to_call_v2
    ?calling_party_a=<the person's CallerDesk number, else mobile, 10 digits>
    &calling_party_b=<the lead's number>
    &deskphone=<virtual number>&call_from_did=1&authcode=<API key>[&group_name=<call group>]
```

CallerDesk rings the telecaller first, then the lead. Only an answer with `type` "success" is an
accepted call; its top-level `campid` is remembered
for 6 hours with the lead and the person, so the call report lands on that lead. Answer to the
user: "CallerDesk is calling your phone…". Errors are plain: "CallerDesk didn't accept the call:
<its reason>" (after "Agent on break/Inactive…" it adds "Ask an admin to add your number as an active member in CallerDesk."), "…it refused the API key…", "Couldn't reach CallerDesk. Try again in a minute."
The key is in the request's URL, so no failure message or log shows the URL.

**Test connection** (settings API) only checks that the key and the virtual number are set;
CallerDesk has no call that proves the key without placing a call.

The "Reverse C2C" (`click_to_call_v3`, customer first) variant isn't used. Click-to-call does not
use click-to-call coins; OBD coins are deducted (support, 2026-11-14).

### 4.1 Other CallerDesk APIs we could use (not built)

- **Add / Update member** (`add_member_v2`, `update_member`, POST): add a telecaller as an active
  member from the Telephony users table, so "Agent on break/Inactive" never has to be fixed by hand.
- **Member list** (POST): check, before dialling, that the person's number is an active member,
  and tell them plainly if not.
- **Member break status / control** (`member_analysis_v2`, `update-working-status-v2`): read
  `working_status` (Ready, breaks) to know the member is callable, or set it (Login, Sleep, breaks).
- **live_call_v2** (GET): the calls running now; a poll for the call box if Live Call webhooks are missed.
- **call_list_v2** (POST, filters by date, `Flow_type`, `callstatus`): used since v1.3 to store any call whose webhook never arrived (§3.2).
- **click_to_call_v4** (GET, `member_id` instead of party A): rings a member by id, so a changed phone number needs no change here.
- **singleleg-clicktocall** (POST, `agent_number`, `customer_number`, `callerid`) and **app_call_list_v2**: for calls dialled from CallerDesk's mobile app (one leg), with their own report.

None is called today; we make no live calls except click-to-call.

### 3.2 Missed webhooks: the reconcile (`calls.reconcile_callerdesk`, `calls.refresh_my_call`)

A missed webhook would lose the call and its recording (the first real call, 2026-11-14, never
arrived). So every **10 minutes**, while telephony is on, the provider is CallerDesk and the API key is
set, the site asks CallerDesk's **call report** for today's and yesterday's finished calls:

```
POST https://app.callerdesk.io/api/call_list_v2   (form)
    authcode, start_date=yyyy-mm-dd, end_date=yyyy-mm-dd, current_page=1, 2, …
```

- Pages are read until one adds no `sid_id` not seen before (at most 20 pages of 25).
- `callerdesk.report_from_row` turns a row into the Call Report the webhook would have sent:
  `sid_id` → `CallSid`, `caller_num` → `SourceNumber` (customer), `member_num` → `DialWhomNumber`
  (telecaller), `callstatus` → `Status`, `startdatetime` / `enddatetime`, `total_duration` →
  `CallDuration`, `talk_duration` → `TalkDuration`, `file` → `CallRecordingUrl`, the leg times;
  Direction is WEBOBD when `channel` / `Flow_type` says so or `callresult` is "Click-to-call",
  else IVR. A row with no `enddatetime` (still running) is left for the next run.
- Each report goes through the normal `calls.receive`: lead match, agent, status, durations,
  recording download, AI summary and batch sync all follow. **Idempotent:** a `sid_id` that already
  has a closed TBO Call is skipped; one the Live Call webhook stored but no report closed is
  completed. The CallSid is assumed to equal `sid_id` (§1.3).
- Rate-safe: one run at a time (a 5-minute lock), one request per page, no more than 20. A CallerDesk
  failure is logged without the request (the key is in it).

`tbo_smart.api.telephony.refresh_call(reference_id)` (POST, hub-leads-api §2.33) does the same
for the hub's call box when it reaches "Can't see this call any more", for **the person's own
calls only**: the person must be the one who placed the click-to-call, it looks at their calls (by
`member_num` = their number) of the last 6 hours, stores the missing ones, links the newest to the
click-to-call's `campid` so `get_call_status` answers `ended`, and runs at most once every 20 s per
person. Returns `{stored}`.

### 3.1 Live Call and the call box (`calls.receive_live`, `calls.call_status`)

The same address takes a Live Call event. It is told apart from a Call Report by its `type`
(`call_report` is the report; any other `type` is live) and, with no `type`, by having a `CallSid`
but no `EndTime` and no `CallDuration` (`callerdesk.is_live`); the token check is the same. `receive_live` keeps the call's **live status** on its TBO Call (`live_status`: Ringing,
On call, Ended; `leg_a_picked_at`, `leg_b_picked_at`; `last_event` = `live` until the report):

- `Status` `ANSWER` → **On call** (`leg_b_picked_at` = when the event arrived), except an outgoing
  call whose `DialWhomNumber` is a telecaller's number: that is the telecaller's own phone being
  picked up, **Agent picked** (`leg_a_picked_at`), and the lead's phone rings next; any other
  status → **Ringing**. The Call Report sets **Ended** and the real `LegA_Picked_time` / `LegB_Picked_time`.
- Idempotent: it only moves a call forward (Ringing → On call); a repeat, an older status, or any
  event for a call the Call Report has already closed returns `{"ok": true, "duplicate": true}`
  and changes nothing. An event without a `CallSid` is ignored. It makes no lead Event and no
  task: that stays the Call Report's job (`process_call`), which completes the same row.
- The agent of an outgoing call is the click-to-call's person (by `campid`); of an incoming call,
  the telecaller whose number is `DialWhomNumber`.

`get_call_status(reference_id)` (`tbo_smart.api.telephony`, hub-leads-api §2.33) reads it for the
box: `calling` (placed, no event yet), `ringing` (the person's phone), `ringing_lead`, `on_call`, `ended`. Only the person who placed
the call (or whose call it is) may ask. MyOperator rows only ever say calling, then ended
(`call.end`). `get_incoming_call` returns a call ringing the person right now (a Live Call event
in the last minute that the report hasn't closed).

**Assumed (see §1.2):** Live Call fires when a leg is ringing/connected and again
with `ANSWER` when picked; for an outgoing click-to-call we can't tell which leg `ANSWER` is, so
"On call" starts when it arrives. Incoming Live Call events carry `Direction` `IVR` and the
caller in `SourceNumber`. If none of this arrives, nothing breaks: the box waits for the report.

## 5. What the owner does

In CallerDesk (app.callerdesk.io):

1. Dashboard → API & Integration: copy the **API key (authcode)**.
2. Add each telecaller as a member with their mobile (and a call group, if the account uses one).
3. Dashboard → API & Integration → Webhooks → **Create Webhook**: URL = the address shown under
   Webhook token in TBO Smart Settings, method POST, an alert email, events **Call Report** and
   **Live Call** (the hub's call box shows "Ringing" and "On call" from it; without it the box still
   works but only says "Calling your phone…" until the call ends). If the webhook
   isn't offered, ask the account manager (it's a premium feature).
4. Play the recording notice (DPDP) in the number's greeting before turning recording on.

In TBO Smart Settings → Telephony:

1. Tick **Enable telephony**, choose **CallerDesk**, fill the virtual number and the API key
   (and the call group), **Save**; the webhook address appears. Paste it into CallerDesk.
2. In **Telephony users**, add each telecaller with their CallerDesk number (or mobile).
3. Place a test call: it shows on the lead with its recording.

Switching the provider doesn't touch existing calls. Switch only when nobody is on a call: the
old provider's webhook is refused from then on.

## 6. Tests (`tbo_smart/tests/test_callerdesk.py`)

An answered report lands on the right lead with agent, status, durations and time zone; the
recording is downloaded privately and the job is queued on the long queue; a repeated `CallSid`
is ignored; wrong, empty or missing tokens and the wrong provider are refused; a missed call
makes a Call back and no recording; an unknown caller becomes a lead, or not when switched off;
an outgoing call finds its customer; a click-to-call's `campid` lands the call on its lead;
the request's URL and params, with and without a call group, a mobile instead of a CallerDesk
number; refusals in plain words and no key in them; the token is made on choosing CallerDesk,
never returned by the settings API and shown only to a System Manager; Live Call events (`TestLiveCall`): ringing then on call, repeats and late events ignored, the report closes the call, only the caller can read its status, an incoming call shows to its telecaller only. every documented `Status`, the `CallStatus` fallback, Busy and the telecaller's own failure for the lead and the batches (`TestStatuses`, `test_call_batches.py`), report vs live by `type`. `TestReconcile` (v1.3): a missed call lands on its lead with its recording, a stored call isn't stored twice, a stuck live call is closed, an unfinished row waits, no run for another provider, a failing call list logs without the key, the box's refresh is the caller's own and throttled. MyOperator's tests
(`test_telephony.py`) are unchanged.

## Changelog

- **v1.3 (2026-11-14):** the reconcile (§3.2): a 10-minute job that stores calls whose webhook
  never arrived from `call_list_v2`, and `refresh_call` for the hub's box; questions for
  CallerDesk (hang-up API, `sid_id`). No migrate needed.
- **v1.2 (2026-11-14):** aligned with CallerDesk's API reference and support's answers: report
  vs live by `type`; customer = `SourceNumber`, agent = `DialWhomNumber` on outgoing calls;
  `TalkDuration` no longer falls back to the whole call; every documented `Status` and the
  `CallStatus` fallback (new `provider_status` on TBO Call); Busy / AgentEngaged count as Busy,
  cancel-Agent / Agent-Unregistered as no attempt (lead and batches); only `type` "success" is
  an accepted click-to-call; hint after "Agent on break/Inactive"; §1 split into confirmed /
  assumed / to confirm; §4.1 other APIs. Needs `bench migrate` (new field).
- **v1.1 (2026-11-14):** Live Call events (§3.1), `live_status` and picked-at fields on TBO Call,
  `get_call_status`, `get_incoming_call`, for the hub's call box. Owner: tick Live Call in the webhook.
- **v1.0 (2026-11-14):** first version: provider switch, Call Report webhook, click-to-call,
  recordings, CallerDesk number in the users table.
