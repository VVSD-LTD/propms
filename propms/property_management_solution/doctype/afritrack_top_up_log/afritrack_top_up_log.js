frappe.ui.form.on("Afritrack Top-up Log", {
	refresh: function (frm) {
		if (
			frm.doc.status &&
			["Failed", "Partial", "Blocked", "Pending"].includes(frm.doc.status) &&
			!frm.is_new()
		) {
			frm.add_custom_button(__("Retry TrackSPM Load"), function () {
				frappe.call({
					method:
						"propms.property_management_solution.doctype.afritrack_top_up_log.afritrack_top_up_log.retry_afritrack_topup",
					args: { topup_log_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Retrying Afritrack load…"),
					callback: function (r) {
						if (!r.exc) {
							frappe.show_alert({
								message: __("Retry finished"),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			}).addClass("btn-primary");
		}
	},
});
