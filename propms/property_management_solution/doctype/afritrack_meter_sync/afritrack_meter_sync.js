// Copyright (c) 2026, VVSD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Afritrack Meter Sync", {
	refresh: function (frm) {
		frm.disable_save();
		frm.clear_custom_buttons();

		frm.add_custom_button(__("Run Sync Now"), function () {
			frappe.call({
				method:
					"propms.property_management_solution.doctype.afritrack_meter_sync.afritrack_meter_sync.sync_now",
				freeze: true,
				freeze_message: __("Fetching /units/list and updating Meters…"),
				callback: function (r) {
					if (r.exc || !r.message) {
						return;
					}
					var m = r.message;
					if (m.status === "success") {
						frappe.show_alert({
							message: __(
								"Updated {0} meters, skipped {1}, rows {2}",
								[m.updated, m.skipped, m.total_rows]
							),
							indicator: "green",
						});
						frm.reload_doc();
					} else {
						frappe.msgprint({
							title: __("Afritrack Meter Sync"),
							message: __(m.message || "Sync failed"),
							indicator: "red",
						});
					}
				},
			});
		});
	},
});
