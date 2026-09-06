# -*- coding: utf-8 -*-
"""Invoices API for Property Management Solution (Mobile App)."""

from __future__ import unicode_literals

import json
import frappe
from frappe import _
from frappe.utils import today, getdate, date_diff, flt, cint
from frappe.utils.pdf import get_pdf
from propms.custom.lease import get_customer_from_lease


def _get_current_user_email():
    """Return the normalized email of the logged-in session user."""
    current = frappe.session.user
    if not current or current == "Guest":
        return None
    return (frappe.db.get_value("User", current, "email") or current).strip().lower()


def _get_tenant_context(user_email=None):
    """
    Resolve all active Lease documents, Properties, and Cost Centers for the tenant user.
    
    Returns:
        tuple: (lease_names, customer_names, lease_to_prop_map, cost_centers, cc_to_prop_map)
    """
    email = user_email or _get_current_user_email()
    if not email:
        return [], [], {}, [], {}

    # Find all active Lease records where this email is in Tenant Details
    tenant_details = frappe.get_all(
        "Tenant Details",
        filters={
            "user_email": email,
            "parenttype": "Lease",
            "enabled": 1,
        },
        fields=["parent"],
    )

    lease_names = list({row["parent"] for row in tenant_details if row.get("parent")})
    
    # Backward compatibility with Tenant parenttype
    if not lease_names:
        tenant_details_legacy = frappe.get_all(
            "Tenant Details",
            filters={
                "user_email": email,
                "parenttype": "Tenant",
                "enabled": 1,
            },
            fields=["parent"],
        )
        for td in tenant_details_legacy:
            cust = frappe.db.get_value("Tenant", td["parent"], "customer")
            if cust:
                return [], [cust], {}, [], {}

    # Build lease -> property map and resolve cost centers strictly for these leases
    lease_to_prop = {}
    cost_centers = []
    cc_to_prop = {}
    customer_set = set()

    if lease_names:
        lease_docs = frappe.get_all(
            "Lease",
            filters={"name": ["in", lease_names]},
            fields=["name", "property", "lease_customer"],
        )
        prop_names = []
        for ld in lease_docs:
            p_name = ld.get("property")
            lease_to_prop[ld["name"]] = p_name or ld["name"]
            if p_name:
                prop_names.append(p_name)
            if ld.get("lease_customer"):
                customer_set.add(ld["lease_customer"])

        if prop_names:
            prop_rows = frappe.get_all(
                "Property",
                filters={"name": ["in", prop_names]},
                fields=["name", "cost_center"],
            )
            for pr in prop_rows:
                cc = pr.get("cost_center")
                if cc:
                    cost_centers.append(cc)
                    cc_to_prop[cc] = pr["name"]

        # Also resolve Customers via Lease Item Service Charge paid_by
        for lease_name in lease_names:
            cust = get_customer_from_lease(lease_name)
            if cust:
                customer_set.add(cust)

    return lease_names, list(customer_set), lease_to_prop, cost_centers, cc_to_prop


def _check_invoice_access(invoice_name, user_email=None):
    """
    Verify that the logged-in tenant owns or has access to the requested invoice.
    An invoice is accessible if it is tied to any of the tenant's active leases
    (via `lease` or `lease_name`) or property cost centers.
    
    Returns:
        frappe.Document: The Sales Invoice doc if authorized.
    Raises:
        frappe.PermissionError if not authorized.
    """
    if not frappe.db.exists("Sales Invoice", invoice_name):
        frappe.throw(_("Invoice {0} not found").format(invoice_name), frappe.DoesNotExistError)

    inv = frappe.get_doc("Sales Invoice", invoice_name)
    user_leases, user_customers, lease_to_prop, cost_centers, cc_to_prop = _get_tenant_context(user_email)

    lease_ref = inv.get("lease") or inv.get("lease_name")

    # Check if invoice is linked to one of user's assigned leases or cost centers
    has_access = False
    if lease_ref and lease_ref in user_leases:
        has_access = True
    elif inv.cost_center and inv.cost_center in cost_centers:
        has_access = True
    elif "System Manager" in frappe.get_roles(frappe.session.user):
        has_access = True

    if not has_access:
        frappe.throw(_("You are not authorized to view invoice {0}").format(invoice_name), frappe.PermissionError)

    return inv


