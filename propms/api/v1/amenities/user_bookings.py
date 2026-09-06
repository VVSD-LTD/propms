# -*- coding: utf-8 -*-
"""User amenity bookings & cancellation service."""

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload


@frappe.whitelist(methods=["GET", "POST"])
def get_my_bookings(status="all", page=1, page_length=20):
	"""Return upcoming & past amenity bookings for the logged-in resident."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"status": status, "page": page, "page_length": page_length})
		current_user = frappe.session.user
		target_status = (payload.get("status") or "all").strip().lower()
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 20)))
		offset = (page_num - 1) * page_len

		filters = {"tenant": current_user}
		if target_status == "confirmed" or target_status == "upcoming":
			filters["status"] = "Confirmed"
		elif target_status == "completed":
			filters["status"] = "Completed"
		elif target_status == "cancelled":
			filters["status"] = "Cancelled"

		fields = [
			"name",
			"amenity",
			"booking_date",
			"start_time",
			"end_time",
			"guests_count",
			"status",
			"property_unit",
			"lease",
			"notes",
			"cancellation_reason",
			"creation",
		]

		bookings = frappe.get_all(
			"Viva Amenity Booking",
			filters=filters,
			fields=fields,
			order_by="booking_date desc, start_time desc",
			limit_start=offset,
			limit_page_length=page_len,
		)

		# Enrich with Amenity metadata (cover_image, category, etc.)
		for b in bookings:
			amenity_meta = frappe.db.get_value(
				"Viva Amenity",
				b.amenity,
				["amenity_name", "category", "cover_image"],
				as_dict=True,
			) or {}
			b["amenity_name"] = amenity_meta.get("amenity_name") or b.amenity
			b["category"] = amenity_meta.get("category")
			b["cover_image"] = amenity_meta.get("cover_image")

		all_user_bookings = frappe.get_all("Viva Amenity Booking", filters={"tenant": current_user}, fields=["status"])
		counts = {
			"total": len(all_user_bookings),
			"confirmed": sum(1 for x in all_user_bookings if x.status == "Confirmed"),
			"completed": sum(1 for x in all_user_bookings if x.status == "Completed"),
			"cancelled": sum(1 for x in all_user_bookings if x.status == "Cancelled"),
		}

		return {
			"status": "success",
			"summary": counts,
			"page": page_num,
			"page_length": page_len,
			"bookings": bookings,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_my_bookings")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def cancel_booking(booking_id=None, cancellation_reason=None):
	"""Cancel a confirmed amenity booking."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"booking_id": booking_id, "cancellation_reason": cancellation_reason})
		target_id = (payload.get("booking_id") or "").strip()

		if not target_id or not frappe.db.exists("Viva Amenity Booking", target_id):
			return {"status": "error", "message": f"Booking {target_id} not found"}

		doc = frappe.get_doc("Viva Amenity Booking", target_id)
		current_user = frappe.session.user
		user_roles = frappe.get_roles(current_user)
		is_staff = any(r in user_roles for r in ["System Manager", "Mobile Maintenance Manager", "Mobile Maintenance Officer"])

		if not is_staff and doc.tenant != current_user:
			frappe.throw(_("Not permitted to cancel this booking"), frappe.PermissionError)

		if doc.status == "Cancelled":
			return {"status": "error", "message": "Booking is already cancelled"}
		if doc.status == "Completed":
			return {"status": "error", "message": "Cannot cancel a completed booking"}

		doc.status = "Cancelled"
		if payload.get("cancellation_reason"):
			doc.cancellation_reason = payload.get("cancellation_reason").strip()
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		# Publish WebSocket event so other residents see the freed slot live
		frappe.publish_realtime(
			event="amenity_booking_cancelled",
			message={
				"booking_id": doc.name,
				"amenity": doc.amenity,
				"booking_date": str(doc.booking_date),
				"start_time": str(doc.start_time),
				"end_time": str(doc.end_time),
			},
			after_commit=True,
		)

		return {
			"status": "success",
			"message": "Booking cancelled successfully",
			"booking_id": doc.name,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "cancel_booking")
		return {"status": "error", "message": str(e)}
