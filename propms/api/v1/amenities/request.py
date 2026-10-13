# -*- coding: utf-8 -*-
"""Amenity booking request create + auto-approve → Series + Confirmed Booking(s).

Create path: one Amenity Booking Request + occurrence child rows (no Series yet).
Approve / auto-approve: one Series + one Amenity Booking per occurrence.
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate, now_datetime, nowdate

from propms.api.v1.amenities.doctypes import (
	AMENITY_BOOKING,
	AMENITY_BOOKING_REQUEST,
	AMENITY_BOOKING_SERIES,
)
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.amenities.overlap import find_conflicting_booking, is_time_on_step
from propms.api.v1.amenities.slots import _parse_time_str, optional_positive_days
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit, _parse_request_payload

# Mon=0 .. Sun=6 — matches Amenity Booking Series week_* checks
_WEEKDAY_CHECK_FIELDS = (
	"week_mon",
	"week_tue",
	"week_wed",
	"week_thu",
	"week_fri",
	"week_sat",
	"week_sun",
)


def _weekday_checks_for_date(booking_date):
	"""One weekday check=1 for the booking date (One Time series)."""
	wd = getdate(booking_date).weekday()
	return {field: (1 if i == wd else 0) for i, field in enumerate(_WEEKDAY_CHECK_FIELDS)}


def _occurrence_rows_from_request(req):
	"""Child occurrences, or legacy parent date/time as a single synthetic row."""
	rows = list(getattr(req, "occurrences", None) or [])
	if rows:
		return rows
	return [
		frappe._dict(
			{
				"booking_date": req.booking_date,
				"start_time": req.start_time,
				"end_time": req.end_time,
				"status": "Pending",
				"booking": None,
				"name": None,
				"idx": 1,
			}
		)
	]


def _validate_occurrence_slot(amenity_doc, target_date, s_time, e_time, today_date):
	"""Validate one date/time against advance window, past start, hours, step."""
	max_advance = optional_positive_days(amenity_doc.max_advance_days)

	if target_date < today_date:
		return {"status": "error", "message": "Cannot book for a past date"}
	if max_advance is not None:
		max_date = getdate(add_days(today_date, max_advance))
		if target_date > max_date:
			return {
				"status": "error",
				"message": (
					f"Bookings can only be made up to {max_advance} days in advance "
					f"(until {max_date})."
				),
			}

	if e_time <= s_time:
		return {"status": "error", "message": "End time must be after start time"}

	if target_date == today_date:
		start_dt = get_datetime(f"{target_date} {s_time}")
		if start_dt <= now_datetime():
			return {
				"status": "error",
				"message": "Cannot book a time that has already started",
			}

	step = max(
		1,
		cint(
			getattr(amenity_doc, "booking_time_step_mins", None)
			or amenity_doc.slot_duration_mins
			or 30
		),
	)
	open_s = _parse_time_str(amenity_doc.open_time).strftime("%H:%M:%S")
	close_s = _parse_time_str(amenity_doc.close_time).strftime("%H:%M:%S")

	if not is_time_on_step(s_time, step) or not is_time_on_step(e_time, step):
		return {
			"status": "error",
			"message": f"Start and end times must align to {step}-minute steps",
		}

	if s_time < open_s or e_time > close_s:
		return {
			"status": "error",
			"message": f"Booking must be within operating hours ({open_s}–{close_s})",
		}

	return None


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

	target_date = getdate(payload.get("booking_date"))
	today_date = getdate(nowdate())
	slot_err = _validate_occurrence_slot(amenity_doc, target_date, s_time, e_time, today_date)
	if slot_err:
		return slot_err, None

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
		amenity_doc.name, target_date, s_time, e_time, 0
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


def _create_confirmed_booking_from_request(req, occurrence=None):
	"""Insert Confirmed Amenity Booking from a Request occurrence (or legacy parent)."""
	booking_date = occurrence.booking_date if occurrence else req.booking_date
	start_time = occurrence.start_time if occurrence else req.start_time
	end_time = occurrence.end_time if occurrence else req.end_time

	booking_doc = frappe.get_doc(
		{
			"doctype": AMENITY_BOOKING,
			"amenity": req.amenity,
			"booking_date": booking_date,
			"start_time": start_time,
			"end_time": end_time,
			"guests_count": req.guests_count or 1,
			"tenant": req.tenant,
			"tenant_name": req.tenant_name,
			"property_unit": req.property_unit,
			"lease": req.lease,
			"notes": req.notes,
			"status": "Confirmed",
			"approved_by": frappe.session.user,
			"approved_on": now_datetime(),
			"request": req.name,
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


def _build_series_from_request(req, occurrences, series_type):
	"""Create Amenity Booking Series from an approved Request + its occurrences."""
	dates = [getdate(o.booking_date) for o in occurrences]
	first_date = min(dates)
	last_date = max(dates)
	s_time = _parse_time_str(occurrences[0].start_time).strftime("%H:%M:%S")
	e_time = _parse_time_str(occurrences[0].end_time).strftime("%H:%M:%S")

	if series_type == "Recurring":
		weekdays = {getdate(o.booking_date).weekday() for o in occurrences}
		checks = {field: (1 if i in weekdays else 0) for i, field in enumerate(_WEEKDAY_CHECK_FIELDS)}
	else:
		checks = _weekday_checks_for_date(first_date)

	series = frappe.get_doc(
		{
			"doctype": AMENITY_BOOKING_SERIES,
			"amenity": req.amenity,
			"series_type": series_type,
			"status": "Confirmed",
			"series_start_date": str(first_date),
			"series_end_date": str(last_date),
			"start_time": s_time,
			"end_time": e_time,
			"tenant": req.tenant,
			"tenant_name": req.tenant_name,
			"property_unit": req.property_unit,
			"lease": req.lease,
			"guests_count": req.guests_count or 1,
			"notes": req.notes,
			"booked_count": len(occurrences),
			"conflict_count": 0,
			"approved_by": frappe.session.user,
			"approved_on": now_datetime(),
			**checks,
		}
	)
	series.insert(ignore_permissions=True)
	return series


def fulfill_request_to_series_and_bookings(req, notify=True, commit=True):
	"""Open Request (+ children) → Series + Confirmed Booking per occurrence.

	All-or-nothing: any conflict aborts with no writes committed by caller.
	``req`` must already be locked Open (caller uses for_update).
	"""
	occurrences = _occurrence_rows_from_request(req)
	if not occurrences:
		return {"status": "error", "message": "Request has no occurrences to approve"}

	series_type = (getattr(req, "series_type", None) or "One Time").strip() or "One Time"

	# Conflict-check every occurrence first (all-or-nothing)
	for occ in occurrences:
		s = _parse_time_str(occ.start_time).strftime("%H:%M:%S")
		e = _parse_time_str(occ.end_time).strftime("%H:%M:%S")
		conflict = find_conflicting_booking(
			req.amenity,
			occ.booking_date,
			s,
			e,
			0,
			exclude_request=req.name,
		)
		if conflict:
			return {
				"status": "error",
				"message": (
					f"Cannot approve: {occ.booking_date} overlaps {conflict.name}"
				),
				"conflict_booking_id": conflict.name,
				"conflict_date": str(occ.booking_date),
			}

	# Create Series only on approve (unless already linked — legacy)
	if getattr(req, "series", None) and frappe.db.exists(AMENITY_BOOKING_SERIES, req.series):
		series = frappe.get_doc(AMENITY_BOOKING_SERIES, req.series, for_update=True)
		series.status = "Confirmed"
		if not series.approved_by:
			series.approved_by = frappe.session.user
			series.approved_on = now_datetime()
		series.booked_count = len(occurrences)
		series.save(ignore_permissions=True)
	else:
		series = _build_series_from_request(req, occurrences, series_type)
		req.series = series.name

	booking_ids = []
	first_booking = None
	has_child_table = bool(getattr(req, "occurrences", None))

	for idx, occ in enumerate(occurrences):
		booking_doc = _create_confirmed_booking_from_request(req, occurrence=occ)
		booking_ids.append(booking_doc.name)
		if first_booking is None:
			first_booking = booking_doc

		if has_child_table and getattr(occ, "idx", None):
			# Update child row via parent doc
			for child in req.occurrences:
				if child.idx == occ.idx or (
					str(child.booking_date) == str(occ.booking_date)
					and str(child.start_time) == str(occ.start_time)
				):
					child.booking = booking_doc.name
					child.status = "Booked"
					break
		elif has_child_table and idx < len(req.occurrences):
			req.occurrences[idx].booking = booking_doc.name
			req.occurrences[idx].status = "Booked"

	req.status = "Approved"
	req.booking = first_booking.name if first_booking else None
	req.approved_by = frappe.session.user
	req.approved_on = now_datetime()
	req.save(ignore_permissions=True)

	series.reload()
	series.booked_count = len(booking_ids)
	series.status = "Confirmed"
	series.save(ignore_permissions=True)

	if commit:
		frappe.db.commit()

	if notify and first_booking:
		try:
			from propms.api.v1.amenities.notify import notify_amenity_booking_approved

			notify_amenity_booking_approved(first_booking, request_id=req.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "fulfill_request.notify")

	return {
		"status": "success",
		"message": _("Booking approved ({0} day(s))").format(len(booking_ids)),
		"request_id": req.name,
		"booking_id": first_booking.name if first_booking else None,
		"booking_ids": booking_ids,
		"series_id": series.name,
		"series_type": series_type,
		"doc": first_booking.as_dict() if first_booking else req.as_dict(),
	}


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
	"""One-time path: Request + 1 occurrence; Series + Booking only on approve/auto."""
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
		auto = cint(getattr(amenity_doc, "auto_approval", 0))

		# 1) Amenity Booking Request + occurrence child (no Series yet)
		req = frappe.get_doc(
			{
				"doctype": AMENITY_BOOKING_REQUEST,
				"amenity": ctx["amenity_name"],
				"series_type": "One Time",
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
				"occurrences": [
					{
						"booking_date": str(target_date),
						"start_time": s_time,
						"end_time": e_time,
						"status": "Pending",
					}
				],
			}
		)
		req.insert(ignore_permissions=True)

		booking_name = None
		booking_doc = None
		series_name = None
		series_status = None

		if auto:
			req.reload()
			result = fulfill_request_to_series_and_bookings(req, notify=False, commit=False)
			if result.get("status") != "success":
				frappe.db.rollback()
				return result
			booking_name = result.get("booking_id")
			booking_ids = result.get("booking_ids") or []
			series_name = result.get("series_id")
			series_status = "Confirmed"
			if booking_name:
				booking_doc = frappe.get_doc(AMENITY_BOOKING, booking_name)
			req.reload()
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

		return {
			"status": "success",
			"message": message,
			"request_id": req.name,
			"series_id": series_name,
			"series_type": "One Time",
			"series_status": series_status,
			"booking_id": booking_name,
			"booking_ids": [booking_name] if booking_name else [],
			"request_status": req.status,
			"occurrence_count": 1,
			"auto_approved": bool(auto),
			"doc": booking_doc.as_dict() if booking_doc else req.as_dict(),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "create_booking_request")
		return {"status": "error", "message": str(e)}
