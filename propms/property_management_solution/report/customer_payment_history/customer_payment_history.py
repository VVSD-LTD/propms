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

	# ── 2. Fetch payments from both Payment Entry and Journal Entry ───────────
	pe_map = _payment_dates_from_pe(invoice_names)
	jv_map = _payment_dates_from_jv(invoice_names)

	# Merge: keep earliest date; note the source for display
	payment_map = {}
	for name in invoice_names:
		pe_row = pe_map.get(name)   # (date, "Payment Entry") or None
		jv_row = jv_map.get(name)   # (date, "Journal Entry") or None

		if pe_row and jv_row:
			payment_map[name] = pe_row if pe_row[0] <= jv_row[0] else jv_row
		elif pe_row:
			payment_map[name] = pe_row
		elif jv_row:
			payment_map[name] = jv_row

	# ── 3. Build report rows ──────────────────────────────────────────────────
	data = []
	for inv in invoices:
		inv_name     = inv["invoice_no"]
		invoice_date = inv["invoice_date"]
		due_date     = inv["due_date"]
		amount       = flt(inv["amount"])
		is_penalty   = _is_penalty_invoice(inv)
		cost_center  = inv["cost_center"]

		# Period ──────────────────────────────────────────────────────────────
		# Penalty invoices always show "Penalty charges".
		# Normal invoices use the lease from_date / to_date on the invoice.
		period = "Penalty charges" if is_penalty else _build_period(inv)

		# Payment info ────────────────────────────────────────────────────────
		payment_info   = payment_map.get(inv_name)
		payment_date   = payment_info[0] if payment_info else None
		payment_source = payment_info[1] if payment_info else ""

		# Delay & Remarks ─────────────────────────────────────────────────────
		days_delay_raw     = None
		days_delay_display = ""
		remarks            = ""

		if is_penalty:
			# Penalty rows: no due date, no payment date, no delay shown
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
			# Invoice exists but no payment found yet
			remarks = "OUTSTANDING"

		data.append({
			"invoice_no":      inv_name,
			"period":          period,
			"amount":          amount,
			"invoice_date":    invoice_date,
			"due_date":        due_date,
			"payment_date":    payment_date,
			"payment_source":  payment_source,
			"days_delay":      days_delay_display,
			"_days_delay_raw": days_delay_raw,   # used by JS formatter only
			"remarks":         remarks,
			"cost_center":     cost_center,
		})

	return data


# ── Payment Entry helper ──────────────────────────────────────────────────────

def _payment_dates_from_pe(invoice_names):
    """
    Returns {invoice_name: (earliest_posting_date, "Payment Entry")}
    Looks in tabPayment Entry Reference linked to submitted Payment Entries.
    """
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
    Returns {invoice_name: (earliest_posting_date, "Journal Entry")}

    In the real data, JV payments appear as a Journal Entry Account row that:
      - has reference_type = 'Sales Invoice'
      - has reference_name = '<invoice_name>'
      - has credit > 0  (crediting the Debtors account = customer paying)

    Example from actual JV data:
      account: "11401 - Debtors - TZS - VPL"
      credit_in_account_currency: 8663088
      reference_type: "Sales Invoice"
      reference_name: "ACC-SINV-2025-02348"
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
            jea.reference_type  = 'Sales Invoice'
            AND jea.reference_name IN %(names)s
            AND jea.credit > 0
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
    Format: DD.MM.YYYY - DD.MM.YYYY  (matches the sample report exactly)

    Source: si.from_date and si.to_date — these are the lease service dates
    stored on the Sales Invoice (visible in the real invoice JSON as
    "from_date": "2025-06-20", "to_date": "2025-09-19").

    Falls back to just the invoice posting date if lease dates are absent.
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

    # Fallback: just the invoice posting date
    d = getdate(inv.get("invoice_date"))
    return f"{d.day:02d}.{d.month:02d}.{d.year}"


# ── Penalty invoice detection ─────────────────────────────────────────────────

def _is_penalty_invoice(inv):
    """
    Penalty invoices in this ERPNext setup are identified by an EXTRA
    short numeric suffix appended to the standard invoice name:

      Normal  → ACC-SINV-2025-02348       ends with a long seq number
      Penalty → ACC-SINV-2025-03543-2     has -N after the long seq number

    Regex: -\d{4,}-\d{1,3}$
      \d{4,}    matches the standard 4-5 digit sequence number
      -\d{1,3}$ matches the extra 1–3 digit penalty suffix

    Also checks invoice remarks for known penalty keywords.
    """
    name    = inv.get("invoice_no") or ""
    remarks = (inv.get("invoice_remarks") or "").lower()

    penalty_keywords = (
        "penalty",
        "late payment",
        "late charge",
        "surcharge",
        "interest charge",
    )

    has_extra_suffix = bool(re.search(r"-\d{4,}-\d{1,3}$", name))
    has_keyword      = any(kw in remarks for kw in penalty_keywords)

    return has_extra_suffix or has_keyword