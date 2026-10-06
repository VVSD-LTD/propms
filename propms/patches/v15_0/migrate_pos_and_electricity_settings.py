# -*- coding: utf-8 -*-
"""Move POS hub + water slots to POS Services Settings; seed Electricity Settings."""

from __future__ import unicode_literals

import frappe
from frappe.utils import cint


def execute():
	_migrate_pos_services()
	_migrate_electricity_settings()


def _migrate_pos_services():
	if not frappe.db.exists("DocType", "POS Services Settings"):
		return

	new = frappe.get_single("POS Services Settings")

	# Water times: prefer Singles leftovers from Mobile App Settings after field move
	for field in (
		"water_delivery_open_time",
		"water_delivery_close_time",
		"water_delivery_min_window_mins",
		"water_delivery_picker_step_mins",
	):
		val = frappe.db.sql(
			"select value from tabSingles where doctype=%s and field=%s limit 1",
			("Mobile App Settings", field),
		)
		if val and val[0][0] not in (None, ""):
			setattr(new, field, val[0][0])

	if not new.get("pos_store_services"):
		rows = frappe.get_all(
			"Mobile POS Store Service",
			filters={"parent": "Mobile App Settings", "parenttype": "Mobile App Settings"},
			fields=[
				"enabled",
				"sort_order",
				"label",
				"service_type",
				"item",
				"special_key",
				"purchase_mode",
				"idx",
			],
			order_by="idx asc",
		)
		# Also accept rows already under POS Services Settings (re-run)
		if not rows:
			rows = frappe.get_all(
				"Mobile POS Store Service",
				filters={"parent": "POS Services Settings", "parenttype": "POS Services Settings"},
				fields=[
					"enabled",
					"sort_order",
					"label",
					"service_type",
					"item",
					"special_key",
					"purchase_mode",
					"idx",
				],
				order_by="idx asc",
			)
			if rows:
				# Already migrated
				return

		for row in rows:
			new.append(
				"pos_store_services",
				{
					"enabled": cint(row.enabled),
					"sort_order": cint(row.sort_order),
					"label": row.label or "",
					"service_type": row.service_type or "Item",
					"item": row.item or "",
					"special_key": row.special_key or "",
					"purchase_mode": row.purchase_mode or "qty",
				},
			)

	new.flags.ignore_permissions = True
	new.save(ignore_permissions=True)
	frappe.db.commit()

	# Clear stale Mobile App Settings Singles keys for moved fields
	for field in (
		"water_delivery_open_time",
		"water_delivery_close_time",
		"water_delivery_min_window_mins",
		"water_delivery_picker_step_mins",
		"pos_store_services",
	):
		frappe.db.delete("Singles", {"doctype": "Mobile App Settings", "field": field})
	frappe.db.commit()


def _migrate_electricity_settings():
	if not frappe.db.exists("DocType", "Electricity Settings"):
		return

	doc = frappe.get_single("Electricity Settings")
	if doc.get("items"):
		return

	tanesco = "Electricity - TANESCO"
	generator = "Electricity - Generator"
	lease_item = "Electricity"
	if frappe.db.exists("DocType", "Afritrack Settings"):
		a = frappe.get_single("Afritrack Settings")
		if (getattr(a, "item_tanesco", None) or "").strip():
			tanesco = a.item_tanesco.strip()
		if (getattr(a, "item_generator", None) or "").strip():
			generator = a.item_generator.strip()
		if (getattr(a, "lease_item_electricity", None) or "").strip():
			lease_item = a.lease_item_electricity.strip()

	doc.lease_item = lease_item
	if frappe.db.exists("Item", tanesco):
		doc.append(
			"items",
			{
				"enabled": 1,
				"sort_order": 0,
				"item": tanesco,
				"label": "TANESCO",
				"trackspm_tariff": "t1",
			},
		)
	if frappe.db.exists("Item", generator):
		doc.append(
			"items",
			{
				"enabled": 1,
				"sort_order": 1,
				"item": generator,
				"label": "Generator",
				"trackspm_tariff": "t2",
			},
		)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	frappe.db.commit()
