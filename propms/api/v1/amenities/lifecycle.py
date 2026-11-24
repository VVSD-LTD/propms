# -*- coding: utf-8 -*-
"""Amenity booking lifecycle helpers.

Best practice: Confirmed bookings whose end datetime has passed → Completed
via a scheduled reconciler (and on list/read so mobile stays correct).
Open Requests whose start has passed → Expired.
Paid-amenity unpaid holds past Amenity.payment_hold_minutes → Expired.
"""

from __future__ import unicode_literals

import frappe
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from propms.api.v1.amenities.doctypes import AMENITY_BOOKING, AMENITY_BOOKING_REQUEST

# Unpaid payment statuses that still block the slot
_UNPAID_HOLD_STATUSES = ("Unpaid", "Pending", "Failed")


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


def _expire_open_request(req_name, has_items, item_doctype):
	frappe.db.set_value(
		AMENITY_BOOKING_REQUEST,
		req_name,
		"status",
		"Expired",
		update_modified=True,
	)
	if has_items:
		frappe.db.sql(
			"""
			UPDATE `tabAmenity Booking Request Item`
			SET status = 'Cancelled'
			WHERE parent = %s AND parenttype = %s
			  AND IFNULL(status, 'Pending') = 'Pending'
			""",
			(req_name, AMENITY_BOOKING_REQUEST),
		)


def reconcile_stale_open_amenity_requests(limit=500):
	"""Expire Open Requests that should no longer hold a slot.

	1. Earliest occurrence start has passed (all Open requests).
	2. Paid amenity unpaid hold: require_payment + payment_status in
	   Unpaid/Pending/Failed and age >= Amenity.payment_hold_minutes
	   (0 = skip timed hold; still expire at start).
	"""
	now = now_datetime()
	meta = frappe.get_meta(AMENITY_BOOKING_REQUEST)
	fields = ["name", "booking_date", "start_time", "amenity", "creation"]
	if meta.has_field("require_payment"):
		fields += ["require_payment", "payment_status"]

	rows = frappe.get_all(
		AMENITY_BOOKING_REQUEST,
		filters={"status": "Open"},
		fields=fields,
		limit_page_length=limit,
		ignore_permissions=True,
	)
	updated = 0
	item_doctype = "Amenity Booking Request Item"
	has_items = frappe.db.exists("DocType", item_doctype)
	hold_cache = {}

	amenity_meta = frappe.get_meta("Amenity") if frappe.db.exists("DocType", "Amenity") else None
	has_hold_field = bool(amenity_meta and amenity_meta.has_field("payment_hold_minutes"))

	for row in rows:
		expire = False

		# (2) unpaid payment hold TTL
		if (
			has_hold_field
			and cint(row.get("require_payment"))
			and (row.get("payment_status") or "") in _UNPAID_HOLD_STATUSES
		):
			amenity_name = row.get("amenity")
			if amenity_name not in hold_cache:
				hold_cache[amenity_name] = cint(
					frappe.db.get_value("Amenity", amenity_name, "payment_hold_minutes")
				)
			hold_mins = hold_cache[amenity_name]
			if hold_mins > 0 and row.get("creation"):
				deadline = add_to_date(row.creation, minutes=hold_mins)
				if deadline <= now:
					expire = True

		# (1) past booking start
		if not expire:
			earliest = None
			if has_items:
				items = frappe.get_all(
					item_doctype,
					filters={
						"parent": row.name,
						"parenttype": AMENITY_BOOKING_REQUEST,
						"status": ["in", ["Pending", ""]],
					},
					fields=["booking_date", "start_time"],
					order_by="booking_date asc, start_time asc",
					limit_page_length=1,
					ignore_permissions=True,
				)
				if items:
					earliest = items[0]

			if not earliest:
				if not row.booking_date or not row.start_time:
					continue
				earliest = row

			try:
				start_dt = get_datetime(f"{earliest.booking_date} {earliest.start_time}")
			except Exception:
				continue
			if start_dt < now:
				expire = True

		if not expire:
			continue

		_expire_open_request(row.name, has_items, item_doctype)
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