@frappe.whitelist(methods=["GET", "POST"])
def get_tenant_invoices(status="all", lease=None, page=1, page_length=20):
    """
    Fetch all Sales Invoices for the authenticated tenant mobile user.
    
    Invoices are strictly scoped to the Lease(s) and Apartment Properties assigned
    to the tenant user in Tenant Details.
    
    Parameters:
        status (str): "all", "outstanding" / "pending", "overdue", "paid"
        lease (str): Optional lease ID to filter by specific apartment
        page (int): Page number (default: 1)
        page_length (int): Items per page (default: 20)
        
    Returns:
        dict: { status, summary, invoices, pagination }
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    user_email = _get_current_user_email()
    user_leases, user_customers, lease_to_prop, cost_centers, cc_to_prop = _get_tenant_context(user_email)

    if not user_leases and not cost_centers:
        return {
            "status": "success",
            "summary": {
                "total_outstanding": {},
                "total_overdue": {},
                "counts": {"total": 0, "pending": 0, "overdue": 0, "paid": 0},
            },
            "invoices": [],
            "pagination": {
                "page": 1,
                "page_length": page_length,
                "total_records": 0,
                "total_pages": 0,
            },
        }

    # If specific lease requested, ensure user owns it and restrict to that lease
    if lease:
        if lease not in user_leases:
            frappe.throw(_("Access denied for lease {0}").format(lease), frappe.PermissionError)
        query_leases = [lease]
        
        # Resolve cost center for only this lease
        p_name = lease_to_prop.get(lease)
        query_ccs = []
        if p_name:
            cc = frappe.db.get_value("Property", p_name, "cost_center")
            if cc:
                query_ccs.append(cc)
    else:
        query_leases = user_leases
        query_ccs = cost_centers

    # Base WHERE clause: invoice strictly belongs to assigned lease(s) OR apartment cost centers
    where_conditions = ["si.docstatus = 1"]
    or_clauses = []
    base_params = []

    if query_leases:
        placeholders = ', '.join(['%s'] * len(query_leases))
        or_clauses.append(f"(si.lease IN ({placeholders}) OR si.lease_name IN ({placeholders}))")
        base_params.extend(query_leases)
        base_params.extend(query_leases)

    if query_ccs:
        # Invoices (e.g. POS / Maintenance) where cost center matches the leased apartment property
        or_clauses.append(f"(si.cost_center IN ({', '.join(['%s'] * len(query_ccs))}))")
        base_params.extend(query_ccs)

    if not or_clauses:
        return {
            "status": "success",
            "summary": {
                "total_outstanding": {},
                "total_overdue": {},
                "counts": {"total": 0, "pending": 0, "overdue": 0, "paid": 0},
            },
            "invoices": [],
            "pagination": {
                "page": 1,
                "page_length": page_length,
                "total_records": 0,
                "total_pages": 0,
            },
        }

    where_conditions.append(f"({' OR '.join(or_clauses)})")

    # 1. Compute financial summary stats across all tenant invoices
    summary_sql = f"""
        SELECT 
            si.status,
            si.currency,
            si.due_date,
            si.outstanding_amount,
            si.grand_total
        FROM `tabSales Invoice` si
        WHERE {' AND '.join(where_conditions)}
    """
    all_tenant_invoices = frappe.db.sql(summary_sql, base_params, as_dict=True)

    today_date = getdate(today())
    total_outstanding_by_curr = {}
    total_overdue_by_curr = {}
    counts = {"total": len(all_tenant_invoices), "pending": 0, "overdue": 0, "paid": 0}

    for inv in all_tenant_invoices:
        curr = inv.currency or "TZS"
        out_amt = flt(inv.outstanding_amount)
        due_d = getdate(inv.due_date) if inv.due_date else None
        is_ovd = out_amt > 0 and due_d and due_d < today_date
        is_paid = inv.status == "Paid" or out_amt <= 0

        if is_paid:
            counts["paid"] += 1
        else:
            counts["pending"] += 1
            total_outstanding_by_curr[curr] = flt(total_outstanding_by_curr.get(curr, 0) + out_amt, 2)
            if is_ovd:
                counts["overdue"] += 1
                total_overdue_by_curr[curr] = flt(total_overdue_by_curr.get(curr, 0) + out_amt, 2)

    # 2. Apply status filter for the paginated invoice list
    filter_conditions = list(where_conditions)
    filter_params = list(base_params)

    status_lower = (status or "all").strip().lower()
    if status_lower in ("outstanding", "pending", "unpaid"):
        filter_conditions.append("si.outstanding_amount > 0 AND si.status != 'Paid'")
    elif status_lower == "overdue":
        filter_conditions.append("si.outstanding_amount > 0 AND si.status != 'Paid' AND si.due_date < %s")
        filter_params.append(today_date)
    elif status_lower == "paid":
        filter_conditions.append("(si.status = 'Paid' OR si.outstanding_amount <= 0)")

    # Count filtered records
    count_sql = f"SELECT COUNT(*) FROM `tabSales Invoice` si WHERE {' AND '.join(filter_conditions)}"
    total_records = frappe.db.sql(count_sql, filter_params)[0][0]

    # Pagination params
    page = max(1, cint(page))
    page_length = max(1, min(100, cint(page_length)))
    limit_start = (page - 1) * page_length
    total_pages = (total_records + page_length - 1) // page_length if total_records > 0 else 0

    # 3. Query paginated invoices
    invoices_sql = f"""
        SELECT 
            si.name,
            si.customer,
            COALESCE(NULLIF(si.lease, ''), si.lease_name) AS lease,
            si.cost_center,
            l.property AS lease_property,
            si.posting_date,
            si.due_date,
            si.grand_total,
            si.outstanding_amount,
            si.currency,
            si.status,
            si.is_pos
        FROM `tabSales Invoice` si
        LEFT JOIN `tabLease` l ON l.name = COALESCE(NULLIF(si.lease, ''), si.lease_name)
        WHERE {' AND '.join(filter_conditions)}
        ORDER BY si.posting_date DESC, si.name DESC
        LIMIT %s OFFSET %s
    """
    query_params = filter_params + [page_length, limit_start]
    rows = frappe.db.sql(invoices_sql, query_params, as_dict=True)

    # Fetch line items summary in batch for performance
    invoice_names = [r["name"] for r in rows]
    items_by_invoice = {}
    if invoice_names:
        items_rows = frappe.db.sql(
            f"""
            SELECT parent, item_name, description
            FROM `tabSales Invoice Item`
            WHERE parent IN ({', '.join(['%s'] * len(invoice_names))})
            ORDER BY idx ASC
            """,
            invoice_names,
            as_dict=True,
        )
        for it in items_rows:
            items_by_invoice.setdefault(it["parent"], []).append(it["item_name"] or it["description"] or "")

    invoices_list = []
    for r in rows:
        due_d = getdate(r["due_date"]) if r.get("due_date") else None
        out_amt = flt(r.get("outstanding_amount"))
        is_ovd = bool(out_amt > 0 and due_d and due_d < today_date)
        days_ovd = date_diff(today_date, due_d) if is_ovd else 0

        # Normalise status display
        display_status = r["status"]
        if is_ovd and r["status"] != "Paid":
            display_status = "Overdue"
        elif out_amt <= 0:
            display_status = "Paid"

        # Resolve apartment/unit name from lease, or from cost center if lease is empty
        apartment_name = (
            r.get("lease_property")
            or lease_to_prop.get(r.get("lease"))
            or cc_to_prop.get(r.get("cost_center"))
            or r.get("lease")
            or (r.get("cost_center").replace(" - VPL", "") if r.get("cost_center") else None)
            or "Unit"
        )

        item_names = items_by_invoice.get(r["name"], [])
        summary_str = ", ".join(item_names[:3]) + ("..." if len(item_names) > 3 else "")

        invoices_list.append({
            "name": r["name"],
            "lease": r.get("lease"),
            "apartment_name": apartment_name,
            "customer": r["customer"],
            "cost_center": r.get("cost_center"),
            "posting_date": str(r.get("posting_date") or ""),
            "due_date": str(r.get("due_date") or ""),
            "grand_total": flt(r.get("grand_total"), 2),
            "outstanding_amount": flt(out_amt, 2),
            "currency": r.get("currency") or "TZS",
            "status": display_status,
            "is_pos": bool(r.get("is_pos")),
            "is_overdue": is_ovd,
            "days_overdue": days_ovd,
            "items_summary": summary_str or "Rent / Service Charge",
            "pdf_url": f"/api/method/propms.api.v1.invoices.invoices.download_invoice_pdf?invoice_name={r['name']}",
        })

    return {
        "status": "success",
        "summary": {
            "total_outstanding": total_outstanding_by_curr,
            "total_overdue": total_overdue_by_curr,
            "counts": counts,
        },
        "invoices": invoices_list,
        "pagination": {
            "page": page,
            "page_length": page_length,
            "total_records": total_records,
            "total_pages": total_pages,
        },
    }


@frappe.whitelist(methods=["GET", "POST"])
def get_invoice_details(invoice_name=None):
    """
    Fetch comprehensive invoice details including line items, taxes, and payment history.
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    if not invoice_name:
        frappe.throw(_("invoice_name is required"))

    inv = _check_invoice_access(invoice_name)

    today_date = getdate(today())
    due_d = getdate(inv.due_date) if inv.due_date else None
    out_amt = flt(inv.outstanding_amount)
    is_ovd = bool(out_amt > 0 and due_d and due_d < today_date)
    days_ovd = date_diff(today_date, due_d) if is_ovd else 0

    # 1. Line items
    items = []
    for it in inv.items:
        items.append({
            "item_code": it.item_code,
            "item_name": it.item_name,
            "description": it.description,
            "qty": flt(it.qty),
            "rate": flt(it.rate, 2),
            "amount": flt(it.amount, 2),
            "service_start_date": str(getattr(it, "service_start_date", None) or ""),
            "service_end_date": str(getattr(it, "service_end_date", None) or ""),
        })

    # 2. Taxes and charges
    taxes = []
    for tx in inv.taxes:
        taxes.append({
            "charge_type": tx.charge_type,
            "account_head": tx.account_head,
            "description": tx.description,
            "rate": flt(tx.rate),
            "tax_amount": flt(tx.tax_amount, 2),
        })

    # 3. Payment entries & payment schedule
    payments_history = []
    pe_references = frappe.db.sql(
        """
        SELECT 
            pe.name,
            pe.posting_date,
            pe.mode_of_payment,
            pe.reference_no,
            per.allocated_amount
        FROM `tabPayment Entry Reference` per
        INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
        WHERE per.reference_doctype = 'Sales Invoice' AND per.reference_name = %s AND pe.docstatus = 1
        ORDER BY pe.posting_date DESC
        """,
        invoice_name,
        as_dict=True,
    )
    for p in pe_references:
        payments_history.append({
            "payment_entry": p["name"],
            "posting_date": str(p["posting_date"]),
            "mode_of_payment": p.get("mode_of_payment") or "Direct Payment",
            "reference_no": p.get("reference_no") or "",
            "allocated_amount": flt(p.get("allocated_amount"), 2),
        })

    # POS payments if any
    for pay in getattr(inv, "payments", []) or []:
        if flt(pay.amount) > 0:
            payments_history.append({
                "payment_entry": "POS Payment",
                "posting_date": str(inv.posting_date),
                "mode_of_payment": pay.mode_of_payment,
                "reference_no": "",
                "allocated_amount": flt(pay.amount, 2),
            })

    lease_ref = inv.get("lease") or inv.get("lease_name")
    property_name = frappe.db.get_value("Lease", lease_ref, "property") if lease_ref else None

    return {
        "status": "success",
        "invoice": {
            "name": inv.name,
            "customer": inv.customer,
            "customer_name": inv.customer_name or inv.customer,
            "lease": lease_ref,
            "apartment_name": property_name or lease_ref or (inv.cost_center.replace(" - VPL", "") if inv.cost_center else "Unit"),
            "cost_center": inv.cost_center,
            "posting_date": str(inv.posting_date),
            "due_date": str(inv.due_date or ""),
            "currency": inv.currency or "TZS",
            "net_total": flt(inv.net_total, 2),
            "total_taxes_and_charges": flt(inv.total_taxes_and_charges, 2),
            "grand_total": flt(inv.grand_total, 2),
            "outstanding_amount": flt(out_amt, 2),
            "status": "Overdue" if is_ovd else ("Paid" if out_amt <= 0 else inv.status),
            "is_overdue": is_ovd,
            "days_overdue": days_ovd,
            "from_date": str(getattr(inv, "from_date", None) or ""),
            "to_date": str(getattr(inv, "to_date", None) or ""),
            "items": items,
            "taxes": taxes,
            "payments_history": payments_history,
            "pdf_url": f"/api/method/propms.api.v1.invoices.invoices.download_invoice_pdf?invoice_name={inv.name}",
        },
    }


