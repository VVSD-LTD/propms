# -*- coding: utf-8 -*-
"""Amenity slot calculation and availability engine."""

from __future__ import unicode_literals
from datetime import datetime, timedelta
import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, get_time, getdate, now_datetime, nowdate


def _parse_time_str(val):
	"""Parse time string into datetime.time object."""
	if not val:
		return get_time("00:00:00")
	if isinstance(val, timedelta):
		total_seconds = int(val.total_seconds())
		hours = total_seconds // 3600
		minutes = (total_seconds % 3600) // 60
		seconds = total_seconds % 60
		return datetime.strptime(f"{hours:02d}:{minutes:02d}:{seconds:02d}", "%H:%M:%S").time()
	if isinstance(val, str):
		try:
			return datetime.strptime(val[:8], "%H:%M:%S").time()
		except Exception:
			try:
				return datetime.strptime(val[:5], "%H:%M").time()
			except Exception:
				return get_time(val)
	return getattr(val, "time", lambda: val)()


@frappe.whitelist(methods=["GET", "POST"])
def get_available_slots(amenity=None, booking_date=None):
	"""Calculate time slots and real-time availability for an amenity on a given date."""
	try:
		if not amenity:
			return {"status": "error", "message": "Amenity is required"}

		target_date = getdate(booking_date or nowdate())
		today_date = getdate(nowdate())
		now_ts = now_datetime()

		if not frappe.db.exists("Viva Amenity", amenity):
			return {"status": "error", "message": f"Amenity {amenity} not found"}

		doc = frappe.get_doc("Viva Amenity", amenity)
		if not doc.is_active:
			return {"status": "error", "message": f"Amenity {doc.amenity_name} is currently inactive"}

		max_advance = cint(doc.max_advance_days or 7)
		max_date = getdate(add_days(today_date, max_advance))

		if target_date < today_date:
			return {"status": "error", "message": "Cannot view slots for a past date"}
		if target_date > max_date:
			return {
				"status": "error",
				"message": f"Bookings can only be made up to {max_advance} days in advance (until {max_date}).",
			}

		open_t = _parse_time_str(doc.open_time or "06:00:00")
		close_t = _parse_time_str(doc.close_time or "22:00:00")
		duration_mins = max(15, cint(doc.slot_duration_mins or 60))
		capacity = max(1, cint(doc.capacity or 10))

		current_user = frappe.session.user

		# Query active bookings on target date
		existing_bookings = frappe.get_all(
			"Viva Amenity Booking",
			filters={
				"amenity": doc.name,
				"booking_date": str(target_date),
				"status": "Confirmed",
			},
			fields=["name", "start_time", "end_time", "guests_count", "tenant"],
		)

		slots = []
		dummy_date = datetime(2026, 1, 1)
		slot_start_dt = datetime.combine(dummy_date, open_t)
		slot_end_boundary = datetime.combine(dummy_date, close_t)
		if slot_end_boundary <= slot_start_dt:
			slot_end_boundary += timedelta(days=1)

		while slot_start_dt + timedelta(minutes=duration_mins) <= slot_end_boundary:
			slot_end_dt = slot_start_dt + timedelta(minutes=duration_mins)
			start_str = slot_start_dt.strftime("%H:%M:%S")
			end_str = slot_end_dt.strftime("%H:%M:%S")

			# Check overlap & capacity
			booked_guests = 0
			is_my_booking = False
			my_booking_id = None

			for b in existing_bookings:
				b_start = _parse_time_str(b.start_time).strftime("%H:%M:%S")
				b_end = _parse_time_str(b.end_time).strftime("%H:%M:%S")

				# Overlap check
				if not (end_str <= b_start or start_str >= b_end):
					booked_guests += max(1, cint(b.guests_count or 1))
					if current_user and current_user != "Guest" and b.tenant == current_user:
						is_my_booking = True
						my_booking_id = b.name

			# Check if slot time in the past for today
			actual_slot_start = datetime.combine(target_date, slot_start_dt.time())
			is_past = actual_slot_start < now_ts

			available_spots = max(0, capacity - booked_guests)
			is_available = (not is_past) and (available_spots > 0)

			slots.append({
				"start_time": start_str,
				"end_time": end_str,
				"display_time": f"{slot_start_dt.strftime('%I:%M %p')} - {slot_end_dt.strftime('%I:%M %p')}",
				"capacity": capacity,
				"booked_count": booked_guests,
				"available_spots": available_spots,
				"is_available": is_available,
				"is_past": is_past,
				"is_my_booking": is_my_booking,
				"my_booking_id": my_booking_id,
			})

			slot_start_dt = slot_end_dt

		return {
			"status": "success",
			"amenity": doc.name,
			"amenity_name": doc.amenity_name,
			"category": doc.category,
			"capacity": capacity,
			"booking_date": str(target_date),
			"total_slots": len(slots),
			"available_slots_count": sum(1 for s in slots if s["is_available"]),
			"slots": slots,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_available_slots")
		return {"status": "error", "message": str(e)}
