# -*- coding: utf-8 -*-
"""POS Store Services — catalog from Mobile App Settings + pay-first checkout.

Hub rows come from Mobile App Settings → POS Store Services (child table):
  - Item + purchase_mode=qty → product detail → checkout_pos_item (maintenance_pos)
  - Special + purchase_mode=amount (e.g. electricity) → existing amount flow (TANESCO + Generator)

No Item checkbox. Not configured on Selcom Settings.
"""

from __future__ import unicode_literals

import base64
import json
import re

import frappe
from frappe import _
from frappe.utils import flt, cint
from erpnext.utilities.product import get_price

from propms.api.v1.payments.workflows import WORKFLOW_MAINTENANCE_POS
from propms.api.v1.water.water import (
	_require_auth,
	_parse_request_payload,
	_resolve_tenant_billing,
	_get_mobile_cart_stock_context,
	_get_bin_qty,
	_is_water_item,
)
from propms.api.v1.pos_store.delivery_window import (
	serialize_delivery_window_for_api,
	validate_delivery_window,
)

SPECIAL_SERVICE_META = {
	"electricity": {
		"default_label": "Electricity",
		"api": "propms.api.mobile.get_electricity_rates",
		"checkout_api": "propms.api.mobile.checkout_electricity",
		"preview_api": "propms.api.mobile.preview_electricity_purchase",
	},
}


def _get_pos_store_service_rows(enabled_only=True):
	"""Enabled hub rows from Mobile App Settings, ordered."""
	if not frappe.db.exists("DocType", "Mobile App Settings"):
		return []
	settings = frappe.get_single("Mobile App Settings")
	rows = list(settings.get("pos_store_services") or [])
	if enabled_only:
		rows = [r for r in rows if cint(r.enabled)]
	rows.sort(key=lambda r: (cint(r.sort_order), cint(r.idx)))
	return rows


def _is_item_enabled_in_settings(item_code):
	if not item_code:
		return False
	for row in _get_pos_store_service_rows(enabled_only=True):
		if (row.service_type or "") == "Item" and (row.item or "") == item_code:
			if (row.purchase_mode or "qty") == "qty":
				return True
	return False


def _selling_rate(item_code, company, price_list, standard_rate=None):
	rate = flt(standard_rate) or 0.0
	price_rec = get_price(item_code, price_list, None, company)
	if price_rec and price_rec.get("price_list_rate"):
		rate = flt(price_rec["price_list_rate"])
	return rate


def _serialize_item(item, company, currency, price_list, warehouse, label=None):
	rate = _selling_rate(item["item_code"], company, price_list, item.get("standard_rate"))
	is_stock_item = cint(item.get("is_stock_item"))
	actual_qty = 0.0
	in_stock = True
	if is_stock_item:
		actual_qty = _get_bin_qty(item["item_code"], warehouse) if warehouse else 0.0
		in_stock = bool(warehouse) and actual_qty > 0

	display = label or item.get("item_name") or item["item_code"]
	requires = bool(_is_water_item(item["item_code"]))
	out = {
		"item_code": item["item_code"],
		"item_name": item.get("item_name") or item["item_code"],
		"label": display,
		"item_group": item.get("item_group") or "",
		"description": item.get("description") or "",
		"stock_uom": item.get("stock_uom") or "Nos",
		"rate": rate,
		"formatted_rate": f"{currency} {rate:,.2f}",
		"currency": currency,
		"image": item.get("image") or "",
		"is_stock_item": is_stock_item,
		"actual_qty": actual_qty,
		"in_stock": in_stock,
		"sold_out": is_stock_item and not in_stock,
		"warehouse": warehouse or "",
		"service_type": "Item",
		"purchase_mode": "qty",
		"requires_delivery_window": requires,
	}
	if requires:
		out["delivery_window"] = serialize_delivery_window_for_api()
	return out


def _load_item_doc_fields(item_code):
	if not item_code or not frappe.db.exists("Item", item_code):
		return None
	return frappe.db.get_value(
		"Item",
		item_code,
		[
			"name",
			"item_name",
			"item_code",
			"item_group",
			"description",
			"stock_uom",
			"image",
			"standard_rate",
			"is_stock_item",
			"disabled",
			"is_sales_item",
		],
		as_dict=True,
	)


