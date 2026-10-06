# -*- coding: utf-8 -*-
"""Hide Sales Invoice.lease_item — internal category tag, not for cashiers."""

from __future__ import unicode_literals

import frappe


def execute():
	name = "Sales Invoice-lease_item"
	if not frappe.db.exists("Custom Field", name):
		# Field may exist under dt/fieldname only
		name = frappe.db.get_value(
			"Custom Field",
			{"dt": "Sales Invoice", "fieldname": "lease_item"},
			"name",
		)
	if not name:
		return

	frappe.db.set_value(
		"Custom Field",
		name,
		{
			"hidden": 1,
			"read_only": 1,
			"description": (
				"Internal PropMS category tag (Electricity / Water / POS Store). "
				"Auto-set by the system — hidden so it does not confuse cashiers."
			),
		},
		update_modified=False,
	)
	frappe.clear_cache(doctype="Sales Invoice")
