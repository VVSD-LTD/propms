# -*- coding: utf-8 -*-
"""Amenity booking request create + auto-approve → Confirmed Booking."""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime, nowdate

from propms.api.v1.amenities.doctypes import AMENITY_BOOKING, AMENITY_BOOKING_REQUEST
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.amenities.overlap import find_conflicting_booking, is_time_on_step
from propms.api.v1.amenities.slots import _parse_time_str
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit, _parse_request_payload


def _validate_create_request_inputs(payload):
	"""Shared create validations. Returns (error_dict, None) or (None, ctx)."""
	amenity_name = payload.get("amenity")
	if not amenity_name or not frappe.db.exists("Amenity", amenity_name):
		return {"status": "error", "message": "Valid amenity is required"}, None

	amenity_doc = frappe.get_doc("Amenity", amenity_name)
	if not amenity_doc.is_active:
		return {
			"status": "error",
			"message": f"{amenity_doc.amenity_name} is currently inactive",
		}, None

	if not cint(amenity_doc.requires_booking):
		return {
			"status": "error",
			"message": _("This amenity does not require booking"),
		}, None

	is_staff = _is_amenity_staff()
	if not cint(amenity_doc.is_published) and not is_staff:
		return {"status": "error", "message": _("Amenity is not published yet")}, None

	target_date = getdate(payload.get("booking_date"))
	today_date = getdate(nowdate())
	max_advance = cint(amenity_doc.max_advance_days or 7)
	max_date = getdate(add_days(today_date, max_advance))

	if target_date < today_date:
		return {"status": "error", "message": "Cannot book for a past date"}, None
	if target_date > max_date:
		return {
			"status": "error",
			"message": (
				f"Bookings can only be made up to {max_advance} days in advance "
				f"(until {max_date})."
			),
		}, None

	from propms.api.v1.amenities.lifecycle import (
		reconcile_stale_open_amenity_requests,
		reconcile_stale_pending_amenity_bookings,
	)

	reconcile_stale_pending_amenity_bookings()
	reconcile_stale_open_amenity_requests()

	s_time = (payload.get("start_time") or "").strip()
	e_time = (payload.get("end_time") or "").strip()
	if not s_time or not e_time:
		return {"status": "error", "message": "Start time and end time are required"}, None

	s_time = _parse_time_str(s_time).strftime("%H:%M:%S")
	e_time = _parse_time_str(e_time).strftime("%H:%M:%S")

	if e_time <= s_time:
		return {"status": "error", "message": "End time must be after start time"}, None

	if target_date == today_date:
		start_dt = get_datetime(f"{target_date} {s_time}")
		if start_dt <= now_datetime():
			return {
				"status": "error",
				"message": "Cannot book a time that has already started",
			}, None

	step = max(
		1,
		cint(
			getattr(amenity_doc, "booking_time_step_mins", None)
			or amenity_doc.slot_duration_mins
			or 30
		),
	)
	buffer_mins = 0
	open_s = _parse_time_str(amenity_doc.open_time).strftime("%H:%M:%S")
	close_s = _parse_time_str(amenity_doc.close_time).strftime("%H:%M:%S")

	if not is_time_on_step(s_time, step) or not is_time_on_step(e_time, step):
		return {
			"status": "error",
			"message": f"Start and end times must align to {step}-minute steps",
		}, None

	if s_time < open_s or e_time > close_s:
		return {
			"status": "error",
			"message": f"Booking must be within operating hours ({open_s}–{close_s})",
		}, None

	current_user = frappe.session.user
	tenant_email = frappe.db.get_value("User", current_user, "email") or current_user
	auto_unit, auto_lease, auto_resident = _get_tenant_default_unit(tenant_email)

	prop_unit = payload.get("property_unit") or auto_unit or "Viva Towers Unit"
	lease_name = payload.get("lease") or auto_lease
	resident_name = (
		auto_resident
		or frappe.db.get_value("User", current_user, "full_name")
		or current_user
	)

	if not is_staff:
		from propms.api.v1.amenities.audience import assert_amenity_allowed_for_property

		denied = assert_amenity_allowed_for_property(
			amenity_doc,
			lease=lease_name,
			property_name=prop_unit if prop_unit != "Viva Towers Unit" else None,
		)
		if denied:
			return {"status": "error", "message": denied}, None

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
			"conflict_source": conflict.get("source"),
		}, None

	requested_guests = max(1, cint(payload.get("guests_count") or 1))

	ctx = {
		"amenity_doc": amenity_doc,
		"amenity_name": amenity_doc.name,
		"target_date": target_date,
		"s_time": s_time,
		"e_time": e_time,
		"requested_guests": requested_guests,
		"current_user": current_user,
		"resident_name": resident_name,
		"prop_unit": prop_unit,
		"lease_name": lease_name,
		"notes": (payload.get("notes") or "").strip(),
	}
	return None, ctx


