frappe.ui.form.on('Sales Invoice', {
    refresh: function(frm) {
        if (frm.doc.docstatus !== 1 || !cint(frm.doc.penalty_paid) || frm.doc.penalty_invoice) {
            return;
        }

        frm.add_custom_button(__('Create Penalty Invoice'), function() {
            frappe.confirm(
                __('Create a draft Sales Invoice for the accrued penalty on {0}?', [frm.doc.name]),
                function() {
                    frappe.call({
                        method: 'propms.custom.sales_invoice_penalty.create_penalty_invoice',
                        args: { sales_invoice: frm.doc.name },
                        freeze: true,
                        freeze_message: __('Creating penalty invoice...'),
                        callback: function(r) {
                            if (!r.message || !r.message.penalty_invoice) {
                                return;
                            }
                            frm.reload_doc();
                            frappe.msgprint({
                                title: __('Penalty Invoice'),
                                indicator: 'green',
                                message: __('Draft penalty invoice {0} created.', [
                                    '<a href="/app/sales-invoice/' + r.message.penalty_invoice + '">' +
                                        r.message.penalty_invoice +
                                    '</a>'
                                ]),
                            });
                        },
                    });
                }
            );
        }).addClass('btn-primary');
    },
    property_name: function(frm, cdt, cdn) {
        frappe.model.set_value(cdt, cdn, "customer", "");
	if (frm.doc.cost_center) {
            frappe.call({
                method: "frappe.client.get_value",
                args: {
                    doctype: "Property",
                    fieldname: "status",
                    filters: {
                        name: frm.doc.cost_center
                    },
                },
                callback: function(r, rt) {
                    if (r.message) {
                        if (r.message.status == "On Lease") {
                            frappe.call({
                                method: "frappe.client.get_value",
                                args: {
                                    doctype: "Lease",
                                    fieldname: "customer",
                                    filters: {
                                        property: frm.doc.cost_center
                                    },
                                },
                                callback: function(r, rt) {
                                    if (r.message) {
                                        frappe.model.set_value(cdt, cdn, "customer", r.message.customer);
                                    }
                                }
                            });
                        }
                    }
                }
            });
        } else {
            frappe.model.set_value(cdt, cdn, "customer", "");
        }
    }
})