@frappe.whitelist(methods=["GET"])
def download_invoice_pdf(invoice_name=None):
    """
    Stream the official PDF representation of the Sales Invoice to the mobile app.
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    if not invoice_name:
        frappe.throw(_("invoice_name is required"))

    # Permission check (verifies this tenant user owns this invoice)
    _check_invoice_access(invoice_name)

    # Determine print format
    print_format = "Property Tax Invoice" if frappe.db.exists("Print Format", "Property Tax Invoice") else None

    current_user = frappe.session.user
    try:
        # Render print format in system context to allow website tenant users to download their PDF
        frappe.set_user("Administrator")
        try:
            html = frappe.get_print(
                doctype="Sales Invoice",
                name=invoice_name,
                print_format=print_format,
                no_letterhead=0,
                as_pdf=False,
            )
            pdf_bytes = get_pdf(html)
        except Exception:
            html = frappe.get_print(
                doctype="Sales Invoice",
                name=invoice_name,
                no_letterhead=0,
                as_pdf=False,
            )
            pdf_bytes = get_pdf(html)
    finally:
        frappe.set_user(current_user)

    frappe.response.filename = f"{invoice_name}.pdf"
    frappe.response.filecontent = pdf_bytes
    frappe.response.type = "pdf"


########################################################################################################################
# Real-Time WebSockets & FCM Push Integration
########################################################################################################################

def publish_invoice_realtime(invoice_doc, event_type="invoice_update"):
    """
    Emit real-time WebSocket events for invoice creation or status update to tenant user rooms.
    """
    try:
        if not invoice_doc:
            return

        lease_ref = getattr(invoice_doc, "lease", None) or getattr(invoice_doc, "lease_name", None)

        tenant_emails = set()
        if lease_ref:
            emails = frappe.get_all(
                "Tenant Details",
                filters={"parent": lease_ref, "parenttype": "Lease", "enabled": 1},
                pluck="user_email",
            )
            for em in emails:
                if em:
                    tenant_emails.add(em.strip().lower())

        today_date = getdate(today())
        due_d = getdate(invoice_doc.due_date) if invoice_doc.due_date else None
        out_amt = flt(invoice_doc.outstanding_amount)
        is_ovd = bool(out_amt > 0 and due_d and due_d < today_date)

        property_name = (
            frappe.db.get_value("Lease", lease_ref, "property")
            if lease_ref
            else invoice_doc.cost_center
        )

        payload = {
            "event": event_type,
            "invoice_name": invoice_doc.name,
            "lease": lease_ref,
            "apartment_name": property_name or invoice_doc.name,
            "customer": invoice_doc.customer,
            "status": "Paid" if out_amt <= 0 else ("Overdue" if is_ovd else invoice_doc.status),
            "grand_total": flt(invoice_doc.grand_total, 2),
            "outstanding_amount": flt(out_amt, 2),
            "currency": invoice_doc.currency or "TZS",
            "posting_date": str(invoice_doc.posting_date),
            "due_date": str(invoice_doc.due_date or ""),
            "timestamp": str(frappe.utils.now()),
        }

        rooms = {f"doc:Sales Invoice/{invoice_doc.name}"}
        for email in tenant_emails:
            rooms.add(f"user:{email}")

        for room in rooms:
            frappe.publish_realtime(
                event=event_type,
                message=payload,
                room=room,
                after_commit=True,
            )
    except Exception as e:
        frappe.logger().error(f"Failed to publish invoice realtime event: {str(e)}")


def enqueue_invoice_push(user_email, invoice_name, title, body, notification_type="invoice_new"):
    """
    Send FCM push notification for invoices via background worker.
    """
    try:
        tokens = frappe.get_all(
            "User Device",
            filters={"user": user_email},
            pluck="token",
        )
        if not tokens:
            return

        data = {
            "type": notification_type,
            "invoice_name": invoice_name,
            "user": user_email,
        }

        from propms.api.v1.utils.fcm import send_to_tokens
        send_to_tokens(tokens=tokens, data=data, title=title, body=body)
    except Exception as e:
        frappe.logger().error(f"FCM Invoice Push Error for user {user_email}, invoice {invoice_name}: {str(e)}")


def on_sales_invoice_submit(doc, method=None):
    """Document hook on Sales Invoice submission."""
    if doc.docstatus != 1:
        return

    # Realtime WebSocket
    publish_invoice_realtime(doc, event_type="invoice_created")

    lease_ref = getattr(doc, "lease", None) or getattr(doc, "lease_name", None)
    if not lease_ref:
        return

    # FCM Push to tenant users
    tenant_emails = frappe.get_all(
        "Tenant Details",
        filters={"parent": lease_ref, "parenttype": "Lease", "enabled": 1},
        pluck="user_email",
    )

    apartment = frappe.db.get_value("Lease", lease_ref, "property") or lease_ref
    title = "New Invoice Issued"
    body = f"Invoice {doc.name} for {doc.currency or 'TZS'} {flt(doc.grand_total, 2):,.2f} has been generated for {apartment}."

    for email in tenant_emails:
        if email:
            frappe.enqueue(
                "propms.api.v1.invoices.invoices.enqueue_invoice_push",
                user_email=email.strip().lower(),
                invoice_name=doc.name,
                title=title,
                body=body,
                notification_type="invoice_new",
                queue="short",
            )


def on_payment_entry_submit(doc, method=None):
    """Document hook on Payment Entry submission to notify paid invoices."""
    if doc.docstatus != 1:
        return

    for ref in getattr(doc, "references", []) or []:
        if ref.reference_doctype == "Sales Invoice" and ref.reference_name:
            inv = frappe.get_doc("Sales Invoice", ref.reference_name)

            # Realtime WebSocket
            publish_invoice_realtime(inv, event_type="invoice_paid")

            lease_ref = getattr(inv, "lease", None) or getattr(inv, "lease_name", None)
            if not lease_ref:
                continue

            # FCM Push
            tenant_emails = frappe.get_all(
                "Tenant Details",
                filters={"parent": lease_ref, "parenttype": "Lease", "enabled": 1},
                pluck="user_email",
            )
            title = "Payment Confirmation"
            body = f"Payment of {doc.paid_to_account_currency or 'TZS'} {flt(ref.allocated_amount, 2):,.2f} received for Invoice {inv.name}."

            for email in tenant_emails:
                if email:
                    frappe.enqueue(
                        "propms.api.v1.invoices.invoices.enqueue_invoice_push",
                        user_email=email.strip().lower(),
                        invoice_name=inv.name,
                        title=title,
                        body=body,
                        notification_type="invoice_paid",
                        queue="short",
                    )


########################################################################################################################
# Selcom Payment Integration Endpoints
########################################################################################################################

@frappe.whitelist(methods=["POST"])
def initiate_invoice_payment(invoice_name=None, amount=None, phone_number=None, payment_channel="SELCOM_PUSH"):
    """
    Prepare and initiate payment for an outstanding invoice with Selcom.
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    if not invoice_name:
        frappe.throw(_("invoice_name is required"))

    inv = _check_invoice_access(invoice_name)

    outstanding = flt(inv.outstanding_amount)
    if outstanding <= 0:
        frappe.throw(_("Invoice {0} is already fully paid.").format(invoice_name))

    pay_amount = flt(amount) if amount else outstanding
    if pay_amount <= 0 or pay_amount > outstanding:
        frappe.throw(_("Payment amount must be between 1 and {0}").format(outstanding))

    import uuid
    transaction_ref = f"SEL-{inv.name}-{uuid.uuid4().hex[:8].upper()}"

    return {
        "status": "success",
        "message": "Payment initiation prepared successfully",
        "transaction_ref": transaction_ref,
        "invoice_name": inv.name,
        "customer": inv.customer,
        "amount": pay_amount,
        "currency": inv.currency or "TZS",
        "phone_number": phone_number,
        "payment_channel": payment_channel,
    }


