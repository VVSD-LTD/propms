frappe.ui.form.on("Afritrack Settings", {
	refresh: function (frm) {
		frm.set_intro(
			__(
				"Complete flow: Sync Meters fetches TrackSPM /units/list, updates Afritrack Meter Sync (Single) metadata, and writes unit details onto each existing Meter (does not create meters). Mobile meter status reads Meter fields. Keep Restrict Purchases on while testing with Afritrack’s test meter only."
			)
		);
		if (!frm.is_new()) {
			frm.add_custom_button(__("Sync Meters from TrackSPM"), function () {
				frappe.confirm(
					__(
						"This calls TrackSPM /units/list, updates Afritrack Meter Sync (Single), and writes unit details onto existing meters only. Continue?"
					),
					function () {
						frappe.call({
							method:
								"propms.property_management_solution.doctype.afritrack_meter_sync.afritrack_meter_sync.sync_now",
							freeze: true,
							freeze_message: __("Syncing meters from TrackSPM…"),
							callback: function (r) {
								if (!r.exc && r.message) {
									var m = r.message;
									frappe.msgprint({
										title: __("Afritrack Meter Sync"),
										message:
											m.status === "success"
												? __(
														"Sync {0}: Updated {1}, Skipped {2}, API rows {3}",
														[
															m.sync_name,
															m.updated,
															m.skipped,
															m.total_rows,
														]
												  )
												: __(m.message || "Sync failed"),
										indicator: m.status === "success" ? "green" : "red",
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
