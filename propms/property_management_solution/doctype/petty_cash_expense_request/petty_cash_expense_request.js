// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Petty Cash Expense Request", {
	amount_excl_vat: (frm) => {
            frm.set_value("amount", frm.doc.amount_excl_vat + frm.doc.vat_amount);
	},
	vat_amount: (frm) => {
            frm.set_value("amount", frm.doc.amount_excl_vat + frm.doc.vat_amount);

	},
});
