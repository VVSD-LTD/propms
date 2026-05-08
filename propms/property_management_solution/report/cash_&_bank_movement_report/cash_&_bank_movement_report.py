# Copyright (c) 2025, Aakvatech and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, getdate


def execute(filters=None):
    validate_filters(filters)
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def validate_filters(filters):
    """Validate required filters"""
    if not filters.get("from_date"):
        frappe.throw(_("Please select From Date"))
    
    if not filters.get("to_date"):
        frappe.throw(_("Please select To Date"))
    
    if not filters.get("company"):
        frappe.throw(_("Please select a Company"))
    
    if getdate(filters.get("from_date")) > getdate(filters.get("to_date")):
        frappe.throw(_("From Date cannot be greater than To Date"))


def get_columns():
    return [
        {
            "fieldname": "account",
            "label": _("Account"),
            "fieldtype": "Link",
            "options": "Account",
            "width": 300
        },
        {
            "fieldname": "currency",
            "label": _("Currency"),
            "fieldtype": "Data",
            "width": 100
        },
        {
            "fieldname": "opening",
            "label": _("Opening"),
            "fieldtype": "Float",
            "width": 150
        },
        {
            "fieldname": "receipts",
            "label": _("Receipts"),
            "fieldtype": "Float",
            "width": 150
        },
        {
            "fieldname": "contra_in",
            "label": _("Contra IN"),
            "fieldtype": "Float",
            "width": 150
        },
        {
            "fieldname": "contra_out",
            "label": _("Contra OUT"),
            "fieldtype": "Float",
            "width": 150
        },
        {
            "fieldname": "payments",
            "label": _("Payments"),
            "fieldtype": "Float",
            "width": 150
        },
        {
            "fieldname": "closing",
            "label": _("Closing"),
            "fieldtype": "Float",
            "width": 150
        },
        {
            "fieldname": "indent",
            "label": _("Indent"),
            "fieldtype": "Int",
            "width": 0,
            "hidden": 1
        },
		{
			"fieldname": "is_group",
			"label": _("Is Group"),
			"fieldtype": "Check",
			"hidden": 1,
		}
    ]