@frappe.whitelist(allow_guest=True, methods=["POST"])
def selcom_payment_webhook():
    """
    Webhook receiver for Selcom payment confirmations.
    Automatically creates and submits an ERPNext Payment Entry against the Sales Invoice.
    """
    try:
        data = frappe.request.get_json() if hasattr(frappe, "request") and frappe.request else None
        if not data:
            data = frappe.form_dict

        order_id = data.get("order_id") or data.get("transid") or data.get("reference")
        payment_status = (data.get("payment_status") or data.get("result") or "").upper()
        amount = flt(data.get("amount"))
        invoice_name = data.get("invoice_name")

        if not invoice_name and order_id and "SEL-" in str(order_id):
            parts = order_id.split("-")
            if len(parts) >= 3:
                invoice_name = "-".join(parts[1:-1])

        if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
            return {"status": "error", "message": "Invoice not found from payment payload"}

        if payment_status not in ("SUCCESS", "COMPLETED", "000"):
            return {"status": "ignored", "message": f"Payment status {payment_status} not successful"}

        inv = frappe.get_doc("Sales Invoice", invoice_name)
        if flt(inv.outstanding_amount) <= 0:
            return {"status": "success", "message": "Invoice already paid"}

        pay_amount = min(amount if amount > 0 else flt(inv.outstanding_amount), flt(inv.outstanding_amount))

        # Create Payment Entry
        pe = frappe.new_doc("Payment Entry")
        pe.payment_type = "Receive"
        pe.party_type = "Customer"
        pe.party = inv.customer
        pe.company = inv.company
        pe.paid_amount = pay_amount
        pe.received_amount = pay_amount
        pe.paid_to_account_currency = inv.currency or "TZS"
        pe.reference_no = str(order_id)
        pe.reference_date = today()
        pe.mode_of_payment = "Selcom" if frappe.db.exists("Mode of Payment", "Selcom") else "Bank Draft"

        pe.append("references", {
            "reference_doctype": "Sales Invoice",
            "reference_name": inv.name,
            "total_amount": inv.grand_total,
            "outstanding_amount": inv.outstanding_amount,
            "allocated_amount": pay_amount,
        })

        pe.insert(ignore_permissions=True)
        pe.submit()

        return {
            "status": "success",
            "message": "Payment Entry created and invoice reconciled",
            "payment_entry": pe.name,
            "invoice": inv.name,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Selcom Webhook Error")
        return {"status": "error", "message": str(e)}
