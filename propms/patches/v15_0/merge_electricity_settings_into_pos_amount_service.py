# -*- coding: utf-8 -*-
"""Merge Electricity Settings into POS Amount Service (Electricity) and retire Single."""

from __future__ import unicode_literals

import frappe
from frappe.utils import cint


def execute():
	if not frappe.db.exists("DocType", "POS Amount Service"):
		return

	_upsert_electricity_amount_service()
	_retire_electricity_settings_workspace()


def _upsert_electricity_amount_service():
	rows = []

	# Prefer existing Electricity Settings if still present
	if frappe.db.exists("DocType", "Electricity Settings") and frappe.db.exists(
		"Singles", {"doctype": "Electricity Settings"}
	):
		try:
			old = frappe.get_single("Electricity Settings")
			for r in old.get("items") or []:
				if not cint(r.enabled) or not (r.item or "").strip():
					continue
				tariff = (r.trackspm_tariff or "").strip()
				if tariff not in ("t1", "t2"):
					# Infer from label/item name
					name = ((r.label or "") + " " + (r.item or "")).lower()
					if "gen" in name:
						tariff = "t2"
					else:
						tariff = "t1"
				rows.append(
					{
						"enabled": 1,
						"sort_order": cint(r.sort_order),
						"item": r.item.strip(),
						"label": (r.label or "").strip() or r.item.strip(),
						"trackspm_tariff": tariff,
					}
				)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "merge Electricity Settings read")

	if not rows:
		# Defaults if Items exist
		for item, label, tariff, sort in (
			("Electricity - TANESCO", "TANESCO", "t1", 0),
			("Electricity - Generator", "Generator", "t2", 1),
		):
			if frappe.db.exists("Item", item):
				rows.append(
					{
						"enabled": 1,
						"sort_order": sort,
						"item": item,
						"label": label,
						"trackspm_tariff": tariff,
					}
				)

	if frappe.db.exists("POS Amount Service", "Electricity"):
		doc = frappe.get_doc("POS Amount Service", "Electricity")
	else:
		doc = frappe.get_doc(
			{
				"doctype": "POS Amount Service",
				"title": "Electricity",
				"handler": "Electricity",
				"enabled": 1,
			}
		)

	doc.handler = "Electricity"
	doc.enabled = 1
	if not doc.get("items") and rows:
		doc.set("items", [])
		for r in rows:
			doc.append("items", r)
	elif not doc.get("items"):
		# leave validate to fail loudly if empty — seed at least defaults if items exist
		pass

	doc.flags.ignore_permissions = True
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	frappe.db.commit()


def _retire_electricity_settings_workspace():
	# Soft-retire: clear Singles so Desk doesn't use stale values
	if frappe.db.exists("DocType", "Electricity Settings"):
		frappe.db.sql("delete from tabSingles where doctype=%s", ("Electricity Settings",))
		# Child table rows for the Single
		if frappe.db.exists("DocType", "Electricity Settings Item"):
			frappe.db.sql(
				"delete from `tabElectricity Settings Item` where parent=%s",
				("Electricity Settings",),
			)
		frappe.db.commit()
