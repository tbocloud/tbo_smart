// Copyright (c) 2026, sammish and contributors
// For license information, please see license.txt

// Decision buttons for the approver the request is waiting for. The server checks every
// action again (tbo_smart.api.quotation_adjustment.decide_adjustment).

const QADJ_API = "tbo_smart.api.quotation_adjustment.";

frappe.ui.form.on("TBO Quotation Adjustment", {
	refresh(frm) {
		frm.disable_save();
		frm.add_custom_button(__("Open quotation"), () =>
			frappe.set_route("Form", "Quotation", frm.doc.quotation)
		);
		frappe.call({ method: QADJ_API + "get_adjustment_limits" }).then(({ message }) => {
			qadj_add_decisions(frm, message || {});
		});
	},
});

function qadj_add_decisions(frm, limits) {
	const level = limits.level;
	const doc = frm.doc;
	if (!level || doc.requested_by === frappe.session.user) return;
	const open = !doc.voided && ["Pending", "Escalated"].includes(doc.status);
	const waiting =
		level.level === doc.current_level ||
		((level.approves_directly || level.unlimited) && level.rank > doc.current_rank);
	const limit = (limits.limits || {})[doc.kind];
	const covers = limit === null || doc.requested_percent <= limit;

	if (open && waiting) {
		frm.set_intro(
			covers
				? __(
						"This request is within your limit ({0}). Approve, approve less, or reject.",
						[limits.limit_labels[doc.kind]]
				  )
				: __(
						"This request is above your limit ({0}). Approve up to your limit or forward it.",
						[limits.limit_labels[doc.kind]]
				  ),
			covers ? "blue" : "orange"
		);
		frm.add_custom_button(__("Approve"), () => qadj_decide(frm, "approve"), __("Decide"));
		if (covers) {
			frm.add_custom_button(__("Reject"), () => qadj_decide(frm, "reject"), __("Decide"));
		}
		frm.add_custom_button(__("Forward"), () => qadj_decide(frm, "forward"), __("Decide"));
	}
	if (level.unlimited && !doc.voided) {
		frm.add_custom_button(__("Override"), () => qadj_decide(frm, "override"), __("Decide"));
	}
}

function qadj_decide(frm, action) {
	const doc = frm.doc;
	const needs_percent = ["approve", "override"].includes(action);
	const needs_remarks = ["reject", "override"].includes(action);
	const titles = {
		approve: __("Approve {0}", [__(doc.kind).toLowerCase()]),
		reject: __("Reject request"),
		forward: __("Forward to the next level"),
		override: __("Override"),
	};
	const fields = [];
	if (needs_percent) {
		fields.push({
			fieldname: "percent",
			fieldtype: "Percent",
			label: __("Approved %"),
			default: doc.requested_percent,
			reqd: 1,
			description:
				action === "approve"
					? __("You can approve less than the requested percentage.")
					: "",
		});
	}
	fields.push({
		fieldname: "remarks",
		fieldtype: "Small Text",
		label: action === "override" ? __("Reason for the override") : __("Remarks"),
		reqd: needs_remarks ? 1 : 0,
	});
	const dialog = new frappe.ui.Dialog({
		title: titles[action],
		fields,
		primary_action_label: titles[action],
		primary_action(values) {
			frappe
				.call({
					method: QADJ_API + "decide_adjustment",
					type: "POST",
					args: {
						name: doc.name,
						action,
						percent: values.percent,
						remarks: values.remarks,
					},
					freeze: true,
				})
				.then(() => {
					dialog.hide();
					frm.reload_doc();
				});
		},
	});
	dialog.show();
}
