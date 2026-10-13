# -*- coding: utf-8 -*-
"""Backfill Amenity Booking.request from Amenity Booking Request.booking."""

from __future__ import unicode_literals

import frappe


def execute():
	if not frappe.db.exists("DocType", "Amenity Booking"):
		return
	if not frappe.db.has_column("Amenity Booking", "request"):
		return
	if not frappe.db.exists("DocType", "Amenity Booking Request"):
		return

	frappe.db.sql(
		"""
		UPDATE `tabAmenity Booking` b
		INNER JOIN `tabAmenity Booking Request` r
			ON r.booking = b.name
		SET b.request = r.name
		WHERE IFNULL(b.request, '') = ''
			AND IFNULL(r.booking, '') != ''
		"""
	)
