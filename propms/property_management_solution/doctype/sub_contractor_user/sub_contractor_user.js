frappe.ui.form.on("Sub Contractor User", {
	refresh(frm) {
		// Filter Sub Contractor to suppliers in the "Sub-Contractor" supplier group
		frm.set_query("sub_contractor", () => {
			return {
				filters: {
					supplier_group: "Sub-Contractor",
				},
			};
		});
	},

	sub_contractor(frm) {
		if (!frm.doc.sub_contractor) {
			return;
		}

		// Fetch supplier name and email (if any) from Supplier
		frappe.db.get_value(
			"Supplier",
			frm.doc.sub_contractor,
			["supplier_name", "email_id"],
		).then((r) => {
			if (!r || !r.message) return;

			const data = r.message;

			// Set full name from supplier name if not already set
			if (!frm.doc.full_name && data.supplier_name) {
				frm.set_value("full_name", data.supplier_name);
			}

			// Use email_id from Supplier if present
			const email = data.email_id;
			if (email && !frm.doc.user_email) {
				frm.set_value("user_email", email);
			}
		});
	},
});
// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Sub Contractor User", {
// 	refresh(frm) {

// 	},
// });
