// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mobile Notifications", {
	setup(frm) {
		frm.set_query("property", function (doc) {
			if (!doc.customer) {
				return {
					filters: [["Property", "name", "in", ["__none__"]]],
				};
			}
			return {
				query: "propms.api.v1.notifications.staff.property_link_query",
				filters: { customer: doc.customer },
			};
		});
	},

	refresh(frm) {
		frm.set_df_property("company", "hidden", 1);
		frm.set_df_property("company", "reqd", 0);
		if (frm.is_new() && !frm.doc.company) {
			const company = frappe.defaults.get_user_default("Company");
			if (company) {
				frm.set_value("company", company);
			}
		}
	},

	customer(frm) {
		// Property depends on Customer — clear when Customer changes
		if (frm.doc.property) {
			frm.set_value("property", "");
		}
	},
});
