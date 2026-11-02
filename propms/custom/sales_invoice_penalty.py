# -*- coding: utf-8 -*-
import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, today
from propms.utils.business_calendar import get_excluded_days_map, is_excluded_today


def is_penalty_excluded_today(settings_doc, target_date=None):
    if not target_date:
        target_date = today()

    excluded_days_map = get_excluded_days_map(settings_doc)
    return is_excluded_today(excluded_days_map, target_date)


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


def iter_penalty_dates(due_date, settings, current_date=None, existing_dates=None):
    """Dates that should get a penalty row for one invoice, using current settings."""
    if not due_date:
        return []

    current_date = getdate(current_date or today())
    due_date = getdate(due_date)
    existing_dates = {getdate(d) for d in (existing_dates or [])}
    days_after_overdue = cint(getattr(settings, "days_after_overdue", None) or 1)
    start_date = add_days(due_date, days_after_overdue)
    if start_date > current_date:
        return []

    dates = []
    day = start_date
    while day <= current_date:
        if day not in existing_dates:
            is_excluded, _reason = is_penalty_excluded_today(settings, day)
            if not is_excluded:
                dates.append(day)
        day = add_days(day, 1)
    return dates


def _existing_penalty_dates(doc):
    return {
        getdate(row.date)
        for row in doc.get("sales_invoice_penalty_details", [])
        if row.get("date")
    }


def _append_penalty_row(doc, settings, target_date):
    penalty_amount = evaluate_penalty_formula(settings.formula, settings, doc)
    if penalty_amount <= 0:
        return False

    doc.append(
        "sales_invoice_penalty_details",
        {
            "date": getdate(target_date),
            "outstanding_amount": doc.outstanding_amount,
            "formula": settings.formula,
            "percent": settings.penalty_percent,
            "amount": penalty_amount,
            "currency": doc.currency,
        },
    )
    return True


def _save_penalty_totals(doc):
    total_penalty = sum(
        flt(row.amount) for row in doc.get("sales_invoice_penalty_details", [])
    )
    doc.total_penalty_amount = total_penalty
    doc.outstanding_penalty_amount = total_penalty
    doc.flags.ignore_validate = True
    doc.save(ignore_permissions=True)


def _eligible_penalty_invoices(current_date):
    return frappe.db.sql(
        """
        SELECT name, due_date, outstanding_amount, outstanding_withholding
        FROM `tabSales Invoice`
        WHERE docstatus = 1
          AND outstanding_amount >= 1
          AND IFNULL(outstanding_withholding, 0) = 0
          AND due_date < %s
        """,
        (current_date,),
        as_dict=True,
    )


@frappe.whitelist()
def backfill_previous_sales_invoice_penalties():
    """Create missing daily penalty rows from due date + T+X through today."""
    return run_penalty_backfill()


def run_penalty_backfill():
    current_date = today()
    settings = frappe.get_single("Sales Invoice Penalty Settings")
    invoices_updated = 0
    rows_added = 0

    for inv in _eligible_penalty_invoices(current_date):
        doc = frappe.get_doc("Sales Invoice", inv.name)
        dates = iter_penalty_dates(
            due_date=inv.due_date,
            settings=settings,
            current_date=current_date,
            existing_dates=_existing_penalty_dates(doc),
        )
        if not dates:
            continue

        added = 0
        for day in dates:
            if _append_penalty_row(doc, settings, day):
                added += 1

        if not added:
            continue

        _save_penalty_totals(doc)
        frappe.db.commit()
        invoices_updated += 1
        rows_added += added

    message = _(
        "Penalty backfill finished: {0} row(s) added on {1} sales invoice(s)."
    ).format(rows_added, invoices_updated)
    frappe.publish_realtime(
        "msgprint",
        {"message": message, "title": _("Penalty Backfill"), "indicator": "green"},
        user=frappe.session.user,
    )
    frappe.logger().info(message)
    return {"message": message, "invoices_updated": invoices_updated, "rows_added": rows_added}


