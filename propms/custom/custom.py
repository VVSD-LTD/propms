import frappe
from datetime import timedelta

def before_save(doc, method):
    # Only for Sales Invoice
    if doc.doctype != "Sales Invoice":
        return

    # Respect manual override
    if doc.edit_payment_due_date:
        return

    TERMS_MAP = {
        3: "3 DAYS",
        7: "7 Days",
        21: "21 Days",
    }

    for item in doc.items:
        if not item.due_date_duration:
            continue

        # duration is stored in SECONDS
        seconds = int(item.due_date_duration)
        days = seconds // 86400  # normalize seconds → days

        # Set due date (safe for Date field)
        doc.due_date = (
            frappe.utils.get_datetime(doc.posting_date)
            + timedelta(seconds=seconds)
        ).date()

        # Set payment terms template based on days
        payment_terms = TERMS_MAP.get(days)
        if payment_terms:
            doc.payment_terms_template = None  # Clear existing template to avoid conflicts
            doc.payment_terms_template = payment_terms
            doc.payment_schedule = []
            doc.append("payment_schedule", {
                "payment_term": payment_terms,
                "due_date": doc.due_date,
                "invoice_portion": 100,
                "payment_amount": doc.grand_total,
                "base_payment_amount": doc.base_grand_total,
            })

        # First valid item defines invoice due date
        break

