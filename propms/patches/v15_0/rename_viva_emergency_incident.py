# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Rename DocType Viva Emergency Incident → Emergency Incident."""
	old, new = "Viva Emergency Incident", "Emergency Incident"
	if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
		frappe.rename_doc("DocType", old, new, force=True)
		frappe.clear_cache()
