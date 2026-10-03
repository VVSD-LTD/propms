# -*- coding: utf-8 -*-
"""Seed / refresh default Viva Directory contacts (one per department)."""

from __future__ import unicode_literals

import frappe

DEFAULT_CONTACTS = [
	{
		"department": "Management Office",
		"contact_name": "Viva Towers Estate Management",
		"phone_number": "+255722444555",
		"whatsapp_number": "+255722444555",
		"email": "management@vivatowers.com",
		"operating_hours": "Mon - Fri: 08:00 AM - 05:00 PM",
		"priority_order": 1,
		"is_emergency": 0,
	},
	{
		"department": "Reception",
		"contact_name": "Tower 1 Main Reception",
		"phone_number": "+255711222333",
		"whatsapp_number": "+255711222333",
		"email": "reception@vivatowers.com",
		"operating_hours": "24/7 Front Desk",
		"priority_order": 2,
		"is_emergency": 0,
	},
	{
		"department": "Security",
		"contact_name": "Main Gate & Security Control",
		"phone_number": "+255711222001",
		"whatsapp_number": "+255711222001",
		"email": "",
		"operating_hours": "24/7",
		"priority_order": 3,
		"is_emergency": 1,
	},
	{
		"department": "Maintenance",
		"contact_name": "Facilities & MEP Desk",
		"phone_number": "+255733555777",
		"whatsapp_number": "+255733555777",
		"email": "",
		"operating_hours": "24/7 On-Call",
		"priority_order": 4,
		"is_emergency": 0,
	},
	{
		"department": "Emergency",
		"contact_name": "24/7 Emergency Command Center",
		"phone_number": "+255700999111",
		"whatsapp_number": "+255700999111",
		"email": "",
		"operating_hours": "24/7",
		"priority_order": 1,
		"is_emergency": 1,
	},
	{
		"department": "Leasing",
		"contact_name": "Commercial & Residential Leasing",
		"phone_number": "+255744666888",
		"whatsapp_number": "+255744666888",
		"email": "",
		"operating_hours": "Mon - Sat: 08:30 AM - 04:30 PM",
		"priority_order": 5,
		"is_emergency": 0,
	},
]


def execute():
	"""Ensure at least one active contact per directory department."""
	if not frappe.db.exists("DocType", "Viva Building Contact"):
		return

	# Map old department labels → new (if any legacy rows)
	legacy_map = {
		"Reception Desk": "Reception",
		"Security Desk": "Security",
		"Maintenance Team": "Maintenance",
		"Leasing Office": "Leasing",
		"Emergency Hotline": "Emergency",
	}
	for old, new in legacy_map.items():
		frappe.db.sql(
			"UPDATE `tabViva Building Contact` SET department=%s WHERE department=%s",
			(new, old),
		)

	for row in DEFAULT_CONTACTS:
		exists = frappe.db.exists(
			"Viva Building Contact",
			{"department": row["department"], "is_active": 1},
		)
		if exists:
			continue
		doc = frappe.get_doc(
			{
				"doctype": "Viva Building Contact",
				"naming_series": "VBC-.####",
				**row,
				"is_active": 1,
			}
		)
		doc.insert(ignore_permissions=True)

	frappe.db.commit()
