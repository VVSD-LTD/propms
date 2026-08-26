# -*- coding: utf-8 -*-
import frappe
from frappe.utils import cint, date_diff, flt, getdate, today
from propms.property_management_solution.doctype.sales_invoice_penalty_settings.sales_invoice_penalty_settings import (
    is_date_public_holiday,
)


def is_penalty_excluded_today(settings_doc, target_date=None):
    if not target_date:
        target_date = today()

    dt = getdate(target_date)
    day_name = dt.strftime("%A")

    excluded_days_map = {
        row.day: row.exclude for row in settings_doc.get("excluded_days", [])
    }

    # Check Day of Week (Monday - Sunday)
    if excluded_days_map.get(day_name):
        return True, f"Day of week ({day_name}) is excluded"

    # Check Public Holiday
    if excluded_days_map.get("Public Holiday"):
        if is_date_public_holiday(dt):
            return True, f"Date ({dt}) is a Public Holiday"

    return False, None


def evaluate_penalty_formula(formula, settings_doc, invoice_doc):
    if not formula:
        return 0.0

    context = {}
    if hasattr(invoice_doc, "as_dict"):
        context.update(invoice_doc.as_dict())
    elif isinstance(invoice_doc, dict):
        context.update(invoice_doc)

    if hasattr(settings_doc, "as_dict"):
        settings_dict = settings_doc.as_dict()
    elif isinstance(settings_doc, dict):
        settings_dict = settings_doc
    else:
        settings_dict = {}

    # Fields on formula pick from settings first, then sales invoice
    context.update(settings_dict)

    # Convenience aliases
    context["days"] = flt(settings_dict.get("days_to_divide_percent") or 365)
    context["percent"] = flt(settings_dict.get("penalty_percent") or 0)
    context["days_after_overdue"] = cint(settings_dict.get("days_after_overdue") or 1)
    context["doc"] = invoice_doc
    context["settings"] = settings_doc
    context["flt"] = flt
    context["cint"] = cint

    try:
        result = frappe.safe_eval(formula, eval_globals={}, eval_locals=context)
        return flt(result)
    except Exception as e:
        frappe.log_error(
            f"Error evaluating penalty formula '{formula}' for Sales Invoice {getattr(invoice_doc, 'name', '')}: {str(e)}",
            "Sales Invoice Penalty Calculation Error",
        )
        return 0.0


def process_daily_sales_invoice_penalties():
    current_date = today()
    settings = frappe.get_single("Sales Invoice Penalty Settings")

    is_excluded, reason = is_penalty_excluded_today(settings, current_date)
    if is_excluded:
        frappe.logger().info(f"Skipping daily penalty calculation for {current_date}: {reason}")
        return

    days_after_overdue = cint(settings.days_after_overdue or 1)

    invoices = frappe.db.sql(
        """
        SELECT name, due_date, outstanding_amount, outstanding_withholding
        FROM `tabSales Invoice`
        WHERE docstatus = 1
          AND outstanding_amount >= 1
          AND outstanding_withholding = 0
          AND due_date < %s
        """,
        (current_date,),
        as_dict=True,
    )

    for inv in invoices:
        # if inv.get("outstanding_withholding"):
        #     continue

        due_date = getdate(inv.due_date)
        days_overdue = date_diff(current_date, due_date)

        if days_overdue < days_after_overdue:
            continue

        doc = frappe.get_doc("Sales Invoice", inv.name)

        already_processed = any(
            getdate(row.date) == getdate(current_date)
            for row in doc.get("sales_invoice_penalty_details", [])
        )
        if already_processed:
            continue

        penalty_amount = evaluate_penalty_formula(settings.formula, settings, doc)
        if penalty_amount <= 0:
            continue

        doc.append(
            "sales_invoice_penalty_details",
            {
                "date": current_date,
                "outstanding_amount": doc.outstanding_amount,
                "formula": settings.formula,
                "percent": settings.penalty_percent,
                "amount": penalty_amount,
                "currency": doc.currency,
            },
        )

        total_penalty = sum(
            flt(row.amount) for row in doc.get("sales_invoice_penalty_details", [])
        )
        doc.total_penalty_amount = total_penalty
        doc.outstanding_penalty_amount = total_penalty

        doc.flags.ignore_validate = True
        doc.save(ignore_permissions=True)
        frappe.db.commit()
