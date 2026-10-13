# -*- coding: utf-8 -*-
"""Backfill Amenity Booking Series.series_type for rows created before the field existed."""

from __future__ import unicode_literals

import frappe


def execute():
	if not frappe.db.exists("DocType", "Amenity Booking Series"):
		return
	if not frappe.db.has_column("Amenity Booking Series", "series_type"):
		return

	# Existing series were recurring-only before One Time path existed
	frappe.db.sql(
		"""
		UPDATE `tabAmenity Booking Series`
		SET series_type = 'Recurring'
		WHERE IFNULL(series_type, '') = ''
		"""
	)
