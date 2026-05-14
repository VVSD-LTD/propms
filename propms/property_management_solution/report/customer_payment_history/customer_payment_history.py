# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import re
import frappe
from frappe import _
from frappe.utils import date_diff, getdate, flt


def execute(filters=None):
    columns = get_columns()
    data    = get_data(filters or {})
    return columns, data


# ── Columns ──────────────────────────────────────────────────────────────────

def get_columns():
    return [
        {
            "fieldname": "invoice_no",
            "label":     _("Invoice Voucher No"),
            "fieldtype": "Link",
            "options":   "Sales Invoice",
            "width":     220,
        },
        {
            "fieldname": "cost_center",
            "label":     _("Cost Center"),
            "fieldtype": "Link",
            "options":   "Cost Center",
            "width":     150,
        },
        {
            "fieldname": "period",
            "label":     _("Period"),
            "fieldtype": "Data",
            "width":     210,
        },
        {
            "fieldname": "amount",
            "label":     _("Rent & Maintenance"),
            "fieldtype": "Currency",
            "width":     170,
        },
        {
            "fieldname": "invoice_date",
            "label":     _("Invoice Issue Date"),
            "fieldtype": "Date",
            "width":     140,
        },
        {
            "fieldname": "due_date",
            "label":     _("Due Date"),
            "fieldtype": "Date",
            "width":     120,
        },
        {
            "fieldname": "payment_date",
            "label":     _("Payment Received Date"),
            "fieldtype": "Date",
            "width":     170,
        },
        # {
        #     "fieldname": "payment_source",
        #     "label":     _("Paid Via"),
        #     "fieldtype": "Data",
        #     "width":     130,
        # },
        {
            "fieldname": "days_delay",
            "label":     _("Days Delay in Payment"),
            "fieldtype": "Data",
            "width":     170,
        },
        {
            "fieldname": "remarks",
            "label":     _("Remarks"),
            "fieldtype": "Data",
            "width":     190,
        },
    ]


# ── Main data fetch ───────────────────────────────────────────────────────────

