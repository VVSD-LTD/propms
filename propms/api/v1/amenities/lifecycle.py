# -*- coding: utf-8 -*-
"""Amenity booking lifecycle helpers.

Best practice: Confirmed bookings whose end datetime has passed → Completed
via a scheduled reconciler (and on list/read so mobile stays correct).
"""

from __future__ import unicode_literals

import frappe
from frappe.utils import get_datetime, now_datetime


def reconcile_completed_amenity_bookings(limit=500):
	"""Mark past Confirmed bookings as Completed.

	Industry pattern: after reservation end time, status moves from Upcoming
	(Confirmed) → Completed automatically. No-show can be a later staff action.
	"""
	now = now_datetime()
	rows = frappe.get_all(
		"Viva Amenity Booking",
		filters={"status": "Confirmed"},
		fields=["name", "booking_date", "end_time"],
		limit_page_length=limit,
		ignore_permissions=True,
	)
	updated = 0
	for row in rows:
		if not row.booking_date or not row.end_time:
			continue
		try:
			end_dt = get_datetime(f"{row.booking_date} {row.end_time}")
		except Exception:
			continue
		if end_dt >= now:
			continue
		frappe.db.set_value(
			"Viva Amenity Booking",
			row.name,
			"status",
			"Completed",
			update_modified=True,
		)
		updated += 1

	if updated:
		frappe.db.commit()
	return {"updated": updated}


def reconcile_stale_pending_amenity_bookings(limit=500):
	"""Cancel Pending bookings whose start datetime has passed."""
	now = now_datetime()
	rows = frappe.get_all(
		"Viva Amenity Booking",
		filters={"status": "Pending"},
		fields=["name", "booking_date", "start_time"],
		limit_page_length=limit,
		ignore_permissions=True,
	)
	updated = 0
	for row in rows:
		if not row.booking_date or not row.start_time:
			continue
		try:
			start_dt = get_datetime(f"{row.booking_date} {row.start_time}")
		except Exception:
			continue
		if start_dt >= now:
			continue
		frappe.db.set_value(
			"Viva Amenity Booking",
			row.name,
			{
				"status": "Cancelled",
				"cancellation_reason": "Auto-cancelled: pending approval past start time",
			},
			update_modified=True,
		)
		updated += 1

	if updated:
		frappe.db.commit()
	return {"updated": updated}


def complete_past_confirmed_bookings():
	"""Scheduler entrypoint (hourly)."""
	done = reconcile_completed_amenity_bookings()
	pending = reconcile_stale_pending_amenity_bookings()
	return {"completed": done, "pending_cancelled": pending}
