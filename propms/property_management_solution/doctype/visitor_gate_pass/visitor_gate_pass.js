// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on('Visitor Gate Pass', {
	refresh: function(frm) {
		if (frm.doc.status === 'Active') {
			frm.add_custom_button(__('Check In Visitor'), function() {
				frappe.call({
					method: 'propms.api.v1.gate_pass.gate_pass.validate_and_checkin_visitor',
					args: { pass_id: frm.doc.name },
					callback: function(r) {
						if (r.message && r.message.status === 'success') {
							frappe.show_alert({ message: __('Visitor Checked In'), indicator: 'green' });
							frm.reload_doc();
						}
					}
				});
			}).addClass('btn-primary');

			frm.add_custom_button(__('Cancel Pass'), function() {
				frappe.confirm(__('Are you sure you want to cancel this visitor pass?'), function() {
					frappe.call({
						method: 'propms.api.v1.gate_pass.gate_pass.cancel_visitor_pass',
						args: { pass_id: frm.doc.name },
						callback: function(r) {
							if (r.message && r.message.status === 'success') {
								frappe.show_alert({ message: __('Pass Cancelled'), indicator: 'orange' });
								frm.reload_doc();
							}
						}
					});
				});
			});
		} else if (frm.doc.status === 'Checked In') {
			frm.add_custom_button(__('Check Out Visitor'), function() {
				frappe.call({
					method: 'propms.api.v1.gate_pass.gate_pass.checkout_visitor',
					args: { pass_id: frm.doc.name },
					callback: function(r) {
						if (r.message && r.message.status === 'success') {
							frappe.show_alert({ message: __('Visitor Checked Out'), indicator: 'blue' });
							frm.reload_doc();
						}
					}
				});
			});
		}
	}
});
