import base64
import json
import re
import frappe
from frappe import _
from frappe.utils import flt, now, today
from propms.custom.lease import get_tenant_context_for_user
from propms.api.v1.payments.selcom_client import SelcomClient, get_selcom_settings


def _normalize_phone_number(phone):
    """Normalize phone number to 255XXXXXXXXX format."""
    if not phone:
        return ""
    # Remove whitespace, dashes, plus signs
    cleaned = re.sub(r"[^\d]", "", str(phone).strip())
    if cleaned.startswith("0") and len(cleaned) == 10:
        cleaned = "255" + cleaned[1:]
    elif cleaned.startswith("255") and len(cleaned) == 12:
        pass
    elif len(cleaned) == 9:
        cleaned = "255" + cleaned
    return cleaned


def _check_invoice_access(invoice_name):
    """Ensure current session user is authorized to pay for the invoice."""
    if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
        frappe.throw(_("Sales Invoice {0} not found.").format(invoice_name))

    inv = frappe.get_doc("Sales Invoice", invoice_name)
    if inv.docstatus != 1:
        frappe.throw(_("Invoice {0} is not submitted.").format(invoice_name))

    roles = frappe.get_roles(frappe.session.user)
    if "System Manager" in roles or "Mobile Maintenance Manager" in roles or "Accounts Manager" in roles:
        return inv

    # Tenant verification
    ctx = get_tenant_context_for_user()
    tenant_customer = ctx.get("customer") if ctx else None
    if tenant_customer and inv.customer == tenant_customer:
        return inv

    # Check if user email matches customer portal user or contact
    if inv.contact_email == frappe.session.user:
        return inv

    portal_user_customer = frappe.db.get_value("Portal User", {"user": frappe.session.user}, "parent")
    if portal_user_customer and portal_user_customer == inv.customer:
        return inv

    frappe.throw(_("You are not permitted to access this invoice."), frappe.PermissionError)


