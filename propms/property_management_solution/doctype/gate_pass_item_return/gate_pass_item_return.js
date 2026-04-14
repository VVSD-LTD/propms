// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Gate Pass Item Return", {
	on_submit(frm) {
		const ref = frm.doc.reference_no;
		if (ref) {
			frappe.model.remove_from_locals("Returnable Gate Pass", ref);
		}
	},
});