def process_daily_sales_invoice_penalties():
    current_date = today()
    settings = frappe.get_single("Sales Invoice Penalty Settings")

    is_excluded, reason = is_penalty_excluded_today(settings, current_date)
    if is_excluded:
        frappe.logger().info(f"Skipping daily penalty calculation for {current_date}: {reason}")
        return

    days_after_overdue = cint(settings.days_after_overdue or 1)

    for inv in _eligible_penalty_invoices(current_date):
        due_date = getdate(inv.due_date)
        days_overdue = date_diff(current_date, due_date)

        if days_overdue < days_after_overdue:
            continue

        doc = frappe.get_doc("Sales Invoice", inv.name)
        if getdate(current_date) in _existing_penalty_dates(doc):
            continue

        if not _append_penalty_row(doc, settings, current_date):
            continue

        _save_penalty_totals(doc)
        frappe.db.commit()


def penalty_amount(source):
    amount = flt(source.get("outstanding_penalty_amount"))
    if amount <= 0:
        amount = flt(source.get("total_penalty_amount"))
    return amount


def assert_can_create_penalty_invoice(source, settings):
    if cint(source.get("docstatus")) != 1:
        frappe.throw(_("Penalty invoice can only be created from a submitted Sales Invoice"))
    if not cint(source.get("penalty_paid")):
        frappe.throw(_("Tick Penalty Paid before creating a penalty invoice"))
    if source.get("penalty_invoice"):
        frappe.throw(
            _("Penalty invoice {0} already exists").format(source.get("penalty_invoice"))
        )
    if not settings.get("penalty_item"):
        frappe.throw(_("Set Penalty Item on Sales Invoice Penalty Settings"))
    if penalty_amount(source) <= 0:
        frappe.throw(_("This invoice has no penalty amount to bill"))


def penalty_invoice_tax_template(source):
    """Default Sales Taxes and Charges Template for the company, else the source invoice."""
    company = source.get("company")
    default_template = None
    if company:
        default_template = frappe.db.get_value(
            "Sales Taxes and Charges Template",
            {"is_default": 1, "company": company, "disabled": 0},
            "name",
        )
    return default_template or source.get("taxes_and_charges")


def penalty_invoice_exchange_rates(source):
    """Reuse the source invoice rate so a foreign-currency penalty does not need a new Currency Exchange row."""
    company = source.get("company")
    currency = source.get("currency")
    if not (company and currency):
        return {}

    company_currency = frappe.get_cached_value("Company", company, "default_currency")
    if not company_currency or currency == company_currency:
        return {}

    rates = {}
    conversion_rate = flt(source.get("conversion_rate"))
    if conversion_rate:
        rates["conversion_rate"] = conversion_rate

    price_list_currency = source.get("price_list_currency")
    plc_conversion_rate = flt(source.get("plc_conversion_rate"))
    if price_list_currency and price_list_currency != company_currency and plc_conversion_rate:
        rates["plc_conversion_rate"] = plc_conversion_rate
    return rates


def penalty_invoice_payload(source, settings):
    amount = penalty_amount(source)
    cost_center = source.get("cost_center")
    payload = {
        "doctype": "Sales Invoice",
        "customer": source.get("customer"),
        "company": source.get("company"),
        "currency": source.get("currency"),
        "selling_price_list": source.get("selling_price_list"),
        "cost_center": cost_center,
        "update_stock": 0,
        "remarks": _("Late payment penalty for {0}").format(source.get("name")),
        "items": [
            {
                "item_code": settings.get("penalty_item"),
                "qty": 1,
                "rate": amount,
                "cost_center": cost_center,
            }
        ],
    }
    payload.update(penalty_invoice_exchange_rates(source))
    tax_template = penalty_invoice_tax_template(source)
    if tax_template:
        payload["taxes_and_charges"] = tax_template
    return payload


@frappe.whitelist()
def create_penalty_invoice(sales_invoice):
    """Create a draft Sales Invoice for accrued penalty and store its name."""
    source = frappe.get_doc("Sales Invoice", sales_invoice)
    settings = frappe.get_single("Sales Invoice Penalty Settings")
    assert_can_create_penalty_invoice(source, settings)

    payload = penalty_invoice_payload(source, settings)
    if not payload.get("cost_center"):
        company_cost_center = frappe.db.get_value("Company", source.company, "cost_center")
        payload["cost_center"] = company_cost_center
        payload["items"][0]["cost_center"] = company_cost_center

    invoice = frappe.get_doc(payload)
    if invoice.get("taxes_and_charges"):
        invoice.append_taxes_from_master()
    invoice.calculate_taxes_and_totals()
    invoice.insert(ignore_permissions=True)
    source.db_set("penalty_invoice", invoice.name, update_modified=True)
    frappe.db.commit()
    return {"penalty_invoice": invoice.name}
