# -*- coding: utf-8 -*-
"""Migrate legacy Pending Amenity Bookings to Open Amenity Booking Requests."""

from __future__ import unicode_literals

import frappe

MIGRATE_REASON = "Migrated to Amenity Booking Request"


def _already_has_open_request(booking):
	"""Idempotency: skip if matching Open Request already exists for this slot/tenant."""
	filters = {
		"amenity": booking.amenity,
		"booking_date": booking.booking_date,
		"start_time": booking.start_time,
		"end_time": booking.end_time,
		"tenant": booking.tenant,
		"status": "Open",
	}
	return bool(
		frappe.db.exists("Amenity Booking Request", filters)
	)


def execute():
	if not frappe.db.exists("DocType", "Amenity Booking"):
		return
	if not frappe.db.exists("DocType", "Amenity Booking Request"):
		return

	pending = frappe.get_all(
		"Amenity Booking",
		filters={"status": "Pending"},
		fields=[
			"name",
			"amenity",
			"booking_date",
			"start_time",
			"end_time",
			"guests_count",
			"tenant",
			"tenant_name",
			"property_unit",
			"lease",
			"series",
			"notes",
		],
		order_by="creation asc",
		ignore_permissions=True,
	)

	for row in pending or []:
		if _already_has_open_request(row):
			# Request already holds the slot — still cancel leftover Pending
			pass
		else:
			req = frappe.get_doc(
				{
					"doctype": "Amenity Booking Request",
					"amenity": row.amenity,
					"booking_date": row.booking_date,
					"start_time": row.start_time,
					"end_time": row.end_time,
					"status": "Open",
					"guests_count": row.guests_count or 1,
					"tenant": row.tenant,
					"tenant_name": row.tenant_name,
					"property_unit": row.property_unit,
					"lease": row.lease,
					"series": row.series,
					"notes": row.notes,
				}
			)
			req.insert(ignore_permissions=True)

		booking = frappe.get_doc("Amenity Booking", row.name)
		if booking.status != "Pending":
			continue
		booking.status = "Cancelled"
		booking.cancellation_reason = MIGRATE_REASON
		booking.flags.ignore_permissions = True
		booking.save(ignore_permissions=True)

	frappe.db.commit()
