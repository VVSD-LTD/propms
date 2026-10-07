# -*- coding: utf-8 -*-
"""Settle Selcom success by creating a Payment Entry against an existing Sales Invoice.

This is the original / default Selcom settlement path (rent, utilities, etc.).
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import flt, today, now
from propms.api.v1.payments.selcom_client import get_selcom_settings
from propms.api.v1.electricity.electricity import get_electricity_catalog


def settle_sales_invoice_payment(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
    """Create/submit Payment Entry for txn.sales_invoice and mark txn Success."""
    invoice_name = txn.sales_invoice if txn else None
    if not invoice_name and order_id and "SINV" in order_id:
        parts = order_id.split("-")
        for p in parts:
            if p.startswith("SINV") or frappe.db.exists("Sales Invoice", p):
                invoice_name = p
                break

    if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
        frappe.log_error(f"Cannot resolve Sales Invoice for order: {order_id}", "Selcom Webhook Error")
        return {"status": "error", "message": "Sales Invoice not found for this order"}

    # Idempotency: already Success with PE
    if txn and txn.status == "Success" and txn.payment_entry and frappe.db.exists("Payment Entry", txn.payment_entry):
        return {
            "status": "success",
            "message": "Transaction already processed successfully",
            "order_id": order_id,
            "payment_entry": txn.payment_entry,
            "invoice": txn.sales_invoice,
            "payment_workflow": txn.payment_workflow,
        }

    inv = frappe.get_doc("Sales Invoice", invoice_name)
    settings = get_selcom_settings()

    paid_amt = flt(amount) if amount else (flt(txn.amount) if txn else flt(inv.outstanding_amount))
    if paid_amt <= 0:
        paid_amt = flt(inv.outstanding_amount)

    allocated_amt = min(paid_amt, flt(inv.outstanding_amount)) if flt(inv.outstanding_amount) > 0 else paid_amt

    # Idempotency: PE already exists for this Selcom reference
    existing_pe = None
    ref_query = selcom_ref or order_id
    if ref_query:
        existing_pe = frappe.db.get_value("Payment Entry", {"reference_no": ref_query, "docstatus": 1}, "name")

    if existing_pe:
        if txn:
            txn.status = "Success"
            txn.payment_entry = existing_pe
            txn.sales_invoice = inv.name
            txn.selcom_reference = selcom_ref or txn.selcom_reference
            if raw_payload:
                txn.ipn_payload = frappe.as_json(raw_payload)
            txn.save(ignore_permissions=True)
            frappe.db.commit()

        return {
            "status": "success",
            "message": "Payment Entry already exists and linked",
            "payment_entry": existing_pe,
            "invoice": inv.name,
        }

    paid_to_account = settings.get("default_bank_account")
    if not paid_to_account or not frappe.db.exists("Account", paid_to_account):
        frappe.throw(
            _(
                "Please set <b>Default Bank Account</b> in "
                "<a href='/app/selcom-settings'>Selcom Settings</a>."
            ),
            title=_("Bank Account Not Configured"),
        )

    # Optional label on PE only (not required by ERPNext / not from Settings)
    mode_of_payment = "Selcom" if frappe.db.exists("Mode of Payment", "Selcom") else None

    original_user = frappe.session.user
    frappe.set_user("Administrator")

    try:
        try:
            from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

            pe = get_payment_entry("Sales Invoice", inv.name, party_amount=allocated_amt)
            pe.reference_no = str(selcom_ref or order_id)
            pe.reference_date = today()
            pe.paid_to = paid_to_account
            if mode_of_payment:
                pe.mode_of_payment = mode_of_payment
        except Exception:
            pe = frappe.new_doc("Payment Entry")
            pe.payment_type = "Receive"
            pe.party_type = "Customer"
            pe.party = inv.customer
            pe.company = inv.company
            pe.paid_amount = allocated_amt
            pe.received_amount = allocated_amt
            pe.paid_to_account_currency = inv.currency or "TZS"
            pe.reference_no = str(selcom_ref or order_id)
            pe.reference_date = today()
            pe.paid_to = paid_to_account
            if mode_of_payment:
                pe.mode_of_payment = mode_of_payment

            if flt(inv.outstanding_amount) > 0:
                pe.append(
                    "references",
                    {
                        "reference_doctype": "Sales Invoice",
                        "reference_name": inv.name,
                        "total_amount": inv.grand_total,
                        "outstanding_amount": inv.outstanding_amount,
                        "allocated_amount": allocated_amt,
                    },
                )

        pe.insert(ignore_permissions=True)
        pe.submit()
    finally:
        if original_user:
            frappe.set_user(original_user)

    if txn:
        txn.status = "Success"
        txn.selcom_reference = str(selcom_ref or order_id)
        txn.payment_entry = pe.name
        txn.sales_invoice = inv.name
        if raw_payload:
            txn.ipn_payload = frappe.as_json(raw_payload)
        txn.save(ignore_permissions=True)
    else:
        txn = frappe.get_doc(
            {
                "doctype": "Selcom Payment Transaction Log",
                "order_id": order_id,
                "payment_workflow": "sales_invoice_payment",
                "reference_doctype": "Sales Invoice",
                "reference_name": inv.name,
                "sales_invoice": inv.name,
                "customer": inv.customer,
                "amount": allocated_amt,
                "currency": inv.currency or "TZS",
                "payment_channel": "HOSTED",
                "status": "Success",
                "selcom_reference": str(selcom_ref or order_id),
                "payment_entry": pe.name,
                "ipn_payload": frappe.as_json(raw_payload) if raw_payload else None,
            }
        ).insert(ignore_permissions=True)

    frappe.db.commit()

    try:
        realtime_payload = {
            "order_id": order_id,
            "invoice_name": inv.name,
            "amount": allocated_amt,
            "currency": inv.currency or "TZS",
            "status": "PAID",
            "payment_entry": pe.name,
            "payment_workflow": "sales_invoice_payment",
            "reference_no": str(selcom_ref or order_id),
            "timestamp": now(),
        }
        frappe.publish_realtime(
            event="payment_completed",
            message=realtime_payload,
            room=f"doc:Sales Invoice/{inv.name}",
        )
        if inv.contact_email:
            frappe.publish_realtime(
                event="payment_completed",
                message=realtime_payload,
                room=f"user:{inv.contact_email}",
            )
    except Exception as e:
        frappe.log_error(f"WebSocket publish error on payment: {str(e)}", "Selcom Webhook Realtime")

    try:
        target_user = None
        if inv.contact_email and frappe.db.exists("User", inv.contact_email):
            target_user = inv.contact_email
        else:
            portal_user = frappe.db.get_value("Portal User", {"parent": inv.customer}, "user")
            if portal_user:
                target_user = portal_user

        if target_user:
            tokens = frappe.db.get_all("Mobile Device Token", {"user": target_user}, pluck="token")
            if tokens:
                from propms.utils.fcm import send_to_tokens

                send_to_tokens(
                    tokens=tokens,
                    title="Payment Received",
                    body=(
                        f"Your payment of {inv.currency or 'TZS'} {allocated_amt:,.0f} "
                        f"for invoice {inv.name} was successfully received. Thank you!"
                    ),
                    data={"type": "invoice_paid", "invoice": inv.name, "order_id": order_id},
                )
    except Exception as e:
        frappe.log_error(title="Selcom FCM Error", message=f"FCM notification error on payment: {str(e)}")

    return {
        "status": "success",
        "result": "SUCCESS",
        "message": "Payment Entry created and invoice reconciled",
        "payment_entry": pe.name,
        "invoice": inv.name,
        "amount": allocated_amt,
        "payment_workflow": "sales_invoice_payment",
    }
