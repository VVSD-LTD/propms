// -*- coding: utf-8 -*-
frappe.ui.form.on('Sales Invoice Penalty Settings', {
	refresh: function(frm) {
		frm.get_field('excluded_days').grid.cannot_add_rows = true;
		frm.get_field('excluded_days').grid.cannot_delete_rows = true;
		frm.refresh_field('excluded_days');

		frm.add_custom_button(__('Create Previous Penalties'), function() {
			frappe.confirm(
				__(
					'This will create missing daily penalty rows for all submitted Sales Invoices where ' +
					'<b>Outstanding is W/H</b> is not ticked and <b>outstanding amount &gt; 1</b>. ' +
					'Days already recorded and excluded calendar days will be skipped. Continue?'
				),
				function() {
					frappe.call({
						method: 'propms.custom.sales_invoice_penalty.backfill_previous_sales_invoice_penalties',
						freeze: true,
						freeze_message: __('Queuing penalty backfill...'),
						callback: function(r) {
							if (r.message && r.message.message) {
								frappe.msgprint({
									title: __('Penalty Backfill'),
									indicator: 'blue',
									message: r.message.message,
								});
							}
						},
					});
				}
			);
		}).addClass('btn-primary');
	},
});
