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
    An invoice is accessible if it belongs to the tenant's customer AND is tied
    to any of the tenant's active leases (or has no lease and matches tenant cost center).
    
    Returns:
        frappe.Document: The Sales Invoice doc if authorized.
    Raises:
        frappe.PermissionError if not authorized.
    """
    if not frappe.db.exists("Sales Invoice", invoice_name):
        frappe.throw(_("Invoice {0} not found").format(invoice_name), frappe.DoesNotExistError)

    inv = frappe.get_doc("Sales Invoice", invoice_name)
    if "System Manager" in frappe.get_roles(frappe.session.user):
        return inv

    user_leases, user_customers, lease_to_prop, cost_centers, cc_to_prop = _get_tenant_context(user_email)

    if not user_customers and not user_leases:
        frappe.throw(_("You are not authorized to view invoice {0}").format(invoice_name), frappe.PermissionError)

    # Must belong to tenant's customer
    if user_customers and inv.customer not in user_customers:
        frappe.throw(_("You are not authorized to view invoice {0}").format(invoice_name), frappe.PermissionError)

    lease_ref = inv.get("lease") or inv.get("lease_name")
    if lease_ref and user_leases and lease_ref not in user_leases:
        frappe.throw(_("You are not authorized to view invoice {0}").format(invoice_name), frappe.PermissionError)

    return inv


@frappe.whitelist(methods=["GET", "POST"])
def get_tenant_invoices(status="all", lease=None, page=1, page_length=20):
    """
    Fetch all Sales Invoices for the authenticated tenant mobile user.
    
    Invoices are strictly scoped to the tenant's customer, Lease(s), and Apartment Properties.
    
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

    if not user_leases and not user_customers:
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

    where_conditions = ["si.docstatus = 1"]
    base_params = []

    # 1. Scope strictly to tenant's customer
    if user_customers:
        cust_placeholders = ', '.join(['%s'] * len(user_customers))
        where_conditions.append(f"si.customer IN ({cust_placeholders})")
        base_params.extend(user_customers)

    # 2. Scope by lease / apartment cost center
    if lease:
        if lease not in user_leases:
            frappe.throw(_("Access denied for lease {0}").format(lease), frappe.PermissionError)
        where_conditions.append("(si.lease = %s OR si.lease_name = %s)")
        base_params.extend([lease, lease])
    elif user_leases:
        lease_placeholders = ', '.join(['%s'] * len(user_leases))
        lease_clause = f"(si.lease IN ({lease_placeholders}) OR si.lease_name IN ({lease_placeholders}) OR COALESCE(NULLIF(si.lease, ''), si.lease_name) IS NULL"

        base_params.extend(user_leases)
        base_params.extend(user_leases)

        if cost_centers:
            cc_placeholders = ', '.join(['%s'] * len(cost_centers))
            lease_clause += f" OR si.cost_center IN ({cc_placeholders}) OR si.cost_center IS NULL"
            base_params.extend(cost_centers)

        lease_clause += ")"
        where_conditions.append(lease_clause)

    # 1. Compute financial summary stats across all tenant invoices
    summary_sql = f"""
        SELECT 
            si.status,
            si.currency,
            si.due_date,
            si.outstanding_amount,
            si.grand_total,
            IFNULL(si.outstanding_penalty_amount, 0) AS outstanding_penalty_amount,
            IFNULL(si.total_penalty_amount, 0) AS total_penalty_amount,
            IFNULL(si.penalty_paid, 0) AS penalty_paid,
            IFNULL(si.outstanding_withholding, 0) AS outstanding_withholding,
            si.penalty_invoice
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
        filter_conditions.append(
            "si.outstanding_amount > 0 AND si.status != 'Paid' AND IFNULL(si.outstanding_withholding, 0) = 0"
        )
    elif status_lower == "overdue":
        filter_conditions.append(
            "si.outstanding_amount > 0 AND si.status != 'Paid' AND IFNULL(si.outstanding_withholding, 0) = 0 AND si.due_date < %s"
        )
        filter_params.append(today_date)
    elif status_lower in ("wh", "withholding", "w/h"):
        filter_conditions.append("si.outstanding_amount > 0 AND IFNULL(si.outstanding_withholding, 0) = 1")
    elif status_lower in ("paid_penalty_pending", "penalty_pending"):
        # Principal cleared; unpaid penalty SI or accrued not yet waived/billed
        filter_conditions.append(
            """
            si.outstanding_amount <= 0
            AND (
                (
                    IFNULL(si.penalty_invoice, '') != ''
                    AND EXISTS (
                        SELECT 1 FROM `tabSales Invoice` psi
                        WHERE psi.name = si.penalty_invoice
                          AND psi.docstatus = 1
                          AND psi.outstanding_amount > 0
                    )
                )
                OR (
                    IFNULL(si.penalty_invoice, '') = ''
                    AND IFNULL(si.penalty_paid, 0) = 0
                    AND (
                        IFNULL(si.outstanding_penalty_amount, 0) > 0
                        OR IFNULL(si.total_penalty_amount, 0) > 0
                    )
                )
            )
            """
        )
    elif status_lower == "paid":
        # Fully settled: principal clear and no open penalty left
        filter_conditions.append(
            """
            si.outstanding_amount <= 0
            AND NOT (
                IFNULL(si.penalty_invoice, '') != ''
                AND EXISTS (
                    SELECT 1 FROM `tabSales Invoice` psi
                    WHERE psi.name = si.penalty_invoice
                      AND psi.docstatus = 1
                      AND psi.outstanding_amount > 0
                )
            )
            AND NOT (
                IFNULL(si.penalty_invoice, '') = ''
                AND IFNULL(si.penalty_paid, 0) = 0
                AND (
                    IFNULL(si.outstanding_penalty_amount, 0) > 0
                    OR IFNULL(si.total_penalty_amount, 0) > 0
                )
            )
            """
        )

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
            IFNULL(si.outstanding_penalty_amount, 0) AS outstanding_penalty_amount,
            IFNULL(si.total_penalty_amount, 0) AS total_penalty_amount,
            IFNULL(si.penalty_paid, 0) AS penalty_paid,
            IFNULL(si.outstanding_withholding, 0) AS outstanding_withholding,
            si.outstanding_withholding_date,
            si.penalty_invoice,
            si.currency,
            si.status,
            si.is_pos,
            si.docstatus,
            si.is_return
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

    from propms.api.v1.payments.invoice_amount import (
        compute_mobile_invoice_status,
        payment_limits,
    )

    invoices_list = []
    for r in rows:
        due_d = getdate(r["due_date"]) if r.get("due_date") else None
        out_amt = flt(r.get("outstanding_amount"))
        is_ovd = bool(out_amt > 0 and due_d and due_d < today_date)
        days_ovd = date_diff(today_date, due_d) if is_ovd else 0

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

        limits = payment_limits(r["name"], outstanding=out_amt)
        mobile = compute_mobile_invoice_status(
            principal_outstanding=limits.get("principal_outstanding"),
            penalty_amount_due=limits.get("penalty_amount_due"),
            grand_total=r.get("grand_total"),
            due_date=r.get("due_date"),
            outstanding_withholding=r.get("outstanding_withholding"),
            docstatus=r.get("docstatus"),
            is_return=r.get("is_return"),
            today_date=today_date,
        )
        # WH is not "overdue" for Flutter chips even if past due
        is_wh = cint(r.get("outstanding_withholding")) and out_amt > 0
        display_overdue = bool(is_ovd and not is_wh)

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
            "outstanding_penalty_amount": flt(r.get("outstanding_penalty_amount"), 2),
            "total_penalty_amount": flt(r.get("total_penalty_amount"), 2),
            "penalty_amount_due": limits.get("penalty_amount_due"),
            "penalty_paid": cint(limits.get("penalty_paid")),
            "penalty_invoice": r.get("penalty_invoice") or None,
            "outstanding_withholding": cint(r.get("outstanding_withholding")),
            "outstanding_withholding_date": str(r.get("outstanding_withholding_date") or ""),
            "principal_outstanding": limits.get("principal_outstanding"),
            "total_amount_due": limits.get("total_amount_due"),
            "allow_partial_payment": limits["allow_partial_payment"],
            "allow_payment": limits.get("allow_payment", 1),
            "min_payment_amount": limits["min_payment_amount"],
            "max_payment_amount": limits["max_payment_amount"],
            "available_to_pay": limits["available_to_pay"],
            "open_payment_reserved": limits["open_payment_reserved"],
            "currency": r.get("currency") or "TZS",
            "mobile_status": mobile["mobile_status"],
            "mobile_status_label": mobile["mobile_status_label"],
            "status": mobile["mobile_status_label"],
            "erp_status": r.get("status"),
            "is_pos": bool(r.get("is_pos")),
            "is_overdue": display_overdue,
            "days_overdue": days_ovd if display_overdue else 0,
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
        pe_name = p["name"]
        payments_history.append({
            "payment_entry": pe_name,
            "posting_date": str(p["posting_date"]),
            "mode_of_payment": p.get("mode_of_payment") or "Direct Payment",
            "reference_no": p.get("reference_no") or "",
            "allocated_amount": flt(p.get("allocated_amount"), 2),
            "receipt_pdf_url": (
                f"/api/method/propms.api.v1.invoices.invoices.download_receipt_pdf"
                f"?payment_entry_name={pe_name}&invoice_name={invoice_name}"
            ),
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
                "receipt_pdf_url": None,
            })

    lease_ref = inv.get("lease") or inv.get("lease_name")
    property_name = frappe.db.get_value("Lease", lease_ref, "property") if lease_ref else None

    from propms.api.v1.payments.invoice_amount import (
        compute_mobile_invoice_status,
        payment_limits,
    )

    limits = payment_limits(inv.name, outstanding=out_amt)
    mobile = compute_mobile_invoice_status(
        principal_outstanding=limits.get("principal_outstanding"),
        penalty_amount_due=limits.get("penalty_amount_due"),
        grand_total=inv.grand_total,
        due_date=inv.due_date,
        outstanding_withholding=getattr(inv, "outstanding_withholding", 0),
        docstatus=inv.docstatus,
        is_return=getattr(inv, "is_return", 0),
        today_date=today_date,
    )
    is_wh = cint(getattr(inv, "outstanding_withholding", 0)) and out_amt > 0
    display_overdue = bool(is_ovd and not is_wh)

    penalty_rows = []
    for row in getattr(inv, "sales_invoice_penalty_details", None) or []:
        penalty_rows.append(
            {
                "date": str(row.date or ""),
                "outstanding_amount": flt(row.outstanding_amount, 2),
                "amount": flt(row.amount, 2),
                "percent": flt(row.percent, 2),
                "currency": row.currency or inv.currency or "TZS",
            }
        )

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
            "outstanding_penalty_amount": flt(getattr(inv, "outstanding_penalty_amount", 0), 2),
            "total_penalty_amount": flt(getattr(inv, "total_penalty_amount", 0), 2),
            "penalty_amount_due": limits.get("penalty_amount_due"),
            "penalty_paid": cint(limits.get("penalty_paid")),
            "penalty_invoice": getattr(inv, "penalty_invoice", None) or None,
            "outstanding_withholding": cint(getattr(inv, "outstanding_withholding", 0)),
            "outstanding_withholding_date": str(getattr(inv, "outstanding_withholding_date", None) or ""),
            "penalty_details": penalty_rows,
            "principal_outstanding": limits.get("principal_outstanding"),
            "total_amount_due": limits.get("total_amount_due"),
            "allow_partial_payment": limits["allow_partial_payment"],
            "allow_payment": limits.get("allow_payment", 1),
            "min_payment_amount": limits["min_payment_amount"],
            "max_payment_amount": limits["max_payment_amount"],
            "available_to_pay": limits["available_to_pay"],
            "open_payment_reserved": limits["open_payment_reserved"],
            "currency_precision": limits["currency_precision"],
            "mobile_status": mobile["mobile_status"],
            "mobile_status_label": mobile["mobile_status_label"],
            "status": mobile["mobile_status_label"],
            "erp_status": inv.status,
            "is_overdue": display_overdue,
            "days_overdue": days_ovd if display_overdue else 0,
            "from_date": str(getattr(inv, "from_date", None) or ""),
            "to_date": str(getattr(inv, "to_date", None) or ""),
            "items": items,
            "taxes": taxes,
            "payments_history": payments_history,
            "pdf_url": f"/api/method/propms.api.v1.invoices.invoices.download_invoice_pdf?invoice_name={inv.name}",
        },
    }


def _payment_entry_print_format():
    """Prefer VIVA PE voucher, then AV voucher, else ERPNext default."""
    for name in ("AV Payment Entry Voucher VIVA", "AV Payment Entry Voucher"):
        if frappe.db.exists("Print Format", name):
            return name
    return None


def _resolve_receipt_payment_entry(invoice_name=None, payment_entry_name=None):
    """Resolve a submitted Payment Entry the tenant may download as a receipt.

    Returns ``(payment_entry_name, linked_invoice_name)``.
    """
    pe_name = (payment_entry_name or "").strip()
    inv_name = (invoice_name or "").strip()

    if pe_name:
        if pe_name == "POS Payment":
            frappe.throw(_("POS payments do not have a separate receipt PDF"))
        if not frappe.db.exists("Payment Entry", pe_name):
            frappe.throw(_("Payment Entry {0} not found").format(pe_name), frappe.DoesNotExistError)
        pe = frappe.get_doc("Payment Entry", pe_name)
        if cint(pe.docstatus) != 1:
            frappe.throw(_("Payment Entry {0} is not submitted").format(pe_name))

        linked_invoices = [
            r.reference_name
            for r in (pe.references or [])
            if r.reference_doctype == "Sales Invoice" and r.reference_name
        ]
        if inv_name:
            if inv_name not in linked_invoices:
                frappe.throw(
                    _("Payment Entry {0} is not linked to invoice {1}").format(pe_name, inv_name)
                )
            _check_invoice_access(inv_name)
            return pe_name, inv_name

        if not linked_invoices:
            frappe.throw(_("Payment Entry {0} is not linked to a Sales Invoice").format(pe_name))

        # Authorize via any linked invoice the tenant owns
        authorized_inv = None
        last_err = None
        for candidate in linked_invoices:
            try:
                _check_invoice_access(candidate)
                authorized_inv = candidate
                break
            except frappe.PermissionError as exc:
                last_err = exc
        if not authorized_inv:
            if last_err:
                raise last_err
            frappe.throw(
                _("You are not authorized to download receipt {0}").format(pe_name),
                frappe.PermissionError,
            )
        return pe_name, authorized_inv

    if not inv_name:
        frappe.throw(_("payment_entry_name or invoice_name is required"))

    _check_invoice_access(inv_name)
    latest = frappe.db.sql(
        """
        SELECT pe.name
        FROM `tabPayment Entry Reference` per
        INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
        WHERE per.reference_doctype = 'Sales Invoice'
          AND per.reference_name = %s
          AND pe.docstatus = 1
        ORDER BY pe.posting_date DESC, pe.creation DESC
        LIMIT 1
        """,
        (inv_name,),
    )
    if not latest:
        frappe.throw(_("No payment receipt found for invoice {0}").format(inv_name))
    return latest[0][0], inv_name


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


@frappe.whitelist(methods=["GET", "POST"])
def download_receipt_pdf(invoice_name=None, payment_entry_name=None):
    """Stream a Payment Entry (receipt) PDF for the mobile app.

    Prefer ``payment_entry_name`` (from ``payments_history``). If only
    ``invoice_name`` is given, the latest submitted PE against that invoice is used.
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    req = getattr(frappe, "form_dict", None) or {}
    invoice_name = invoice_name or req.get("invoice_name")
    payment_entry_name = payment_entry_name or req.get("payment_entry_name") or req.get(
        "payment_entry"
    )

    pe_name, _inv = _resolve_receipt_payment_entry(
        invoice_name=invoice_name, payment_entry_name=payment_entry_name
    )

    print_format = _payment_entry_print_format()
    current_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        try:
            html = frappe.get_print(
                doctype="Payment Entry",
                name=pe_name,
                print_format=print_format,
                no_letterhead=0,
                as_pdf=False,
            )
            pdf_bytes = get_pdf(html)
        except Exception:
            html = frappe.get_print(
                doctype="Payment Entry",
                name=pe_name,
                no_letterhead=0,
                as_pdf=False,
            )
            pdf_bytes = get_pdf(html)
    finally:
        frappe.set_user(current_user)

    frappe.response.filename = f"{pe_name}.pdf"
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


