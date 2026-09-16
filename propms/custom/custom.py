import frappe
from datetime import timedelta
from frappe import _
from frappe.utils import cint, date_diff, flt, fmt_money, getdate, today
from frappe.utils.pdf import get_pdf

from propms.utils.business_calendar import (
	get_email_setting_excluded_days_map,
	get_wh_reference_date,
	is_excluded_today,
	is_schedule_due_today,
	is_after_overdue_invoice_eligible,
	is_wh_invoice_eligible,
	resolve_days_after_overdue,
)


def _format_currency(amount, currency=None, precision=None):
	"""Jinja-friendly money formatter: format_currency(amount, currency)."""
	return fmt_money(flt(amount), precision=precision, currency=currency)


def _format_schedule_label(setting_doc):
	frequency = setting_doc.get("frequency") or ""
	if frequency == "Weekly" and setting_doc.get("weekday"):
		return f"Weekly / {setting_doc.weekday}"
	if frequency == "Monthly" and setting_doc.get("day_of_month"):
		return f"Monthly / day {cint(setting_doc.day_of_month)}"
	return frequency or ""

def before_save(doc, method):
    # Only for Sales Invoice
    if doc.doctype != "Sales Invoice":
        return

    _set_outstanding_withholding_date(doc)
    _apply_due_date_from_item_duration(doc)


def before_update_after_submit(doc, method):
    """Submitted invoices only update allow_on_submit fields via this path."""
    if doc.doctype != "Sales Invoice":
        return

    _set_outstanding_withholding_date(doc)


def _set_outstanding_withholding_date(doc):
    """Stamp the date when Outstanding is W/H is newly ticked (draft or submitted)."""
    if not hasattr(doc, "outstanding_withholding"):
        return

    is_ticked = cint(doc.outstanding_withholding)
    prev = doc.get_doc_before_save()
    was_ticked = cint(prev.get("outstanding_withholding")) if prev else 0

    if is_ticked and not was_ticked:
        doc.outstanding_withholding_date = today()
    elif is_ticked and not doc.get("outstanding_withholding_date"):
        # Already ticked (e.g. before this field existed, or update-after-submit missed stamp)
        doc.outstanding_withholding_date = today()
    elif not is_ticked:
        doc.outstanding_withholding_date = None


def _apply_due_date_from_item_duration(doc):
    # Return if doc does not have edit_payment_due_date field
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
            job_card.label = equipment.label
            job_card.equipment = equipment.parent
            job_card.location = equipment.location
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

def _get_primary_contact_emails(customer_name):
    """
    Returns a set of unique email addresses from all primary contacts of a customer.
    Pulls all emails from the Contact Email child table only (avoids duplicates since
    the contact's main email_id is always present in email_ids as is_primary=1).
    """
    contacts = frappe.db.get_all(
        "Contact",
        filters={
            "link_doctype": "Customer",
            "link_name": customer_name,
            "is_primary_contact": 1,
        },
        fields=["name"],
    )

    emails = set()

    for contact in contacts:
        all_emails = frappe.db.get_all(
            "Contact Email",
            filters={"parent": contact.name},
            fields=["email_id"],
        )
        for row in all_emails:
            if row.email_id:
                emails.add(row.email_id.strip().lower())

    return emails

def get_overdue_sales_invoices():
    """Backward-compatible entry point for the scheduled job."""
    return process_invoice_email_reminders()


def process_invoice_email_reminders():
    enabled_email_settings = frappe.db.get_single_value(
        "Property Management Settings", "enable_due_invoice_email_sending"
    )
    if not enabled_email_settings:
        return []

    email_settings = frappe.db.get_all(
        "Property Management Email Setting",
        filters={"enabled": 1, "docstatus": 1},
        fields=["name", "payment_term", "reminder_type"],
    )
    if not email_settings:
        return []

    for setting in email_settings:
        if not setting.payment_term:
            frappe.log_error(
                f"Payment term not set for email setting: {setting.name}",
                "Invoice Email Reminder",
            )
            continue

        doc = frappe.get_doc("Property Management Email Setting", setting.name)
        reminder_type = doc.reminder_type or "Pre-Due"

        if reminder_type == "Pre-Due":
            _process_pre_due_reminder(doc)
        elif reminder_type == "After Overdue":
            _process_after_overdue_reminder(doc)
        elif reminder_type == "Withholding Tax":
            _process_withholding_tax_reminder(doc)