def get_data(filters):
    from_date = getdate(filters.get("from_date"))
    to_date = getdate(filters.get("to_date"))
    company = filters.get("company")
    account_group_filter = filters.get("account_group")
    account_filter = filters.get("account")
    
    # Get account groups from Custom Report Settings
    account_groups = get_account_groups()
    
    if not account_groups:
        frappe.msgprint(_("No account groups configured in Custom Report Settings"))
        return []
    
    # If specific account filter is provided, filter the groups
    if account_filter:
        account_groups = [account_filter]
    elif account_group_filter:
        account_groups = [account_group_filter]
    
    data = []
    grand_totals = {}  # Store grand totals by currency
    
    for group_account in account_groups:
        # Check if it's a group or leaf account
        account_doc = frappe.get_cached_doc("Account", group_account)
        
        if account_doc.is_group:
            # Get all child accounts
            child_accounts = get_child_accounts(group_account, company)
            
            # Collect child account data grouped by currency
            child_data_by_currency = {}
            for child_account in child_accounts:
                account_data = get_account_movement(child_account, from_date, to_date, company)
                if account_data:
                    currency = account_data.get("currency", "TZS")
                    if currency not in child_data_by_currency:
                        child_data_by_currency[currency] = []
                    account_data["indent"] = 1
                    child_data_by_currency[currency].append(account_data)
            
            # Only add group header and accounts if there are child accounts with activity
            if child_data_by_currency:
                for currency in sorted(child_data_by_currency.keys()):
                    # Calculate group total for this currency
                    group_total = calculate_currency_total(child_data_by_currency[currency], currency)
                    
                    # Add group header with totals and currency
                    # data.append({
                    #     "account": f"{group_account}",
                    #     "currency": currency,
                    #     "opening": group_total["opening"],
                    #     "receipts": group_total["receipts"],
                    #     "contra_in": group_total["contra_in"],
                    #     "contra_out": group_total["contra_out"],
                    #     "payments": group_total["payments"],
                    #     "closing": group_total["closing"],
                    #     "indent": 0,
					# 	"is_group": 1
                    # })
                    data.append({
                        "account": f"{group_account}",
                        "currency": currency,
                        "opening": None,
                        "receipts": None,
                        "contra_in": None,
                        "contra_out": None,
                        "payments": None,
                        "closing": None,
                        "indent": 0,
						"is_group": 1
                    })
                    
                    # Add accounts for this currency
                    data.extend(child_data_by_currency[currency])
                    
                    # Accumulate grand totals
                    if currency not in grand_totals:
                        grand_totals[currency] = {
                            "opening": 0.0,
                            "receipts": 0.0,
                            "contra_in": 0.0,
                            "contra_out": 0.0,
                            "payments": 0.0,
                            "closing": 0.0
                        }
                    
                    grand_totals[currency]["opening"] += flt(group_total["opening"])
                    grand_totals[currency]["receipts"] += flt(group_total["receipts"])
                    grand_totals[currency]["contra_in"] += flt(group_total["contra_in"])
                    grand_totals[currency]["contra_out"] += flt(group_total["contra_out"])
                    grand_totals[currency]["payments"] += flt(group_total["payments"])
                    grand_totals[currency]["closing"] += flt(group_total["closing"])
        else:
            # It's a leaf account, add directly if it has activity
            account_data = get_account_movement(group_account, from_date, to_date, company)
            if account_data:
                account_data["indent"] = 0
                data.append(account_data)
                
                # Accumulate grand totals for leaf accounts
                currency = account_data.get("currency", "TZS")
                if currency not in grand_totals:
                    grand_totals[currency] = {
                        "opening": 0.0,
                        "receipts": 0.0,
                        "contra_in": 0.0,
                        "contra_out": 0.0,
                        "payments": 0.0,
                        "closing": 0.0
                    }
                
                grand_totals[currency]["opening"] += flt(account_data.get("opening"))
                grand_totals[currency]["receipts"] += flt(account_data.get("receipts"))
                grand_totals[currency]["contra_in"] += flt(account_data.get("contra_in"))
                grand_totals[currency]["contra_out"] += flt(account_data.get("contra_out"))
                grand_totals[currency]["payments"] += flt(account_data.get("payments"))
                grand_totals[currency]["closing"] += flt(account_data.get("closing"))
    
    # Add grand totals at the bottom
    if grand_totals:
        for currency in sorted(grand_totals.keys()):
            totals = grand_totals[currency]
            data.append({
                "account": "Grand Total",
                "currency": currency,
                "opening": totals["opening"] if totals["opening"] else None,
                "receipts": totals["receipts"] if totals["receipts"] else None,
                "contra_in": totals["contra_in"] if totals["contra_in"] else None,
                "contra_out": totals["contra_out"] if totals["contra_out"] else None,
                "payments": totals["payments"] if totals["payments"] else None,
                "closing": totals["closing"] if totals["closing"] else None,
                "indent": 0
            })
    
    return data


def get_account_groups():
    """Fetch account groups from Custom Report Settings"""
    if not frappe.db.exists("Custom Report Settings", "Custom Report Settings"):
        return []
    
    settings = frappe.get_doc("Custom Report Settings", "Custom Report Settings")
    
    if not settings.account_groups_for_cash_bank_movement_report:
        return []
    
    return [row.account for row in settings.account_groups_for_cash_bank_movement_report]


def get_child_accounts(parent_account, company):
    """Get all child accounts of a parent account"""
    return frappe.db.sql_list("""
        SELECT name 
        FROM `tabAccount`
        WHERE parent_account = %s
        AND company = %s
        AND is_group = 0
        ORDER BY name
    """, (parent_account, company))


def calculate_currency_total(accounts_data, currency):
    """Calculate total for a specific currency"""
    total = {
        "opening": 0.0,
        "receipts": 0.0,
        "contra_in": 0.0,
        "contra_out": 0.0,
        "payments": 0.0,
        "closing": 0.0
    }
    
    for account_data in accounts_data:
        total["opening"] += flt(account_data.get("opening", 0))
        total["receipts"] += flt(account_data.get("receipts", 0))
        total["contra_in"] += flt(account_data.get("contra_in", 0))
        total["contra_out"] += flt(account_data.get("contra_out", 0))
        total["payments"] += flt(account_data.get("payments", 0))
        total["closing"] += flt(account_data.get("closing", 0))
    
    # Set to None if value is 0
    for key in ["opening", "receipts", "contra_in", "contra_out", "payments", "closing"]:
        if total[key] == 0:
            total[key] = None
    
    return total


