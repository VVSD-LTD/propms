# -*- coding: utf-8 -*-
"""User amenity bookings & cancellation service."""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import cint
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.amenities.lifecycle import reconcile_completed_amenity_bookings


def _apply_status_filter(filters, target_status):
	target_status = (target_status or "all").strip().lower()
	if target_status in ("confirmed", "upcoming"):
		filters["status"] = "Confirmed"
	elif target_status == "completed":
		filters["status"] = "Completed"
	elif target_status == "cancelled":
		filters["status"] = "Cancelled"
	elif target_status == "no show":
		filters["status"] = "No Show"
	return filters


def _enrich_bookings(bookings):
	for b in bookings:
		amenity_meta = (
			frappe.db.get_value(
				"Viva Amenity",
				b.amenity,
				["amenity_name", "category", "cover_image", "floor"],
				as_dict=True,
			)
			or {}
		)
		b["amenity_name"] = amenity_meta.get("amenity_name") or b.amenity
		b["category"] = amenity_meta.get("category")
		b["cover_image"] = amenity_meta.get("cover_image")
		b["floor"] = amenity_meta.get("floor")
		# Ensure tenant display name for staff list
		if not b.get("tenant_name") and b.get("tenant"):
			b["tenant_name"] = (
				frappe.db.get_value("User", b["tenant"], "full_name") or b["tenant"]
			)
	return bookings


def _status_summary(filters_base):
	"""Count by status for the same scope as the list (tenant or all)."""
	rows = frappe.get_all(
		"Viva Amenity Booking",
		filters=filters_base,
		fields=["status"],
		ignore_permissions=True,
	)
	return {
		"total": len(rows),
		"confirmed": sum(1 for x in rows if x.status == "Confirmed"),
		"completed": sum(1 for x in rows if x.status == "Completed"),
		"cancelled": sum(1 for x in rows if x.status == "Cancelled"),
		"no_show": sum(1 for x in rows if x.status == "No Show"),
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_my_bookings(
	status="all",
	page=1,
	page_length=20,
	amenity=None,
	booking_date=None,
	booking_date_from=None,
	booking_date_to=None,
):
	"""List amenity bookings.

	- **Tenant:** own reservations only.
	- **Staff** (Maintenance Manager/Officer, Property Manager, System Manager):
	  **all tenant reservations** across the building (not the staff user's own
	  bookings). Optional filters: amenity, booking_date / from-to, status.
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		# Keep Upcoming/Completed tabs accurate even between cron runs
		reconcile_completed_amenity_bookings()

		payload = _parse_request_payload(
			{
				"status": status,
				"page": page,
				"page_length": page_length,
				"amenity": amenity,
				"booking_date": booking_date,
				"booking_date_from": booking_date_from,
				"booking_date_to": booking_date_to,
			}
		)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)

		target_status = (payload.get("status") or "all").strip().lower()
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 20)))
		offset = (page_num - 1) * page_len

		# Scope: staff → entire building; tenant → self only
		filters = {}
		if not is_staff:
			filters["tenant"] = current_user

		amenity_name = (payload.get("amenity") or "").strip()
		if amenity_name:
			filters["amenity"] = amenity_name

		# Single day or range (staff building calendar / filters)
		one_day = (payload.get("booking_date") or "").strip()
		date_from = (payload.get("booking_date_from") or "").strip()
		date_to = (payload.get("booking_date_to") or "").strip()
		if one_day:
			filters["booking_date"] = one_day
		else:
			if date_from and date_to:
				filters["booking_date"] = ["between", [date_from, date_to]]
			elif date_from:
				filters["booking_date"] = [">=", date_from]
			elif date_to:
				filters["booking_date"] = ["<=", date_to]

		# Summary uses scope without status filter
		scope_for_summary = dict(filters)
		_apply_status_filter(filters, target_status)

		fields = [
			"name",
			"amenity",
			"booking_date",
			"start_time",
			"end_time",
			"guests_count",
			"status",
			"tenant",
			"tenant_name",
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
			ignore_permissions=True,
		)
		_enrich_bookings(bookings)

		counts = _status_summary(scope_for_summary)

		return {
			"status": "success",
			"is_staff": is_staff,
			"scope": "all_tenants" if is_staff else "own",
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
	"""Cancel a confirmed amenity booking (owner or staff)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{"booking_id": booking_id, "cancellation_reason": cancellation_reason}
		)
		target_id = (payload.get("booking_id") or "").strip()

		if not target_id or not frappe.db.exists("Viva Amenity Booking", target_id):
			return {"status": "error", "message": f"Booking {target_id} not found"}

		doc = frappe.get_doc("Viva Amenity Booking", target_id)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)

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

		# Tenant cancel → staff FCM/WS; staff cancel → tenant FCM/WS
		try:
			from propms.api.v1.amenities.notify import notify_amenity_booking_cancelled

			notify_amenity_booking_cancelled(doc, cancelled_by=current_user)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "cancel_booking.notify")

		return {
			"status": "success",
			"message": "Booking cancelled successfully",
			"booking_id": doc.name,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "cancel_booking")
		return {"status": "error", "message": str(e)}
