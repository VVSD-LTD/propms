// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

const DEFAULT_EXCLUDED_DAYS = [
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
	"Sunday",
	"Public Holiday",
];

frappe.ui.form.on("Property Management Email Setting", {
	refresh(frm) {
		toggle_excluded_days_grid(frm);
		if (should_show_excluded_days(frm)) {
			populate_excluded_days(frm);
		}
		add_test_reminder_button(frm);
	},

	use_penalty_exclusion_calendar(frm) {
		if (should_show_excluded_days(frm)) {
			populate_excluded_days(frm);
		}
		toggle_excluded_days_grid(frm);
	},

	reminder_type(frm) {
		if (should_show_excluded_days(frm)) {
			populate_excluded_days(frm);
		}
		toggle_excluded_days_grid(frm);
		clear_unused_schedule_fields(frm);
	},

	frequency(frm) {
		clear_unused_schedule_fields(frm);
	},

	before_excluded_days_remove(frm) {
		if (should_show_excluded_days(frm)) {
			frappe.throw(__("Rows cannot be removed from Excluded Days."));
		}
	},
});

function clear_unused_schedule_fields(frm) {
	if (!["After Overdue", "Withholding Tax"].includes(frm.doc.reminder_type)) {
		frm.set_value("frequency", "");
		frm.set_value("weekday", "");
		frm.set_value("day_of_month", "");
		return;
	}

	if (frm.doc.frequency === "Daily") {
		if (frm.doc.weekday) frm.set_value("weekday", "");
		if (frm.doc.day_of_month) frm.set_value("day_of_month", "");
	} else if (frm.doc.frequency === "Weekly") {
		if (frm.doc.day_of_month) frm.set_value("day_of_month", "");
	} else if (frm.doc.frequency === "Monthly") {
		if (frm.doc.weekday) frm.set_value("weekday", "");
	}
}
function should_show_excluded_days(frm) {
	return (
		["After Overdue", "Withholding Tax"].includes(frm.doc.reminder_type) &&
		!frm.doc.use_penalty_exclusion_calendar
	);
}

function populate_excluded_days(frm) {
	const existing_days = (frm.doc.excluded_days || []).map((row) => row.day);

	DEFAULT_EXCLUDED_DAYS.forEach((day) => {
		if (!existing_days.includes(day)) {
			frm.add_child("excluded_days", { day, exclude: 0 });
		}
	});

	frm.refresh_field("excluded_days");
	lock_excluded_days_grid(frm);
}

function toggle_excluded_days_grid(frm) {
	if (!should_show_excluded_days(frm) || !frm.fields_dict.excluded_days) {
		return;
	}

	lock_excluded_days_grid(frm);
}

function lock_excluded_days_grid(frm) {
	const grid = frm.fields_dict.excluded_days?.grid;
	if (!grid) {
		return;
	}

	grid.cannot_add_rows = true;
	grid.cannot_delete_rows = true;
	grid.wrapper.find(".grid-delete-row").hide();
	grid.wrapper.find(".grid-remove-rows").hide();
	grid.wrapper.find(".grid-remove-all-rows").hide();
	grid.wrapper.find(".grid-row-check").hide();
	grid.wrapper.find(".grid-heading-row .grid-row-check").hide();

	if (grid.grid_rows) {
		grid.grid_rows.forEach((row) => {
			row.wrapper.find(".grid-delete-row").hide();
			row.wrapper.find(".grid-row-check").hide();
		});
	}

	frm.refresh_field("excluded_days");
}

function add_test_reminder_button(frm) {
	if (!frappe.user_roles.includes("System Manager") || frm.is_new()) {
		return;
	}

	frm.add_custom_button(__("Test Reminder"), () => open_test_reminder_dialog(frm)).addClass(
		"btn-primary"
	);
}

function open_test_reminder_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Test Invoice Email Reminder"),
		fields: [
			{
				fieldname: "sales_invoice",
				fieldtype: "Link",
				options: "Sales Invoice",
				label: __("Sales Invoice"),
				reqd: 1,
				get_query: () => ({
					filters: {
						docstatus: 1,
						outstanding_amount: [">", 1],
					},
				}),
				onchange: () => {
					const invoice = d.get_value("sales_invoice");
					if (!invoice) {
						return;
					}
					frappe.call({
						method: "propms.custom.custom.get_invoice_emails_for_reminder_test",
						args: { sales_invoice: invoice },
						callback: (r) => {
							const emails = r.message || [];
							d.set_df_property(
								"to_email",
								"description",
								emails.length
									? __("Suggested: {0}", [emails.join(", ")])
									: __("No primary contact emails found")
							);
							if (emails.length && !d.get_value("to_email")) {
								d.set_value("to_email", emails[0]);
							}
						},
					});
				},
			},
			{
				fieldname: "to_email",
				fieldtype: "Data",
				options: "Email",
				label: __("To Email"),
				reqd: 1,
			},
			{
				fieldname: "bcc",
				fieldtype: "Data",
				label: __("BCC (optional, comma-separated)"),
			},
		],
		primary_action_label: __("Create & Send Test"),
		primary_action(values) {
			d.hide();
			frappe.call({
				method: "propms.custom.custom.test_property_management_email_setting",
				args: {
					email_setting: frm.doc.name,
					sales_invoice: values.sales_invoice,
					to_email: values.to_email,
					bcc: values.bcc,
				},
				freeze: true,
				freeze_message: __("Creating test Notify Customer and sending email..."),
				callback: (r) => {
					if (!r.message) {
						return;
					}
					const msg = r.message;
					frappe.msgprint({
						title: __("Test Reminder Result"),
						indicator: msg.email_sent ? "green" : "orange",
						message: __(
							"<b>Notify Customer:</b> {0}<br>" +
								"<b>To:</b> {1}<br>" +
								"<b>BCC:</b> {2}<br>" +
								"<b>Email Sent:</b> {3}<br>" +
								"<b>Eligible:</b> {4}<br>" +
								"<b>Reference Date:</b> {5}<br>" +
								"<b>Next Month Start:</b> {6}<br>" +
								"<b>Subject:</b> {7}",
							[
								frappe.utils.get_form_link(
									"Notify Customer",
									msg.notify_customer,
									true
								),
								msg.to_email,
								(msg.bcc || []).join(", ") || "-",
								msg.email_sent ? __("Yes") : __("No"),
								msg.is_eligible,
								msg.reference_date || "-",
								msg.next_month_start || "-",
								frappe.utils.escape_html(msg.subject || ""),
							]
						),
					});
				},
			});
		},
	});
	d.show();
}
