// Quotation form: show the user's discount / commission limit, the approval state of each
// limited kind, and a "Request approval" flow when the quotation is above the limit.
// The server (tbo_smart.quotation_control) enforces all of this; the form only explains it.

const QA_API = "tbo_smart.api.quotation_adjustment.";
const QA_FIELDS = {
	Discount: "additional_discount_percentage",
	Commission: "tbo_commission_rate",
};

frappe.ui.form.on("Quotation", {
	onload(frm) {
		qa_show_limits(frm);
	},
	refresh(frm) {
		qa_show_status(frm);
	},
});

function qa_show_limits(frm) {
	frappe.call({ method: QA_API + "get_adjustment_limits" }).then(({ message }) => {
		if (!message || !message.enabled) return;
		message.kinds.forEach((kind) => {
			let help =
				message.limits[kind] === 0
					? __("Your limit: not set yet, so any {0} needs approval before submit.", [
							__(kind).toLowerCase(),
					  ])
					: __(
							"Your limit: {0}. Above it, the quotation needs approval before submit.",
							[message.limit_labels[kind]]
					  );
			if (kind === "Discount") help += " " + __("Item discounts count too.");
			frm.set_df_property(QA_FIELDS[kind], "description", help);
		});
	});
}

function qa_show_status(frm) {
	if (frm.is_new()) return;
	frappe
		.call({
			method: QA_API + "get_quotation_adjustment_status",
			args: { quotation: frm.doc.name },
		})
		.then(({ message }) => {
			if (!message || !message.kinds.length) return;
			frm.dashboard.clear_headline();
			frm.dashboard.set_headline_alert(message.kinds.map(qa_status_line).join(""));
			if (frm.doc.docstatus !== 0) return;
			message.kinds.forEach((row) => qa_add_buttons(frm, row));
		});
}

function qa_status_line(row) {
	const latest = row.latest;
	let state;
	let color;
	if (row.percent <= 0) {
		state = __("None");
		color = "gray";
	} else if (latest && ["Pending", "Escalated"].includes(latest.status)) {
		state = __("Waiting for {0}", [latest.current_level]);
		color = "orange";
	} else if (row.approved || row.within_limit) {
		state = __("Approved");
		color = "green";
	} else if (latest && latest.status === "Rejected") {
		state = __("Rejected");
		color = "red";
	} else {
		state = __("Needs approval");
		color = "red";
	}
	return `<div class="flex align-center" style="gap: var(--padding-sm); margin: 2px 0;">
		<span class="indicator-pill ${color}">${frappe.utils.escape_html(state)}</span>
		<span>${__("{0} {1}%", [__(row.kind), format_number(row.percent, null, 2)])}</span>
		<span class="text-muted">${__("Your limit: {0}", [row.limit_label])}</span>
	</div>`;
}

function qa_add_buttons(frm, row) {
	const latest = row.latest;
	const group = __("Adjustment");
	if (latest && ["Pending", "Escalated"].includes(latest.status)) {
		frm.add_custom_button(
			__("View {0} request", [__(row.kind).toLowerCase()]),
			() => frappe.set_route("Form", "TBO Quotation Adjustment", latest.name),
			group
		);
		return;
	}
	if (row.percent > 0 && !row.approved && !row.within_limit) {
		frm.add_custom_button(
			__("Request {0} approval", [__(row.kind).toLowerCase()]),
			() => qa_request_dialog(frm, row),
			group
		);
	}
}

function qa_request_dialog(frm, row) {
	const dialog = new frappe.ui.Dialog({
		title: __("Request {0} approval", [__(row.kind).toLowerCase()]),
		fields: [
			{
				fieldtype: "HTML",
				options: `<p class="text-muted">${__(
					"Your limit is {0}. The next level up decides; until then the quotation stays a draft.",
					[row.limit_label]
				)}</p>`,
			},
			{
				fieldname: "percent",
				fieldtype: "Percent",
				label: __("{0} %", [__(row.kind)]),
				default: row.percent,
				reqd: 1,
			},
			{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
		],
		primary_action_label: __("Request approval"),
		primary_action(values) {
			frappe
				.call({
					method: QA_API + "request_adjustment",
					type: "POST",
					args: {
						quotation: frm.doc.name,
						kind: row.kind,
						percent: values.percent,
						reason: values.reason,
					},
					freeze: true,
				})
				.then(({ message }) => {
					dialog.hide();
					frappe.show_alert({
						message: __("Sent to {0} for approval", [message.current_level]),
						indicator: "green",
					});
					frm.reload_doc();
				});
		},
	});
	dialog.show();
}
