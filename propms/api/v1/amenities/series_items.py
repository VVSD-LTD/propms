# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import frappe


def sync_series_booking_row(booking_name):
	"""Upsert read-only child row on Amenity Booking Series from a Booking."""
	b = frappe.db.get_value(
		"Amenity Booking",
		booking_name,
		["name", "series", "booking_date", "start_time", "end_time", "status"],
		as_dict=True,
	)
	if not b or not b.series:
		return
	if not frappe.db.exists("Amenity Booking Series", b.series):
		return
	series = frappe.get_doc("Amenity Booking Series", b.series)
	existing = None
	for row in series.get("bookings") or []:
		if row.booking == b.name:
			existing = row
			break
	if existing:
		existing.booking_date = b.booking_date
		existing.start_time = b.start_time
		existing.end_time = b.end_time
		existing.status = b.status
	else:
		series.append(
			"bookings",
			{
				"booking": b.name,
				"booking_date": b.booking_date,
				"start_time": b.start_time,
				"end_time": b.end_time,
				"status": b.status,
			},
		)
	series.flags.ignore_permissions = True
	series.save(ignore_permissions=True)
