// Copyright (c) 2026, sammish and contributors
// For license information, please see license.txt

frappe.ui.form.on("TBO Smart Settings", {
	refresh(frm) {
		show_callerdesk_url(frm);
	},
	regenerate_callerdesk_token(frm) {
		frappe.confirm(
			__(
				"Make a new webhook token? CallerDesk's webhook is refused until you paste the new address there."
			),
			() =>
				frappe
					.call({ method: "tbo_smart.api.telephony.regenerate_callerdesk_token" })
					.then(() => frm.reload_doc())
		);
	},
	regenerate_webhook_secret(frm) {
		frappe.confirm(
			__(
				"Make a new webhook secret? MyOperator's webhooks are refused until you paste it there."
			),
			() =>
				frappe
					.call({ method: "tbo_smart.api.telephony.regenerate_webhook_secret" })
					.then(({ message }) => {
						frappe.msgprint({
							title: __("New webhook secret"),
							message: __(
								"In MyOperator → Webhooks v2, set the URL to {0} and add the header {1} with the value {2}. This secret is shown only now.",
								[
									`<code>${frappe.utils.escape_html(message.url)}</code>`,
									`<code>${message.header}</code>`,
									`<code>${frappe.utils.escape_html(message.secret)}</code>`,
								]
							),
						});
						frm.reload_doc();
					})
		);
	},
});

// the address to paste into CallerDesk carries the token, so only a System Manager's form shows it
function show_callerdesk_url(frm) {
	if (frm.is_new() || frm.doc.telephony_provider !== "CallerDesk") return;
	frappe
		.call({ method: "tbo_smart.api.telephony.get_callerdesk_webhook_url" })
		.then(({ message }) => {
			if (!message || !message.url) return;
			frm.set_df_property(
				"callerdesk_webhook_token",
				"description",
				__("Paste this address as the webhook URL in CallerDesk:") +
					` <code>${frappe.utils.escape_html(message.url)}</code>`
			);
		});
}
