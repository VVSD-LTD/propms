# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Rename DocType Viva Amenity → Amenity."""
	old, new = "Viva Amenity", "Amenity"
	if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
		frappe.rename_doc("DocType", old, new, force=True)
		frappe.clear_cache()
