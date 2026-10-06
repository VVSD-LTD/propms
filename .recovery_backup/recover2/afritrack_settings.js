frappe.ui.form.on("Afritrack Settings", {
	refresh: function (frm) {
		frm.set_intro(
			__(
				"Complete flow: Sync Meters stores TrackSPM meter_id on each Meter. Purchases resolve SI meter serial → Meter.trackspm_meter_id. Keep Restrict Purchases on while testing with Afritrack’s test meter only."
			)
		);
		if (!frm.is_new()) {
			frm.add_custom_button(__("Sync Meters from TrackSPM"), function () {
				frappe.confirm(
					__(
						"This calls the live TrackSPM units/list API (read-only). It does not purchase power. Continue?"
					),
					function () {
						frappe.call({
							method:
								"propms.property_management_solution.doctype.afritrack_settings.afritrack_settings.sync_meters_from_trackspm",
							freeze: true,
							freeze_message: __("Syncing meters from TrackSPM…"),
							callback: function (r) {
								if (!r.exc && r.message) {
									frappe.msgprint({
										title: __("Meter Sync"),
										message: __(
											"Updated: {0}, Created: {1}, Skipped: {2} (rows: {3})",
											[
												r.message.updated,
												r.message.created,
												r.message.skipped,
												r.message.total_rows,
											]
										),
										indicator: "green",
									});
									frm.reload_doc();
								}
							},
						});
					}
				);
			}).addClass("btn-primary");
		}
	},
});
