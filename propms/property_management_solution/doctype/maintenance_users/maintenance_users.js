// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Maintenance Users", {
	refresh(frm) {
		// Limit Employee link to MAINTENANCE - VPL department
		frm.set_query("employee", function () {
			return {
				filters: {
					department: "MAINTENANCE - VPL",
				},
			};
		});
	},

	employee(frm) {
		// When Employee changes, copy name + email into app fields
		// employee_name and email are fetched via fetch_from where possible
		if (frm.doc.employee_name) {
			frm.set_value("full_name", frm.doc.employee_name);
		}
		// Prefer User email if present; fall back to existing email
		if (frm.doc.email) {
			frm.set_value("user_email", frm.doc.email);
		} else if (frm.doc.user) {
			frappe.db.get_value("User", frm.doc.user, "email").then((r) => {
				if (r && r.message && r.message.email) {
					frm.set_value("email", r.message.email);
					frm.set_value("user_email", r.message.email);
				}
			});
		}
	},
});