def is_penalty_reimbursement_invoice(doc):
    """True for late-payment penalty Sales Invoices (not the source rent/service bill).

    Used to skip "please pay" pushes/emails when the penalty SI is created during
    settlement of a payment that already covers it (or is only a child of the source).
    """
    if not doc:
        return False
    remarks = str(doc.get("remarks") or "")
    if "Late payment penalty for" in remarks:
        return True
    name = doc.get("name")
    if name and frappe.db.exists("Sales Invoice", {"penalty_invoice": name}):
        return True
    try:
        penalty_item = frappe.db.get_single_value(
            "Sales Invoice Penalty Settings", "penalty_item"
        )
    except Exception:
        penalty_item = None
    if penalty_item:
        for row in doc.get("items") or []:
            if (row.get("item_code") if hasattr(row, "get") else getattr(row, "item_code", None)) == penalty_item:
                return True
    return False


def on_sales_invoice_submit(doc, method=None):
    """Document hook on Sales Invoice submission."""
    if doc.docstatus != 1:
        return

    # Skip paid POS / mobile checkout receipts (ID Card fee, cart, top-ups, etc.).
    # Those already charged the tenant; "New Invoice Issued" is for bills to pay.
    if getattr(doc.flags, "ignore_push_and_realtime", False):
        return
    if cint(getattr(doc, "is_pos", 0)) and flt(getattr(doc, "outstanding_amount", 0)) <= 0:
        return
    # Never nudge tenants to "pay" a penalty reimbursement SI — it is billed from
    # the source invoice / Selcom settle and is often paid in the same PE.
    if is_penalty_reimbursement_invoice(doc):
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

            # Realtime WebSocket (including penalty SI outstanding refresh)
            publish_invoice_realtime(inv, event_type="invoice_paid")

            # Skip separate FCM for penalty SI — principal/source confirmation is enough
            # when one PE pays both (accountants' single-receipt model).
            if is_penalty_reimbursement_invoice(inv):
                # Mark source.penalty_paid only when the penalty SI is actually cleared
                try:
                    from propms.custom.sales_invoice_penalty import sync_penalty_paid_flag

                    sync_penalty_paid_flag(penalty_invoice_name=inv.name)
                except Exception:
                    frappe.log_error(
                        frappe.get_traceback(), "sync_penalty_paid_flag on PE submit"
                    )
                continue

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

    from propms.api.v1.payments.invoice_amount import money, payment_limits, resolve_invoice_pay_amount

    outstanding = money(inv.outstanding_amount)
    pay_amount, amount_err = resolve_invoice_pay_amount(
        amount, outstanding, invoice_name=inv.name
    )
    if amount_err:
        frappe.throw(amount_err)

    import uuid
    transaction_ref = f"SEL-{inv.name}-{uuid.uuid4().hex[:8].upper()}"
    limits = payment_limits(inv.name, outstanding=outstanding)

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
        "is_partial": 1 if money(pay_amount) < outstanding else 0,
        "outstanding_before": outstanding,
        "remaining_after_payment": money(max(0.0, outstanding - money(pay_amount))),
        "payment_limits": limits,
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
