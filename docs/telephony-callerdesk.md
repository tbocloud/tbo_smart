# Telephony through CallerDesk (living spec, v1.1)

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

Sources: <https://docs.callerdesk.io/sample-payload>, <https://docs.callerdesk.io/setup-live-webhook>,
<https://docs.callerdesk.io/what-is-webhook-event>, the API reference
<https://api.callerdesk.io/view/metadata/2sA3kPp4vp> (its Postman collection needs a Postman login
and could not be read), and CallerDesk's own LeadSquared guide
(<https://docs.callerdesk.io/leadsquard-integration/>), the only public page that shows the
click-to-call URL. Read on 2026-11-14.

### 1.1 Confirmed

| Topic | What the docs say |
| --- | --- |
| Auth | The API key goes in the `authcode` parameter; HTTPS only. (Postman: Basic auth, key and secret; we don't use it.) |
| Call Report webhook | Fields `SourceNumber`, `DestinationNumber` (virtual number), `DialWhomNumber` (who answered), `CallDuration`, `coins`, `Status` (sample `ANSWER`), `StartTime`, `EndTime` (`yyyy-mm-dd hh:mm:ss`), `CallSid` (id), `CallRecordingUrl`, `Direction` (`IVR` incoming, `WEBOBD` outgoing), `campid`, `TalkDuration`, `call_group`, `receiver_name`, `error_code`, `LegA_Picked_time`, `LegB_Start_time`, `LegB_Picked_time`. |
| Live Call webhook | Sample fields `SourceNumber`, `DestinationNumber`, `DialWhomNumber` (the agent number that answered), `Status` (`ANSWER`; the docs say outcomes such as agent engaged, busy, abandoned, answered, cancel), `StartTime`, `CallSid`, `Direction`, `campid`. No `EndTime`, no `CallDuration`. A "Live Call (Only Picked)" event exists too. |
| Webhook setup | Dashboard → API & Integration → Webhooks → Create Webhook: a public HTTPS URL, an alert email, the events. Up to five URLs. A premium feature (ask the account manager). URLs containing CallerDesk's domain are refused. |
| Click-to-call URL | `GET https://app.callerdesk.io/api/click_to_call_v2` with `calling_party_a` (the agent, no country code), `calling_party_b` (the customer), `deskphone` (the virtual number), `call_from_did=1`, `authcode`. The "with call group" variant adds `group_name` (calling_party_a must be a member of the group). Leg A is dialled first. |

### 1.2 Assumed (confirm with CallerDesk)

1. **No signature, no header auth.** The docs say nothing about either, so the webhook's
   address carries a long random token (`?token=…`), checked in constant time.
2. **Status values.** Only `ANSWER` is documented. Anything else (BUSY, CANCEL, NOANSWER, …) is
   stored as Missed.
3. **Who is the customer on an outgoing call.** The docs don't say which of `SourceNumber` and
   `DialWhomNumber` is the telecaller's on `WEBOBD`. We take the one that is a mapped telecaller's
   number (CallerDesk number or mobile) as the agent and the other as the customer; a click-to-call
   is found by its `campid` anyway.
4. **Durations.** The sample shows `CallDuration` 18 and `TalkDuration` 40, so which is the
   conversation isn't clear. We store `CallDuration` as the call's duration and `TalkDuration`
   (else `CallDuration`) as the talk time, for answered calls only.
5. **The click-to-call answer.** Not documented. Seen live (2026-11-14): a refusal is HTTP 200
   with `{"type": "error", "message": "Invalid Auth Code!"}`, so `type` is read too. A 2xx whose JSON doesn't say it failed
   (`status` error/fail/false, or an `error` key) is accepted; the `campid` is read from the
   answer's `campid` or `data.campid`. If CallerDesk sends none, the call report finds the lead by
   the customer's number instead.
6. **Customer numbers.** Indian numbers are sent as 10 digits; any other country's with its code.
7. **Webhook delivery.** Another CRM integration reports that CallerDesk didn't reliably push
   Call Reports and only sent the validation ping. Check the webhook is active, and ask the
   account manager to enable the event; only then do calls arrive. The recording links need no
   login (the same report).
8. **Recordings** are WAV files; we keep the format. `CallRecordingUrl` must be https.

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
   | status | `Status` `ANSWER` → Answered, anything else Missed |
   | customer_number | incoming: `SourceNumber`; outgoing: the number that isn't a telecaller's (§1.2) |
   | agent | incoming: `DialWhomNumber`; outgoing: the telecaller's number; mapped to a user through the table |
   | started_at, ended_at | `StartTime`, `EndTime` (India time, converted to the site's time zone) |
   | duration, talk_seconds | `CallDuration`, `TalkDuration` (§1.2) |
   | recording_filename | `CallRecordingUrl`, answered calls only |
   | reference_id | `campid` |

4. `process_call` is unchanged: the click-to-call's lead and agent first (by `campid`), else the
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

CallerDesk rings the telecaller first, then the lead. The `campid` of its answer is remembered
for 6 hours with the lead and the person, so the call report lands on that lead. Answer to the
user: "CallerDesk is calling your phone…". Errors are plain: "CallerDesk didn't accept the call:
<its reason>", "…it refused the API key…", "Couldn't reach CallerDesk. Try again in a minute."
The key is in the request's URL, so no failure message or log shows the URL.

**Test connection** (settings API) only checks that the key and the virtual number are set;
CallerDesk has no call that proves the key without placing a call.

The "Reverse C2C" and "C2C via member id" variants aren't used: they ring the customer first,
or need CallerDesk member IDs.

### 3.1 Live Call and the call box (`calls.receive_live`, `calls.call_status`)

The same address takes a Live Call event. It is told apart from a Call Report by having a
`CallSid` but no `EndTime` and no `CallDuration` (`callerdesk.is_live`); the token check is the
same. `receive_live` keeps the call's **live status** on its TBO Call (`live_status`: Ringing,
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

**Assumed (confirm with CallerDesk):** Live Call fires when a leg is ringing/connected and again
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
never returned by the settings API and shown only to a System Manager; Live Call events (`TestLiveCall`): ringing then on call, repeats and late events ignored, the report closes the call, only the caller can read its status, an incoming call shows to its telecaller only. MyOperator's tests
(`test_telephony.py`) are unchanged.

## Changelog

- **v1.1 (2026-11-14):** Live Call events (§3.1), `live_status` and picked-at fields on TBO Call,
  `get_call_status`, `get_incoming_call`, for the hub's call box. Owner: tick Live Call in the webhook.
- **v1.0 (2026-11-14):** first version: provider switch, Call Report webhook, click-to-call,
  recordings, CallerDesk number in the users table.
