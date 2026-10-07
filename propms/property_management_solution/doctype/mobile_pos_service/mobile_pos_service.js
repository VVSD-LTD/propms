// Copyright (c) 2026, VVSD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mobile POS Service", {
	purchase_mode(frm) {
		toggle_mode_fields(frm);
	},
	requires_delivery_window(frm) {
		frm.toggle_reqd("delivery_open_time", cint(frm.doc.requires_delivery_window));
		frm.toggle_reqd("delivery_close_time", cint(frm.doc.requires_delivery_window));
	},
	refresh(frm) {
		toggle_mode_fields(frm);
	},
});

function toggle_mode_fields(frm) {
	const qty = (frm.doc.purchase_mode || "amount") === "qty";
	frm.toggle_reqd("item", qty);
	frm.toggle_reqd("handler", !qty);
	frm.toggle_display("items", !qty);
}
