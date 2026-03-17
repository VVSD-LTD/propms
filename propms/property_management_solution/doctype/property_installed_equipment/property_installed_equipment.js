// Copyright (c) 2026, Aakvatech and contributors
// For license information, please see license.txt

frappe.ui.form.on("Property Installed Equipment Detail", {
    enable(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        frappe.call({
            method: "propms.property_management_solution.doctype.property_installed_equipment.property_installed_equipment.enable_equipment",
            args: { equipment_detail: row.name },
            callback: function(r) {
                if (r.message?.status === "success") {
                    frm.reload_doc(); // fetches fresh data from server, then re-renders
                }
            }
        });
    },

    disable(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        frappe.call({
            method: "propms.property_management_solution.doctype.property_installed_equipment.property_installed_equipment.disable_equipment",
            args: { equipment_detail: row.name },
            callback: function(r) {
                if (r.message?.status === "success") {
                    frm.reload_doc();
                }
            }
        });
    }
});
