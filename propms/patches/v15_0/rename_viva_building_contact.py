# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Rename DocType Viva Building Contact → Building Contact."""
	old, new = "Viva Building Contact", "Building Contact"
	if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
		frappe.rename_doc("DocType", old, new, force=True)
		frappe.clear_cache()
