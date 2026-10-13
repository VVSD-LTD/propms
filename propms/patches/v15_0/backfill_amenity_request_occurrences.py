# -*- coding: utf-8 -*-
"""Backfill Amenity Booking Request Item rows + series_type for legacy requests."""

from __future__ import unicode_literals

import frappe


def execute():
	if not frappe.db.exists("DocType", "Amenity Booking Request"):
		return
	if not frappe.db.exists("DocType", "Amenity Booking Request Item"):
		return

	if frappe.db.has_column("Amenity Booking Request", "series_type"):
		frappe.db.sql(
			"""
			UPDATE `tabAmenity Booking Request`
			SET series_type = 'One Time'
			WHERE IFNULL(series_type, '') = ''
			"""
		)

	# Requests with no child rows get one occurrence from parent date/time
	parents = frappe.db.sql(
		"""
		SELECT r.name, r.booking_date, r.start_time, r.end_time, r.status, r.booking
		FROM `tabAmenity Booking Request` r
		LEFT JOIN `tabAmenity Booking Request Item` i
			ON i.parent = r.name AND i.parenttype = 'Amenity Booking Request'
		WHERE i.name IS NULL
		  AND r.booking_date IS NOT NULL
		  AND r.start_time IS NOT NULL
		  AND r.end_time IS NOT NULL
		""",
		as_dict=True,
	)

	for row in parents:
		item_status = "Pending"
		if row.status == "Approved" and row.booking:
			item_status = "Booked"
		elif row.status in ("Rejected", "Cancelled", "Expired"):
			item_status = "Cancelled"

		frappe.get_doc(
			{
				"doctype": "Amenity Booking Request Item",
				"parent": row.name,
				"parenttype": "Amenity Booking Request",
				"parentfield": "occurrences",
				"booking_date": row.booking_date,
				"start_time": row.start_time,
				"end_time": row.end_time,
				"booking": row.booking if item_status == "Booked" else None,
				"status": item_status,
			}
		).db_insert()

	# Infer Recurring when request already links a Recurring series
	if frappe.db.exists("DocType", "Amenity Booking Series") and frappe.db.has_column(
		"Amenity Booking Request", "series_type"
	):
		frappe.db.sql(
			"""
			UPDATE `tabAmenity Booking Request` r
			INNER JOIN `tabAmenity Booking Series` s ON s.name = r.series
			SET r.series_type = 'Recurring'
			WHERE s.series_type = 'Recurring'
			"""
		)

	frappe.db.commit()