def get_data(filters):
    customer  = filters.get("customer")
    from_date = filters.get("from_date")
    to_date   = filters.get("to_date")
    # company   = filters.get("company")
    is_pos    = filters.get("is_pos")   # checkbox: 1 = POS invoices, 0/None = non-POS

    if not customer:
        return []

    # ── 1. Build invoice query conditions ────────────────────────────────────
    conds = [
        "si.customer = %(customer)s",
        "si.docstatus = 1",
        "si.posting_date BETWEEN %(from_date)s AND %(to_date)s",
    ]

    # if company:
    # 	conds.append("si.company = %(company)s")

    # is_pos filter: checked = show POS invoices, unchecked = exclude POS
    if not is_pos:
        conds.append("si.is_pos = 0")

    where = " AND ".join(conds)

    invoices = frappe.db.sql(
        f"""
        SELECT
            si.name               AS invoice_no,
            si.posting_date       AS invoice_date,
            si.due_date           AS due_date,
            si.grand_total        AS amount,
            si.outstanding_amount AS outstanding_amount,
            si.from_date          AS lease_from,
            si.to_date            AS lease_to,
            si.remarks            AS invoice_remarks,
            si.status             AS status,
            si.is_pos             AS is_pos,
            si.cost_center         AS cost_center
        FROM
            `tabSales Invoice` si
        WHERE
            {where}
        ORDER BY
            si.posting_date ASC
        """,
        {
            "customer":  customer,
            "from_date": from_date,
            "to_date":   to_date,
        },
        as_dict=True,
    )

    if not invoices:
        return []

    invoice_names = [inv["invoice_no"] for inv in invoices]

    invoice_names = [inv["invoice_no"] for inv in invoices]

    # ── 2. Batch-fetch item names for ALL invoices in ONE query ───────────────
    #
    # This is the ONLY reliable way to detect penalty invoices.
    # ERPNext uses the same -N suffix for both amendments (ACC-SINV-2026-00012-1)
    # and penalty invoices (ACC-SINV-2025-03543-2), so name-based regex fails.
    # Instead, we check what items are actually on the invoice.
    #
    # Penalty items will have names like "Penalty", "Late Payment Charges", etc.
    # Normal items will be "Commercial Rent", "Service Charge - Commercial", etc.
    #
    item_rows = frappe.db.sql(
        """
        SELECT
            parent      AS invoice_no,
            item_code   AS item_code,
            item_name   AS item_name
        FROM
            `tabSales Invoice Item`
        WHERE
            parent IN %(names)s
            AND docstatus = 1
        """,
        {"names": invoice_names},
        as_dict=True,
    )

    # Build {invoice_name: [list of lowercase item names/codes]}
    items_map = {}
    for row in item_rows:
        key = row["invoice_no"]
        items_map.setdefault(key, [])
        items_map[key].append((row["item_code"] or "").lower())
        items_map[key].append((row["item_name"] or "").lower())

    # ── 3. Fetch payments: Payment Entry + Journal Entry ──────────────────────
    pe_map = _payment_dates_from_pe(invoice_names)
    jv_map = _payment_dates_from_jv(invoice_names)

    # Merge — take the earliest payment date across both sources
    payment_map = {}
    for name in invoice_names:
        pe_row = pe_map.get(name)
        jv_row = jv_map.get(name)
        if pe_row and jv_row:
            payment_map[name] = pe_row if pe_row[0] <= jv_row[0] else jv_row
        elif pe_row:
            payment_map[name] = pe_row
        elif jv_row:
            payment_map[name] = jv_row

    # ── 4. Build report rows ──────────────────────────────────────────────────
    data = []
    for inv in invoices:
        inv_name     = inv["invoice_no"]
        invoice_date = inv["invoice_date"]
        due_date     = inv["due_date"]
        amount       = flt(inv["amount"])
        cost_center  = inv["cost_center"]

        # Penalty check: based on item names, NOT invoice name suffix
        inv_items    = items_map.get(inv_name, [])
        is_penalty   = _is_penalty_invoice(inv_items)

        # Period
        period = "Penalty charges" if is_penalty else _build_period(inv)

        # Payment
        payment_info   = payment_map.get(inv_name)
        payment_date   = payment_info[0] if payment_info else None
        payment_source = payment_info[1] if payment_info else ""

        # Delay & Remarks
        days_delay_raw     = None
        days_delay_display = ""
        remarks            = ""

        if is_penalty:
            remarks        = "LATE PAYMENT CHARGES"
            due_date       = None
            payment_date   = None
            payment_source = ""

        elif payment_date and due_date:
            days_delay_raw = date_diff(payment_date, due_date)
            if days_delay_raw < 0:
                days_delay_display = f"{days_delay_raw}  DAYS"
                remarks = "PAID IN TIME"
            elif days_delay_raw == 0:
                days_delay_display = "0  DAYS"
                remarks = "PAID IN TIME"
            else:
                days_delay_display = f"+{days_delay_raw}  DAYS"
                remarks = "DELAYED"

        elif flt(inv["outstanding_amount"]) > 0:
            remarks = "OUTSTANDING"

        data.append({
            "invoice_no":      inv_name,
            "period":          period,
            "amount":          amount,
            "cost_center":     cost_center,
            "invoice_date":    invoice_date,
            "due_date":        due_date,
            "payment_date":    payment_date,
            "payment_source":  payment_source,
            "days_delay":      days_delay_display,
            "_days_delay_raw": days_delay_raw,
            "remarks":         remarks,
        })

    return data

 
# ── Payment Entry helper ──────────────────────────────────────────────────────
 
