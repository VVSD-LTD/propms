// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Petty Cash Expense Request", {
	amount_excl_vat: (frm) => {
		frm.set_value("amount", frm.doc.amount_excl_vat + frm.doc.vat_amount);
	},
	vat_amount: (frm) => {
		frm.set_value("amount", frm.doc.amount_excl_vat + frm.doc.vat_amount);
	},
	refresh: function(frm) {
		if (frm.doc.dr_account && frm.doc.cr_account) {
			// Check if a non-submitted JE with this cheque_no already exists
			frappe.db.get_value('Journal Entry', {
				cheque_no: frm.doc.name,
				docstatus: ["IN", [0, 1]]  // 0 = Draft, 1 = Submitted, 2 = Cancelled
			}, 'name').then(r => {
				if (r.message && r.message.name) {
					// JE already exists, skip adding the button
					return;
				}

				frm.add_custom_button(__('Create Journal Entry'), function() {
					frappe.route_options = {
						"voucher_type": "Journal Entry",
						// "company": frm.doc.company,
						// "posting_date": frm.doc.posting_date,
						"remarks": `Journal entry for Petty Cash Expense Request ${frm.doc.name}`
					};

					frappe.new_doc('Journal Entry', null, function(new_doc) {
						new_doc.accounts = [];
						new_doc.posting_date = frappe.datetime.get_today();
						new_doc.company = frappe.defaults.get_user_default("Company")
						
						let dr_row = frappe.model.add_child(new_doc, 'accounts');
						dr_row.account = frm.doc.dr_account;
						dr_row.debit_in_account_currency = frm.doc.amount_excl_vat;
						dr_row.credit_in_account_currency = 0;
						
						if (frm.doc.vat_amount > 0) {
							let vat_row = frappe.model.add_child(new_doc, 'accounts');
							vat_row.account = frm.doc.tax_account;
							vat_row.debit_in_account_currency = frm.doc.vat_amount;
							vat_row.credit_in_account_currency = 0;
						}

						let cr_row = frappe.model.add_child(new_doc, 'accounts');
						cr_row.account = frm.doc.cr_account;
						cr_row.debit_in_account_currency = 0;
						cr_row.credit_in_account_currency = frm.doc.amount;

						new_doc.user_remark = frm.doc.narration;
						new_doc.remark = frm.doc.narration;
						new_doc.cheque_no = frm.doc.name;
						new_doc.cheque_date = frm.doc.posting_date;

						frappe.set_route('Form', 'Journal Entry', new_doc.name);
					});
				});
			});
		}
	}
});
