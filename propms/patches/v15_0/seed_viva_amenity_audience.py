# -*- coding: utf-8 -*-
"""Seed Viva Amenity.audience for residential vs commercial catalog."""

from __future__ import unicode_literals

import frappe


RESIDENTIAL = ("Gym", "Pool", "Court")
COMMERCIAL = ("Party Hall", "Pool Side Private Party")


def execute():
	if not frappe.db.exists("DocType", "Viva Amenity"):
		return
	if not frappe.db.has_column("Viva Amenity", "audience"):
		return

	for name in RESIDENTIAL:
		if frappe.db.exists("Viva Amenity", name):
			frappe.db.set_value("Viva Amenity", name, "audience", "Residential", update_modified=False)

	for name in COMMERCIAL:
		if frappe.db.exists("Viva Amenity", name):
			frappe.db.set_value("Viva Amenity", name, "audience", "Commercial", update_modified=False)

	# Sports Bar and any other amenities keep default Everyone
