// Copyright (c) 2026, sammish and contributors
// For license information, please see license.txt

const QADJ_COLORS = {
	Pending: "orange",
	Escalated: "orange",
	Approved: "green",
	Overridden: "blue",
	Rejected: "red",
};

frappe.listview_settings["TBO Quotation Adjustment"] = {
	add_fields: ["status", "voided", "current_level"],
	get_indicator(doc) {
		if (doc.voided) return [__("Voided"), "gray", "voided,=,1"];
		const label = ["Pending", "Escalated"].includes(doc.status)
			? __("Waiting for {0}", [doc.current_level])
			: __(doc.status);
		return [label, QADJ_COLORS[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Waiting for me"), () => {
			frappe
				.call({ method: "tbo_smart.api.quotation_adjustment.get_pending_approvals" })
				.then(({ message }) => {
					const names = (message || []).map((row) => row.name);
					listview.filter_area.clear(false).then(() => {
						listview.filter_area.add([
							[
								"TBO Quotation Adjustment",
								"name",
								"in",
								names.length ? names : [""],
							],
						]);
					});
				});
		});
	},
};
