// -*- coding: utf-8 -*-
frappe.ui.form.on('Sales Invoice Penalty Settings', {
    refresh: function(frm) {
        // Prevent user from adding or removing rows from the excluded days table
        frm.get_field('excluded_days').grid.cannot_add_rows = true;
        frm.get_field('excluded_days').grid.cannot_delete_rows = true;
        frm.refresh_field('excluded_days');
    }
});
