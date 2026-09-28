# -*- coding: utf-8 -*-
"""Staff approve / reject for Pending amenity bookings."""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload
from propms.api.v1.amenities.overlap import find_conflicting_booking
from propms.api.v1.amenities.slots import _parse_time_str


@frappe.whitelist(methods=["POST"])
def approve_amenity_booking(booking_id=None):
	"""Staff-only: Pending → Confirmed (re-check exclusive overlap excluding self)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can approve bookings"), frappe.PermissionError)

		payload = _parse_request_payload({"booking_id": booking_id})
		target = (payload.get("booking_id") or "").strip()
		if not target or not frappe.db.exists("Viva Amenity Booking", target):
			return {"status": "error", "message": f"Booking {target} not found"}

		doc = frappe.get_doc("Viva Amenity Booking", target)
		if doc.status != "Pending":
			return {
				"status": "error",
				"message": f"Only Pending bookings can be approved (current: {doc.status})",
			}

		amenity = frappe.get_doc("Viva Amenity", doc.amenity)
		buffer_mins = max(0, cint(getattr(amenity, "cleanup_buffer_mins", 0) or 0))
		s = _parse_time_str(doc.start_time).strftime("%H:%M:%S")
		e = _parse_time_str(doc.end_time).strftime("%H:%M:%S")
		conflict = find_conflicting_booking(
			doc.amenity, doc.booking_date, s, e, buffer_mins, exclude_name=doc.name
		)
		if conflict:
			return {
				"status": "error",
				"message": f"Cannot approve: overlaps {conflict.name}",
			}

		doc.status = "Confirmed"
		doc.approved_by = frappe.session.user
		doc.approved_on = now_datetime()
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		try:
			from propms.api.v1.amenities.notify import notify_amenity_booking_approved

			notify_amenity_booking_approved(doc)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "approve_amenity_booking.notify")

		return {
			"status": "success",
			"message": "Booking approved",
			"booking_id": doc.name,
			"doc": doc.as_dict(),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "approve_amenity_booking")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def reject_amenity_booking(booking_id=None, rejection_reason=None):
	"""Staff-only: Pending → Rejected (rejection_reason required)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can reject bookings"), frappe.PermissionError)

		payload = _parse_request_payload({
			"booking_id": booking_id,
			"rejection_reason": rejection_reason,
		})
		target = (payload.get("booking_id") or "").strip()
		reason = (payload.get("rejection_reason") or "").strip()
		if not target or not frappe.db.exists("Viva Amenity Booking", target):
			return {"status": "error", "message": f"Booking {target} not found"}
		if not reason:
			return {"status": "error", "message": "rejection_reason is required"}

		doc = frappe.get_doc("Viva Amenity Booking", target)
		if doc.status != "Pending":
			return {
				"status": "error",
				"message": f"Only Pending bookings can be rejected (current: {doc.status})",
			}

		doc.status = "Rejected"
		doc.rejection_reason = reason
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		try:
			from propms.api.v1.amenities.notify import notify_amenity_booking_rejected

			notify_amenity_booking_rejected(doc)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "reject_amenity_booking.notify")

		return {"status": "success", "message": "Booking rejected", "booking_id": doc.name}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "reject_amenity_booking")
		return {"status": "error", "message": str(e)}
