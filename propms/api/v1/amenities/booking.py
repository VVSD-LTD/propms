# -*- coding: utf-8 -*-
"""Amenity booking creation service."""

from __future__ import unicode_literals
from datetime import datetime, timedelta
import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime, nowdate
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit, _parse_request_payload
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.amenities.overlap import (
	is_time_on_step,
	find_conflicting_booking,
)
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
	"""Create a new exclusive booking for a resident on a Viva Amenity."""
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

		is_staff = _is_amenity_staff()
		if not cint(amenity_doc.is_published) and not is_staff:
			return {"status": "error", "message": _("Amenity is not published yet")}

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

		from propms.api.v1.amenities.lifecycle import reconcile_stale_pending_amenity_bookings
		reconcile_stale_pending_amenity_bookings()

		s_time = (payload.get("start_time") or "").strip()
		e_time = (payload.get("end_time") or "").strip()
		if not s_time or not e_time:
			return {"status": "error", "message": "Start time and end time are required"}

		# Format times cleanly as HH:MM:SS
		s_time = _parse_time_str(s_time).strftime("%H:%M:%S")
		e_time = _parse_time_str(e_time).strftime("%H:%M:%S")

		if e_time <= s_time:
			return {"status": "error", "message": "End time must be after start time"}

		if target_date == today_date:
			start_dt = get_datetime(f"{target_date} {s_time}")
			if start_dt <= now_datetime():
				return {"status": "error", "message": "Cannot book a time that has already started"}

		step = max(1, cint(getattr(amenity_doc, "booking_time_step_mins", None) or amenity_doc.slot_duration_mins or 30))
		buffer_mins = max(0, cint(getattr(amenity_doc, "cleanup_buffer_mins", None) or 0))
		open_s = _parse_time_str(amenity_doc.open_time).strftime("%H:%M:%S")
		close_s = _parse_time_str(amenity_doc.close_time).strftime("%H:%M:%S")

		if not is_time_on_step(s_time, step) or not is_time_on_step(e_time, step):
			return {"status": "error", "message": f"Start and end times must align to {step}-minute steps"}

		if s_time < open_s or e_time > close_s:
			return {"status": "error", "message": f"Booking must be within operating hours ({open_s}–{close_s})"}

		conflict = find_conflicting_booking(
			amenity_doc.name, target_date, s_time, e_time, buffer_mins
		)
		if conflict:
			cs = _parse_time_str(conflict.start_time).strftime("%H:%M:%S")
			ce = _parse_time_str(conflict.end_time).strftime("%H:%M:%S")
			return {
				"status": "error",
				"message": f"This time overlaps an existing booking ({cs}–{ce}).",
				"conflict_booking_id": conflict.name,
				"conflict_start": cs,
				"conflict_end": ce,
			}

		requested_guests = max(1, cint(payload.get("guests_count") or 1))
		initial_status = "Pending" if cint(getattr(amenity_doc, "requires_approval", 0)) else "Confirmed"

		# Resolve tenant information via Lease-first identity
		current_user = frappe.session.user
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
			"status": initial_status,
		})
		booking_doc.insert(ignore_permissions=True)
		frappe.db.commit()

		# Staff WebSocket + FCM (tenant booked → staff phones)
		try:
			from propms.api.v1.amenities.notify import notify_amenity_booked

			notify_amenity_booked(booking_doc, amenity_doc)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "create_booking.notify_staff")

		return {
			"status": "success",
			"message": f"Booking {initial_status.lower()} for {amenity_doc.amenity_name} on {target_date} ({s_time} - {e_time})",
			"booking_id": booking_doc.name,
			"doc": booking_doc.as_dict(),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "create_booking")
		return {"status": "error", "message": str(e)}
