# -*- coding: utf-8 -*-
"""Seed Item + Item Price for Staff ID Card fee (not POS Store)."""

from __future__ import unicode_literals

import frappe
from frappe.utils import flt


ITEM_CODE = "Staff ID Card"
DEFAULT_RATE = 20000.0


def execute():
	_ensure_item()
	_ensure_item_price()


def _ensure_item_group():
	# Prefer Services; fall back to first non-group
	if frappe.db.exists("Item Group", "Services"):
		return "Services"
	name = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
	return name or "All Item Groups"


def _ensure_item():
	if frappe.db.exists("Item", ITEM_CODE):
		return
	group = _ensure_item_group()
	doc = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": ITEM_CODE,
			"item_name": ITEM_CODE,
			"item_group": group,
			"stock_uom": "Nos",
			"is_stock_item": 0,
			"is_sales_item": 1,
			"is_purchase_item": 0,
			"include_item_in_manufacturing": 0,
			"description": "Fee for Staff ID Card application (mobile / Selcom).",
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def _ensure_item_price():
	price_list = (
		frappe.db.get_single_value("Selling Settings", "selling_price_list")
		or "Standard Selling"
	)
	if not frappe.db.exists("Price List", price_list):
		return

	existing = frappe.db.get_value(
		"Item Price",
		{"item_code": ITEM_CODE, "price_list": price_list, "selling": 1},
		"name",
	)
	if existing:
		return

	currency = frappe.db.get_value("Price List", price_list, "currency") or "TZS"
	ip = frappe.get_doc(
		{
			"doctype": "Item Price",
			"item_code": ITEM_CODE,
			"price_list": price_list,
			"selling": 1,
			"buying": 0,
			"price_list_rate": flt(DEFAULT_RATE),
			"currency": currency,
		}
	)
	ip.flags.ignore_permissions = True
	ip.insert(ignore_permissions=True)
