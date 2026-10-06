# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

frappe.ui.form.on("Afritrack Meter Sync", {
	onload: function (frm) {
		frm.disable_save();
	},
	refresh: function (frm) {
		frm.disable_save();
		frm.clear_custom_buttons();

		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Run Sync Now"), function () {
			frappe.call({
				method:
					"propms.property_management_solution.doctype.afritrack_meter_sync.afritrack_meter_sync.sync_now",
				freeze: true,
				freeze_message: __("Fetching /units/list from TrackSPM…"),
				callback: function (r) {
					if (r.exc || !r.message) {
						return;
					}
					var m = r.message;
					if (m.status === "success") {
						frappe.show_alert({
							message: __(
								"Sync {0}: updated {1}, skipped {2}, rows {3}",
								[m.sync_name, m.updated, m.skipped, m.total_rows]
							),
							indicator: "green",
						});
						if (m.sync_name && m.sync_name !== frm.doc.name) {
							frappe.set_route("Form", "Afritrack Meter Sync", m.sync_name);
						} else {
							frm.reload_doc();
						}
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