def _payment_dates_from_pe(invoice_names):
    """Returns {invoice_name: (earliest_date, "Payment Entry")}"""
    if not invoice_names:
        return {}
    rows = frappe.db.sql(
        """
        SELECT
            per.reference_name   AS invoice_no,
            MIN(pe.posting_date) AS payment_date
        FROM
            `tabPayment Entry Reference` per
        INNER JOIN `tabPayment Entry` pe
            ON  pe.name        = per.parent
            AND pe.docstatus   = 1
            AND pe.payment_type IN ('Receive', 'Pay')
        WHERE
            per.reference_doctype = 'Sales Invoice'
            AND per.reference_name IN %(names)s
        GROUP BY
            per.reference_name
        """,
        {"names": invoice_names},
        as_dict=True,
    )
    return {r["invoice_no"]: (r["payment_date"], "Payment Entry") for r in rows}
 
 
# ── Journal Entry helper ──────────────────────────────────────────────────────
 
def _payment_dates_from_jv(invoice_names):
    """
    Returns {invoice_name: (earliest_date, "Journal Entry")}
 
    Looks for JE Account rows where:
      - reference_type = 'Sales Invoice'
      - reference_name = invoice name
      - credit > 0  (Debtors account credited = customer payment received)
    """
    if not invoice_names:
        return {}
    rows = frappe.db.sql(
        """
        SELECT
            jea.reference_name   AS invoice_no,
            MIN(je.posting_date) AS payment_date
        FROM
            `tabJournal Entry Account` jea
        INNER JOIN `tabJournal Entry` je
            ON  je.name      = jea.parent
            AND je.docstatus = 1
        WHERE
            jea.reference_type     = 'Sales Invoice'
            AND jea.reference_name IN %(names)s
            AND jea.credit         > 0
        GROUP BY
            jea.reference_name
        """,
        {"names": invoice_names},
        as_dict=True,
    )
    return {r["invoice_no"]: (r["payment_date"], "Journal Entry") for r in rows}
 
 
# ── Period builder ────────────────────────────────────────────────────────────
 
def _build_period(inv):
    """
    Reads si.from_date and si.to_date (lease service period dates) and
    formats them as DD.MM.YYYY - DD.MM.YYYY.
 
    Both invoices in the real data confirm these fields:
      ACC-SINV-2025-02348 : from_date=2025-06-20, to_date=2025-09-19
      ACC-SINV-2026-00012-1: from_date=2026-01-01, to_date=2026-06-30
    """
    lease_from = inv.get("lease_from")
    lease_to   = inv.get("lease_to")
 
    if lease_from and lease_to:
        f = getdate(lease_from)
        t = getdate(lease_to)
        return (
            f"{f.day:02d}.{f.month:02d}.{f.year}"
            " - "
            f"{t.day:02d}.{t.month:02d}.{t.year}"
        )
 
    d = getdate(inv.get("invoice_date"))
    return f"{d.day:02d}.{d.month:02d}.{d.year}"
 
 
# ── Penalty invoice detection ─────────────────────────────────────────────────
 
# Keywords that identify a penalty / late-charge item.
# Add more here if your system uses other naming conventions.
_PENALTY_ITEM_KEYWORDS = (
    "penalty",
    "late payment",
    "late charge",
    "surcharge",
    "interest charge",
    "penal",
)
 
def _is_penalty_invoice(inv_item_strings):
    """
    Determines whether an invoice is a penalty/late-charge invoice by
    inspecting the actual item codes and item names on the invoice.
 
    WHY NOT USE THE NAME SUFFIX?
    ERPNext appends -1, -2, -3 … to both:
      • Amended invoices  (ACC-SINV-2026-00012-1) — normal invoice
      • Penalty invoices  (ACC-SINV-2025-03543-2) — late charge invoice
    The suffix alone is therefore ambiguous and unreliable.
 
    WHAT WE DO INSTEAD:
    We check a list of lowercase item_code + item_name strings for that
    invoice against known penalty keywords. If any match → penalty invoice.
 
    inv_item_strings: list of lowercase item_code/item_name strings
                      built in get_data from the batch items_map lookup.
    """
    return any(
        kw in item_str
        for item_str in inv_item_strings
        for kw in _PENALTY_ITEM_KEYWORDS
    )