# -*- coding: utf-8 -*-
"""Amenity booking lifecycle helpers.

Best practice: Confirmed bookings whose end datetime has passed → Completed
via a scheduled reconciler (and on list/read so mobile stays correct).
Open Requests whose start has passed → Expired.
"""

from __future__ import unicode_literals

import frappe
from frappe.utils import get_datetime, now_datetime

from propms.api.v1.amenities.doctypes import AMENITY_BOOKING, AMENITY_BOOKING_REQUEST


def reconcile_completed_amenity_bookings(limit=500):
	"""Mark past Confirmed bookings as Completed.

	Industry pattern: after reservation end time, status moves from Upcoming
	(Confirmed) → Completed automatically. No-show can be a later staff action.
	"""
	now = now_datetime()
	rows = frappe.get_all(
		AMENITY_BOOKING,
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
			AMENITY_BOOKING,
			row.name,
			"status",
			"Completed",
			update_modified=True,
		)
		# set_value skips Document.on_update — keep Series child row in sync
		series = frappe.db.get_value(AMENITY_BOOKING, row.name, "series")
		if series:
			from propms.api.v1.amenities.series_items import sync_series_booking_row

			sync_series_booking_row(row.name)
		updated += 1

	if updated:
		frappe.db.commit()
	return {"updated": updated}


def reconcile_stale_pending_amenity_bookings(limit=500):
	"""Cancel Pending bookings whose start datetime has passed.

	Kept until Pending→Request migration is complete.
	"""
	now = now_datetime()
	rows = frappe.get_all(
		AMENITY_BOOKING,
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
			AMENITY_BOOKING,
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


def reconcile_stale_open_amenity_requests(limit=500):
	"""Expire Open Requests whose start datetime has passed."""
	now = now_datetime()
	rows = frappe.get_all(
		AMENITY_BOOKING_REQUEST,
		filters={"status": "Open"},
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
			AMENITY_BOOKING_REQUEST,
			row.name,
			"status",
			"Expired",
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
	expired = reconcile_stale_open_amenity_requests()
	return {
		"completed": done,
		"pending_cancelled": pending,
		"requests_expired": expired,
	}
