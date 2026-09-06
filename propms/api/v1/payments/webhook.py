import json
import frappe
from frappe import _
from frappe.utils import flt, today, now
from propms.api.v1.payments.selcom_client import get_selcom_settings


def process_successful_payment(order_id, selcom_ref=None, amount=None, raw_payload=None):
    """Atomically create ERPNext Payment Entry, clear invoice balance, and notify tenant.

    Guarantees strict idempotency so duplicate webhooks or retries never create duplicate entries.
    """
    if not order_id:
        return {"status": "error", "message": "order_id is required"}

    # Find matching payment transaction
    txn = None
    if frappe.db.exists("Viva Payment Transaction", {"order_id": order_id}):
        txn = frappe.get_doc("Viva Payment Transaction", {"order_id": order_id})
    elif frappe.db.exists("Viva Payment Transaction", order_id):
        txn = frappe.get_doc("Viva Payment Transaction", order_id)

    # Idempotency check 1: Transaction already marked Success with payment entry
    if txn and txn.status == "Success" and txn.payment_entry and frappe.db.exists("Payment Entry", txn.payment_entry):
        return {
            "status": "success",
            "message": "Transaction already processed successfully",
            "order_id": order_id,
            "payment_entry": txn.payment_entry,
            "invoice": txn.sales_invoice,
        }

    # Identify Sales Invoice
    invoice_name = txn.sales_invoice if txn else None
    if not invoice_name and "SINV" in order_id:
        # Fallback extract from order_id pattern
        parts = order_id.split("-")
        for p in parts:
            if p.startswith("SINV") or frappe.db.exists("Sales Invoice", p):
                invoice_name = p
                break

    if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
        frappe.log_error(f"Cannot resolve Sales Invoice for order: {order_id}", "Selcom Webhook Error")
        return {"status": "error", "message": "Sales Invoice not found for this order"}

    inv = frappe.get_doc("Sales Invoice", invoice_name)
    settings = get_selcom_settings()

    # Determine amount
    paid_amt = flt(amount) if amount else (flt(txn.amount) if txn else flt(inv.outstanding_amount))
    if paid_amt <= 0:
        paid_amt = flt(inv.outstanding_amount)

    allocated_amt = min(paid_amt, flt(inv.outstanding_amount)) if flt(inv.outstanding_amount) > 0 else paid_amt

    # Idempotency check 2: Check if Payment Entry already exists for this Selcom reference
    existing_pe = None
    ref_query = selcom_ref or order_id
    if ref_query:
        existing_pe = frappe.db.get_value("Payment Entry", {"reference_no": ref_query, "docstatus": 1}, "name")

    if existing_pe:
        if txn:
            txn.status = "Success"
            txn.payment_entry = existing_pe
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

    # Determine Mode of Payment & Accounts
    mode_of_payment = settings.get("mode_of_payment") or "Selcom"
    if not frappe.db.exists("Mode of Payment", mode_of_payment):
        if frappe.db.exists("Mode of Payment", "Bank Draft"):
            mode_of_payment = "Bank Draft"
        elif frappe.db.exists("Mode of Payment", "Cash"):
            mode_of_payment = "Cash"
        else:
            first_mop = frappe.db.get_value("Mode of Payment", {}, "name")
            mode_of_payment = first_mop or "Selcom"

    # Resolve paid_to account
    paid_to_account = settings.get("default_bank_account")
    if not paid_to_account and mode_of_payment and frappe.db.exists("Mode of Payment", mode_of_payment):
        paid_to_account = frappe.db.get_value(
            "Mode of Payment Account",
            {"parent": mode_of_payment, "company": inv.company},
            "default_account",
        )
    if not paid_to_account:
        paid_to_account = (
            frappe.db.get_value("Company", inv.company, "default_bank_account")
            or frappe.db.get_value("Company", inv.company, "default_cash_account")
            or frappe.db.get_value("Account", {"company": inv.company, "account_type": "Bank", "is_group": 0}, "name")
            or frappe.db.get_value("Account", {"company": inv.company, "account_type": "Cash", "is_group": 0}, "name")
        )

    # Use ERPNext's official get_payment_entry factory to ensure ledger integrity
    try:
        from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
        pe = get_payment_entry("Sales Invoice", inv.name, party_amount=allocated_amt)
        pe.reference_no = str(selcom_ref or order_id)
        pe.reference_date = today()
        pe.mode_of_payment = mode_of_payment
        if paid_to_account:
            pe.paid_to = paid_to_account
    except Exception:
        # Fallback manual document construction
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
        pe.mode_of_payment = mode_of_payment
        if paid_to_account:
            pe.paid_to = paid_to_account

        if flt(inv.outstanding_amount) > 0:
            pe.append("references", {
                "reference_doctype": "Sales Invoice",
                "reference_name": inv.name,
                "total_amount": inv.grand_total,
                "outstanding_amount": inv.outstanding_amount,
                "allocated_amount": allocated_amt,
            })

    pe.insert(ignore_permissions=True)
    pe.submit()

    # Update Transaction record
    if txn:
        txn.status = "Success"
        txn.selcom_reference = str(selcom_ref or order_id)
        txn.payment_entry = pe.name
        if raw_payload:
            txn.ipn_payload = frappe.as_json(raw_payload)
        txn.save(ignore_permissions=True)
    else:
        # Create audit transaction if missing
        txn = frappe.get_doc({
            "doctype": "Viva Payment Transaction",
            "order_id": order_id,
            "sales_invoice": inv.name,
            "customer": inv.customer,
            "amount": allocated_amt,
            "currency": inv.currency or "TZS",
            "payment_channel": "HOSTED",
            "status": "Success",
            "selcom_reference": str(selcom_ref or order_id),
            "payment_entry": pe.name,
            "ipn_payload": frappe.as_json(raw_payload) if raw_payload else None,
        }).insert(ignore_permissions=True)

    frappe.db.commit()

    # Real-time WebSocket Broadcast
    try:
        realtime_payload = {
            "order_id": order_id,
            "invoice_name": inv.name,
            "amount": allocated_amt,
            "currency": inv.currency or "TZS",
            "status": "PAID",
            "payment_entry": pe.name,
            "reference_no": str(selcom_ref or order_id),
            "timestamp": now(),
        }
        frappe.publish_realtime(
            event="payment_completed",
            message=realtime_payload,
            room=f"doc:Sales Invoice/{inv.name}",
        )
        # Also broadcast to customer room
        if inv.contact_email:
            frappe.publish_realtime(
                event="payment_completed",
                message=realtime_payload,
                room=f"user:{inv.contact_email}",
            )
    except Exception as e:
        frappe.log_error(f"WebSocket publish error on payment: {str(e)}", "Selcom Webhook Realtime")

    # Firebase Cloud Messaging (FCM) Push Notification
    try:
        target_user = None
        if inv.contact_email and frappe.db.exists("User", inv.contact_email):
            target_user = inv.contact_email
        else:
            portal_user = frappe.db.get_value("Portal User", {"parent": inv.customer}, "user")
            if portal_user:
                target_user = portal_user

        if target_user:
            from propms.api.v1.job_card.job_card import _send_push_notification_to_user
            _send_push_notification_to_user(
                user=target_user,
                title="Payment Received",
                body=f"Your payment of {inv.currency or 'TZS'} {allocated_amt:,.0f} for invoice {inv.name} was successfully received. Thank you!",
                notification_type="invoice_paid",
            )
    except Exception as e:
        frappe.log_error(f"FCM notification error on payment: {str(e)}", "Selcom Webhook FCM")

    return {
        "status": "success",
        "result": "SUCCESS",
        "message": "Payment Entry created and invoice reconciled",
        "payment_entry": pe.name,
        "invoice": inv.name,
        "amount": allocated_amt,
    }


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def selcom_ipn_webhook(*args, **kwargs):
    """Public Webhook / IPN Receiver for Selcom Payment Gateway.

    Matches the permanent URL:
    https://dev15-viva2.vvsdtz.com/api/method/propms.api.v1.payments.selcom_ipn_webhook
    """
    try:
        # 1. Extract payload from JSON body, form dict, or kwargs
        data = None
        if hasattr(frappe, "request") and frappe.request:
            try:
                data = frappe.request.get_json(silent=True)
            except Exception:
                data = None

        if not data:
            data = getattr(frappe, "form_dict", None) or {}
        if kwargs:
            data.update(kwargs)

        try:
            frappe.logger().info(f"Selcom IPN Webhook Received: {frappe.as_json(data)}")
        except Exception:
            pass

        # 2. Extract transaction parameters
        order_id = (
            data.get("order_id")
            or data.get("orderId")
            or data.get("transid")
            or data.get("reference")
            or data.get("reference_id")
        )
        selcom_ref = data.get("transid") or data.get("reference") or order_id
        result_code = str(data.get("resultcode") or data.get("result_code") or "").strip()
        result_status = str(data.get("result") or data.get("payment_status") or data.get("status") or "").upper()
        amount = flt(data.get("amount") or data.get("paid_amount") or 0)

        # Basic ping/test check
        if not order_id:
            return {
                "result": "SUCCESS",
                "status": "success",
                "message": "Selcom IPN Webhook is active and listening",
            }

        # Check for success indicators
        is_success = (
            result_status in ("SUCCESS", "COMPLETED", "PAID", "000", "200")
            or result_code in ("000", "200")
        )

        if not is_success:
            # Update transaction to failed if found
            if frappe.db.exists("Viva Payment Transaction", {"order_id": order_id}):
                txn = frappe.get_doc("Viva Payment Transaction", {"order_id": order_id})
                txn.status = "Failed"
                txn.error_message = f"Status: {result_status}, Code: {result_code}"
                txn.ipn_payload = frappe.as_json(data)
                txn.save(ignore_permissions=True)
                frappe.db.commit()

            return {
                "result": "FAIL",
                "status": "failed",
                "message": f"Payment status {result_status} (Code {result_code}) not successful",
            }

        # 3. Process the successful payment atomically
        res = process_successful_payment(
            order_id=order_id,
            selcom_ref=selcom_ref,
            amount=amount,
            raw_payload=data,
        )
        return res

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Selcom IPN Webhook Exception")
        return {"result": "ERROR", "status": "error", "message": str(e)}
