# Copyright (c) 2026, VV Systems Developer LTD and contributors
"""Remap Email Setting.payment_term to Payment Terms Template names.

The field used to link Payment Term; the scheduler filters
Sales Invoice.payment_terms_template. On case-insensitive MySQL,
'3 Days' still matched '3 DAYS', which hid the bug.
"""

import frappe

# Payment Term name -> Payment Terms Template name
TERM_MAP = {
	"3 Days": "3 DAYS",
	"Immediately": "IMMEDIATELY",
	"IMMEDIATELY": "IMMEDIATELY",
	"None": None,
}


def execute():
	templates = set(frappe.get_all("Payment Terms Template", pluck="name"))

	rows = frappe.db.sql(
		"""
		SELECT name, payment_term
		FROM `tabProperty Management Email Setting`
		WHERE IFNULL(payment_term, '') != ''
		""",
		as_dict=True,
	)

	for row in rows:
		current = row.payment_term
		if current in templates:
			continue

		mapped = TERM_MAP.get(current)
		if mapped and mapped in templates:
			frappe.db.set_value(
				"Property Management Email Setting",
				row.name,
				"payment_term",
				mapped,
				update_modified=False,
			)
			continue

		# Case-insensitive match against templates
		match = next((t for t in templates if t.lower() == (current or "").lower()), None)
		if match:
			frappe.db.set_value(
				"Property Management Email Setting",
				row.name,
				"payment_term",
				match,
				update_modified=False,
			)
