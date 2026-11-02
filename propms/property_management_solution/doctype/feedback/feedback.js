// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Feedback", {
	refresh(frm) {
		frm.clear_custom_buttons();
		if (frm.is_new()) {
			return;
		}

		// Simple workflow only: Open ↔ Closed (not a Job Card)
		if (frm.doc.status === "Open") {
			frm
				.add_custom_button(__("Mark Closed"), () => {
					frm.set_value("status", "Closed");
					frm.save();
				})
				.addClass("btn-primary");
		}

		if (frm.doc.status === "Closed") {
			frm.add_custom_button(__("Reopen"), () => {
				frm.set_value("status", "Open");
				frm.save();
			});
		}
	},
});
