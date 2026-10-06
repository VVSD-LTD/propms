import json
import frappe
from frappe import _
from frappe.utils import flt
from propms.api.v1.payments.workflows import get_success_handler, WORKFLOW_SALES_INVOICE_PAYMENT


def process_successful_payment(order_id, selcom_ref=None, amount=None, raw_payload=None):
    """Dispatch Selcom success to the workflow-specific settlement handler.

    Existing invoice payments keep creating a Payment Entry.
    Cart / Sales Order workflows (Water now, Electricity later) use their own
    handlers registered in propms.api.v1.payments.handlers.
    """
    if not order_id:
        return {"status": "error", "message": "order_id is required"}

    # Elevate execution privileges to Administrator for unauthenticated webhook calls
    if frappe.session.user == "Guest":
        frappe.set_user("Administrator")

    txn = None
    if frappe.db.exists("Viva Payment Transaction", {"order_id": order_id}):
        txn = frappe.get_doc("Viva Payment Transaction", {"order_id": order_id})
    elif frappe.db.exists("Viva Payment Transaction", order_id):
        txn = frappe.get_doc("Viva Payment Transaction", order_id)

    if not txn:
        # Legacy fallback: treat as invoice payment if we can resolve an SI from order_id
        frappe.log_error(
            f"Viva Payment Transaction not found for order_id={order_id}; attempting invoice fallback",
            "Selcom Webhook Warning",
        )
        from propms.api.v1.payments.handlers_invoice import settle_sales_invoice_payment

        return settle_sales_invoice_payment(
            txn=None,
            order_id=order_id,
            selcom_ref=selcom_ref,
            amount=amount,
            raw_payload=raw_payload,
        )

    # Backfill workflow for older transactions created before this field existed
    payment_workflow = (txn.payment_workflow or "").strip() or WORKFLOW_SALES_INVOICE_PAYMENT
    if not txn.payment_workflow:
        txn.payment_workflow = payment_workflow
        if not txn.reference_doctype and txn.sales_invoice:
            txn.reference_doctype = "Sales Invoice"
            txn.reference_name = txn.sales_invoice
        txn.db_update()

    handler = get_success_handler(payment_workflow)
    return handler(
        txn=txn,
        order_id=order_id,
        selcom_ref=selcom_ref,
        amount=amount,
        raw_payload=raw_payload,
    )


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

        # 3. Process the successful payment atomically via workflow dispatcher
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