@frappe.whitelist(methods=["POST"])
def initiate_payment(invoice_name=None, amount=None, payment_method="MOBILE_MONEY", phone_number=None):
    """Initiate a Selcom payment for a Sales Invoice.

    Args:
        invoice_name: Name of the Sales Invoice (e.g. ACC-SINV-2026-00001)
        amount: Optional custom amount to pay (defaults to outstanding balance)
        payment_method: "MOBILE_MONEY", "CARD", "QR_CODE", or "HOSTED"
        phone_number: Required if payment_method is MOBILE_MONEY (e.g. 0714000111 or 255714000111)
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    req = getattr(frappe, "form_dict", None) or {}
    invoice_name = (invoice_name or req.get("invoice_name") or req.get("invoice_id") or req.get("sales_invoice") or "").strip()
    amount_val = amount if amount is not None else req.get("amount")
    payment_method = (payment_method or req.get("payment_method") or req.get("channel") or "MOBILE_MONEY").upper()
    phone_number = (phone_number or req.get("phone_number") or req.get("phone") or req.get("msisdn") or "").strip()

    if not invoice_name:
        return {"status": "error", "message": "invoice_name is required"}

    inv = _check_invoice_access(invoice_name)
    outstanding = flt(inv.outstanding_amount)

    if outstanding <= 0:
        return {"status": "error", "message": f"Invoice {inv.name} is already fully paid."}

    pay_amount = flt(amount_val) if (amount_val and flt(amount_val) > 0) else outstanding
    if pay_amount <= 0 or pay_amount > outstanding:
        return {"status": "error", "message": f"Payment amount must be between 1 and {outstanding:,.2f}"}

    # Normalize phone
    clean_phone = _normalize_phone_number(phone_number)
    if not clean_phone and inv.contact_mobile:
        clean_phone = _normalize_phone_number(inv.contact_mobile)
    if not clean_phone:
        user_phone = frappe.db.get_value("User", frappe.session.user, "mobile_no") or frappe.db.get_value("User", frappe.session.user, "phone")
        clean_phone = _normalize_phone_number(user_phone)

    if payment_method == "MOBILE_MONEY" and not clean_phone:
        return {"status": "error", "message": "A valid phone number is required for Mobile Money payment."}

    # Initialize Client & Settings
    client = SelcomClient()
    if not client.enabled:
        return {"status": "error", "message": "Selcom payments are currently disabled."}
    if not client.vendor_id or not client.api_key or not client.api_secret:
        return {"status": "error", "message": "Selcom gateway credentials are not configured in Viva Selcom Settings."}

    # Generate unique order reference
    rand_suffix = frappe.generate_hash(length=6).upper()
    clean_inv_id = re.sub(r"[^A-Za-z0-9]", "", inv.name)
    order_id = f"ORD-{clean_inv_id[:12]}-{rand_suffix}"

    user_info = frappe.db.get_value("User", frappe.session.user, ["full_name", "email"], as_dict=True) or {}
    buyer_email = user_info.get("email") or frappe.session.user
    buyer_name = user_info.get("full_name") or inv.customer_name or "Viva Tenant"
    buyer_phone = clean_phone or "255700000000"

    # Create transaction audit record
    txn = frappe.get_doc({
        "doctype": "Viva Payment Transaction",
        "order_id": order_id,
        "sales_invoice": inv.name,
        "customer": inv.customer,
        "amount": pay_amount,
        "currency": inv.currency or "TZS",
        "payment_channel": payment_method,
        "phone_number": clean_phone,
        "status": "Pending",
        "raw_request": json.dumps({
            "order_id": order_id,
            "invoice": inv.name,
            "amount": pay_amount,
            "method": payment_method,
            "phone": clean_phone,
        }),
    })
    txn.insert(ignore_permissions=True)
    frappe.db.commit()

    # 1. Step 1: Create Order with Selcom
    name_parts = (buyer_name or "Viva Tenant").strip().split(" ", 1)
    first_name = name_parts[0] if name_parts else "Viva"
    last_name = name_parts[1] if len(name_parts) > 1 else "Tenant"

    # Build base64 callback URLs for dynamic Selcom IPN notification
    site_url = (frappe.utils.get_url() or "https://dev15-viva2.vvsdtz.com").rstrip("/")
    webhook_raw = f"{site_url}/api/method/propms.api.v1.payments.selcom_ipn_webhook"
    webhook_b64 = base64.b64encode(webhook_raw.encode("utf-8")).decode("utf-8")
    redirect_b64 = base64.b64encode(f"{site_url}/payment-success".encode("utf-8")).decode("utf-8")
    cancel_b64 = base64.b64encode(f"{site_url}/payment-cancel".encode("utf-8")).decode("utf-8")

    # Fetch stored buyer_uuid if available
    buyer_uuid = frappe.defaults.get_user_default("selcom_gateway_buyer_uuid", buyer_email) or ""

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
            "currency": inv.currency or "TZS",
            "no_of_items": 1,
            "payment_methods": "ALL",
            "webhook": webhook_b64,
            "redirect_url": redirect_b64,
            "cancel_url": cancel_b64,
            "buyer_remarks": f"Invoice {inv.name}",
            "merchant_remarks": "Viva Towers Rent/Utility",
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
            "currency": inv.currency or "TZS",
            "no_of_items": 1,
            "webhook": webhook_b64,
            "redirect_url": redirect_b64,
            "cancel_url": cancel_b64,
            "buyer_remarks": f"Invoice {inv.name}",
            "merchant_remarks": "Viva Towers Rent/Utility",
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

    # Extract gateway url, qr string, and payment_token if available
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

    # If gateway_url is base64 encoded, decode it
    if gateway_url and not str(gateway_url).startswith("http"):
        try:
            decoded = base64.b64decode(gateway_url).decode("utf-8")
            if decoded.startswith("http"):
                gateway_url = decoded
        except Exception:
            pass

    txn.gateway_url = gateway_url or ""

    # 2. Step 2: Handle specific payment rail
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
                "message": f"USSD PIN prompt sent to {clean_phone}. Please enter your PIN on your phone to complete payment.",
                "order_id": order_id,
                "transaction_id": txn.name,
                "invoice_name": inv.name,
                "amount": pay_amount,
                "currency": inv.currency or "TZS",
                "payment_method": "MOBILE_MONEY",
                "phone_number": clean_phone,
                "action": "WAIT_FOR_USSD_PIN",
            }
        else:
            err = wallet_res.get("message") or "Failed to trigger USSD push on mobile wallet"
            txn.error_message = err
            txn.save(ignore_permissions=True)
            frappe.db.commit()
            return {
                "status": "error",
                "message": err,
                "order_id": order_id,
                "wallet_response": wallet_res,
            }

    elif payment_method in ("CARD", "HOSTED"):
        txn.save(ignore_permissions=True)
        frappe.db.commit()
        return {
            "status": "success",
            "message": "Payment session initialized. Please complete payment on the secure gateway.",
            "order_id": order_id,
            "transaction_id": txn.name,
            "invoice_name": inv.name,
            "amount": pay_amount,
            "currency": inv.currency or "TZS",
            "payment_method": payment_method,
            "gateway_url": gateway_url,
            "action": "OPEN_WEBVIEW",
        }

    elif payment_method == "QR_CODE":
        # TanQR EMVCo raw string from Selcom
        qr_string = raw_qr or gateway_url or f"SELCOM:ORDER:{order_id}:AMOUNT:{pay_amount}"
        txn.qr_data = qr_string
        txn.save(ignore_permissions=True)
        frappe.db.commit()
        return {
            "status": "success",
            "message": "Dynamic TanQR code generated. Scan with your banking app or M-Pesa.",
            "order_id": order_id,
            "transaction_id": txn.name,
            "invoice_name": inv.name,
            "amount": pay_amount,
            "currency": inv.currency or "TZS",
            "payment_method": "QR_CODE",
            "qr_data": qr_string,
            "payment_token": payment_token,
            "gateway_url": gateway_url,
            "action": "DISPLAY_QR",
        }

    txn.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "order_id": order_id,
        "transaction_id": txn.name,
        "invoice_name": inv.name,
        "amount": pay_amount,
        "currency": inv.currency or "TZS",
        "gateway_url": gateway_url,
    }


@frappe.whitelist(methods=["GET", "POST"])
def get_payment_status(order_id=None, transaction_id=None):
    """Query current payment status from local DB and Selcom Gateway."""
    req = getattr(frappe, "form_dict", None) or {}
    order_id = (order_id or transaction_id or req.get("order_id") or req.get("transaction_id") or req.get("order") or "").strip()

    if not order_id:
        return {"status": "error", "message": "order_id is required"}

    # Look up by order_id or transaction name
    if frappe.db.exists("Viva Payment Transaction", order_id):
        txn = frappe.get_doc("Viva Payment Transaction", order_id)
    elif frappe.db.exists("Viva Payment Transaction", {"order_id": order_id}):
        txn = frappe.get_doc("Viva Payment Transaction", {"order_id": order_id})
    else:
        return {"status": "error", "message": "Payment transaction not found"}

    # If transaction is already resolved, return status directly
    if txn.status in ("Success", "Failed", "Cancelled"):
        return {
            "status": "success",
            "order_id": txn.order_id,
            "transaction_status": txn.status,
            "invoice_name": txn.sales_invoice,
            "amount": txn.amount,
            "currency": txn.currency,
            "payment_entry": txn.payment_entry,
            "selcom_reference": txn.selcom_reference,
        }

    # Query gateway for live status
    client = SelcomClient()
    if client.enabled and client.vendor_id:
        status_res = client.get("/v1/checkout/order-status", {"order_id": txn.order_id})
        data = status_res.get("data")
        payment_status = None
        if isinstance(data, list) and len(data) > 0:
            payment_status = data[0].get("payment_status") or data[0].get("order_status")
        elif isinstance(data, dict):
            payment_status = data.get("payment_status") or data.get("order_status")

        if payment_status in ("COMPLETED", "SUCCESS", "PAID"):
            # Trigger reconciliation if not yet reconciled
            from propms.api.v1.payments.webhook import process_successful_payment
            process_successful_payment(
                order_id=txn.order_id,
                selcom_ref=status_res.get("transid") or txn.order_id,
                amount=txn.amount,
                raw_payload=status_res,
            )
            txn.reload()

    return {
        "status": "success",
        "order_id": txn.order_id,
        "transaction_status": txn.status,
        "invoice_name": txn.sales_invoice,
        "amount": txn.amount,
        "currency": txn.currency,
        "payment_entry": txn.payment_entry,
        "selcom_reference": txn.selcom_reference,
    }


@frappe.whitelist(methods=["POST"])
def cancel_payment(order_id=None):
    """Cancel an active pending payment order."""
    req = getattr(frappe, "form_dict", None) or {}
    order_id = (order_id or req.get("order_id") or "").strip()

    if not order_id:
        return {"status": "error", "message": "order_id is required"}

    if not frappe.db.exists("Viva Payment Transaction", {"order_id": order_id}):
        return {"status": "error", "message": "Payment transaction not found"}

    txn = frappe.get_doc("Viva Payment Transaction", {"order_id": order_id})
    if txn.status == "Success":
        return {"status": "error", "message": "Cannot cancel an already completed transaction"}

    client = SelcomClient()
    if client.enabled:
        client.delete("/v1/checkout/cancel-order", {"order_id": order_id})

    txn.status = "Cancelled"
    txn.save(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "message": "Payment order cancelled successfully",
        "order_id": order_id,
    }


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_payment_methods():
    """Return list of enabled payment methods and configurations for the mobile app."""
    settings = get_selcom_settings()
    enabled = bool(settings.get("enabled", 1))

    methods = [
        {
            "id": "MOBILE_MONEY",
            "title": "Mobile Money",
            "subtitle": "Instant USSD Push (M-Pesa, Tigo, Airtel, HaloPesa)",
            "icon": "phone_android",
            "enabled": enabled,
            "providers": [
                {"name": "Vodacom M-Pesa", "code": "MPESA", "prefix": ["074", "075", "076"]},
                {"name": "Mixx by Yas (Tigo)", "code": "TIGO", "prefix": ["071", "065", "067"]},
                {"name": "Airtel Money", "code": "AIRTEL", "prefix": ["068", "069", "078"]},
                {"name": "HaloPesa", "code": "HALOPESA", "prefix": ["062"]},
            ],
        },
        {
            "id": "CARD",
            "title": "Credit / Debit Card",
            "subtitle": "Visa, Mastercard, UnionPay (3D-Secure)",
            "icon": "credit_card",
            "enabled": enabled,
            "providers": [
                {"name": "Visa", "code": "VISA"},
                {"name": "Mastercard", "code": "MASTERCARD"},
            ],
        },
        {
            "id": "QR_CODE",
            "title": "QR Code (TanQR / Masterpass)",
            "subtitle": "Scan & Pay with any Tanzanian Banking App",
            "icon": "qr_code_scanner",
            "enabled": enabled,
            "providers": [
                {"name": "TanQR (National Standard)", "code": "TANQR"},
                {"name": "Masterpass QR", "code": "MASTERPASS"},
            ],
        },
        {
            "id": "HOSTED",
            "title": "All Payment Options",
            "subtitle": "Selcom Secure Web Checkout",
            "icon": "language",
            "enabled": enabled,
        },
    ]

    return {
        "status": "success",
        "gateway_enabled": enabled,
        "currency": "TZS",
        "methods": methods,
    }


@frappe.whitelist(methods=["GET", "POST"])
def get_stored_cards():
    """Retrieve list of saved card tokens for the authenticated tenant user."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    buyer_email = frappe.db.get_value("User", frappe.session.user, "email") or frappe.session.user
    buyer_uuid = frappe.defaults.get_user_default("selcom_gateway_buyer_uuid", buyer_email) or ""
    client = SelcomClient()

    if not client.enabled:
        return {"status": "success", "cards": []}

    res = client.get("/v1/checkout/stored-cards", {"buyer_userid": buyer_email, "gateway_buyer_uuid": buyer_uuid})
    data = res.get("data") or []
    
    deleted_str = frappe.defaults.get_user_default("selcom_deleted_cards", buyer_email) or ""
    deleted_tokens = set(t.strip() for t in deleted_str.split(",") if t.strip())

    cards = []
    if isinstance(data, list):
        for c in data:
            if isinstance(c, dict):
                token = c.get("card_token") or c.get("token")
                if token and token not in deleted_tokens:
                    cards.append({
                        "id": c.get("id"),
                        "card_token": token,
                        "masked_card": c.get("masked_card") or c.get("card_number") or "****",
                        "card_brand": c.get("card_brand") or c.get("brand") or "Card",
                        "expiry": c.get("expiry") or c.get("expiry_date") or "",
                    })

    return {
        "status": "success",
        "buyer_userid": buyer_email,
        "gateway_buyer_uuid": buyer_uuid,
        "cards": cards,
    }


