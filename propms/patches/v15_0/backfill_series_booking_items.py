# -*- coding: utf-8 -*-
"""Backfill Amenity Booking Series child rows from bookings that have series set."""

from __future__ import unicode_literals

import frappe

from propms.api.v1.amenities.series_items import sync_series_booking_row


def execute():
	if not frappe.db.exists("DocType", "Amenity Booking"):
		return
	if not frappe.db.exists("DocType", "Amenity Booking Series"):
		return
	if not frappe.db.has_column("Amenity Booking", "series"):
		return

	bookings = frappe.get_all(
		"Amenity Booking",
		filters={"series": ["is", "set"]},
		fields=["name"],
		order_by="creation asc",
		ignore_permissions=True,
	)

	for row in bookings or []:
		try:
			sync_series_booking_row(row.name)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				"backfill_series_booking_items.{}".format(row.name),
			)

	frappe.db.commit()
