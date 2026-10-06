# -*- coding: utf-8 -*-
"""Shared Selcom checkout helpers used by all payment workflows."""

from __future__ import unicode_literals

import base64
import json
import re
import frappe
from frappe import _
from frappe.utils import flt
from propms.api.v1.payments.selcom_client import SelcomClient
from propms.api.v1.payments.workflows import WORKFLOW_SALES_INVOICE_PAYMENT


def normalize_phone_number(phone):
    """Normalize phone number to 255XXXXXXXXX format."""
    if not phone:
        return ""
    cleaned = re.sub(r"[^\d]", "", str(phone).strip())
    if cleaned.startswith("0") and len(cleaned) == 10:
        cleaned = "255" + cleaned[1:]
    elif cleaned.startswith("255") and len(cleaned) == 12:
        pass
    elif len(cleaned) == 9:
        cleaned = "255" + cleaned
    return cleaned


def start_selcom_checkout(
    *,
    reference_doctype,
    reference_name,
    customer,
    amount,
    currency="TZS",
    payment_workflow=WORKFLOW_SALES_INVOICE_PAYMENT,
    payment_method="MOBILE_MONEY",
    phone_number=None,
    buyer_remarks=None,
    merchant_remarks=None,
    sales_invoice=None,
    sales_order=None,
    extra_raw_request=None,
):
    """Create Viva Payment Transaction + Selcom order for any reference document.

    Returns a standard mobile response dict. Does not change existing invoice
    settlement behaviour — callers choose payment_workflow.
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    pay_amount = flt(amount)
    if pay_amount <= 0:
        return {"status": "error", "message": "Payment amount must be greater than 0"}

    payment_method = (payment_method or "MOBILE_MONEY").upper()
    clean_phone = normalize_phone_number(phone_number)

    if not clean_phone:
        user_phone = frappe.db.get_value("User", frappe.session.user, "mobile_no") or frappe.db.get_value(
            "User", frappe.session.user, "phone"
        )
        clean_phone = normalize_phone_number(user_phone)

    if payment_method == "MOBILE_MONEY" and not clean_phone:
        return {"status": "error", "message": "A valid phone number is required for Mobile Money payment."}

    client = SelcomClient()
    if not client.enabled:
        return {"status": "error", "message": "Selcom payments are currently disabled."}
    if not client.vendor_id or not client.api_key or not client.api_secret:
        return {"status": "error", "message": "Selcom gateway credentials are not configured in Selcom Settings."}

    rand_suffix = frappe.generate_hash(length=6).upper()
    clean_ref = re.sub(r"[^A-Za-z0-9]", "", reference_name or "")[:12]
    order_id = f"ORD-{clean_ref}-{rand_suffix}"

    user_info = frappe.db.get_value("User", frappe.session.user, ["full_name", "email"], as_dict=True) or {}
    buyer_email = user_info.get("email") or frappe.session.user
    customer_name = frappe.db.get_value("Customer", customer, "customer_name") if customer else None
    buyer_name = user_info.get("full_name") or customer_name or "Viva Tenant"
    buyer_phone = clean_phone or "255700000000"
    currency = currency or "TZS"

    raw_request = {
        "order_id": order_id,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "payment_workflow": payment_workflow,
        "amount": pay_amount,
        "method": payment_method,
        "phone": clean_phone,
    }
    if extra_raw_request and isinstance(extra_raw_request, dict):
        raw_request.update(extra_raw_request)

    txn = frappe.get_doc(
        {
            "doctype": "Viva Payment Transaction",
            "order_id": order_id,
            "payment_workflow": payment_workflow,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "sales_invoice": sales_invoice,
            "sales_order": sales_order,
            "customer": customer,
            "amount": pay_amount,
            "currency": currency,
            "payment_channel": payment_method,
            "phone_number": clean_phone,
            "status": "Pending",
            "raw_request": json.dumps(raw_request),
        }
    )
    txn.insert(ignore_permissions=True)
    frappe.db.commit()

    name_parts = (buyer_name or "Viva Tenant").strip().split(" ", 1)
    first_name = name_parts[0] if name_parts else "Viva"
    last_name = name_parts[1] if len(name_parts) > 1 else "Tenant"

    site_url = (frappe.utils.get_url() or "https://dev15-viva2.vvsdtz.com").rstrip("/")
    webhook_raw = f"{site_url}/api/method/propms.api.v1.payments.selcom_ipn_webhook"
    webhook_b64 = base64.b64encode(webhook_raw.encode("utf-8")).decode("utf-8")
    redirect_b64 = base64.b64encode(f"{site_url}/payment-success".encode("utf-8")).decode("utf-8")
    cancel_b64 = base64.b64encode(f"{site_url}/payment-cancel".encode("utf-8")).decode("utf-8")
    buyer_uuid = frappe.defaults.get_user_default("selcom_gateway_buyer_uuid", buyer_email) or ""

    buyer_remarks = buyer_remarks or f"{reference_doctype} {reference_name}"
    merchant_remarks = merchant_remarks or "Viva Towers Payment"

    if payment_method in ("CARD", "HOSTED"):
        endpoint = "/v1/checkout/create-order"
        order_payload = {
            "vendor": client.vendor_id,
            "order_id": order_id,
            "buyer_email": buyer_email,
            "buyer_name": buyer_name,
            "buyer_userid": buyer_email,
            "buyer_phone": buyer_phone,
            "gateway_buyer_uuid": buyer_uuid,
            "amount": int(round(pay_amount)),
            "currency": currency,
            "no_of_items": 1,
            "payment_methods": "ALL",
            "webhook": webhook_b64,
            "redirect_url": redirect_b64,
            "cancel_url": cancel_b64,
            "buyer_remarks": buyer_remarks,
            "merchant_remarks": merchant_remarks,
            "billing.firstname": first_name,
            "billing.lastname": last_name,
            "billing.address_1": "Viva Towers",
            "billing.address_2": "",
            "billing.city": "Dar es Salaam",
            "billing.state_or_region": "Dar es Salaam",
            "billing.postcode_or_pobox": "10000",
            "billing.country": "TZ",
            "billing.phone": buyer_phone,
        }
    else:
        endpoint = "/v1/checkout/create-order-minimal"
        order_payload = {
            "vendor": client.vendor_id,
            "order_id": order_id,
            "buyer_email": buyer_email,
            "buyer_name": buyer_name,
            "buyer_phone": buyer_phone,
            "amount": int(round(pay_amount)),
            "currency": currency,
            "no_of_items": 1,
            "webhook": webhook_b64,
            "redirect_url": redirect_b64,
            "cancel_url": cancel_b64,
            "buyer_remarks": buyer_remarks,
            "merchant_remarks": merchant_remarks,
        }

    order_res = client.post(endpoint, order_payload)
    txn.raw_response = json.dumps(order_res)

    result_code = (order_res.get("resultcode") or order_res.get("result_code") or "").strip()
    result_status = (order_res.get("result") or order_res.get("status") or "").upper()

    if result_status not in ("SUCCESS", "COMPLETED", "200") and result_code not in ("000", "200"):
        error_msg = order_res.get("message") or order_res.get("error") or "Failed to initialize order with Selcom"
        txn.status = "Failed"
        txn.error_message = str(error_msg)
        txn.save(ignore_permissions=True)
        frappe.db.commit()
        return {"status": "error", "message": error_msg, "order_id": order_id, "selcom_response": order_res}

    gateway_url = None
    raw_qr = None
    payment_token = None
    gateway_buyer_uuid = None
    data_field = order_res.get("data")
    if isinstance(data_field, list) and len(data_field) > 0:
        first_data = data_field[0]
        if isinstance(first_data, dict):
            gateway_url = first_data.get("payment_gateway_url") or first_data.get("gateway_url")
            raw_qr = first_data.get("qr")
            payment_token = first_data.get("payment_token")
            gateway_buyer_uuid = first_data.get("gateway_buyer_uuid")
    elif isinstance(data_field, dict):
        gateway_url = data_field.get("payment_gateway_url") or data_field.get("gateway_url")
        raw_qr = data_field.get("qr")
        payment_token = data_field.get("payment_token")
        gateway_buyer_uuid = data_field.get("gateway_buyer_uuid")

    if gateway_buyer_uuid and buyer_email:
        frappe.defaults.set_user_default("selcom_gateway_buyer_uuid", gateway_buyer_uuid, user=buyer_email)

    if gateway_url and not str(gateway_url).startswith("http"):
        try:
            decoded = base64.b64decode(gateway_url).decode("utf-8")
            if decoded.startswith("http"):
                gateway_url = decoded
        except Exception:
            pass

    txn.gateway_url = gateway_url or ""

    base_success = {
        "order_id": order_id,
        "transaction_id": txn.name,
        "reference_doctype": reference_doctype,
        "reference_name": reference_name,
        "payment_workflow": payment_workflow,
        "amount": pay_amount,
        "currency": currency,
        "payment_method": payment_method,
    }
    if sales_invoice:
        base_success["invoice_name"] = sales_invoice
    if sales_order:
        base_success["sales_order"] = sales_order

    if payment_method == "MOBILE_MONEY":
        wallet_payload = {
            "order_id": order_id,
            "transid": f"TXN-{order_id}",
            "msisdn": clean_phone,
        }
        wallet_res = client.post("/v1/checkout/wallet-payment", wallet_payload)
        txn.raw_response = json.dumps({"order_minimal": order_res, "wallet_payment": wallet_res})

        wallet_result = (wallet_res.get("result") or "").upper()
        wallet_code = (wallet_res.get("resultcode") or "").strip()

        if wallet_result in ("SUCCESS", "PENDING", "000") or wallet_code in ("000", "200"):
            txn.save(ignore_permissions=True)
            frappe.db.commit()
            return {
                "status": "success",
                "message": (
                    f"USSD PIN prompt sent to {clean_phone}. "
                    "Please enter your PIN on your phone to complete payment."
                ),
                "phone_number": clean_phone,
                "action": "WAIT_FOR_USSD_PIN",
                **base_success,
            }

        err = wallet_res.get("message") or "Failed to trigger USSD push on mobile wallet"
        txn.error_message = err
        txn.status = "Failed"
        txn.save(ignore_permissions=True)
        frappe.db.commit()
        return {
            "status": "error",
            "message": err,
            "order_id": order_id,
            "wallet_response": wallet_res,
        }

    if payment_method in ("CARD", "HOSTED"):
        txn.save(ignore_permissions=True)
        frappe.db.commit()
        return {
            "status": "success",
            "message": "Payment session initialized. Please complete payment on the secure gateway.",
            "gateway_url": gateway_url,
            "action": "OPEN_WEBVIEW",
            **base_success,
        }

    if payment_method == "QR_CODE":
        qr_string = raw_qr or gateway_url or f"SELCOM:ORDER:{order_id}:AMOUNT:{pay_amount}"
        txn.qr_data = qr_string
        txn.save(ignore_permissions=True)
        frappe.db.commit()
        return {
            "status": "success",
            "message": "Dynamic TanQR code generated. Scan with your banking app or M-Pesa.",
            "qr_data": qr_string,
            "payment_token": payment_token,
            "gateway_url": gateway_url,
            "action": "DISPLAY_QR",
            **base_success,
        }

    txn.save(ignore_permissions=True)
    frappe.db.commit()
    return {
        "status": "success",
        "message": "Payment initialized",
        "gateway_url": gateway_url,
        "action": "OPEN_WEBVIEW",
        **base_success,
    }
