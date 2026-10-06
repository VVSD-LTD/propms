# -*- coding: utf-8 -*-
"""POS Store Services — catalog from POS Services Settings + pay-first checkout.

Hub rows (POS Services Settings → POS Store Services):
  - Item + qty → Maintenance POS product checkout
  - Amount + POS Amount Service → amount split catalog
      · Service type Electricity → electricity APIs / TrackSPM meter top-up
      · Service type Other → items on that POS Amount Service (e.g. Cooking Gas)
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

ELECTRICITY_HANDLER_META = {
	"default_label": "Electricity",
	"api": "propms.api.mobile.get_amount_service_rates",
	"checkout_api": "propms.api.mobile.checkout_amount_service",
	"preview_api": "propms.api.mobile.preview_amount_service",
	# Legacy electricity-only methods (meter status / older app builds)
	"legacy_api": "propms.api.mobile.get_electricity_rates",
	"legacy_checkout_api": "propms.api.mobile.checkout_electricity",
	"legacy_preview_api": "propms.api.mobile.preview_electricity_purchase",
	"meter_status_api": "propms.api.mobile.get_electricity_meter_status",
}

OTHER_AMOUNT_HANDLER_META = {
	"api": "propms.api.mobile.get_amount_service_rates",
	"checkout_api": "propms.api.mobile.checkout_amount_service",
	"preview_api": "propms.api.mobile.preview_amount_service",
}

ALLOCATION_TOLERANCE = 1.0  # TZS


def _slug_key(title):
	raw = re.sub(r"[^a-z0-9]+", "_", (title or "").strip().lower()).strip("_")
	return raw or "amount"


def _amount_items_from_service(svc, company=None, price_list=None):
	"""Catalog lines for an Amount service, optionally with selling rates."""
	rows = sorted(
		[r for r in (svc.get("items") or []) if cint(r.enabled) and (r.item or "").strip()],
		key=lambda r: (cint(r.sort_order), cint(r.idx)),
	)
	if company is None:
		company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
			"Company", {}, "name"
		)
	if price_list is None:
		price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"

	out = []
	for r in rows:
		item_code = (r.item or "").strip()
		rate = _selling_rate(item_code, company, price_list)
		out.append(
			{
				"item_code": item_code,
				"label": (r.label or "").strip() or item_code,
				"trackspm_tariff": (r.trackspm_tariff or "").strip() or None,
				"sort_order": cint(r.sort_order),
				"rate": flt(rate, 4),
				"uom": "Nos",
			}
		)
	return out


def _get_amount_service_doc(amount_service):
	name = (amount_service or "").strip()
	if not name or not frappe.db.exists("POS Amount Service", name):
		frappe.throw(_("POS Amount Service {0} not found").format(name or "—"))
	svc = frappe.get_doc("POS Amount Service", name)
	if not cint(svc.enabled):
		frappe.throw(_("POS Amount Service {0} is disabled").format(name))
	return svc


def _normalize_handler(svc):
	handler = (svc.handler or "Other").strip()
	if handler == "Generic":
		handler = "Other"
	return handler


def _amount_hub_entry(row, company=None, price_list=None):
	"""Build hub entry for Service Type = Amount (Electricity or Other)."""
	svc_name = (getattr(row, "amount_service", None) or "").strip()
	label = (row.label or "").strip() or None
	sort_order = cint(row.sort_order)

	# Legacy Electricity / Special rows without amount_service yet
	if not svc_name and _is_legacy_electricity_row(row):
		svc_name = "Electricity"

	if not svc_name or not frappe.db.exists("POS Amount Service", svc_name):
		return None

	svc = frappe.get_cached_doc("POS Amount Service", svc_name)
	if not cint(svc.enabled):
		return None

	handler = _normalize_handler(svc)
	title = (svc.title or svc_name).strip()
	hub_label = label or title
	amount_items = _amount_items_from_service(svc, company=company, price_list=price_list)
	if not amount_items and handler != "Electricity":
		return None

	if handler == "Electricity":
		from propms.api.v1.electricity.electricity import get_electricity_catalog

		catalog = get_electricity_catalog()
		meta = ELECTRICITY_HANDLER_META
		# Prefer live catalog lines (with rates) when available
		if not amount_items:
			amount_items = catalog.get("items") or []
		return {
			"key": "electricity",
			"special_key": "electricity",
			"service_type": "Amount",
			"ui_mode": "amount_split",
			"purchase_mode": "amount",
			"amount_handler": "Electricity",
			"amount_service": svc_name,
			"label": hub_label or meta["default_label"],
			"api": meta["api"],
			"checkout_api": meta["checkout_api"],
			"preview_api": meta["preview_api"],
			"legacy_api": meta["legacy_api"],
			"legacy_checkout_api": meta["legacy_checkout_api"],
			"legacy_preview_api": meta["legacy_preview_api"],
			"meter_status_api": meta["meter_status_api"],
			"sort_order": sort_order,
			"lease_item": catalog.get("lease_item") or "Electricity",
			"amount_items": amount_items,
			"electricity_items": amount_items,
			"item_tanesco": catalog.get("item_tanesco"),
			"item_generator": catalog.get("item_generator"),
			"catalog_source": catalog.get("source"),
			"features": {
				"meter": True,
				"meter_status": True,
				"trackspm_topup": True,
				"requires_lease": True,
			},
		}

	meta = OTHER_AMOUNT_HANDLER_META
	key = _slug_key(title)
	return {
		"key": key,
		"special_key": key,
		"service_type": "Amount",
		"ui_mode": "amount_split",
		"purchase_mode": "amount",
		"amount_handler": "Other",
		"amount_service": svc_name,
		"label": hub_label,
		"api": meta["api"],
		"checkout_api": meta["checkout_api"],
		"preview_api": meta["preview_api"],
		"sort_order": sort_order,
		"lease_item": title,
		"amount_items": amount_items,
		"features": {
			"meter": False,
			"meter_status": False,
			"trackspm_topup": False,
			"requires_lease": True,
		},
	}


def _is_legacy_electricity_row(row):
	stype = (row.service_type or "").strip()
	if stype == "Electricity":
		return True
	if stype == "Special" and (getattr(row, "special_key", None) or "").strip().lower() == "electricity":
		return True
	return False


def _is_amount_row(row):
	stype = (row.service_type or "").strip()
	if stype == "Amount":
		return True
	return _is_legacy_electricity_row(row)



def _get_pos_store_service_rows(enabled_only=True):
	"""Enabled hub rows from POS Services Settings (fallback: Mobile App Settings)."""
	settings = None
	if frappe.db.exists("DocType", "POS Services Settings"):
		settings = frappe.get_single("POS Services Settings")
	elif frappe.db.exists("DocType", "Mobile App Settings"):
		# Legacy until migrate patch moves rows
		settings = frappe.get_single("Mobile App Settings")
	if not settings:
		return []
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
	"""Item must be enabled on POS Services Settings (Item + qty) and sellable."""
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
	"""Hub catalog from POS Services Settings → POS Store Services."""
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

		if _is_amount_row(row):
			entry = _amount_hub_entry(row, company=company, price_list=price_list)
			if entry:
				services.append(entry)
				special_services.append(entry)
			continue

		if stype == "Item":
			item = _load_item_doc_fields(row.item)
			if not item or cint(item.get("disabled")) or not cint(item.get("is_sales_item")):
				continue
			payload = _serialize_item(item, company, currency, price_list, warehouse, label=label or None)
			payload["service_type"] = "Item"
			payload["ui_mode"] = "qty"
			payload["purchase_mode"] = mode or "qty"
			payload["key"] = f"item:{item['item_code']}"
			payload["sort_order"] = cint(row.sort_order)
			payload["detail_api"] = "propms.api.mobile.get_pos_store_item"
			payload["checkout_api"] = "propms.api.mobile.checkout_pos_item"
			payload["features"] = {
				"meter": False,
				"meter_status": False,
				"trackspm_topup": False,
				"requires_lease": True,
				"delivery_window": bool(_is_water_item(item["item_code"])),
			}
			services.append(payload)
			if (mode or "qty") == "qty":
				items.append(payload)

	return {
		"status": "success",
		"currency": currency,
		"count": len(services),
		"pos_profile": stock_ctx.get("pos_profile"),
		"warehouse": warehouse or "",
		"services": services,
		"items": items,
		"special_services": special_services,
		"amount_services": [s for s in services if s.get("service_type") == "Amount"],
		"item_services": [s for s in services if s.get("service_type") == "Item"],
		"source": "POS Services Settings",
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


# -------------------------------------------------------------------------
# Amount services (Electricity / Cooking Gas / …) — pay-by-amount split UI
# -------------------------------------------------------------------------


def _units_from_amount(inclusive_amount, rate):
	rate = flt(rate)
	if rate <= 0:
		frappe.throw(_("Selling rate must be greater than zero"))
	return flt(inclusive_amount) / rate


def _parse_allocations(payload, allowed_codes):
	"""Parse allocations from list or {item_code: amount} map."""
	raw = payload.get("allocations") or payload.get("lines") or payload.get("amounts")
	out = []
	if isinstance(raw, dict):
		for code, amt in raw.items():
			code = (code or "").strip()
			if not code:
				continue
			out.append({"item_code": code, "amount": flt(amt)})
	elif isinstance(raw, (list, tuple)):
		for row in raw:
			if not isinstance(row, dict):
				continue
			code = (row.get("item_code") or row.get("item") or "").strip()
			if not code:
				continue
			out.append({"item_code": code, "amount": flt(row.get("amount") or row.get("amount_inclusive"))})
	else:
		# Electricity-style convenience keys when amount_service is Electricity
		for key in ("tanesco_amount", "generator_amount"):
			if payload.get(key) is None:
				continue
			# Resolve via catalog later — handled by caller
			pass

	filtered = []
	for row in out:
		if row["item_code"] not in allowed_codes:
			frappe.throw(
				_("Item {0} is not in this amount service catalog").format(row["item_code"])
			)
		if flt(row["amount"]) < 0:
			frappe.throw(_("Allocation amounts cannot be negative"))
		if flt(row["amount"]) > 0:
			filtered.append(row)
	return filtered


def _build_amount_service_lines(svc, allocations, company, price_list):
	allowed = {
		(r.item or "").strip()
		for r in (svc.get("items") or [])
		if cint(r.enabled) and (r.item or "").strip()
	}
	lines = []
	total = 0.0
	for row in allocations:
		code = row["item_code"]
		amt = flt(row["amount"])
		if amt <= 0:
			continue
		rate = _selling_rate(code, company, price_list)
		if rate <= 0:
			frappe.throw(_("Selling rate not configured for {0}").format(code))
		lines.append(
			{
				"item_code": code,
				"amount_inclusive": amt,
				"rate": rate,
				"qty": _units_from_amount(amt, rate),
			}
		)
		total += amt
	if not lines:
		frappe.throw(_("Add at least one allocation amount greater than zero"))
	return lines, flt(total, 2)


def _prepare_amount_service_intent(amount_service=None, total_amount=None, allocations=None, lease=None):
	payload = _parse_request_payload(
		{
			"amount_service": amount_service,
			"total_amount": total_amount,
			"allocations": allocations,
			"lease": lease,
			"tanesco_amount": None,
			"generator_amount": None,
		}
	)
	svc = _get_amount_service_doc(payload.get("amount_service"))
	handler = _normalize_handler(svc)
	title = (svc.title or svc.name).strip()

	billing = _resolve_tenant_billing((payload.get("lease") or "").strip() or None)
	if not billing.get("customer"):
		frappe.throw(_("No customer/lease found for this user"))

	company = billing.get("company")
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
	allowed = {
		(r.item or "").strip()
		for r in (svc.get("items") or [])
		if cint(r.enabled) and (r.item or "").strip()
	}
	if not allowed:
		frappe.throw(_("POS Amount Service {0} has no enabled items").format(svc.name))

	alloc = _parse_allocations(payload, allowed)

	# Electricity convenience: tanesco_amount / generator_amount
	if handler == "Electricity" and not alloc:
		from propms.api.v1.electricity.electricity import get_electricity_catalog

		catalog = get_electricity_catalog()
		for code, key in (
			(catalog["item_tanesco"], "tanesco_amount"),
			(catalog["item_generator"], "generator_amount"),
		):
			amt = flt(payload.get(key))
			if amt > 0:
				alloc.append({"item_code": code, "amount": amt})

	lines, split_total = _build_amount_service_lines(svc, alloc, company, price_list)
	total = flt(payload.get("total_amount"))
	if total <= 0:
		total = split_total
	if abs(split_total - total) > ALLOCATION_TOLERANCE:
		frappe.throw(
			_("Allocation amounts ({0}) must equal total_amount ({1})").format(split_total, total)
		)

	meter_number = None
	if handler == "Electricity":
		from propms.api.v1.electricity.electricity import resolve_electricity_meter

		try:
			meter_number = resolve_electricity_meter(billing.get("property"))
		except Exception:
			frappe.throw(_("Could not resolve electricity meter for this lease"))

	return {
		"svc": svc,
		"handler": handler,
		"title": title,
		"billing": billing,
		"lines": lines,
		"total": total,
		"meter_number": meter_number,
		"price_list": price_list,
		"payment_method": (payload.get("payment_method") or payload.get("channel") or "MOBILE_MONEY"),
		"phone_number": payload.get("phone_number") or payload.get("phone") or payload.get("msisdn"),
		"amount": payload.get("amount"),
		"card_token": payload.get("card_token") or payload.get("token"),
		"cvv": payload.get("cvv"),
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_amount_service_rates(amount_service=None, lease=None):
	"""Rates + catalog lines for any POS Amount Service (Electricity, Cooking Gas, …)."""
	_require_auth()
	payload = _parse_request_payload({"amount_service": amount_service, "lease": lease})
	svc = _get_amount_service_doc(payload.get("amount_service"))
	handler = _normalize_handler(svc)
	title = (svc.title or svc.name).strip()

	billing = _resolve_tenant_billing((payload.get("lease") or "").strip() or None)
	company = (billing.get("company") if billing else None) or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	) or frappe.db.get_value("Company", {}, "name")
	currency = frappe.db.get_value("Company", company, "default_currency") or "TZS"
	price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
	items = _amount_items_from_service(svc, company=company, price_list=price_list)

	meter = None
	meter_error = None
	if handler == "Electricity" and billing and billing.get("property"):
		try:
			from propms.api.v1.electricity.electricity import resolve_electricity_meter

			meter = {
				"meter_number": resolve_electricity_meter(billing["property"]),
				"property": billing["property"],
				"lease": billing.get("lease"),
			}
		except Exception as e:
			meter_error = str(e)

	return {
		"status": "success",
		"amount_service": svc.name,
		"amount_handler": handler,
		"label": title,
		"ui_mode": "amount_split",
		"currency": currency,
		"price_list": price_list,
		"lease_item": "Electricity" if handler == "Electricity" else title,
		"amount_items": items,
		"items": [
			{"item_code": i["item_code"], "item_name": i["label"], "rate": i["rate"], "uom": i["uom"]}
			for i in items
		],
		"meter": meter,
		"meter_warning": meter_error,
		"features": {
			"meter": handler == "Electricity",
			"meter_status": handler == "Electricity",
			"trackspm_topup": handler == "Electricity",
			"requires_lease": True,
		},
		"meter_status_api": (
			"propms.api.mobile.get_electricity_meter_status" if handler == "Electricity" else None
		),
	}


@frappe.whitelist(methods=["GET", "POST"])
def preview_amount_service(
	amount_service=None, total_amount=None, allocations=None, lease=None
):
	"""Preview line split for an amount service purchase (no payment)."""
	_require_auth()
	intent = _prepare_amount_service_intent(
		amount_service=amount_service,
		total_amount=total_amount,
		allocations=allocations,
		lease=lease,
	)
	return {
		"status": "success",
		"amount_service": intent["svc"].name,
		"amount_handler": intent["handler"],
		"label": intent["title"],
		"total_amount": intent["total"],
		"meter_number": intent.get("meter_number"),
		"currency": "TZS",
		"lines": [
			{
				"item_code": ln["item_code"],
				"qty": flt(ln["qty"], 6),
				"rate": flt(ln["rate"], 4),
				"amount": flt(ln["amount_inclusive"], 2),
			}
			for ln in intent["lines"]
		],
	}


@frappe.whitelist(methods=["POST"])
def checkout_amount_service(
	amount_service=None,
	total_amount=None,
	allocations=None,
	lease=None,
	payment_method="MOBILE_MONEY",
	phone_number=None,
	amount=None,
	tanesco_amount=None,
	generator_amount=None,
):
	"""Start Selcom for any Amount service; Paid POS SI on success.

	For Electricity, also accepts tanesco_amount / generator_amount (legacy).
	For Cooking Gas / Other, pass allocations=[{item_code, amount}, ...].
	"""
	_require_auth()
	from propms.api.v1.payments.checkout import start_selcom_checkout
	from propms.api.v1.payments.workflows import WORKFLOW_AMOUNT_POS, WORKFLOW_ELECTRICITY_POS

	if payment_method:
		frappe.form_dict["payment_method"] = payment_method
	if phone_number:
		frappe.form_dict["phone_number"] = phone_number
	if amount is not None:
		frappe.form_dict["amount"] = amount
	if tanesco_amount is not None:
		frappe.form_dict["tanesco_amount"] = tanesco_amount
	if generator_amount is not None:
		frappe.form_dict["generator_amount"] = generator_amount
	if allocations is not None:
		frappe.form_dict["allocations"] = allocations

	intent = _prepare_amount_service_intent(
		amount_service=amount_service,
		total_amount=total_amount,
		allocations=allocations,
		lease=lease,
	)
	# Electricity with TrackSPM: keep dedicated settle path
	if intent["handler"] == "Electricity":
		workflow = WORKFLOW_ELECTRICITY_POS
	else:
		workflow = WORKFLOW_AMOUNT_POS

	billing = intent["billing"]
	total = intent["total"]
	pay_amount = (
		flt(intent.get("amount")) if intent.get("amount") and flt(intent.get("amount")) > 0 else total
	)
	if abs(pay_amount - total) > ALLOCATION_TOLERANCE:
		return {
			"status": "error",
			"message": _("Payment amount must equal total_amount ({0})").format(total),
		}

	ref_name = billing.get("lease") or billing.get("customer") or intent["title"]
	extra = {
		"amount_service_purchase": True,
		"payment_workflow": workflow,
		"amount_service": intent["svc"].name,
		"amount_handler": intent["handler"],
		"lease": billing.get("lease"),
		"property": billing.get("property"),
		"customer": billing.get("customer"),
		"company": billing.get("company"),
		"cost_center": billing.get("cost_center"),
		"meter_number": intent.get("meter_number"),
		"total_amount": total,
		"lease_item": "Electricity" if intent["handler"] == "Electricity" else intent["title"],
		"lines": [
			{
				"item_code": ln["item_code"],
				"qty": flt(ln["qty"]),
				"rate": flt(ln["rate"]),
				"amount_inclusive": flt(ln["amount_inclusive"]),
			}
			for ln in intent["lines"]
		],
		"price_list": intent.get("price_list"),
	}
	# Electricity settle still reads these
	if intent["handler"] == "Electricity":
		from propms.api.v1.electricity.electricity import get_electricity_catalog

		catalog = get_electricity_catalog()
		extra["electricity_purchase"] = True
		by_code = {ln["item_code"]: flt(ln["amount_inclusive"]) for ln in intent["lines"]}
		extra["tanesco_amount"] = by_code.get(catalog["item_tanesco"], 0)
		extra["generator_amount"] = by_code.get(catalog["item_generator"], 0)

	return start_selcom_checkout(
		reference_doctype="Lease" if billing.get("lease") else "Customer",
		reference_name=ref_name,
		customer=billing["customer"],
		amount=pay_amount,
		currency="TZS",
		payment_workflow=workflow,
		payment_method=intent.get("payment_method") or "MOBILE_MONEY",
		phone_number=intent.get("phone_number"),
		buyer_remarks="{0} purchase".format(intent["title"]),
		merchant_remarks="Viva Towers {0}".format(intent["title"]),
		extra_raw_request=extra,
	)
