# -*- coding: utf-8 -*-
"""User amenity bookings & cancellation service."""

from __future__ import unicode_literals

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from propms.api.v1.amenities.doctypes import AMENITY_BOOKING, AMENITY_BOOKING_REQUEST
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.amenities.lifecycle import (
	reconcile_completed_amenity_bookings,
	reconcile_stale_open_amenity_requests,
	reconcile_stale_pending_amenity_bookings,
)
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload


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
	elif target_status == "pending":
		filters["status"] = "Pending"
	elif target_status == "rejected":
		filters["status"] = "Rejected"
	elif target_status in ("open", "approved", "expired"):
		# Request-only statuses; exclude bookings from this filter
		filters["status"] = "__skip__"
	return filters


def _request_statuses_for_list(target_status):
	"""Which Amenity Booking Request statuses to include for a list filter."""
	target_status = (target_status or "all").strip().lower()
	if target_status in ("all", "pending", "open"):
		return ["Open"]
	if target_status == "cancelled":
		return ["Cancelled"]
	if target_status == "rejected":
		return ["Rejected"]
	if target_status == "expired":
		return ["Expired"]
	if target_status == "approved":
		return ["Approved"]
	# confirmed / upcoming / completed / no show → bookings only
	return []


def _enrich_bookings(bookings):
	for b in bookings:
		amenity_meta = (
			frappe.db.get_value(
				"Amenity",
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


def _status_summary(filters_base, request_filters_base=None):
	"""Count by status for the same scope as the list (tenant or all)."""
	rows = frappe.get_all(
		AMENITY_BOOKING,
		filters=filters_base,
		fields=["status"],
		ignore_permissions=True,
	)
	open_requests = 0
	if request_filters_base is not None:
		req_filters = dict(request_filters_base)
		req_filters["status"] = "Open"
		open_requests = frappe.db.count(
			AMENITY_BOOKING_REQUEST, filters=req_filters
		)

	pending_bookings = sum(1 for x in rows if x.status == "Pending")
	return {
		"total": len(rows) + open_requests,
		"confirmed": sum(1 for x in rows if x.status == "Confirmed"),
		"completed": sum(1 for x in rows if x.status == "Completed"),
		"cancelled": sum(1 for x in rows if x.status == "Cancelled"),
		"no_show": sum(1 for x in rows if x.status == "No Show"),
		"pending": pending_bookings + open_requests,
		"open": open_requests,
	}


def _row_sort_key(row):
	return (
		str(row.get("booking_date") or ""),
		str(row.get("start_time") or ""),
		str(row.get("creation") or ""),
	)


def _tag_booking_row(row):
	row = dict(row)
	row["kind"] = "booking"
	row["booking_id"] = row.get("name")
	row["request_id"] = None
	return row


def _tag_request_row(row):
	row = dict(row)
	row["kind"] = "request"
	row["request_id"] = row.get("name")
	# Linked booking after approve; Open requests have none
	row["booking_id"] = row.get("booking") or None
	# Flutter compat: treat Open as pending-like
	if row.get("status") == "Open":
		row["display_status"] = "Pending"
	else:
		row["display_status"] = row.get("status")
	# Keep keys Flutter already reads; request has no cancellation_reason
	if "cancellation_reason" not in row:
		row["cancellation_reason"] = None
	return row


def _apply_common_scope_filters(filters, payload, current_user, is_staff):
	"""Mutate filters with tenant/amenity/date scope shared by Booking + Request."""
	if not is_staff:
		filters["tenant"] = current_user

	amenity_name = (payload.get("amenity") or "").strip()
	if amenity_name:
		filters["amenity"] = amenity_name

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
	return filters


def _check_cancel_deadline(doc, is_staff):
	"""Return error dict if tenant cancel window has closed, else None."""
	if is_staff:
		return None
	amenity = frappe.get_doc("Amenity", doc.amenity)
	hours = cint(getattr(amenity, "cancel_before_hours", None) or 2)
	start_dt = get_datetime(f"{doc.booking_date} {doc.start_time}")
	deadline = start_dt - timedelta(hours=hours)
	if now_datetime() >= deadline:
		return {
			"status": "error",
			"message": f"Cancellation is only allowed until {hours} hour(s) before start.",
		}
	return None


def _cancel_booking_doc(doc, reason, current_user, is_staff):
	"""Apply existing Confirmed/Pending booking cancel rules. Returns result dict."""
	if doc.status == "Cancelled":
		return {"status": "error", "message": "Booking is already cancelled"}
	if doc.status == "Completed":
		return {"status": "error", "message": "Cannot cancel a completed booking"}

	deadline_err = _check_cancel_deadline(doc, is_staff)
	if deadline_err:
		return deadline_err

	doc.status = "Cancelled"
	if reason:
		doc.cancellation_reason = reason
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		from propms.api.v1.amenities.notify import notify_amenity_booking_cancelled

		notify_amenity_booking_cancelled(doc, cancelled_by=current_user)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "cancel_booking.notify")

	return {
		"status": "success",
		"message": "Booking cancelled successfully",
		"booking_id": doc.name,
		"request_id": None,
		"kind": "booking",
	}


def _cancel_open_request(req, reason, current_user, is_staff):
	"""Open Request → Cancelled (no cancel-before-hours gate).

	Uses for_update + re-check Open (same race guard as `_claim_open_request`).
	"""
	if not is_staff and req.tenant != current_user:
		frappe.throw(_("Not permitted to cancel this booking"), frappe.PermissionError)

	# Lock + re-check so concurrent approve cannot race with cancel
	req = frappe.get_doc(AMENITY_BOOKING_REQUEST, req.name, for_update=True)
	if req.status != "Open":
		return {
			"status": "error",
			"message": f"Only Open requests can be cancelled this way (current: {req.status})",
		}

	req.status = "Cancelled"
	# Request DocType has no cancellation_reason; keep reason in notes if provided
	if reason:
		existing = (req.notes or "").strip()
		note = f"Cancelled: {reason}"
		req.notes = f"{existing}\n{note}".strip() if existing else note
	req.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		from propms.api.v1.amenities.notify import notify_amenity_booking_cancelled

		notify_amenity_booking_cancelled(req, cancelled_by=current_user)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "cancel_booking.notify_request")

	return {
		"status": "success",
		"message": "Booking request cancelled successfully",
		"booking_id": None,
		"request_id": req.name,
		"kind": "request",
	}


