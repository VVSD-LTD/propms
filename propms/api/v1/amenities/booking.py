# -*- coding: utf-8 -*-
"""Amenity booking creation service."""

from __future__ import unicode_literals
from datetime import datetime, timedelta
import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime, nowdate
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit, _parse_request_payload
from propms.api.v1.amenities.slots import _parse_time_str


@frappe.whitelist(methods=["POST"])
def create_booking(
	amenity=None,
	booking_date=None,
	start_time=None,
	end_time=None,
	guests_count=1,
	notes=None,
	lease=None,
	property_unit=None,
):
	"""Create a new confirmed booking for a resident on a Viva Amenity."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({
			"amenity": amenity,
			"booking_date": booking_date or nowdate(),
			"start_time": start_time,
			"end_time": end_time,
			"guests_count": guests_count or 1,
			"notes": notes,
			"lease": lease,
			"property_unit": property_unit,
		})

		amenity_name = payload.get("amenity")
		if not amenity_name or not frappe.db.exists("Viva Amenity", amenity_name):
			return {"status": "error", "message": "Valid amenity is required"}

		amenity_doc = frappe.get_doc("Viva Amenity", amenity_name)
		if not amenity_doc.is_active:
			return {"status": "error", "message": f"{amenity_doc.amenity_name} is currently inactive"}

		target_date = getdate(payload.get("booking_date"))
		today_date = getdate(nowdate())
		max_advance = cint(amenity_doc.max_advance_days or 7)
		max_date = getdate(add_days(today_date, max_advance))

		if target_date < today_date:
			return {"status": "error", "message": "Cannot book for a past date"}
		if target_date > max_date:
			return {
				"status": "error",
				"message": f"Bookings can only be made up to {max_advance} days in advance (until {max_date}).",
			}

		s_time = (payload.get("start_time") or "").strip()
		e_time = (payload.get("end_time") or "").strip()
		if not s_time or not e_time:
			return {"status": "error", "message": "Start time and end time are required"}

		# Format times cleanly as HH:MM:SS
		s_time = _parse_time_str(s_time).strftime("%H:%M:%S")
		e_time = _parse_time_str(e_time).strftime("%H:%M:%S")

		requested_guests = max(1, cint(payload.get("guests_count") or 1))
		capacity = max(1, cint(amenity_doc.capacity or 10))

		# Check capacity against existing confirmed bookings for overlapping window
		overlapping = frappe.get_all(
			"Viva Amenity Booking",
			filters={
				"amenity": amenity_doc.name,
				"booking_date": str(target_date),
				"status": "Confirmed",
			},
			fields=["name", "start_time", "end_time", "guests_count", "tenant"],
		)

		current_booked_guests = 0
		current_user = frappe.session.user

		for b in overlapping:
			b_start = _parse_time_str(b.start_time).strftime("%H:%M:%S")
			b_end = _parse_time_str(b.end_time).strftime("%H:%M:%S")

			if not (e_time <= b_start or s_time >= b_end):
				current_booked_guests += max(1, cint(b.guests_count or 1))
				if b.tenant == current_user:
					return {
						"status": "error",
						"message": f"You already have an active booking for this time slot ({b.name}).",
					}

		if current_booked_guests + requested_guests > capacity:
			remaining = max(0, capacity - current_booked_guests)
			return {
				"status": "error",
				"message": f"Slot capacity exceeded. Only {remaining} spot(s) remaining for this time slot.",
				"remaining_spots": remaining,
			}

		# Resolve tenant information via Lease-first identity
		tenant_email = frappe.db.get_value("User", current_user, "email") or current_user
		auto_unit, auto_lease, auto_resident = _get_tenant_default_unit(tenant_email)

		prop_unit = payload.get("property_unit") or auto_unit or "Viva Towers Unit"
		lease_name = payload.get("lease") or auto_lease
		resident_name = auto_resident or frappe.db.get_value("User", current_user, "full_name") or current_user

		booking_doc = frappe.get_doc({
			"doctype": "Viva Amenity Booking",
			"amenity": amenity_doc.name,
			"booking_date": str(target_date),
			"start_time": s_time,
			"end_time": e_time,
			"guests_count": requested_guests,
			"tenant": current_user,
			"tenant_name": resident_name,
			"property_unit": prop_unit,
			"lease": lease_name,
			"notes": (payload.get("notes") or "").strip(),
			"status": "Confirmed",
		})
		booking_doc.insert(ignore_permissions=True)
		frappe.db.commit()

		# Publish real-time event to synchronize all active mobile app calendars
		frappe.publish_realtime(
			event="amenity_booked",
			message={
				"booking_id": booking_doc.name,
				"amenity": amenity_doc.name,
				"booking_date": str(target_date),
				"start_time": s_time,
				"end_time": e_time,
			},
			after_commit=True,
		)

		return {
			"status": "success",
			"message": f"Booking confirmed for {amenity_doc.amenity_name} on {target_date} ({s_time} - {e_time})",
			"booking_id": booking_doc.name,
			"doc": booking_doc.as_dict(),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "create_booking")
		return {"status": "error", "message": str(e)}
