// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Tenant", {
	refresh(frm) {
		// Legacy doctype: tenant-user onboarding now happens on Lease > Mobile VIVA Tenant tab.
	},
	customer(frm) {
		// Keep behavior passive; no additional auto-population from Tenant form.
	},
});
