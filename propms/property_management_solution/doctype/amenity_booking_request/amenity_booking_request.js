// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Amenity Booking Request", {
	refresh(frm) {
		frm.clear_custom_buttons();
		if (frm.is_new() || frm.doc.status !== "Open") {
			return;
		}

		frm.add_custom_button(__("Approve"), () => {
			const days = (frm.doc.occurrences || []).length || 1;
			frappe.confirm(
				__(
					"Approve this request? This creates one Series and {0} Confirmed Amenity Booking(s).",
					[days]
				),
				() => {
					frappe.call({
						method: "propms.api.v1.amenities.staff_actions.approve_amenity_booking",
						args: { request_id: frm.doc.name },
						freeze: true,
						freeze_message: __("Approving..."),
						callback(r) {
							const msg = r.message || {};
							if (msg.status === "success") {
								frappe.show_alert({
									message: msg.message || __("Approved"),
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

		frm.add_custom_button(__("Reject"), () => {
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
						method: "propms.api.v1.amenities.staff_actions.reject_amenity_booking",
						args: {
							request_id: frm.doc.name,
							rejection_reason: values.rejection_reason,
						},
						freeze: true,
						freeze_message: __("Rejecting..."),
						callback(r) {
							const msg = r.message || {};
							if (msg.status === "success") {
								frappe.show_alert({
									message: msg.message || __("Rejected"),
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
				__("Reject Request"),
				__("Reject")
			);
		});
	},
});