def _process_pre_due_reminder(setting_doc):
    target_due_date = frappe.utils.add_days(frappe.utils.today(), setting_doc.days_due or 0)

    invoices = frappe.db.sql(
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
        AND
            outstanding_withholding = 0
        """,
        (setting_doc.payment_term, target_due_date),
        as_dict=True,
    )

    _create_notify_customer_records(
        setting_doc,
        invoices,
        audience="Standard",
        frequency="Daily",
    )


def _process_after_overdue_reminder(setting_doc):
    current_date = today()
    excluded_days_map = get_email_setting_excluded_days_map(setting_doc)
    is_excluded, reason = is_excluded_today(excluded_days_map, current_date)

    if is_excluded:
        frappe.logger().info(
            f"Skipping after-overdue email reminders for {setting_doc.name} on {current_date}: {reason}"
        )
        return

    if not is_schedule_due_today(
        setting_doc.frequency,
        weekday=setting_doc.weekday,
        day_of_month=setting_doc.day_of_month,
        target_date=current_date,
    ):
        return

    days_after_overdue = resolve_days_after_overdue(setting_doc)
    candidates = _get_after_overdue_invoices(
        setting_doc.payment_term,
        current_date,
        days_after_overdue,
        withholding=0,
    )
    eligible_invoices = []
    for invoice in candidates:
        invoice_doc = frappe.get_doc("Sales Invoice", invoice.name)
        if is_after_overdue_invoice_eligible(invoice_doc, setting_doc, current_date):
            eligible_invoices.append(invoice)

    _create_notify_customer_records(
        setting_doc,
        eligible_invoices,
        audience="Standard",
        frequency=setting_doc.frequency,
    )


def _process_withholding_tax_reminder(setting_doc):
    current_date = today()
    excluded_days_map = get_email_setting_excluded_days_map(setting_doc)
    is_excluded, reason = is_excluded_today(excluded_days_map, current_date)

    if is_excluded:
        frappe.logger().info(
            f"Skipping withholding tax email reminders for {setting_doc.name} on {current_date}: {reason}"
        )
        return

    if not is_schedule_due_today(
        setting_doc.frequency,
        weekday=setting_doc.weekday,
        day_of_month=setting_doc.day_of_month,
        target_date=current_date,
    ):
        return

    candidates = frappe.db.sql(
        """
        SELECT
            name, customer, due_date, posting_date, grand_total, outstanding_amount
        FROM
            `tabSales Invoice`
        WHERE
            docstatus = 1
        AND
            outstanding_amount > 1
        AND
            payment_terms_template = %s
        AND
            outstanding_withholding = 1
        """,
        (setting_doc.payment_term,),
        as_dict=True,
    )

    eligible_invoices = []
    for invoice in candidates:
        invoice_doc = frappe.get_doc("Sales Invoice", invoice.name)
        if is_wh_invoice_eligible(invoice_doc, setting_doc, current_date):
            eligible_invoices.append(invoice)

    _create_notify_customer_records(
        setting_doc,
        eligible_invoices,
        audience="Withholding",
        frequency=setting_doc.frequency,
    )


def _get_after_overdue_invoices(payment_term, current_date, days_after_overdue, withholding=0):
    return frappe.db.sql(
        """
        SELECT
            name, customer, due_date, grand_total, outstanding_amount
        FROM
            `tabSales Invoice`
        WHERE
            docstatus = 1
        AND
            payment_terms_template = %s
        AND
            due_date < %s
        AND
            outstanding_withholding = %s
        AND
            DATEDIFF(%s, due_date) >= %s
        """,
        (payment_term, current_date, withholding, current_date, days_after_overdue),
        as_dict=True,
    )


def _already_notified(
    email_setting_name,
    invoice_name,
    customer,
    email,
    audience,
    frequency,
    target_date=None,
):
    target_date = target_date or today()
    dt = getdate(target_date)

    base_filters = {
        "customer": customer,
        "invoice_no": invoice_name,
        "customer_email": email,
        "email_setting": email_setting_name,
        "reminder_audience": audience,
    }

    if frequency == "Daily":
        if frappe.db.exists(
            "Notify Customer",
            {**base_filters, "posting_date": target_date},
        ):
            return True

        # Backward compatibility with notifications created before tracking fields existed
        return frappe.db.exists(
            "Notify Customer",
            {
                "customer": customer,
                "invoice_no": invoice_name,
                "customer_email": email,
                "posting_date": target_date,
                "email_setting": ("is", "not set"),
            },
        )

    if frequency == "Weekly":
        week_start = dt - timedelta(days=dt.weekday())
        week_end = week_start + timedelta(days=6)
        return frappe.db.sql(
            """
            SELECT name
            FROM `tabNotify Customer`
            WHERE customer = %s
              AND invoice_no = %s
              AND customer_email = %s
              AND email_setting = %s
              AND reminder_audience = %s
              AND posting_date BETWEEN %s AND %s
            LIMIT 1
            """,
            (
                customer,
                invoice_name,
                email,
                email_setting_name,
                audience,
                week_start,
                week_end,
            ),
        )

    if frequency == "Monthly":
        return frappe.db.sql(
            """
            SELECT name
            FROM `tabNotify Customer`
            WHERE customer = %s
              AND invoice_no = %s
              AND customer_email = %s
              AND email_setting = %s
              AND reminder_audience = %s
              AND YEAR(posting_date) = %s
              AND MONTH(posting_date) = %s
            LIMIT 1
            """,
            (
                customer,
                invoice_name,
                email,
                email_setting_name,
                audience,
                dt.year,
                dt.month,
            ),
        )

    return False


def _build_reminder_template_context(setting_doc, invoice_doc, customer_doc=None, is_test=False):
    if not customer_doc:
        customer_doc = frappe.get_doc("Customer", invoice_doc.customer)

    currency = (
        customer_doc.default_currency
        or frappe.get_cached_value("Global Defaults", None, "default_currency")
    )
    current_date = getdate(today())
    due_date = getdate(invoice_doc.due_date) if invoice_doc.get("due_date") else None
    posting_date = getdate(invoice_doc.posting_date) if invoice_doc.get("posting_date") else None
    withholding_date = (
        getdate(invoice_doc.get("outstanding_withholding_date"))
        if invoice_doc.get("outstanding_withholding_date")
        else None
    )

    reference_date = None
    is_eligible = None
    eligibility_note = ""
    next_month_start = None
    if setting_doc.reminder_type == "Withholding Tax":
        reference_date = get_wh_reference_date(invoice_doc, setting_doc.wh_date_basis)
        is_eligible = is_wh_invoice_eligible(invoice_doc, setting_doc, current_date)
        if reference_date:
            next_month_start = frappe.utils.get_first_day(
                frappe.utils.add_months(reference_date, 1)
            )
            eligibility_note = (
                f"Reference ({setting_doc.wh_date_basis})={reference_date}; "
                f"next month starts {next_month_start}; "
                f"eligible today={is_eligible}"
            )
        else:
            eligibility_note = (
                f"No reference date for basis '{setting_doc.wh_date_basis}' "
                f"(withholding_date empty?)"
            )
    elif setting_doc.reminder_type == "After Overdue":
        days_after = resolve_days_after_overdue(setting_doc)
        days_overdue = date_diff(current_date, due_date) if due_date else None
        is_eligible = is_after_overdue_invoice_eligible(
            invoice_doc, setting_doc, current_date
        )
        eligibility_note = (
            f"days_overdue={days_overdue}, grace={days_after}, "
            f"WH={cint(invoice_doc.get('outstanding_withholding'))}, "
            f"outstanding={flt(invoice_doc.outstanding_amount)}, "
            f"penalty_outstanding={flt(invoice_doc.get('outstanding_penalty_amount'))}, "
            f"penalty_paid={cint(invoice_doc.get('penalty_paid'))}"
        )
    else:
        target = frappe.utils.add_days(current_date, setting_doc.days_due or 0)
        is_eligible = bool(due_date and due_date == getdate(target))
        eligibility_note = f"Pre-Due target due_date={target}"

    excluded_map = get_email_setting_excluded_days_map(setting_doc) if setting_doc.reminder_type in (
        "After Overdue",
        "Withholding Tax",
    ) else {}
    is_excluded, exclude_reason = (
        is_excluded_today(excluded_map, current_date) if excluded_map is not None else (False, None)
    )
    schedule_due = (
        is_schedule_due_today(
            setting_doc.frequency,
            weekday=setting_doc.weekday,
            day_of_month=setting_doc.day_of_month,
            target_date=current_date,
        )
        if setting_doc.reminder_type in ("After Overdue", "Withholding Tax")
        else True
    )

    context = invoice_doc.as_dict()
    context.update({
        "doc": invoice_doc,
        "customer_doc": customer_doc,
        "currency": currency,
        "frappe": frappe,
        "nowdate": frappe.utils.nowdate,
        "format_currency": _format_currency,
        "add_days": frappe.utils.add_days,
        "add_months": frappe.utils.add_months,
        "get_first_day": frappe.utils.get_first_day,
        "getdate": getdate,
        "today": current_date,
        "days_overdue": date_diff(current_date, due_date) if due_date else None,
        "posting_date": posting_date,
        "due_date": due_date,
        "withholding_date": withholding_date,
        "reference_date": reference_date,
        "next_month_start": next_month_start,
        "is_eligible": is_eligible,
        "eligibility_note": eligibility_note,
        "is_excluded_today": is_excluded,
        "exclude_reason": exclude_reason or "",
        "schedule_due_today": schedule_due,
        "email_setting_name": setting_doc.name,
        "reminder_type": setting_doc.reminder_type,
        "wh_date_basis": setting_doc.get("wh_date_basis"),
        "wh_condition": setting_doc.get("wh_condition") or "",
        "overdue_condition": setting_doc.get("overdue_condition") or "",
        "outstanding_penalty_amount": flt(invoice_doc.get("outstanding_penalty_amount")),
        "penalty_paid": cint(invoice_doc.get("penalty_paid")),
        "frequency": setting_doc.get("frequency") or "",
        "weekday": setting_doc.get("weekday") or "",
        "day_of_month": setting_doc.get("day_of_month") or "",
        "schedule_label": _format_schedule_label(setting_doc),
        "payment_term_setting": setting_doc.payment_term,
        "is_test": is_test,
        "test_run_at": frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
    })
    return context, currency


def _create_notify_customer_records(setting_doc, invoices, audience, frequency):
    for invoice in invoices:
        customer_emails = _get_primary_contact_emails(invoice.customer)

        if not customer_emails:
            frappe.log_error(
                f"No primary contact email found for customer {invoice.customer}, skipping invoice {invoice.name}",
                "Invoice Email Reminder",
            )
            continue

        invoice_doc = frappe.get_doc("Sales Invoice", invoice.name)
        customer_doc = frappe.get_doc("Customer", invoice.customer)
        context, currency = _build_reminder_template_context(
            setting_doc, invoice_doc, customer_doc
        )

        try:
            rendered_body = frappe.render_template(setting_doc.body, context)
            rendered_subject = frappe.render_template(setting_doc.subject, context)
        except Exception as e:
            frappe.log_error(
                f"Failed to render template for invoice {invoice.name}: {str(e)}",
                "Invoice Email Reminder",
            )
            continue

        for email in customer_emails:
            if _already_notified(
                setting_doc.name,
                invoice.name,
                invoice.customer,
                email,
                audience,
                frequency,
            ):
                continue

            notify_doc = frappe.get_doc({
                "doctype": "Notify Customer",
                "posting_date": today(),
                "posting_time": frappe.utils.now_datetime().strftime("%H:%M:%S"),
                "customer": invoice.customer,
                "customer_email": email,
                "subject": rendered_subject,
                "due_amount": invoice_doc.outstanding_amount,
                "invoice_no": invoice.name,
                "message": rendered_body,
                "currency": currency,
                "email_setting": setting_doc.name,
                "reminder_audience": audience,
                "is_test": 0,
            })
            notify_doc.insert(ignore_permissions=True)

            if setting_doc.print_format:
                try:
                    _attach_pdf(
                        notify_doc,
                        invoice_doc,
                        setting_doc.print_format,
                        setting_doc.letter_head,
                    )
                    notify_doc.save(ignore_permissions=True)
                except Exception as e:
                    frappe.log_error(
                        f"Failed to attach PDF for invoice {invoice.name} to {email}: {str(e)}",
                        "Invoice Email Reminder",
                    )

    frappe.db.commit()


@frappe.whitelist()
def get_invoice_emails_for_reminder_test(sales_invoice):
    frappe.only_for("System Manager")
    if not sales_invoice:
        return []

    customer = frappe.db.get_value("Sales Invoice", sales_invoice, "customer")
    if not customer:
        return []

    return sorted(_get_primary_contact_emails(customer))


@frappe.whitelist()
def test_property_management_email_setting(email_setting, sales_invoice, to_email, bcc=None):
    """System Manager test push: render template, create Notify Customer, send email."""
    frappe.only_for("System Manager")

    if not email_setting or not sales_invoice or not to_email:
        frappe.throw(_("Email Setting, Sales Invoice and To Email are required"))

    to_email = to_email.strip().lower()
    bcc_list = []
    if bcc:
        bcc_list = [e.strip().lower() for e in bcc.replace(";", ",").split(",") if e.strip()]

    setting_doc = frappe.get_doc("Property Management Email Setting", email_setting)
    invoice_doc = frappe.get_doc("Sales Invoice", sales_invoice)
    customer_doc = frappe.get_doc("Customer", invoice_doc.customer)

    audience = "Withholding" if setting_doc.reminder_type == "Withholding Tax" else "Standard"
    context, currency = _build_reminder_template_context(
        setting_doc, invoice_doc, customer_doc, is_test=True
    )

    try:
        rendered_body = frappe.render_template(setting_doc.body or "", context)
        rendered_subject = frappe.render_template(setting_doc.subject or "", context)
    except Exception as e:
        frappe.throw(_("Failed to render template: {0}").format(str(e)))

    if not rendered_subject.startswith("[TEST]"):
        rendered_subject = f"[TEST] {rendered_subject}"

    notify_doc = frappe.get_doc({
        "doctype": "Notify Customer",
        "posting_date": today(),
        "posting_time": frappe.utils.now_datetime().strftime("%H:%M:%S"),
        "customer": invoice_doc.customer,
        "customer_email": to_email,
        "bcc": ", ".join(bcc_list) if bcc_list else None,
        "subject": rendered_subject,
        "due_amount": invoice_doc.outstanding_amount,
        "invoice_no": invoice_doc.name,
        "message": rendered_body,
        "currency": currency,
        "email_setting": setting_doc.name,
        "reminder_audience": audience,
        "is_test": 1,
    })
    notify_doc.insert(ignore_permissions=True)

    if setting_doc.print_format:
        try:
            _attach_pdf(
                notify_doc,
                invoice_doc,
                setting_doc.print_format,
                setting_doc.letter_head,
            )
            notify_doc.save(ignore_permissions=True)
        except Exception as e:
            frappe.log_error(
                f"Test reminder PDF attach failed for {invoice_doc.name}: {e}",
                "Invoice Email Reminder Test",
            )

    send_kwargs = {
        "recipients": [to_email],
        "subject": rendered_subject,
        "message": rendered_body,
        "reference_doctype": "Notify Customer",
        "reference_name": notify_doc.name,
        "now": True,
    }
    if bcc_list:
        send_kwargs["bcc"] = bcc_list

    try:
        frappe.sendmail(**send_kwargs)
        email_sent = 1
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Invoice Email Reminder Test Send")
        email_sent = 0
        frappe.msgprint(
            _("Notify Customer created but email send failed: {0}").format(str(e)),
            indicator="orange",
        )

    frappe.db.commit()

    return {
        "notify_customer": notify_doc.name,
        "email_sent": email_sent,
        "to_email": to_email,
        "bcc": bcc_list,
        "is_eligible": context.get("is_eligible"),
        "reference_date": str(context.get("reference_date") or ""),
        "next_month_start": str(context.get("next_month_start") or ""),
        "subject": rendered_subject,
    }


def _attach_pdf(notify_doc, invoice_doc, print_format, letter_head):
    """Render the Sales Invoice as PDF using the given print format
    and attach it to the Notify Customer doc."""

    # Get the HTML for the print format
    html = frappe.get_print(
        doctype="Sales Invoice",
        name=invoice_doc.name,
        print_format=print_format,
        letterhead=letter_head,
        no_letterhead=0,
        as_pdf=False,  # get HTML first so we can pass it to get_pdf
    )

    # Convert HTML to PDF bytes
    pdf_bytes = get_pdf(html)

    # Save as a File record attached to the Notify Customer doc
    pdf_filename = f"{invoice_doc.name}.pdf"

    _file = frappe.get_doc({
        "doctype": "File",
        "file_name": pdf_filename,
        "attached_to_doctype": notify_doc.doctype,
        "attached_to_name": notify_doc.name,
        "attached_to_field": "attachment",
        "is_private": 0,
        "content": pdf_bytes,
    })
    _file.save(ignore_permissions=True)

    # Update the attachment field on the Notify Customer doc
    notify_doc.attachment = _file.file_url