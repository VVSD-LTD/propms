# -*- coding: utf-8 -*-
"""Staff approve / reject for Open Amenity Booking Requests (and legacy Pending bookings)."""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import now_datetime

from propms.api.v1.amenities.doctypes import AMENITY_BOOKING, AMENITY_BOOKING_REQUEST
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.amenities.overlap import find_conflicting_booking
from propms.api.v1.amenities.request import _create_confirmed_booking_from_request
from propms.api.v1.amenities.slots import _parse_time_str
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload


def _resolve_approve_reject_target(payload):
	"""Return (kind, name) where kind is 'request' | 'booking', or (None, error_msg)."""
	target = (payload.get("request_id") or payload.get("booking_id") or "").strip()
	if not target:
		return None, "booking_id or request_id is required"

	if frappe.db.exists(AMENITY_BOOKING_REQUEST, target):
		return "request", target

	if frappe.db.exists(AMENITY_BOOKING, target):
		return "booking", target

	return None, f"Request or booking {target} not found"


def _claim_open_request(req_name):
	"""Lock Request row and return it only if still Open; else error dict."""
	req = frappe.get_doc(AMENITY_BOOKING_REQUEST, req_name, for_update=True)
	if req.status != "Open":
		return None, {
			"status": "error",
			"message": f"Only Open requests can be processed (current: {req.status})",
		}
	return req, None


def _fulfill_open_request(req_name, notify=True, commit=True):
	"""Open Request → Confirmed Booking; mark Request Approved + link.

	``commit`` / ``notify`` can be disabled for batch series approve.
	"""
	req, err = _claim_open_request(req_name)
	if err:
		return err

	buffer_mins = 0
	s = _parse_time_str(req.start_time).strftime("%H:%M:%S")
	e = _parse_time_str(req.end_time).strftime("%H:%M:%S")
	conflict = find_conflicting_booking(
		req.amenity,
		req.booking_date,
		s,
		e,
		buffer_mins,
		exclude_request=req.name,
	)
	if conflict:
		return {
			"status": "error",
			"message": f"Cannot approve: overlaps {conflict.name}",
		}

	booking_doc = _create_confirmed_booking_from_request(req)
	req.status = "Approved"
	req.booking = booking_doc.name
	req.approved_by = frappe.session.user
	req.approved_on = now_datetime()
	req.save(ignore_permissions=True)

	if commit:
		frappe.db.commit()

	if notify:
		try:
			from propms.api.v1.amenities.notify import notify_amenity_booking_approved

			notify_amenity_booking_approved(booking_doc, request_id=req.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "approve_amenity_booking.notify")

	return {
		"status": "success",
		"message": "Booking approved",
		"request_id": req.name,
		"booking_id": booking_doc.name,
		"doc": booking_doc.as_dict(),
	}


def _approve_open_request(req_name):
	"""Open Request → Confirmed Booking; mark Request Approved + link."""
	return _fulfill_open_request(req_name, notify=True, commit=True)


def _reject_open_request(req_name, reason):
	"""Open Request → Rejected (reason required)."""
	req, err = _claim_open_request(req_name)
	if err:
		return err

	req.status = "Rejected"
	req.rejection_reason = reason
	req.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		from propms.api.v1.amenities.notify import notify_amenity_booking_rejected

		notify_amenity_booking_rejected(req, request_id=req.name)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "reject_amenity_booking.notify")

	return {
		"status": "success",
		"message": "Booking rejected",
		"request_id": req.name,
		"booking_id": None,
	}


def _approve_pending_booking(doc):
	"""Legacy migration path: Pending Amenity Booking → Confirmed."""
	if doc.status != "Pending":
		return {
			"status": "error",
			"message": f"Only Pending bookings can be approved (current: {doc.status})",
		}

	buffer_mins = 0  # no cleanup grace after booking end
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


def _reject_pending_booking(doc, reason):
	"""Legacy migration path: Pending Amenity Booking → Rejected."""
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


@frappe.whitelist(methods=["POST"])
def approve_amenity_booking(booking_id=None, request_id=None):
	"""Staff-only: Open Request → Confirmed Booking (or legacy Pending → Confirmed)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can approve bookings"), frappe.PermissionError)

		payload = _parse_request_payload({
			"booking_id": booking_id,
			"request_id": request_id,
		})
		kind, target_or_err = _resolve_approve_reject_target(payload)
		if kind is None:
			return {"status": "error", "message": target_or_err}

		if kind == "request":
			return _approve_open_request(target_or_err)

		doc = frappe.get_doc(AMENITY_BOOKING, target_or_err)
		return _approve_pending_booking(doc)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "approve_amenity_booking")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def reject_amenity_booking(booking_id=None, rejection_reason=None, request_id=None):
	"""Staff-only: Open Request → Rejected (or legacy Pending → Rejected)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can reject bookings"), frappe.PermissionError)

		payload = _parse_request_payload({
			"booking_id": booking_id,
			"request_id": request_id,
			"rejection_reason": rejection_reason,
		})
		kind, target_or_err = _resolve_approve_reject_target(payload)
		if kind is None:
			return {"status": "error", "message": target_or_err}

		reason = (payload.get("rejection_reason") or "").strip()
		if not reason:
			return {"status": "error", "message": "rejection_reason is required"}

		if kind == "request":
			return _reject_open_request(target_or_err, reason)

		doc = frappe.get_doc(AMENITY_BOOKING, target_or_err)
		return _reject_pending_booking(doc, reason)
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "reject_amenity_booking")
		return {"status": "error", "message": str(e)}