@frappe.whitelist(methods=["POST"])
def pay_with_stored_card(invoice_name=None, card_token=None, amount=None, cvv=None):
    """Initiate and process a payment using a previously saved/tokenized card.
    
    Args:
        invoice_name: Sales Invoice docname
        card_token: Secure card token from get_stored_cards
        amount: Optional custom amount
        cvv: Optional CVV/CVC security code
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    req = getattr(frappe, "form_dict", None) or {}
    invoice_name = (invoice_name or req.get("invoice_name") or req.get("invoice_id") or "").strip()
    card_token = (card_token or req.get("card_token") or req.get("token") or "").strip()
    amount_val = amount if amount is not None else req.get("amount")
    cvv_val = (cvv or req.get("cvv") or "").strip()

    if not invoice_name:
        return {"status": "error", "message": "invoice_name is required"}
    if not card_token:
        return {"status": "error", "message": "card_token is required"}

    inv = _check_invoice_access(invoice_name)
    outstanding = flt(inv.outstanding_amount)

    if outstanding <= 0:
        return {"status": "error", "message": f"Invoice {inv.name} is already fully paid."}

    pay_amount = flt(amount_val) if (amount_val and flt(amount_val) > 0) else outstanding
    if pay_amount <= 0 or pay_amount > outstanding:
        return {"status": "error", "message": f"Payment amount must be between 1 and {outstanding:,.2f}"}

    client = SelcomClient()
    if not client.enabled:
        return {"status": "error", "message": "Selcom payments are currently disabled."}

    rand_suffix = frappe.generate_hash(length=6).upper()
    clean_inv_id = re.sub(r"[^A-Za-z0-9]", "", inv.name)
    order_id = f"ORD-{clean_inv_id[:12]}-{rand_suffix}"

    user_info = frappe.db.get_value("User", frappe.session.user, ["full_name", "email", "mobile_no", "phone"], as_dict=True) or {}
    buyer_email = user_info.get("email") or frappe.session.user
    buyer_name = user_info.get("full_name") or inv.customer_name or "Viva Tenant"
    user_phone = user_info.get("mobile_no") or user_info.get("phone") or inv.contact_mobile or "255700000000"
    buyer_phone = _normalize_phone_number(user_phone) or "255700000000"

    # Fetch stored buyer_uuid if available
    buyer_uuid = frappe.defaults.get_user_default("selcom_gateway_buyer_uuid", buyer_email) or ""

    # Create transaction audit record
    txn = frappe.get_doc({
        "doctype": "Viva Payment Transaction",
        "order_id": order_id,
        "sales_invoice": inv.name,
        "customer": inv.customer,
        "amount": pay_amount,
        "currency": inv.currency or "TZS",
        "payment_channel": "CARD",
        "phone_number": buyer_phone,
        "status": "Pending",
        "raw_request": json.dumps({
            "order_id": order_id,
            "invoice": inv.name,
            "amount": pay_amount,
            "card_token": card_token[:6] + "..." if len(card_token) > 6 else card_token,
        }),
    })
    txn.insert(ignore_permissions=True)
    frappe.db.commit()

    # 1. Create full order for card payment
    name_parts = (buyer_name or "Viva Tenant").strip().split(" ", 1)
    first_name = name_parts[0] if name_parts else "Viva"
    last_name = name_parts[1] if len(name_parts) > 1 else "Tenant"

    site_url = (frappe.utils.get_url() or "https://dev15-viva2.vvsdtz.com").rstrip("/")
    webhook_raw = f"{site_url}/api/method/propms.api.v1.payments.selcom_ipn_webhook"
    redirect_raw = f"{site_url}/payment-success"
    cancel_raw = f"{site_url}/payment-cancel"

    webhook_b64 = base64.b64encode(webhook_raw.encode("utf-8")).decode("utf-8")
    redirect_b64 = base64.b64encode(redirect_raw.encode("utf-8")).decode("utf-8")
    cancel_b64 = base64.b64encode(cancel_raw.encode("utf-8")).decode("utf-8")

    order_payload = {
        "vendor": client.vendor_id,
        "order_id": order_id,
        "buyer_email": buyer_email,
        "buyer_name": buyer_name,
        "buyer_userid": buyer_email,
        "buyer_phone": buyer_phone,
        "gateway_buyer_uuid": buyer_uuid,
        "amount": int(round(pay_amount)),
        "currency": inv.currency or "TZS",
        "no_of_items": 1,
        "payment_methods": "CARD",
        "webhook": webhook_b64,
        "redirect_url": redirect_b64,
        "cancel_url": cancel_b64,
        "buyer_remarks": f"Invoice {inv.name}",
        "merchant_remarks": "Viva Towers Card Payment",
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
    order_res = client.post("/v1/checkout/create-order", order_payload)
    result_status = (order_res.get("result") or "").upper()
    result_code = (order_res.get("resultcode") or "").strip()

    if result_status not in ("SUCCESS", "COMPLETED", "200") and result_code not in ("000", "200"):
        error_msg = order_res.get("message") or "Failed to initialize order with Selcom"
        txn.status = "Failed"
        txn.error_message = str(error_msg)
        txn.raw_response = json.dumps(order_res)
        txn.save(ignore_permissions=True)
        frappe.db.commit()
        return {"status": "error", "message": error_msg, "order_id": order_id}

    # 2. Process card payment using token (with vendor, buyer_userid, gateway_buyer_uuid)
    card_payload = {
        "transid": f"TXN-{order_id}",
        "vendor": client.vendor_id,
        "order_id": order_id,
        "card_token": card_token,
        "buyer_userid": buyer_email,
        "gateway_buyer_uuid": buyer_uuid,
    }

    card_res = client.post("/v1/checkout/card-payment", card_payload)
    txn.raw_response = json.dumps({"order_minimal": order_res, "card_payment": card_res})

    card_result = (card_res.get("result") or "").upper()
    card_code = (card_res.get("resultcode") or "").strip()

    # Extract 3DS challenge URL if required
    gateway_url = None
    data_field = card_res.get("data")
    if isinstance(data_field, list) and len(data_field) > 0:
        gateway_url = data_field[0].get("payment_gateway_url") or data_field[0].get("form_url") or data_field[0].get("gateway_url")
    elif isinstance(data_field, dict):
        gateway_url = data_field.get("payment_gateway_url") or data_field.get("form_url") or data_field.get("gateway_url")

    if gateway_url and not str(gateway_url).startswith("http"):
        try:
            decoded = base64.b64decode(gateway_url).decode("utf-8")
            if decoded.startswith("http"):
                gateway_url = decoded
        except Exception:
            pass

    txn.gateway_url = gateway_url or ""

    if card_result in ("SUCCESS", "COMPLETED", "000") or card_code in ("000", "200"):
        if gateway_url:
            txn.save(ignore_permissions=True)
            frappe.db.commit()
            return {
                "status": "success",
                "message": "Please complete 3D-Secure authentication",
                "order_id": order_id,
                "transaction_id": txn.name,
                "invoice_name": inv.name,
                "amount": pay_amount,
                "currency": inv.currency or "TZS",
                "payment_method": "STORED_CARD",
                "gateway_url": gateway_url,
                "action": "OPEN_3DS_WEBVIEW",
            }
        else:
            # Reconcile immediately if frictionless success
            from propms.api.v1.payments.webhook import process_successful_payment
            process_successful_payment(order_id=order_id, selcom_ref=card_res.get("reference") or order_id, amount=pay_amount, raw_payload=card_res)
            txn.reload()
            return {
                "status": "success",
                "message": "Card payment processed successfully",
                "order_id": order_id,
                "transaction_id": txn.name,
                "invoice_name": inv.name,
                "amount": pay_amount,
                "currency": inv.currency or "TZS",
                "payment_method": "STORED_CARD",
                "payment_entry": txn.payment_entry,
                "action": "PAYMENT_COMPLETED",
            }
    else:
        err = card_res.get("message") or "Failed to charge stored card"
        card_code = (card_res.get("resultcode") or "").strip()
        
        # If token is invalid (801), automatically blacklist it so it won't be listed in get_stored_cards()
        if card_code == "801":
            deleted_str = frappe.defaults.get_user_default("selcom_deleted_cards", buyer_email) or ""
            deleted_tokens = set(t.strip() for t in deleted_str.split(",") if t.strip())
            deleted_tokens.add(card_token)
            frappe.defaults.set_user_default("selcom_deleted_cards", ",".join(deleted_tokens), user=buyer_email)
            frappe.db.commit()

        # Check if order_minimal created a valid checkout gateway_url as fallback
        order_gateway_url = None
        data_field = order_res.get("data")
        if isinstance(data_field, list) and len(data_field) > 0:
            order_gateway_url = data_field[0].get("payment_gateway_url") or data_field[0].get("gateway_url")
        elif isinstance(data_field, dict):
            order_gateway_url = data_field.get("payment_gateway_url") or data_field.get("gateway_url")

        if order_gateway_url and not str(order_gateway_url).startswith("http"):
            try:
                decoded = base64.b64decode(order_gateway_url).decode("utf-8")
                if decoded.startswith("http"):
                    order_gateway_url = decoded
            except Exception:
                pass

        if order_gateway_url:
            txn.gateway_url = order_gateway_url
            txn.status = "Pending"
            txn.save(ignore_permissions=True)
            frappe.db.commit()

            return {
                "status": "success",
                "message": "Card token is no longer valid. Opening card checkout...",
                "order_id": order_id,
                "transaction_id": txn.name,
                "invoice_name": inv.name,
                "amount": pay_amount,
                "currency": inv.currency or "TZS",
                "payment_method": "CARD",
                "gateway_url": order_gateway_url,
                "action": "OPEN_WEBVIEW",
            }
        else:
            txn.status = "Failed"
            txn.error_message = err
            txn.save(ignore_permissions=True)
            frappe.db.commit()
            return {
                "status": "error",
                "message": err,
                "order_id": order_id,
                "card_response": card_res,
            }


@frappe.whitelist(methods=["POST", "DELETE"])
def delete_stored_card(card_token=None, id=None):
    """Remove a previously saved card token from Selcom using resource id or token."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    req = getattr(frappe, "form_dict", None) or {}
    card_id = str(id or req.get("id") or card_token or req.get("card_token") or req.get("token") or "").strip()

    if not card_id:
        return {"status": "error", "message": "Card ID or card_token is required"}

    buyer_email = frappe.db.get_value("User", frappe.session.user, "email") or frappe.session.user
    client = SelcomClient()

    if not client.enabled:
        return {"status": "error", "message": "Selcom payments are currently disabled."}

    buyer_uuid = frappe.defaults.get_user_default("selcom_gateway_buyer_uuid", buyer_email) or ""
    res = client.delete("/v1/checkout/delete-card", {"id": card_id, "gateway_buyer_uuid": buyer_uuid})
    result_code = (res.get("resultcode") or "").strip()
    result_status = (res.get("result") or "").upper()

    if result_code in ("000", "200", "404", "801", "999") or result_status in ("SUCCESS", "COMPLETED", "AMBIGUOUS"):
        deleted_str = frappe.defaults.get_user_default("selcom_deleted_cards", buyer_email) or ""
        deleted_tokens = set(t.strip() for t in deleted_str.split(",") if t.strip())
        deleted_tokens.add(card_id)
        frappe.defaults.set_user_default("selcom_deleted_cards", ",".join(deleted_tokens), user=buyer_email)
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Card removed successfully",
            "card_id": card_id,
        }
    else:
        return {
            "status": "error",
            "message": res.get("message") or "Failed to remove card from Selcom",
            "selcom_response": res,
        }