def _create_confirmed_booking_from_request(req):
	"""Insert Confirmed Amenity Booking from an Approved/auto-approved Request."""
	booking_doc = frappe.get_doc(
		{
			"doctype": AMENITY_BOOKING,
			"amenity": req.amenity,
			"booking_date": req.booking_date,
			"start_time": req.start_time,
			"end_time": req.end_time,
			"guests_count": req.guests_count or 1,
			"tenant": req.tenant,
			"tenant_name": req.tenant_name,
			"property_unit": req.property_unit,
			"lease": req.lease,
			"notes": req.notes,
			"status": "Confirmed",
			"approved_by": frappe.session.user,
			"approved_on": now_datetime(),
		}
	)
	if getattr(req, "series", None):
		booking_doc.series = req.series

	# on_update also syncs; skip so we sync once after insert with final name
	booking_doc.flags.skip_series_sync = True
	booking_doc.insert(ignore_permissions=True)

	if getattr(booking_doc, "series", None):
		from propms.api.v1.amenities.series_items import sync_series_booking_row

		sync_series_booking_row(booking_doc.name)

	return booking_doc


@frappe.whitelist(methods=["POST"])
def create_booking_request(
	amenity=None,
	booking_date=None,
	start_time=None,
	end_time=None,
	guests_count=1,
	notes=None,
	lease=None,
	property_unit=None,
):
	"""Create Amenity Booking Request; auto-approve → Confirmed Booking when enabled."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"amenity": amenity,
				"booking_date": booking_date or nowdate(),
				"start_time": start_time,
				"end_time": end_time,
				"guests_count": guests_count or 1,
				"notes": notes,
				"lease": lease,
				"property_unit": property_unit,
			}
		)

		err, ctx = _validate_create_request_inputs(payload)
		if err:
			return err

		amenity_doc = ctx["amenity_doc"]
		target_date = ctx["target_date"]
		s_time = ctx["s_time"]
		e_time = ctx["e_time"]

		req = frappe.get_doc(
			{
				"doctype": AMENITY_BOOKING_REQUEST,
				"amenity": ctx["amenity_name"],
				"booking_date": str(target_date),
				"start_time": s_time,
				"end_time": e_time,
				"status": "Open",
				"tenant": ctx["current_user"],
				"tenant_name": ctx["resident_name"],
				"property_unit": ctx["prop_unit"],
				"lease": ctx["lease_name"],
				"guests_count": ctx["requested_guests"],
				"notes": ctx["notes"],
			}
		)
		req.insert(ignore_permissions=True)

		auto = cint(getattr(amenity_doc, "auto_approval", 0))
		booking_name = None
		booking_doc = None

		if auto:
			booking_doc = _create_confirmed_booking_from_request(req)
			booking_name = booking_doc.name
			req.status = "Approved"
			req.booking = booking_name
			req.approved_by = frappe.session.user
			req.approved_on = now_datetime()
			req.save(ignore_permissions=True)
			try:
				from propms.api.v1.amenities.notify import notify_amenity_booked

				notify_amenity_booked(booking_doc, amenity_doc)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(), "create_booking_request.notify_booked"
				)
		else:
			try:
				from propms.api.v1.amenities.notify import notify_amenity_request_pending

				notify_amenity_request_pending(req, amenity_doc)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(), "create_booking_request.notify_pending"
				)

		frappe.db.commit()

		if auto:
			message = (
				f"Booking confirmed for {amenity_doc.amenity_name} on {target_date} "
				f"({s_time} - {e_time})"
			)
		else:
			message = (
				f"Booking request submitted for {amenity_doc.amenity_name} on "
				f"{target_date} ({s_time} - {e_time})"
			)

		result = {
			"status": "success",
			"message": message,
			"request_id": req.name,
			"booking_id": booking_name,
			"request_status": req.status,
			"auto_approved": bool(auto),
			"doc": booking_doc.as_dict() if booking_doc else req.as_dict(),
		}
		return result
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "create_booking_request")
		return {"status": "error", "message": str(e)}
