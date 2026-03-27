import frappe
from datetime import timedelta

def before_save(doc, method):
    # Only for Sales Invoice
    if doc.doctype != "Sales Invoice":
        return

    # Return if doc doesnot edit_payment_due_date field
    if not hasattr(doc, "edit_payment_due_date"):
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

        if not hasattr(item, "due_date_duration"):
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

@frappe.whitelist()
def create_maintenance_job_card():
    today = frappe.utils.today()
    installed_equipments = frappe.db.sql("""
        SELECT 
            name, equipment_type, next_service_date, parent, label, location
        FROM 
            `tabProperty Installed Equipment Detail`
        WHERE 
            enabled = 1
        AND 
            next_service_date = %s  -- filter in SQL, avoids type mismatch
        AND
            parentfield = 'table_5'  -- ensure we only get enabled equipments
        -- AND
            -- last_service_date != CURDATE()  -- avoid creating multiple job cards for same equipment in a day
    """, today, as_dict=True)

    for equipment in installed_equipments:
        try:
            job_card = frappe.new_doc("Equipment Maintenance Job Card")
            job_card.equipment_type = equipment.equipment_type
            job_card.subject = f"Maintenance of {equipment.parent} - {equipment.label} - {equipment.location}"
            job_card.insert(ignore_permissions=True)  # use insert() for new docs

            frappe.db.set_value(
                "Property Installed Equipment Detail",
                equipment.name,
                "job_card",
                job_card.name
            )
            frappe.db.commit()

        except Exception as e:
            frappe.log_error(frappe.get_traceback(), f"Job Card Creation Failed: {equipment.name}")

def get_overdue_sales_invoices():
    enabled_email_settings = frappe.db.get_single_value(
        "Property Management Settings", "enable_due_invoice_email_sending"
    )
    if not enabled_email_settings:
        return []

    email_settings = frappe.db.get_all(
        "Property Management Email Setting",
        filters={"enabled": 1, "docstatus": 1},
        fields=["name", "payment_term"],
    )
    if not email_settings:
        return []

    for setting in email_settings:
        if not setting.payment_term:
            frappe.log_error(
                f"Payment term not set for email setting: {setting.name}",
                "Overdue Invoice Email Sending",
            )
            continue

        doc = frappe.get_doc("Property Management Email Setting", setting.name)

        # Today + days_due = the due date we are targeting
        # e.g. today = 06-09-2026, days_due = 3 → target = 09-09-2026
        target_due_date = frappe.utils.add_days(frappe.utils.today(), doc.days_due)

        overdue_invoices = frappe.db.sql(
            """
            SELECT
                name, customer, due_date, grand_total, outstanding_amount
            FROM
                `tabSales Invoice`
            WHERE
                docstatus = 1
            AND
                outstanding_amount > 0
            AND
                payment_terms_template = %s
            AND
                due_date = %s
            """,
            (setting.payment_term, target_due_date),
            as_dict=True,
        )

        for invoice in overdue_invoices:
            # Check customer has an email
            customer_email = frappe.db.get_value(
                "Customer", invoice.customer, "email_id"
            )
            if not customer_email:
                frappe.log_error(
                    f"No email found for customer {invoice.customer}, skipping invoice {invoice.name}",
                    "Overdue Invoice Email Sending",
                )
                continue

            # Skip if already notified today for this invoice
            already_notified = frappe.db.exists(
                "Notify Customer",
                {
                    "customer": invoice.customer,
                    "invoice_no": invoice.name,
                    "posting_date": frappe.utils.today(),
                },
            )
            if already_notified:
                continue

            # Fetch full docs so every field is available in Jinja
            invoice_doc = frappe.get_doc("Sales Invoice", invoice.name)
            customer_doc = frappe.get_doc("Customer", invoice.customer)
            currency = (
                customer_doc.default_currency
                or frappe.get_cached_value("Global Defaults", None, "default_currency")
            )

            # Universal context — all fields available at top level and via doc
            context = invoice_doc.as_dict()
            context.update({
                "doc": invoice_doc,
                "customer_doc": customer_doc,
                "currency": currency,
                "frappe": frappe,
                "nowdate": frappe.utils.nowdate,
                "format_currency": frappe.utils.fmt_money,
            })

            try:
                rendered_body = frappe.render_template(doc.body, context)
                rendered_subject = frappe.render_template(doc.subject, context)
            except Exception as e:
                frappe.log_error(
                    f"Failed to render template for invoice {invoice.name}: {str(e)}",
                    "Overdue Invoice Email Sending",
                )
                continue

            notify_doc = frappe.get_doc({
                "doctype": "Notify Customer",
                "posting_date": frappe.utils.today(),
                "posting_time": frappe.utils.now_datetime().strftime("%H:%M:%S"),
                "customer": invoice.customer,
                "customer_email": customer_email,
                "subject": rendered_subject,
                "due_amount": invoice_doc.outstanding_amount,
                "invoice_no": f"<a href='/app/sales-invoice/{invoice_doc.name}'>{invoice_doc.name}</a>",
                "message": rendered_body,
                "currency": currency,
            })
            notify_doc.insert(ignore_permissions=True)
            frappe.db.commit()