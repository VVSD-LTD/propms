# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Rename DocType Payment Transaction → Selcom Payment Transaction Log."""
	old, new = "Payment Transaction", "Selcom Payment Transaction Log"
	if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
		frappe.rename_doc("DocType", old, new, force=True)
		frappe.clear_cache()