def _get_eligible_item(item_code):
	"""Item must be enabled on Mobile App Settings (Item + qty) and sellable."""
	if not item_code or not _is_item_enabled_in_settings(item_code):
		return None
	item = _load_item_doc_fields(item_code)
	if not item:
		return None
	if cint(item.get("disabled")) or not cint(item.get("is_sales_item")):
		return None
	return item


@frappe.whitelist(methods=["GET", "POST"])
def get_pos_store_catalog():
	"""Hub catalog from Mobile App Settings → POS Store Services."""
	_require_auth()

	company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
		"Company", {}, "name"
	)
	currency = frappe.db.get_value("Company", company, "default_currency") or "TZS"
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
	stock_ctx = _get_mobile_cart_stock_context(company)
	warehouse = stock_ctx.get("warehouse")

	services = []
	items = []
	special_services = []

	for row in _get_pos_store_service_rows(enabled_only=True):
		stype = (row.service_type or "Item").strip()
		mode = (row.purchase_mode or "qty").strip()
		label = (row.label or "").strip()

		if stype == "Item":
			item = _load_item_doc_fields(row.item)
			if not item or cint(item.get("disabled")) or not cint(item.get("is_sales_item")):
				continue
			payload = _serialize_item(item, company, currency, price_list, warehouse, label=label or None)
			payload["purchase_mode"] = mode or "qty"
			payload["key"] = f"item:{item['item_code']}"
			payload["sort_order"] = cint(row.sort_order)
			services.append(payload)
			if (mode or "qty") == "qty":
				items.append(payload)

		elif stype == "Special":
			key = (row.special_key or "").strip()
			if not key:
				continue
			meta = SPECIAL_SERVICE_META.get(key) or {}
			entry = {
				"key": f"special:{key}",
				"special_key": key,
				"service_type": "Special",
				"purchase_mode": mode or "amount",
				"label": label or meta.get("default_label") or key.replace("_", " ").title(),
				"api": meta.get("api"),
				"checkout_api": meta.get("checkout_api"),
				"preview_api": meta.get("preview_api"),
				"sort_order": cint(row.sort_order),
			}
			services.append(entry)
			special_services.append(entry)

	return {
		"status": "success",
		"currency": currency,
		"count": len(services),
		"pos_profile": stock_ctx.get("pos_profile"),
		"warehouse": warehouse or "",
		"services": services,
		"items": items,
		"special_services": special_services,
		"source": "Mobile App Settings",
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_water_delivery_window():
	"""Same-day drinking-water delivery rules for the mobile picker."""
	_require_auth()
	return {
		"status": "success",
		"delivery_window": serialize_delivery_window_for_api(),
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_pos_store_item(item_code=None):
	"""Product detail for qty-mode Item services (UI option B)."""
	_require_auth()
	payload = _parse_request_payload({"item_code": item_code})
	item_code = (payload.get("item_code") or "").strip()
	if not item_code:
		return {"status": "error", "message": _("item_code is required")}

	item = _get_eligible_item(item_code)
	if not item:
		return {
			"status": "error",
			"message": _("Item not available in Mobile POS Store"),
		}

	company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
		"Company", {}, "name"
	)
	currency = frappe.db.get_value("Company", company, "default_currency") or "TZS"
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
	stock_ctx = _get_mobile_cart_stock_context(company)

	# Prefer label from settings if set
	hub_label = None
	for row in _get_pos_store_service_rows(enabled_only=True):
		if (row.service_type or "") == "Item" and row.item == item_code and row.label:
			hub_label = row.label
			break

	return {
		"status": "success",
		"item": _serialize_item(
			item, company, currency, price_list, stock_ctx.get("warehouse"), label=hub_label
		),
	}


def _prepare_pos_item_intent(item_code=None, qty=None, lease=None):
	payload = _parse_request_payload(
		{
			"item_code": item_code,
			"qty": qty,
			"quantity": qty,
			"lease": lease,
			"delivery_time_start": None,
			"delivery_time_end": None,
			"delivery_instructions": None,
			"delivery_date": None,
		}
	)
	item_code = (payload.get("item_code") or "").strip()
	qty = flt(payload.get("qty") if payload.get("qty") is not None else payload.get("quantity") or 1)
	if qty <= 0:
		frappe.throw(_("Quantity must be greater than zero"))
	if not item_code:
		frappe.throw(_("item_code is required"))

	item = _get_eligible_item(item_code)
	if not item:
		frappe.throw(_("Item not available in Mobile POS Store"))

	if cint(item.get("is_stock_item")):
		stock_ctx = _get_mobile_cart_stock_context()
		warehouse = stock_ctx.get("warehouse")
		actual = _get_bin_qty(item_code, warehouse) if warehouse else 0.0
		if not warehouse or actual < qty:
			frappe.throw(_("Insufficient stock for {0}").format(item_code))

	billing = _resolve_tenant_billing(payload.get("lease"))
	if not billing.get("customer"):
		frappe.throw(_("No customer/lease found for this user"))

	company = billing.get("company")
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
	rate = _selling_rate(item_code, company, price_list, item.get("standard_rate"))
	if rate <= 0:
		frappe.throw(_("Selling rate not configured for {0}").format(item_code))

	line_amount = flt(rate) * flt(qty)

	delivery_date = None
	delivery_time_start = None
	delivery_time_end = None
	delivery_instructions = None
	if _is_water_item(item_code):
		window = validate_delivery_window(
			start=payload.get("delivery_time_start"),
			end=payload.get("delivery_time_end"),
			delivery_date=payload.get("delivery_date"),
		)
		delivery_date = window["delivery_date"]
		delivery_time_start = window["delivery_time_start"]
		delivery_time_end = window["delivery_time_end"]
		delivery_instructions = (payload.get("delivery_instructions") or "").strip()
		if len(delivery_instructions) > 500:
			frappe.throw(_("delivery_instructions is too long (max 500 characters)"))

	return {
		"billing": billing,
		"item": item,
		"qty": qty,
		"rate": rate,
		"line_amount": line_amount,
		"price_list": price_list,
		"payment_method": (payload.get("payment_method") or payload.get("channel") or "MOBILE_MONEY"),
		"phone_number": payload.get("phone_number") or payload.get("phone") or payload.get("msisdn"),
		"amount": payload.get("amount"),
		"card_token": payload.get("card_token") or payload.get("token"),
		"cvv": payload.get("cvv"),
		"delivery_date": delivery_date,
		"delivery_time_start": delivery_time_start,
		"delivery_time_end": delivery_time_end,
		"delivery_instructions": delivery_instructions,
	}


@frappe.whitelist(methods=["POST"])
def checkout_pos_item(
	item_code=None,
	qty=None,
	quantity=None,
	lease=None,
	payment_method="MOBILE_MONEY",
	phone_number=None,
	amount=None,
	delivery_time_start=None,
	delivery_time_end=None,
	delivery_instructions=None,
	delivery_date=None,
):
	"""Start Selcom for a Maintenance POS catalog item; Paid POS SI on success."""
	_require_auth()
	from propms.api.v1.payments.checkout import start_selcom_checkout

	if payment_method:
		frappe.form_dict["payment_method"] = payment_method
	if phone_number:
		frappe.form_dict["phone_number"] = phone_number
	if amount is not None:
		frappe.form_dict["amount"] = amount
	if delivery_time_start is not None:
		frappe.form_dict["delivery_time_start"] = delivery_time_start
	if delivery_time_end is not None:
		frappe.form_dict["delivery_time_end"] = delivery_time_end
	if delivery_instructions is not None:
		frappe.form_dict["delivery_instructions"] = delivery_instructions
	if delivery_date is not None:
		frappe.form_dict["delivery_date"] = delivery_date
	if quantity is not None and qty is None:
		qty = quantity

	intent = _prepare_pos_item_intent(item_code=item_code, qty=qty, lease=lease)
	billing = intent["billing"]
	total = intent["line_amount"]
	pay_amount = flt(intent.get("amount")) if intent.get("amount") and flt(intent.get("amount")) > 0 else total
	if abs(pay_amount - total) > 0.05:
		return {
			"status": "error",
			"message": _("Payment amount must equal line total ({0})").format(total),
		}

	ref_name = billing.get("lease") or billing.get("customer") or "POS"
	extra = {
		"maintenance_pos_purchase": True,
		"payment_workflow": WORKFLOW_MAINTENANCE_POS,
		"lease": billing.get("lease"),
		"property": billing.get("property"),
		"customer": billing.get("customer"),
		"company": billing.get("company"),
		"cost_center": billing.get("cost_center"),
		"total_amount": total,
		"lines": [
			{
				"item_code": intent["item"]["item_code"],
				"qty": intent["qty"],
				"rate": intent["rate"],
				"amount": total,
			}
		],
		"price_list": intent.get("price_list"),
	}
	if intent.get("delivery_time_start") and intent.get("delivery_time_end"):
		extra["delivery_date"] = intent["delivery_date"]
		extra["delivery_time_start"] = intent["delivery_time_start"]
		extra["delivery_time_end"] = intent["delivery_time_end"]
		extra["delivery_instructions"] = intent.get("delivery_instructions") or ""

	return start_selcom_checkout(
		reference_doctype="Lease" if billing.get("lease") else "Customer",
		reference_name=ref_name,
		customer=billing["customer"],
		amount=pay_amount,
		currency="TZS",
		payment_workflow=WORKFLOW_MAINTENANCE_POS,
		payment_method=intent.get("payment_method") or "MOBILE_MONEY",
		phone_number=intent.get("phone_number"),
		buyer_remarks=f"POS Store {intent['item']['item_code']}",
		merchant_remarks="Viva Towers POS Store",
		extra_raw_request=extra,
	)


@frappe.whitelist(methods=["POST"])
def pay_pos_item_with_stored_card(
	item_code=None,
	qty=None,
	quantity=None,
	lease=None,
	card_token=None,
	amount=None,
	cvv=None,
	delivery_time_start=None,
	delivery_time_end=None,
	delivery_instructions=None,
	delivery_date=None,
):
	"""Charge saved card for Maintenance POS item; settles via maintenance_pos."""
	_require_auth()
	from propms.api.v1.payments.selcom_client import SelcomClient
	from propms.api.v1.payments.checkout import normalize_phone_number

	if quantity is not None and qty is None:
		qty = quantity
	if card_token:
		frappe.form_dict["card_token"] = card_token
	if amount is not None:
		frappe.form_dict["amount"] = amount
	if cvv is not None:
		frappe.form_dict["cvv"] = cvv
	if delivery_time_start is not None:
		frappe.form_dict["delivery_time_start"] = delivery_time_start
	if delivery_time_end is not None:
		frappe.form_dict["delivery_time_end"] = delivery_time_end
	if delivery_instructions is not None:
		frappe.form_dict["delivery_instructions"] = delivery_instructions
	if delivery_date is not None:
		frappe.form_dict["delivery_date"] = delivery_date

	intent = _prepare_pos_item_intent(item_code=item_code, qty=qty, lease=lease)
	card_token = (intent.get("card_token") or "").strip()
	if not card_token:
		return {"status": "error", "message": _("card_token is required")}

	billing = intent["billing"]
	total = intent["line_amount"]
	pay_amount = flt(intent.get("amount")) if intent.get("amount") and flt(intent.get("amount")) > 0 else total

	client = SelcomClient()
	if not client.enabled:
		return {"status": "error", "message": _("Selcom payments are currently disabled.")}

	rand_suffix = frappe.generate_hash(length=6).upper()
	clean_ref = re.sub(r"[^A-Za-z0-9]", "", (billing.get("lease") or billing["customer"] or "POS"))[:12]
	order_id = f"ORD-{clean_ref}-{rand_suffix}"

	user_info = frappe.db.get_value(
		"User", frappe.session.user, ["full_name", "email", "mobile_no", "phone"], as_dict=True
	) or {}
	buyer_email = user_info.get("email") or frappe.session.user
	buyer_name = user_info.get("full_name") or billing["customer"]
	buyer_phone = normalize_phone_number(
		user_info.get("mobile_no") or user_info.get("phone") or "255700000000"
	) or "255700000000"
	buyer_uuid = frappe.defaults.get_user_default("selcom_gateway_buyer_uuid", buyer_email) or ""

	extra = {
		"maintenance_pos_purchase": True,
		"payment_workflow": WORKFLOW_MAINTENANCE_POS,
		"lease": billing.get("lease"),
		"property": billing.get("property"),
		"customer": billing.get("customer"),
		"company": billing.get("company"),
		"cost_center": billing.get("cost_center"),
		"total_amount": total,
		"lines": [
			{
				"item_code": intent["item"]["item_code"],
				"qty": intent["qty"],
				"rate": intent["rate"],
				"amount": total,
			}
		],
		"card_token": card_token[:6] + "..." if len(card_token) > 6 else card_token,
	}
	if intent.get("delivery_time_start") and intent.get("delivery_time_end"):
		extra["delivery_date"] = intent["delivery_date"]
		extra["delivery_time_start"] = intent["delivery_time_start"]
		extra["delivery_time_end"] = intent["delivery_time_end"]
		extra["delivery_instructions"] = intent.get("delivery_instructions") or ""

	txn = frappe.get_doc(
		{
			"doctype": "Selcom Payment Transaction Log",
			"order_id": order_id,
			"payment_workflow": WORKFLOW_MAINTENANCE_POS,
			"reference_doctype": "Lease" if billing.get("lease") else "Customer",
			"reference_name": billing.get("lease") or billing["customer"],
			"customer": billing["customer"],
			"amount": pay_amount,
			"currency": "TZS",
			"payment_channel": "CARD",
			"phone_number": buyer_phone,
			"status": "Pending",
			"raw_request": json.dumps(extra),
		}
	)
	txn.insert(ignore_permissions=True)
	frappe.db.commit()

	name_parts = (buyer_name or "Viva Tenant").strip().split(" ", 1)
	first_name = name_parts[0] if name_parts else "Viva"
	last_name = name_parts[1] if len(name_parts) > 1 else "Tenant"
	site_url = (frappe.utils.get_url() or "https://dev15-viva2.vvsdtz.com").rstrip("/")
	webhook_b64 = base64.b64encode(
		f"{site_url}/api/method/propms.api.v1.payments.selcom_ipn_webhook".encode("utf-8")
	).decode("utf-8")
	redirect_b64 = base64.b64encode(f"{site_url}/payment-success".encode("utf-8")).decode("utf-8")
	cancel_b64 = base64.b64encode(f"{site_url}/payment-cancel".encode("utf-8")).decode("utf-8")

	order_payload = {
		"vendor": client.vendor_id,
		"order_id": order_id,
		"buyer_email": buyer_email,
		"buyer_name": buyer_name,
		"buyer_userid": buyer_email,
		"buyer_phone": buyer_phone,
		"gateway_buyer_uuid": buyer_uuid,
		"amount": int(round(pay_amount)),
		"currency": "TZS",
		"no_of_items": 1,
		"payment_methods": "CARD",
		"webhook": webhook_b64,
		"redirect_url": redirect_b64,
		"cancel_url": cancel_b64,
		"buyer_remarks": f"POS Store {intent['item']['item_code']}",
		"merchant_remarks": "Viva Towers POS Store Card",
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

	card_res = client.post(
		"/v1/checkout/card-payment",
		{
			"transid": f"TXN-{order_id}",
			"vendor": client.vendor_id,
			"order_id": order_id,
			"card_token": card_token,
			"buyer_userid": buyer_email,
			"gateway_buyer_uuid": buyer_uuid,
		},
	)
	txn.raw_response = json.dumps({"order_minimal": order_res, "card_payment": card_res})

	card_result = (card_res.get("result") or "").upper()
	card_code = (card_res.get("resultcode") or "").strip()
	gateway_url = None
	data_field = card_res.get("data")
	if isinstance(data_field, list) and data_field:
		gateway_url = (
			data_field[0].get("payment_gateway_url")
			or data_field[0].get("form_url")
			or data_field[0].get("gateway_url")
		)
	elif isinstance(data_field, dict):
		gateway_url = (
			data_field.get("payment_gateway_url")
			or data_field.get("form_url")
			or data_field.get("gateway_url")
		)
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
				"action": "OPEN_3DS_WEBVIEW",
				"message": "Please complete 3D-Secure authentication",
				"order_id": order_id,
				"gateway_url": gateway_url,
				"payment_workflow": WORKFLOW_MAINTENANCE_POS,
			}

		from propms.api.v1.payments.webhook import process_successful_payment

		process_successful_payment(
			order_id=order_id,
			selcom_ref=card_res.get("reference") or order_id,
			amount=pay_amount,
			raw_payload=card_res,
		)
		txn.reload()
		return {
			"status": "success",
			"action": "PAYMENT_COMPLETED",
			"message": "Card payment processed successfully",
			"order_id": order_id,
			"invoice_name": txn.sales_invoice,
			"payment_workflow": WORKFLOW_MAINTENANCE_POS,
		}

	err = card_res.get("message") or "Failed to charge stored card"
	txn.status = "Failed"
	txn.error_message = str(err)
	txn.save(ignore_permissions=True)
	frappe.db.commit()
	return {"status": "error", "message": err, "order_id": order_id, "gateway": card_res}
