# -*- coding: utf-8 -*-
"""Drop legacy Amenity.open_time / close_time columns (replaced by day_hours)."""

from __future__ import unicode_literals

import frappe


def execute():
	if not frappe.db.exists("DocType", "Amenity"):
		return
	for col in ("open_time", "close_time"):
		if frappe.db.has_column("Amenity", col):
			frappe.db.sql(f"ALTER TABLE `tabAmenity` DROP COLUMN `{col}`")
	frappe.db.commit()
