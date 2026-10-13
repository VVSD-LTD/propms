// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Amenity Booking Series", {
	refresh(frm) {
		frm.clear_custom_buttons();
		if (frm.is_new() || frm.doc.status !== "Pending") {
			return;
		}

		frm.add_custom_button(__("Approve Series"), () => {
			frappe.confirm(
				__(
					"Approve all Open requests in this series and create Confirmed Amenity Bookings?"
				),
				() => {
					frappe.call({
						method: "propms.api.v1.amenities.series.approve_amenity_booking_series",
						args: { series_id: frm.doc.name },
						freeze: true,
						freeze_message: __("Approving series..."),
						callback(r) {
							const msg = r.message || {};
							if (msg.status === "success") {
								frappe.show_alert({
									message: msg.message || __("Series approved"),
									indicator: "green",
								});
								frm.reload_doc();
							} else {
								frappe.msgprint({
									title: __("Approve failed"),
									message: msg.message || __("Unknown error"),
									indicator: "red",
								});
							}
						},
					});
				}
			);
		}).addClass("btn-primary");

		frm.add_custom_button(__("Reject Series"), () => {
			frappe.prompt(
				[
					{
						fieldname: "rejection_reason",
						fieldtype: "Small Text",
						label: __("Rejection Reason"),
						reqd: 1,
					},
				],
				(values) => {
					frappe.call({
						method: "propms.api.v1.amenities.series.reject_amenity_booking_series",
						args: {
							series_id: frm.doc.name,
							rejection_reason: values.rejection_reason,
						},
						freeze: true,
						freeze_message: __("Rejecting series..."),
						callback(r) {
							const msg = r.message || {};
							if (msg.status === "success") {
								frappe.show_alert({
									message: msg.message || __("Series rejected"),
									indicator: "orange",
								});
								frm.reload_doc();
							} else {
								frappe.msgprint({
									title: __("Reject failed"),
									message: msg.message || __("Unknown error"),
									indicator: "red",
								});
							}
						},
					});
				},
				__("Reject Series"),
				__("Reject")
			);
		});
	},
});
