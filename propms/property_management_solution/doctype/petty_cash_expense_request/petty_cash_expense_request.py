# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PettyCashExpenseRequest(Document):
	pass

@frappe.whitelist()
def check_if_journal_created(petty_cash_expense_request):
	petty_cash_expense_request = frappe.get_doc("Petty Cash Expense Request", petty_cash_expense_request)
	
	journal_entry = frappe.db.get_value('Journal Entry', {
		"cheque_no": petty_cash_expense_request.name,
		"docstatus": ["IN", [0, 1]]
	}, 'name')
	if journal_entry:
		if petty_cash_expense_request.journal_entry_created != 1:
			petty_cash_expense_request.journal_entry_created = 1
			petty_cash_expense_request.save()

@frappe.whitelist()
def set_journal_entry_created(petty_cash_expense_request):
	petty_cash_expense_request = frappe.get_doc("Petty Cash Expense Request", petty_cash_expense_request)
	petty_cash_expense_request.journal_entry_created = 1
	petty_cash_expense_request.save()


@frappe.whitelist()
def patch_to_fix_journal_entry_created():
	petty_cash_expense_requests = frappe.get_all("Petty Cash Expense Request", filters={"journal_entry_created": 0}, fields=["name"])
	for request in petty_cash_expense_requests:
		check_if_journal_created(request.name)
	return "Patch executed successfully. Checked and updated journal_entry_created for all Petty Cash Expense Requests."