def _cancel_approved_request(req, reason, current_user, is_staff):
	"""Approved Request → cancel linked Booking if allowed; Request stays Approved."""
	if not is_staff and req.tenant != current_user:
		frappe.throw(_("Not permitted to cancel this booking"), frappe.PermissionError)

	if not req.booking or not frappe.db.exists(AMENITY_BOOKING, req.booking):
		return {
			"status": "error",
			"message": "Approved request has no linked booking to cancel",
		}

	booking = frappe.get_doc(AMENITY_BOOKING, req.booking)
	if not is_staff and booking.tenant != current_user:
		frappe.throw(_("Not permitted to cancel this booking"), frappe.PermissionError)

	result = _cancel_booking_doc(booking, reason, current_user, is_staff)
	if result.get("status") == "success":
		result["request_id"] = req.name
		result["kind"] = "booking"
		result["message"] = "Linked booking cancelled successfully"
	return result


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
	"""List amenity bookings and Open requests.

	- **Tenant:** own reservations / requests only.
	- **Staff** (Maintenance Manager/Officer, Property Manager, System Manager):
	  **all tenant reservations** across the building (not the staff user's own
	  bookings). Optional filters: amenity, booking_date / from-to, status.

	Each row includes ``kind``: ``"request"`` | ``"booking"``.
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		# Past Confirmed → Completed; stale Pending → Cancelled; stale Open → Expired
		reconcile_completed_amenity_bookings()
		reconcile_stale_pending_amenity_bookings()
		reconcile_stale_open_amenity_requests()

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

		filters = {}
		_apply_common_scope_filters(filters, payload, current_user, is_staff)
		scope_for_summary = dict(filters)
		request_scope = dict(filters)

		_apply_status_filter(filters, target_status)

		booking_fields = [
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
			"rejection_reason",
			"approved_by",
			"approved_on",
			"creation",
		]
		request_fields = [
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
			"rejection_reason",
			"approved_by",
			"approved_on",
			"booking",
			"series",
			"creation",
		]

		combined = []

		# Bookings (skip when filter is request-only "open")
		if filters.get("status") != "__skip__":
			bookings = frappe.get_all(
				AMENITY_BOOKING,
				filters=filters,
				fields=booking_fields,
				order_by="booking_date desc, start_time desc",
				ignore_permissions=True,
			)
			_enrich_bookings(bookings)
			combined.extend(_tag_booking_row(b) for b in bookings)

		req_statuses = _request_statuses_for_list(target_status)
		if req_statuses:
			req_filters = dict(request_scope)
			if len(req_statuses) == 1:
				req_filters["status"] = req_statuses[0]
			else:
				req_filters["status"] = ["in", req_statuses]
			requests = frappe.get_all(
				AMENITY_BOOKING_REQUEST,
				filters=req_filters,
				fields=request_fields,
				order_by="booking_date desc, start_time desc",
				ignore_permissions=True,
			)
			_enrich_bookings(requests)
			combined.extend(_tag_request_row(r) for r in requests)

		combined.sort(key=_row_sort_key, reverse=True)
		page_rows = combined[offset : offset + page_len]

		counts = _status_summary(scope_for_summary, request_scope)

		return {
			"status": "success",
			"is_staff": is_staff,
			"scope": "all_tenants" if is_staff else "own",
			"summary": counts,
			"page": page_num,
			"page_length": page_len,
			"bookings": page_rows,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_my_bookings")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def cancel_booking(booking_id=None, cancellation_reason=None, request_id=None):
	"""Cancel an Open Request, Confirmed Booking, or linked Booking of an Approved Request.

	``booking_id`` may be an Amenity Booking name or Amenity Booking Request name
	(Flutter compat). ``request_id`` is also accepted.
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"booking_id": booking_id,
				"request_id": request_id,
				"cancellation_reason": cancellation_reason,
			}
		)
		target_id = (
			payload.get("request_id") or payload.get("booking_id") or ""
		).strip()
		reason = (payload.get("cancellation_reason") or "").strip() or None

		if not target_id:
			return {"status": "error", "message": "booking_id or request_id is required"}

		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)

		# Prefer Request when id exists as both (unlikely given naming)
		if frappe.db.exists(AMENITY_BOOKING_REQUEST, target_id):
			req = frappe.get_doc(AMENITY_BOOKING_REQUEST, target_id)
			if req.status == "Open":
				return _cancel_open_request(req, reason, current_user, is_staff)
			if req.status == "Approved":
				return _cancel_approved_request(req, reason, current_user, is_staff)
			if req.status == "Cancelled":
				return {"status": "error", "message": "Request is already cancelled"}
			return {
				"status": "error",
				"message": f"Cannot cancel request in status {req.status}",
			}

		if frappe.db.exists(AMENITY_BOOKING, target_id):
			doc = frappe.get_doc(AMENITY_BOOKING, target_id)
			if not is_staff and doc.tenant != current_user:
				frappe.throw(
					_("Not permitted to cancel this booking"), frappe.PermissionError
				)
			return _cancel_booking_doc(doc, reason, current_user, is_staff)

		return {"status": "error", "message": f"Booking {target_id} not found"}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "cancel_booking")
		return {"status": "error", "message": str(e)}
