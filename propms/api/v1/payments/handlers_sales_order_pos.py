# -*- coding: utf-8 -*-
"""Settle Selcom success for a draft Sales Order cart checkout.

Flow:
  1. Submit the Sales Order
  2. Create a POS Sales Invoice (is_pos + configured pos_profile)
  3. Let ERPNext POS (`set_pos_fields`) load payment modes from the profile
  4. Allocate paid amount to the profile's default Mode of Payment and submit
  5. No Payment Entry is created
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import flt, today, now, cint
from propms.api.v1.payments.workflows import get_mobile_cart_pos_profile_name


def settle_sales_order_pos(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
    """Submit SO and create paid POS Sales Invoice for a cart checkout."""
    so_name = (txn.sales_order if txn else None) or (
        txn.reference_name if txn and txn.reference_doctype == "Sales Order" else None
    )

    if not so_name or not frappe.db.exists("Sales Order", so_name):
        frappe.log_error(f"Cannot resolve Sales Order for order: {order_id}", "Selcom SO POS Error")
        return {"status": "error", "message": "Sales Order not found for this payment"}

    # Idempotency: already settled with SI
    if txn and txn.status == "Success" and txn.sales_invoice and frappe.db.exists("Sales Invoice", txn.sales_invoice):
        return {
            "status": "success",
            "message": "Transaction already processed successfully",
            "order_id": order_id,
            "sales_order": so_name,
            "invoice": txn.sales_invoice,
            "payment_workflow": txn.payment_workflow,
        }

    original_user = frappe.session.user
    frappe.set_user("Administrator")

    try:
        so = frappe.get_doc("Sales Order", so_name)

        if so.docstatus == 2:
            return {"status": "error", "message": f"Sales Order {so_name} is cancelled"}

        # Submit draft SO first
        if so.docstatus == 0:
            so.flags.ignore_permissions = True
            so.submit()
            so.reload()

        # If SI already linked from a prior partial attempt, reuse it
        existing_si = None
        if txn and txn.sales_invoice and frappe.db.exists("Sales Invoice", txn.sales_invoice):
            existing_si = frappe.get_doc("Sales Invoice", txn.sales_invoice)
        else:
            existing_si_name = frappe.db.get_value(
                "Sales Invoice Item",
                {"sales_order": so_name, "docstatus": ["<", 2]},
                "parent",
            )
            if existing_si_name:
                existing_si = frappe.get_doc("Sales Invoice", existing_si_name)

        if existing_si and existing_si.docstatus == 1:
            if txn:
                _mark_txn_success(txn, so_name, existing_si.name, selcom_ref, order_id, raw_payload)
            return {
                "status": "success",
                "result": "SUCCESS",
                "message": "POS Sales Invoice already exists for this Sales Order",
                "sales_order": so_name,
                "invoice": existing_si.name,
                "amount": flt(existing_si.grand_total),
                "payment_workflow": "sales_order_pos",
            }

        # POS Profile is accountant-configured in Selcom Settings (not hardcoded)
        pos_profile_name = get_mobile_cart_pos_profile_name()
        pos_profile = frappe.get_doc("POS Profile", pos_profile_name)

        if existing_si and existing_si.docstatus == 0:
            invoice = existing_si
        else:
            from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

            invoice = make_sales_invoice(so_name, ignore_permissions=True)
            invoice.flags.ignore_permissions = True
            invoice.flags.ignore_mandatory = True
            invoice.flags.ignore_push_and_realtime = True

        invoice.is_pos = 1
        invoice.pos_profile = pos_profile_name
        invoice.posting_date = today()
        invoice.due_date = today()

        # ERPNext loads warehouse, taxes, and payment modes from the POS Profile
        invoice.set_pos_fields()

        # Carry lease / order-type markers from SO
        if hasattr(so, "lease") and so.lease:
            invoice.lease = so.lease
        order_type = getattr(so, "mobile_order_type", None) or ""
        if order_type:
            invoice.lease_item = order_type
        elif not getattr(invoice, "lease_item", None):
            invoice.lease_item = "Water"

        so_remarks = ""
        if hasattr(so, "remarks") and so.remarks:
            so_remarks = so.remarks
        elif getattr(so, "delivery_instructions", None):
            so_remarks = so.delivery_instructions

        existing_remarks = getattr(invoice, "remarks", None) or ""
        invoice.remarks = (
            f"{existing_remarks}\n{so_remarks}\n"
            f"Paid via Selcom ({selcom_ref or order_id}) against Sales Order {so_name}."
        ).strip()

        if invoice.name:
            invoice.save(ignore_permissions=True)
        else:
            invoice.insert(ignore_permissions=True)

        invoice.reload()

        # set_pos_fields loads mop rows with amount=0; allocate paid total to the
        # profile default Mode of Payment (whatever accountants configured).
        paid_amt = flt(amount) if amount else (flt(txn.amount) if txn else 0)
        allocate_to = flt(invoice.grand_total) if flt(invoice.grand_total) > 0 else paid_amt
        if allocate_to <= 0:
            return {"status": "error", "message": "Invoice grand total is zero; cannot settle payment"}

        _allocate_pos_payment_amount(invoice, allocate_to, pos_profile)
        invoice.save(ignore_permissions=True)
        invoice.submit()
        frappe.db.commit()

        if txn:
            _mark_txn_success(txn, so_name, invoice.name, selcom_ref, order_id, raw_payload)

        _notify_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total), so_name)

        return {
            "status": "success",
            "result": "SUCCESS",
            "message": "Sales Order submitted and POS Sales Invoice created as Paid",
            "sales_order": so_name,
            "invoice": invoice.name,
            "amount": flt(invoice.grand_total),
            "currency": invoice.currency or "TZS",
            "payment_workflow": "sales_order_pos",
            "pos_profile": pos_profile_name,
            "payment_entry": None,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Selcom SO POS Settlement Error")
        return {"status": "error", "message": f"Failed to settle Sales Order payment: {str(e)}"}
    finally:
        if original_user:
            frappe.set_user(original_user)


def _allocate_pos_payment_amount(invoice, amount, pos_profile):
    """Put the paid amount on the POS Profile's default Mode of Payment row.

    Payment method name/account come from the profile — never hardcoded.
    Zero-amount mop rows are cleared by Sales Invoice.clear_unallocated_mode_of_payments on submit.
    """
    if not invoice.payments:
        # Profile has no payment modes — surface a clear config error
        frappe.throw(
            _(
                "POS Profile {0} has no Modes of Payment configured. "
                "Add at least one payment method (and mark one as Default)."
            ).format(frappe.bold(pos_profile.name))
        )

    default_row = None
    for row in invoice.payments:
        if cint(row.default):
            default_row = row
            break
    if not default_row:
        # Fall back to whichever mop is marked default on the POS Profile child table
        for profile_pay in pos_profile.get("payments") or []:
            if cint(profile_pay.default):
                for row in invoice.payments:
                    if row.mode_of_payment == profile_pay.mode_of_payment:
                        default_row = row
                        break
            if default_row:
                break
    if not default_row:
        default_row = invoice.payments[0]

    for row in invoice.payments:
        row.amount = 0
        row.base_amount = 0

    default_row.amount = flt(amount)
    default_row.base_amount = flt(amount)


def _mark_txn_success(txn, so_name, invoice_name, selcom_ref, order_id, raw_payload):
    txn.status = "Success"
    txn.sales_order = so_name
    txn.sales_invoice = invoice_name
    txn.reference_doctype = "Sales Order"
    txn.reference_name = so_name
    txn.selcom_reference = str(selcom_ref or order_id)
    txn.payment_entry = None
    if raw_payload:
        txn.ipn_payload = frappe.as_json(raw_payload)
    txn.save(ignore_permissions=True)
    frappe.db.commit()


def _notify_pos_payment(invoice, order_id, selcom_ref, amount, so_name):
    try:
        realtime_payload = {
            "order_id": order_id,
            "invoice_name": invoice.name,
            "sales_order": so_name,
            "amount": amount,
            "currency": invoice.currency or "TZS",
            "status": "PAID",
            "payment_entry": None,
            "payment_workflow": "sales_order_pos",
            "reference_no": str(selcom_ref or order_id),
            "timestamp": now(),
        }
        frappe.publish_realtime(
            event="payment_completed",
            message=realtime_payload,
            room=f"doc:Sales Invoice/{invoice.name}",
        )
        if invoice.contact_email:
            frappe.publish_realtime(
                event="payment_completed",
                message=realtime_payload,
                room=f"user:{invoice.contact_email}",
            )
    except Exception as e:
        frappe.log_error(f"WebSocket publish error on SO POS payment: {str(e)}", "Selcom Webhook Realtime")

    try:
        target_user = None
        if invoice.contact_email and frappe.db.exists("User", invoice.contact_email):
            target_user = invoice.contact_email
        else:
            portal_user = frappe.db.get_value("Portal User", {"parent": invoice.customer}, "user")
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
                        f"Your payment of {invoice.currency or 'TZS'} {amount:,.0f} "
                        f"for order {so_name} was successful."
                    ),
                    data={
                        "type": "order_paid",
                        "invoice": invoice.name,
                        "sales_order": so_name,
                        "order_id": order_id,
                    },
                )
    except Exception as e:
        frappe.log_error(title="Selcom FCM Error", message=f"FCM notification error on SO POS payment: {str(e)}")
