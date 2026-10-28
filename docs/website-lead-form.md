# Website lead form (living spec)

teambackoffice.com posts an enquiry straight into TBO's leads. One public endpoint, no key:

`POST https://india.teambackoffice.com/api/method/tbo_smart.api.website_lead.submit_website_lead`

| Field | Notes |
| --- | --- |
| `name` | required |
| `email`, `phone` | one of them is required; the email is validated, the phone needs 7+ digits |
| `company`, `message` | optional (`message` is kept up to 2000 characters, as the lead's first note) |
| `department` | optional; used only when it is an active TBO Lead Department, else TBO Smart Settings → **Website lead department** |
| `source` | default `Website`; an unknown Lead Source becomes `Website` |
| `hp` | the honeypot: keep it empty (hidden from people). Anything in it is dropped silently, still answered `{"ok": true}` |

It always answers `{"message": {"ok": true}}` and never returns lead data. Errors (missing name,
bad email, too many submissions) come back as normal Frappe errors with a plain message.

- **Rate limit:** 5 submissions per hour per IP (`frappe.rate_limiter`), then HTTP 429.
- **Duplicates:** an email or phone that is already a lead creates nothing. The existing lead gets
  a note "Website enquiry again: <message>" and its owner (or, with no owner, its department's
  heads) is notified.
- **New lead:** inserted with the source and department. When the department is on **Round robin**
  the lead goes to its next member and they are told on the ERP (`lead-departments.md` §5).

## What the owner sets once

1. TBO Smart Settings → **Lead intake** → **Website lead department** (the department that
   takes the enquiries, e.g. GrowthX, set to Round robin with members).
2. CORS, in the site's `site_config.json` (or `bench --site india.teambackoffice.com set-config
   -p allow_cors '["https://teambackoffice.com","https://www.teambackoffice.com"]'`):

   ```json
   "allow_cors": ["https://teambackoffice.com", "https://www.teambackoffice.com"]
   ```

   Then `bench restart`. Frappe answers the browser's preflight and adds the headers for
   those origins only; the call needs no cookie or token.

## Copy-paste form

```html
<form id="tbo-lead-form">
  <input name="name" placeholder="Your name" required />
  <input name="email" type="email" placeholder="Email" />
  <input name="phone" type="tel" placeholder="Phone" />
  <input name="company" placeholder="Company" />
  <textarea name="message" placeholder="How can we help?"></textarea>
  <!-- honeypot: people never see or fill this -->
  <input name="hp" tabindex="-1" autocomplete="off" style="position:absolute;left:-9999px" aria-hidden="true" />
  <button type="submit">Send</button>
  <p id="tbo-lead-status" role="status"></p>
</form>
<script>
  document.getElementById("tbo-lead-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    const status = document.getElementById("tbo-lead-status");
    const data = Object.fromEntries(new FormData(form));
    data.department = "GrowthX"; // optional; leave out to use the default department
    try {
      const response = await fetch(
        "https://india.teambackoffice.com/api/method/tbo_smart.api.website_lead.submit_website_lead",
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) }
      );
      status.textContent = response.ok
        ? "Thank you, we will be in touch."
        : "Please check your name, and an email or phone number, then try again.";
      if (response.ok) form.reset();
    } catch (error) {
      status.textContent = "Could not send. Please try again.";
    }
  });
</script>
```

## Tests

`tbo_smart/tests/test_lead_wave1.py` (`TestWebsiteLeadForm`).
