// Copyright (c) 2026, sammish and contributors
// For license information, please see license.txt

frappe.ui.form.on("TBO Smart Settings", {
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
