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
			frappe.db.get_value('Journal Entry', {
				cheque_no: frm.doc.name,
				docstatus: ["IN", [0, 1]]
			}, 'name').then(r => {
				if (r.message && r.message.name) return;
				
				frm.add_custom_button(__('Create Journal Entry'), function() {
					const cost_center = frm.doc.cost_center;
					// make confirmation dialog before creating journal entry
					frappe.confirm(__('Are you sure you want to create a journal entry for this petty cash expense request?'), function() {
						frappe.new_doc('Journal Entry', null, function(new_doc) {
							new_doc.accounts = [];
							new_doc.company = frappe.defaults.get_user_default("Company");
							new_doc.voucher_type = "Cash Entry";
							new_doc.posting_date = frm.doc.posting_date;
							new_doc.user_remark = frm.doc.narration;
							new_doc.cheque_no = frm.doc.name;
							new_doc.cheque_date = frm.doc.posting_date;
							new_doc.created_from_petty_cash_request = 1;

							let dr_row = frappe.model.add_child(new_doc, 'accounts');
							dr_row.account = frm.doc.dr_account;
							dr_row.debit_in_account_currency = frm.doc.amount_excl_vat;
							dr_row.credit_in_account_currency = 0;
							dr_row.cost_center = cost_center;
							dr_row.project = frm.doc.project;

							if (frm.doc.vat_amount > 0) {
								let vat_row = frappe.model.add_child(new_doc, 'accounts');
								vat_row.account = frm.doc.tax_account;
								vat_row.debit_in_account_currency = frm.doc.vat_amount;
								vat_row.credit_in_account_currency = 0;
								vat_row.cost_center = cost_center;
								vat_row.project = frm.doc.project;
							}

							let cr_row = frappe.model.add_child(new_doc, 'accounts');
							cr_row.account = frm.doc.cr_account;
							cr_row.debit_in_account_currency = 0;
							cr_row.credit_in_account_currency = frm.doc.amount;
							cr_row.cost_center = cost_center;
							cr_row.project = frm.doc.project;

							
							// so the one-time JE refresh handler can pick it up
							frappe._pending_cost_center = cost_center;

							frappe.set_route('Form', 'Journal Entry', new_doc.name);
						});
						frappe.call({
							method: "propms.property_management_solution.doctype.petty_cash_expense_request.petty_cash_expense_request.set_journal_entry_created",
							args: {
								petty_cash_expense_request: frm.doc.name
							},
							callback: function(r) {
								if (r.message) {
									frm.refresh_field("journal_entry_created");
								}
							}
						});
					});
				})
			});
			
		}
		// if (frm.doc.docstatus === 1 && frm.doc.workflow_state === "Approved" && !frm.doc.journal_entry_created) {
		// 	// Set journal_entry_created flag to true if a journal entry has been created for this petty cash expense request
		// 	frappe.call({
		// 		method: "propms.property_management_solution.doctype.petty_cash_expense_request.petty_cash_expense_request.check_if_journal_created",
		// 		args: {
		// 			petty_cash_expense_request: frm.doc.name
		// 		},
		// 		callback: function(r) {
		// 			if (r.message) {
		// 				frm.refresh_field("journal_entry_created");
		// 			}
		// 		}
		// 	});
		// }
	},

});