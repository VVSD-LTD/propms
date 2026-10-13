# -*- coding: utf-8 -*-
"""Remove leftover Viva Amenity* DocTypes recreated from old fixtures after rename.

Amenity Booking and Viva Amenity Booking both used autoname prefix VAB-, which
blocks saving Amenity Booking DocType ("Series VAB- already used in Viva Amenity B").
"""

from __future__ import unicode_literals

import frappe


GHOSTS = (
	"Viva Amenity Booking",
	"Viva Amenity",
	"Viva Amenity Booking Series",
)


def execute():
	for dt in GHOSTS:
		if not frappe.db.exists("DocType", dt):
			continue
		table = f"tab{dt}"
		try:
			count = frappe.db.sql(f"SELECT COUNT(*) FROM `{table}`")[0][0]
		except Exception:
			count = 0
		if count:
			frappe.logger().warning(
				f"skip delete {dt}: table still has {count} row(s)"
			)
			continue
		frappe.delete_doc("DocType", dt, force=1, ignore_permissions=True)
		frappe.db.commit()
