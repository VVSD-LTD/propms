# -*- coding: utf-8 -*-
"""ID Card fee — ERPNext Item + Item Price from Mobile App Settings (not POS Store)."""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import flt

# Fallback when Mobile App Settings.id_card_fee_item is empty
DEFAULT_FEE_ITEM = "Staff ID Card"
DEFAULT_FEE_TZS = 20000.0

# Backward-compat alias for imports
STAFF_ID_FEE_ITEM = DEFAULT_FEE_ITEM


def get_fee_item_code():
	"""Item code for ID Card fee — configured on Mobile App Settings."""
	item = None
	try:
		if frappe.db.exists("DocType", "Mobile App Settings"):
			item = frappe.db.get_single_value("Mobile App Settings", "id_card_fee_item")
	except Exception:
		item = None
	item = (item or "").strip()
	return item or DEFAULT_FEE_ITEM


def get_selling_price_list():
	return (
		frappe.db.get_single_value("Selling Settings", "selling_price_list")
		or "Standard Selling"
	)


def get_staff_id_fee_amount(price_list=None):
	"""Return selling rate for ID Card fee item (TZS)."""
	item_code = get_fee_item_code()
	if not frappe.db.exists("Item", item_code):
		frappe.throw(
			_("ID Card fee Item '{0}' is not configured. Set it under Mobile App Settings → ID Card.").format(
				item_code
			),
			title=_("ID Card Fee Missing"),
		)

	price_list = price_list or get_selling_price_list()
	try:
		from erpnext.utilities.product import get_price

		company = frappe.db.get_single_value("Global Defaults", "default_company")
		info = get_price(item_code, price_list, customer=None, company=company) or {}
		rate = flt(info.get("price_list_rate"))
		if rate > 0:
			return rate, price_list
	except Exception:
		pass

	rows = frappe.db.sql(
		"""
		SELECT price_list_rate
		FROM `tabItem Price`
		WHERE item_code = %s
		  AND selling = 1
		  AND price_list = %s
		  AND (valid_from IS NULL OR valid_from <= CURDATE())
		  AND (valid_upto IS NULL OR valid_upto >= CURDATE())
		ORDER BY IFNULL(valid_from, '0001-01-01') DESC, modified DESC
		LIMIT 1
		""",
		(item_code, price_list),
		as_dict=True,
	)
	if rows and flt(rows[0].price_list_rate) > 0:
		return flt(rows[0].price_list_rate), price_list

	# Fallback if Item exists but price not set yet
	return flt(DEFAULT_FEE_TZS), price_list


def fee_payload():
	amount, price_list = get_staff_id_fee_amount()
	item_code = get_fee_item_code()
	item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code
	return {
		"payment_required": 1,
		"fee_item": item_code,
		"fee_item_name": item_name,
		"fee_amount": amount,
		"currency": "TZS",
		"price_list": price_list,
	}
