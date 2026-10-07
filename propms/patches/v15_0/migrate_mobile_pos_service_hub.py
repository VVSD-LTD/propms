# -*- coding: utf-8 -*-
"""Migrate POS Services Settings hub + water windows into Mobile POS Service rows."""

from __future__ import unicode_literals

import frappe
from frappe.utils import cint, get_time


DOCTYPE = "Mobile POS Service"
CHILD = "Mobile POS Service Item"
SETTINGS = "POS Services Settings"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	_ensure_amount_service_defaults()
	_migrate_qty_rows_from_hub()
	_retire_settings_hub()
	frappe.clear_cache(doctype=DOCTYPE)


def _ensure_amount_service_defaults():
	"""Existing amount services get purchase_mode=amount and sort_order if missing."""
	if not frappe.db.has_column(DOCTYPE, "purchase_mode"):
		return

	names = frappe.get_all(DOCTYPE, pluck="name")
	for name in names:
		doc = frappe.get_doc(DOCTYPE, name)
		changed = False
		mode = (doc.purchase_mode or "").strip() or "amount"
		if not (doc.purchase_mode or "").strip():
			doc.purchase_mode = "amount"
			mode = "amount"
			changed = True
		if mode == "amount":
			if not (doc.handler or "").strip():
				doc.handler = "Other"
				changed = True
			if doc.item:
				doc.item = None
				changed = True
			if cint(doc.requires_delivery_window) or doc.delivery_open_time or doc.delivery_close_time:
				doc.requires_delivery_window = 0
				doc.delivery_open_time = None
				doc.delivery_close_time = None
				changed = True
		elif mode == "qty":
			if doc.handler:
				doc.handler = None
				changed = True
			if doc.get("items"):
				doc.set("items", [])
				changed = True
		if changed:
			doc.flags.ignore_validate = True
			doc.save(ignore_permissions=True)


def _migrate_qty_rows_from_hub():
	if not frappe.db.exists("DocType", SETTINGS):
		return
	if not frappe.db.exists("DocType", "Mobile POS Store Service"):
		return

	settings = frappe.get_single(SETTINGS)
	open_t = getattr(settings, "water_delivery_open_time", None) or "08:00:00"
	close_t = getattr(settings, "water_delivery_close_time", None) or "18:00:00"

	rows = list(settings.get("pos_store_services") or [])
	rows.sort(key=lambda r: (cint(r.sort_order), cint(r.idx)))

	for row in rows:
		stype = (row.service_type or "").strip()
		mode = (row.purchase_mode or "qty").strip()
		if stype != "Item" or mode != "qty":
			# Amount hub rows already point at renamed Mobile POS Service docs
			_apply_hub_sort_to_amount_service(row)
			continue

		item_code = (row.item or "").strip()
		if not item_code or not frappe.db.exists("Item", item_code):
			continue

		title = (row.label or "").strip() or item_code
		# Prefer stable name = title; avoid colliding with amount services
		existing = frappe.db.get_value(DOCTYPE, {"item": item_code, "purchase_mode": "qty"}, "name")
		if not existing and frappe.db.exists(DOCTYPE, title):
			# Title taken by amount service — append Item suffix only if different doc
			existing_doc = frappe.get_doc(DOCTYPE, title)
			if (existing_doc.purchase_mode or "amount") == "qty" and existing_doc.item == item_code:
				existing = title
			else:
				title = "{0} (Item)".format(title)

		requires = _item_needs_delivery(item_code)

		if existing:
			doc = frappe.get_doc(DOCTYPE, existing)
		else:
			doc = frappe.get_doc(
				{
					"doctype": DOCTYPE,
					"title": title,
					"enabled": cint(row.enabled),
					"purchase_mode": "qty",
					"item": item_code,
					"sort_order": cint(row.sort_order),
					"requires_delivery_window": 1 if requires else 0,
				}
			)

		doc.enabled = cint(row.enabled)
		doc.purchase_mode = "qty"
		doc.item = item_code
		doc.handler = None
		doc.set("items", [])
		doc.sort_order = cint(row.sort_order)
		doc.requires_delivery_window = 1 if requires else 0
		if requires:
			doc.delivery_open_time = str(get_time(open_t))
			doc.delivery_close_time = str(get_time(close_t))
		else:
			doc.delivery_open_time = None
			doc.delivery_close_time = None

		doc.flags.ignore_validate = True
		if existing:
			doc.save(ignore_permissions=True)
		else:
			doc.insert(ignore_permissions=True)


def _apply_hub_sort_to_amount_service(row):
	svc_name = (getattr(row, "amount_service", None) or "").strip()
	if not svc_name and (row.service_type or "") in ("Electricity", "Special"):
		svc_name = "Electricity"
	if not svc_name or not frappe.db.exists(DOCTYPE, svc_name):
		return
	sort_order = cint(row.sort_order)
	current = cint(frappe.db.get_value(DOCTYPE, svc_name, "sort_order") or 0)
	# Only set if still default 0 and hub has a value, or always prefer hub order
	if sort_order or current == 0:
		frappe.db.set_value(DOCTYPE, svc_name, "sort_order", sort_order, update_modified=False)


def _item_needs_delivery(item_code):
	"""Heuristic for drinking water — same idea as water._is_water_item."""
	item_group = (frappe.db.get_value("Item", item_code, "item_group") or "").lower()
	code = (item_code or "").lower()
	name = (frappe.db.get_value("Item", item_code, "item_name") or "").lower()
	if "water" in item_group or "water" in code or "water" in name:
		return True
	return False


def _retire_settings_hub():
	"""Clear hub table so Desk no longer maintains a duplicate catalog."""
	if not frappe.db.exists("DocType", SETTINGS):
		return
	try:
		settings = frappe.get_single(SETTINGS)
		if settings.get("pos_store_services"):
			settings.set("pos_store_services", [])
			settings.flags.ignore_validate = True
			settings.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "migrate_mobile_pos_service_hub retire settings")
