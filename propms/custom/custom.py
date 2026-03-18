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
    installed_equipments = frappe.db.sql("""
        SELECT 
            name, equipment_type, next_service_date, parent, label, location
        FROM 
            `tabProperty Installed Equipment Detail`
        WHERE 
            enabled = 1
        AND 
            next_service_date = CURDATE()  -- filter in SQL, avoids type mismatch
        AND
            parentfield = 'table_5'  -- ensure we only get enabled equipments
        -- AND
            -- last_service_date != CURDATE()  -- avoid creating multiple job cards for same equipment in a day
    """, as_dict=True)

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