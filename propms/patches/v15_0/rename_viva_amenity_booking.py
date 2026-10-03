# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Rename DocType Viva Amenity Booking → Amenity Booking."""
	old, new = "Viva Amenity Booking", "Amenity Booking"
	if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
		frappe.rename_doc("DocType", old, new, force=True)
		frappe.clear_cache()
