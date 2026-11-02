# WhatsApp outreach through TBO Chat (ERP side)

**Status:** built 2026-11-02. The hub (tbocloud/helpdesk, `docs/whatsapp-outreach.md`) holds the
Chatwoot connection, the inbox, the template list and the staff "How to use". This file is what
the ERP does. The call contract is `hub-leads-api.md` §2.19.

## 1. The flow

1. A BD approves a WhatsApp first message (Outreach tab). The hub passes `send_whatsapp=1` when a
   TBO Chat inbox is set and "send automatically" is on; otherwise approving is manual, exactly as
   before (`wa.me`, then **Mark sent**).
2. `sending.DELIVERERS[WhatsApp]` (`whatsapp.queue_for_chat`) queues it: `via_chat`, `chat_status`
   Queued. The ERP cannot call Chatwoot (it holds no token), so the **hub pulls**: it fetches the
   queue (`get_whatsapp_queue`), sends through Chatwoot and reports (`report_whatsapp`). This
   follows the existing direction: the hub calls the ERP.
3. The queue is only handed out in the send window (Monday to Saturday, `prospecting.md` §10).
   A lead that unsubscribed or was lost since approval has its message cancelled instead.
4. **Meta's 24-hour rule** is the hub's to apply, from `window_open` (the lead wrote to us in the
   last 24 hours): inside it free text goes as written; outside it the hub sends an approved
   template filled from `first_name`, `company_name` and `why_call` (the Why call line). With no
   usable template the hub reports `manual` and the BD sends by hand.
5. The lead replies. The hub forwards it (`receive_whatsapp`). The ERP finds the lead by
   conversation, else by the last 10 digits of its mobile, WhatsApp or phone number; files a
   Communication (medium **Chat**, received) on the lead; stores `tbo_whatsapp_last_in`; and queues
   the **same reply sorter as email** (`replies.process_reply`: classes, stage, follow-up, unsubscribe,
   notification). Delivery receipts update `chat_status` (`whatsapp_status`).

## 2. Where things are kept

- Lead (read-only custom fields): `tbo_chat_contact_id`, `tbo_chat_conversation_id`,
  `tbo_whatsapp_last_in`.
- TBO Outreach Message: `via_chat`, `chat_status` (Queued, Sending, Sent, Delivered, Read,
  Failed), `chat_message_id`, `template`.
- The conversation is on the lead's timeline from Communications (medium Chat); the hub shows it
  with an "Open in TBO Chat" link and a reply box.

## 3. Decisions made without the owner

1. The hub pulls and sends; the ERP never holds Chatwoot credentials.
2. `Sent` means TBO Chat accepted it; `Delivered`/`Read` follow from receipts. A receipt of
   `failed` makes the message Failed, which the BD edits (back to Draft) or cancels.
3. A message the hub could not send for lack of setup is not Failed: it stays Approved with the
   manual "Open WhatsApp" path, because the BD can still reach the lead.
4. A WhatsApp reply of just "stop" or "unsubscribe" unsubscribes the lead without asking the AI.
   This is safe on WhatsApp (no quoted opt-out line, unlike email, `prospecting.md` decision 9).
   Longer replies go to the AI classifier like email, which already knows Malayalam and Manglish.
5. The daily cap and the email opt-out line are email rules and do not apply to WhatsApp; the send
   window and the never-contact rules (unsubscribed, Lost-type) do.
6. Numbers are normalised to E.164 with a default country code the hub passes (default 91).
7. A message left in Sending (the hub crashed between fetching and reporting) is not retried, so a
   lead never gets it twice; the BD cancels it or marks it sent.

## 4. Tests

`tbo_smart/tests/test_whatsapp_outreach.py` (no Chatwoot; the hub's calls are made directly):
number normalising and matching by number or conversation; approving with and without an inbox
(the manual fallback and its link); the queue (once only, the country code, the 24-hour flag, the
window, unsubscribed leads, integration user only); reporting sent, failed and manual; delivery
receipts in order; replies (filed once, window opened, classified and moving the stage, "stop"
unsubscribing and ending sends, AI off); the lead page's reply box and timeline. Helpers are in
`test_utils.py` (`make_queued_whatsapp`, `receive_chat`, `delete_chat_communications`).
