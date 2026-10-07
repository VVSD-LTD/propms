// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Services Settings", {
	refresh(frm) {
		(frm.doc.pos_store_services || []).forEach((row) => {
			sync_purchase_mode(frm, row);
		});
	},
	pos_store_services_add(frm, cdt, cdn) {
		sync_purchase_mode(frm, locals[cdt][cdn]);
	},
});

frappe.ui.form.on("Mobile POS Store Service", {
	service_type(frm, cdt, cdn) {
		sync_purchase_mode(frm, locals[cdt][cdn]);
	},
});

function sync_purchase_mode(frm, row) {
	if (!row) {
		return;
	}
	const stype = (row.service_type || "Item").trim();
	const mode = stype === "Amount" ? "amount" : "qty";
	if (row.purchase_mode !== mode) {
		frappe.model.set_value(row.doctype, row.name, "purchase_mode", mode);
	}
}
