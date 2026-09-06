# -*- coding: utf-8 -*-
"""Building contacts & directory service."""

from __future__ import unicode_literals
import frappe
from frappe import _


@frappe.whitelist(methods=["GET", "POST"])
def get_directory_contacts(department=None):
	"""Return active Viva Building Contacts sorted by emergency priority."""
	try:
		filters = {"is_active": 1}
		if department and department != "all":
			filters["department"] = department

		fields = [
			"name",
			"department",
			"contact_name",
			"phone_number",
			"whatsapp_number",
			"email",
			"operating_hours",
			"priority_order",
			"is_emergency",
			"is_active",
		]

		contacts = frappe.get_all(
			"Viva Building Contact",
			filters=filters,
			fields=fields,
			order_by="is_emergency desc, priority_order asc, contact_name asc",
		)

		emergency_hotlines = [c for c in contacts if c.is_emergency]
		regular_contacts = [c for c in contacts if not c.is_emergency]

		return {
			"status": "success",
			"total": len(contacts),
			"emergency_hotlines": emergency_hotlines,
			"contacts": contacts,
			"regular_contacts": regular_contacts,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_directory_contacts")
		return {"status": "error", "message": str(e)}