def get_account_movement(account, from_date, to_date, company):
    """Calculate opening, movements, and closing for an account"""
    
    # Get account currency
    account_currency = frappe.db.get_value("Account", account, "account_currency")
    
    # Get opening balance (balance before from_date)
    opening_balance = get_opening_balance(account, from_date, company)
    
    # Get movements between from_date and to_date
    movements = get_movements(account, from_date, to_date, company)
    
    # Calculate closing balance
    closing_balance = opening_balance + movements["receipts"] + movements["contra_in"] - movements["contra_out"] - movements["payments"]
    
    # Check if account has any activity (opening, movements, or closing)
    has_activity = (
        opening_balance != 0 or 
        movements["receipts"] != 0 or 
        movements["contra_in"] != 0 or 
        movements["contra_out"] != 0 or 
        movements["payments"] != 0 or 
        closing_balance != 0
    )
    
    # Return None if no activity
    if not has_activity:
        return None
    
    return {
        "account": account,
        "currency": account_currency or "TZS",
        "opening": opening_balance,
        "receipts": movements["receipts"] if movements["receipts"] else None,
        "contra_in": movements["contra_in"] if movements["contra_in"] else None,
        "contra_out": movements["contra_out"] if movements["contra_out"] else None,
        "payments": movements["payments"] if movements["payments"] else None,
        "closing": closing_balance
    }


def get_opening_balance(account, from_date, company):
    """Get opening balance before the from_date"""
    
    opening = frappe.db.sql("""
        SELECT SUM(debit_in_account_currency - credit_in_account_currency) as balance
        FROM `tabGL Entry`
        WHERE account = %s
        AND company = %s
        AND posting_date < %s
        AND is_cancelled = 0
    """, (account, company, from_date), as_dict=1)
    
    return flt(opening[0].balance) if opening else 0.0


def get_movements(account, from_date, to_date, company):
    """Get movements (receipts, payments, contra in/out) between from_date and to_date"""
    
    movements = {
        "receipts": 0.0,
        "payments": 0.0,
        "contra_in": 0.0,
        "contra_out": 0.0
    }
    
    # Get all GL entries for the account between from_date and to_date
    gl_entries = frappe.db.sql("""
        SELECT 
            voucher_type,
            voucher_subtype,
            debit_in_account_currency as debit,
            credit_in_account_currency as credit
        FROM `tabGL Entry`
        WHERE account = %s
        AND company = %s
        AND posting_date BETWEEN %s AND %s
        AND is_cancelled = 0
    """, (account, company, from_date, to_date), as_dict=1)
    
    for entry in gl_entries:
        voucher_type = entry.voucher_type
        voucher_subtype = entry.voucher_subtype
        debit = flt(entry.debit)
        credit = flt(entry.credit)
        
        # Journal Entry logic
        if voucher_type == "Journal Entry":
            if voucher_subtype in ["Cash Entry", "Bank Entry"]:
                if debit > 0:
                    movements["receipts"] += debit
                if credit > 0:
                    movements["payments"] += credit
            elif voucher_subtype == "Contra Entry":
                if debit > 0:
                    movements["contra_in"] += debit
                if credit > 0:
                    movements["contra_out"] += credit
        
        # Payment Entry logic
        elif voucher_type == "Payment Entry":
            if voucher_subtype == "Pay":
                if credit > 0:
                    movements["payments"] += credit
            elif voucher_subtype == "Receive":
                if debit > 0:
                    movements["receipts"] += debit
            elif voucher_subtype == "Internal Transfer":
                if debit > 0:
                    movements["contra_in"] += debit
                if credit > 0:
                    movements["contra_out"] += credit

        elif voucher_type == "Purchase Invoice":
            if credit > 0:
                movements["payments"] += credit

        elif voucher_type == "Sales Invoice":
            if debit > 0:
                movements["receipts"] += debit
    
    return movements