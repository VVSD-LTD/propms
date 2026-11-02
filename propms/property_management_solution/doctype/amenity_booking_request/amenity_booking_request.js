// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

function abr_handle_action_result(frm, r, fail_title) {
	const msg = (r && r.message) || {};
	if (msg.status === "success") {
		frappe.show_alert({
			message: msg.message || __("Done"),
			indicator: "green",
		});
		frm.reload_doc();
	} else {
		frappe.msgprint({
			title: fail_title || __("Action failed"),
			message: msg.message || __("Unknown error"),
			indicator: "red",
		});
	}
}

function abr_sync_header_from_first_occurrence(frm) {
	const rows = (frm.doc.occurrences || []).slice().sort((a, b) => {
		const ka = `${a.booking_date || ""} ${a.start_time || ""}`;
		const kb = `${b.booking_date || ""} ${b.start_time || ""}`;
		return ka.localeCompare(kb);
	});
	if (!rows.length) {
		return;
	}
	const first = rows[0];
	if (first.booking_date) {
		frm.set_value("booking_date", first.booking_date);
	}
	if (first.start_time) {
		frm.set_value("start_time", first.start_time);
	}
	if (first.end_time) {
		frm.set_value("end_time", first.end_time);
	}
}

frappe.ui.form.on("Amenity Booking Request", {
	setup(frm) {
		frm.set_query("amenity", () => ({
			filters: { is_active: 1, requires_booking: 1 },
		}));
	},

	refresh(frm) {
		frm.clear_custom_buttons();
		frm.trigger("toggle_editable");
		frm.trigger("toggle_recurring_labels");
		frm.trigger("add_staff_actions");

		if (frm.is_new()) {
			frm.dashboard.set_headline(
				__(
					"One Time: set From Date + times + Resident, then Save. Recurring: set From/Until, weekdays, then Fill Requested Dates."
				)
			);
		} else if (frm.doc.series) {
			frm.add_custom_button(__("Open Series"), () => {
				frappe.set_route("Form", "Amenity Booking Series", frm.doc.series);
			}, __("Links"));
		}
		if (!frm.is_new() && frm.doc.booking) {
			frm.add_custom_button(__("Open Booking"), () => {
				frappe.set_route("Form", "Amenity Booking", frm.doc.booking);
			}, __("Links"));
		}
	},

	toggle_recurring_labels(frm) {
		const recurring = frm.doc.series_type === "Recurring";
		frm.set_df_property(
			"booking_date",
			"label",
			recurring ? __("From Date") : __("Date")
		);
	},

	toggle_editable(frm) {
		const open = frm.is_new() || frm.doc.status === "Open";
		[
			"amenity",
			"series_type",
			"booking_date",
			"series_end_date",
			"start_time",
			"end_time",
			"guests_count",
			"tenant",
			"property_unit",
			"lease",
			"notes",
			"week_mon",
			"week_tue",
			"week_wed",
			"week_thu",
			"week_fri",
			"week_sat",
			"week_sun",
		].forEach((f) => frm.set_df_property(f, "read_only", !open));
		frm.set_df_property("occurrences", "read_only", !open);
	},

	add_staff_actions(frm) {
		const open = frm.is_new() || frm.doc.status === "Open";

		if (open && frm.doc.series_type === "Recurring") {
			frm
				.add_custom_button(__("Fill Requested Dates"), () => {
					frm.trigger("fill_requested_dates");
				})
				.addClass("btn-primary");
		}

		if (frm.is_new() || frm.doc.status !== "Open") {
			return;
		}

		const days = (frm.doc.occurrences || []).length || 1;

		frm.add_custom_button(__("Confirm Booking Now"), () => {
			frappe.confirm(
				__(
					"Confirm this request now? Creates one Series and {0} Confirmed Amenity Booking(s).",
					[days]
				),
				() => frm.trigger("run_approve")
			);
		}).addClass(frm.doc.series_type === "Recurring" ? "" : "btn-primary");

		frm.add_custom_button(__("Approve"), () => {
			frappe.confirm(
				__(
					"Approve this request? This creates one Series and {0} Confirmed Amenity Booking(s).",
					[days]
				),
				() => frm.trigger("run_approve")
			);
		});

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
							abr_handle_action_result(frm, r, __("Reject failed"));
						},
					});
				},
				__("Reject Request"),
				__("Reject")
			);
		});

		frm.add_custom_button(__("Cancel Request"), () => {
			frappe.prompt(
				[
					{
						fieldname: "cancellation_reason",
						fieldtype: "Small Text",
						label: __("Cancellation Reason"),
					},
				],
				(values) => {
					frappe.call({
						method: "propms.api.v1.amenities.user_bookings.cancel_booking",
						args: {
							request_id: frm.doc.name,
							cancellation_reason: values.cancellation_reason,
						},
						freeze: true,
						freeze_message: __("Cancelling..."),
						callback(r) {
							abr_handle_action_result(frm, r, __("Cancel failed"));
						},
					});
				},
				__("Cancel Request"),
				__("Cancel")
			);
		});
	},

	fill_requested_dates(frm) {
		if (!(frm.doc.booking_date && frm.doc.series_end_date)) {
			frappe.msgprint(__("Set From Date and Until Date first."));
			return;
		}
		if (!(frm.doc.start_time && frm.doc.end_time)) {
			frappe.msgprint(__("Set Start Time and End Time first."));
			return;
		}

		frappe.call({
			method: "propms.api.v1.amenities.desk.generate_request_occurrence_dates",
			args: {
				from_date: frm.doc.booking_date,
				until_date: frm.doc.series_end_date,
				start_time: frm.doc.start_time,
				end_time: frm.doc.end_time,
				week_mon: frm.doc.week_mon,
				week_tue: frm.doc.week_tue,
				week_wed: frm.doc.week_wed,
				week_thu: frm.doc.week_thu,
				week_fri: frm.doc.week_fri,
				week_sat: frm.doc.week_sat,
				week_sun: frm.doc.week_sun,
			},
			freeze: true,
			freeze_message: __("Building dates..."),
			callback(r) {
				const msg = (r && r.message) || {};
				if (msg.status !== "success") {
					frappe.msgprint({
						title: __("Could not fill dates"),
						message: msg.message || __("Unknown error"),
						indicator: "red",
					});
					return;
				}
				frm.clear_table("occurrences");
				(msg.occurrences || []).forEach((row) => {
					frm.add_child("occurrences", row);
				});
				frm.refresh_field("occurrences");
				frappe.show_alert({
					message: __("Added {0} date(s)", [msg.count || 0]),
					indicator: "green",
				});
				if (msg.truncated) {
					frappe.msgprint(
						__("Stopped at {0} dates (max). Narrow the range if needed.", [
							msg.max_occurrences,
						])
					);
				}
			},
		});
	},

	run_approve(frm) {
		frappe.call({
			method: "propms.api.v1.amenities.staff_actions.approve_amenity_booking",
			args: { request_id: frm.doc.name },
			freeze: true,
			freeze_message: __("Confirming..."),
			callback(r) {
				abr_handle_action_result(frm, r, __("Approve failed"));
			},
		});
	},

	amenity(frm) {
		frm.trigger("sync_one_time_occurrence");
	},

	series_type(frm) {
		frm.trigger("toggle_recurring_labels");
		frm.trigger("sync_one_time_occurrence");
		frm.clear_custom_buttons();
		frm.trigger("add_staff_actions");
	},

	booking_date(frm) {
		frm.trigger("sync_one_time_occurrence");
	},

	start_time(frm) {
		frm.trigger("sync_one_time_occurrence");
	},

	end_time(frm) {
		frm.trigger("sync_one_time_occurrence");
	},

	tenant(frm) {
		if (!frm.doc.tenant) {
			return;
		}
		frappe.call({
			method: "propms.api.v1.amenities.desk.get_resident_booking_defaults",
			args: { user: frm.doc.tenant },
			callback(r) {
				const msg = (r && r.message) || {};
				if (msg.status !== "success") {
					return;
				}
				if (msg.tenant_name) {
					frm.set_value("tenant_name", msg.tenant_name);
				}
				if (msg.property_unit && !frm.doc.property_unit) {
					frm.set_value("property_unit", msg.property_unit);
				}
				if (msg.lease && !frm.doc.lease) {
					frm.set_value("lease", msg.lease);
				}
			},
		});
	},

	sync_one_time_occurrence(frm) {
		if (frm.doc.series_type !== "One Time") {
			return;
		}
		if (!(frm.doc.booking_date && frm.doc.start_time && frm.doc.end_time)) {
			return;
		}
		const rows = frm.doc.occurrences || [];
		if (!rows.length) {
			frm.add_child("occurrences", {
				booking_date: frm.doc.booking_date,
				start_time: frm.doc.start_time,
				end_time: frm.doc.end_time,
				status: "Pending",
			});
			frm.refresh_field("occurrences");
			return;
		}
		if (rows.length === 1 && (rows[0].status || "Pending") === "Pending" && !rows[0].booking) {
			frappe.model.set_value(rows[0].doctype, rows[0].name, {
				booking_date: frm.doc.booking_date,
				start_time: frm.doc.start_time,
				end_time: frm.doc.end_time,
			});
		}
	},
});

frappe.ui.form.on("Amenity Booking Request Item", {
	booking_date(frm) {
		abr_sync_header_from_first_occurrence(frm);
	},
	start_time(frm) {
		abr_sync_header_from_first_occurrence(frm);
	},
	end_time(frm) {
		abr_sync_header_from_first_occurrence(frm);
	},
});
