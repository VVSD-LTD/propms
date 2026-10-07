# -*- coding: utf-8 -*-
"""Recurring amenity booking series — preview, create, approve/reject, cancel modes."""

from __future__ import unicode_literals

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, get_datetime, getdate, now_datetime, nowdate

from propms.api.v1.amenities.doctypes import AMENITY_BOOKING_REQUEST, AMENITY_BOOKING_SERIES
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.amenities.overlap import find_conflicting_booking, is_time_on_step
from propms.api.v1.amenities.request import _create_confirmed_booking_from_request
from propms.api.v1.amenities.slots import _parse_time_str
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit, _parse_request_payload


def _amenity_requires_approval(amenity_doc):
	"""Mobile-compat: requires_approval is the inverse of auto_approval."""
	if hasattr(amenity_doc, "auto_approval"):
		return 0 if cint(amenity_doc.auto_approval) else 1
	return cint(getattr(amenity_doc, "requires_approval", 0) or 0)

MAX_OCCURRENCES = 60
WEEKDAY_KEYS = (
	("week_mon", 0),
	("week_tue", 1),
	("week_wed", 2),
	("week_thu", 3),
	("week_fri", 4),
	("week_sat", 5),
	("week_sun", 6),
)
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _parse_weekdays(payload):
	"""Return set of Python weekdays (Mon=0..Sun=6) from checks or weekdays list."""
	selected = set()
	raw = payload.get("weekdays")
	if isinstance(raw, str) and raw.strip():
		try:
			import json

			raw = json.loads(raw)
		except Exception:
			raw = [x.strip() for x in raw.split(",") if x.strip()]
	if isinstance(raw, (list, tuple)):
		name_map = {
			"mon": 0,
			"monday": 0,
			"tue": 1,
			"tuesday": 1,
			"wed": 2,
			"wednesday": 2,
			"thu": 3,
			"thursday": 3,
			"fri": 4,
			"friday": 4,
			"sat": 5,
			"saturday": 5,
			"sun": 6,
			"sunday": 6,
		}
		for item in raw:
			if isinstance(item, int):
				if 0 <= item <= 6:
					selected.add(item)
			else:
				key = str(item).strip().lower()
				if key.isdigit():
					v = int(key)
					if 0 <= v <= 6:
						selected.add(v)
				elif key in name_map:
					selected.add(name_map[key])
	for field, wd in WEEKDAY_KEYS:
		if cint(payload.get(field)):
			selected.add(wd)
	return selected


def _weekdays_to_checks(weekdays):
	out = {k: 0 for k, _ in WEEKDAY_KEYS}
	for field, wd in WEEKDAY_KEYS:
		if wd in weekdays:
			out[field] = 1
	return out


def _iter_occurrence_dates(start_date, end_date, weekdays):
	d = getdate(start_date)
	end = getdate(end_date)
	while d <= end:
		if d.weekday() in weekdays:
			yield d
		d = add_days(d, 1)


def _build_occurrence_plan(amenity_doc, start_date, end_date, weekdays, start_time, end_time):
	"""Return free and conflict occurrence dicts (no DB writes)."""
	today = getdate(nowdate())
	s_time = _parse_time_str(start_time).strftime("%H:%M:%S")
	e_time = _parse_time_str(end_time).strftime("%H:%M:%S")
	free = []
	conflicts = []
	for d in _iter_occurrence_dates(start_date, end_date, weekdays):
		if d < today:
			continue
		if d == today:
			start_dt = get_datetime(f"{d} {s_time}")
			if start_dt <= now_datetime():
				conflicts.append(
					{
						"booking_date": str(d),
						"start_time": s_time,
						"end_time": e_time,
						"reason": "past_start",
						"label": "Already started",
					}
				)
				continue
		conflict = find_conflicting_booking(amenity_doc.name, d, s_time, e_time, 0)
		if conflict:
			cs = _parse_time_str(conflict.start_time).strftime("%H:%M:%S")
			ce = _parse_time_str(conflict.end_time).strftime("%H:%M:%S")
			conflicts.append(
				{
					"booking_date": str(d),
					"start_time": s_time,
					"end_time": e_time,
					"reason": "overlap",
					"conflict_booking_id": conflict.name,
					"conflict_start": cs,
					"conflict_end": ce,
					"label": "Booked",
				}
			)
		else:
			free.append({"booking_date": str(d), "start_time": s_time, "end_time": e_time})
		if len(free) + len(conflicts) > MAX_OCCURRENCES * 2:
			break
	# Cap free list for create
	if len(free) > MAX_OCCURRENCES:
		overflow = free[MAX_OCCURRENCES:]
		free = free[:MAX_OCCURRENCES]
		for row in overflow:
			conflicts.append(
				{
					**row,
					"reason": "max_occurrences",
					"label": f"Exceeds max {MAX_OCCURRENCES} occurrences",
				}
			)
	return free, conflicts, s_time, e_time


def _validate_series_inputs(amenity_doc, payload, is_staff=False):
	if not cint(getattr(amenity_doc, "allow_recurring", 1)):
		frappe.throw(_("Recurring booking is not enabled for this amenity"), frappe.ValidationError)
	if not cint(amenity_doc.requires_booking):
		frappe.throw(_("This amenity does not require booking"), frappe.ValidationError)

	weekdays = _parse_weekdays(payload)
	if not weekdays:
		frappe.throw(_("Select at least one weekday (Mon–Sun)"), frappe.ValidationError)

	start_date = getdate(payload.get("series_start_date") or payload.get("start_date"))
	end_date = getdate(payload.get("series_end_date") or payload.get("end_date"))
	today = getdate(nowdate())
	if not start_date or not end_date:
		frappe.throw(_("series_start_date and series_end_date are required"), frappe.ValidationError)
	if end_date < start_date:
		frappe.throw(_("Series end date must be on or after start date"), frappe.ValidationError)
	if start_date < today:
		frappe.throw(_("Series start date cannot be in the past"), frappe.ValidationError)

	max_series = cint(getattr(amenity_doc, "max_series_days", None) or 120)
	span = date_diff(end_date, start_date)
	if span > max_series:
		frappe.throw(
			_("Series can span at most {0} days (your range is {1} days)").format(max_series, span),
			frappe.ValidationError,
		)

	s_raw = (payload.get("start_time") or "").strip()
	e_raw = (payload.get("end_time") or "").strip()
	if not s_raw or not e_raw:
		frappe.throw(_("Start time and end time are required"), frappe.ValidationError)
	s_time = _parse_time_str(s_raw).strftime("%H:%M:%S")
	e_time = _parse_time_str(e_raw).strftime("%H:%M:%S")
	if e_time <= s_time:
		frappe.throw(_("End time must be after start time"), frappe.ValidationError)

	step = max(
		1,
		cint(getattr(amenity_doc, "booking_time_step_mins", None) or amenity_doc.slot_duration_mins or 30),
	)
	if not is_time_on_step(s_time, step) or not is_time_on_step(e_time, step):
		frappe.throw(_("Start and end times must align to {0}-minute steps").format(step), frappe.ValidationError)

	open_s = _parse_time_str(amenity_doc.open_time).strftime("%H:%M:%S")
	close_s = _parse_time_str(amenity_doc.close_time).strftime("%H:%M:%S")
	if s_time < open_s or e_time > close_s:
		frappe.throw(
			_("Booking must be within operating hours ({0}–{1})").format(open_s, close_s),
			frappe.ValidationError,
		)

	return weekdays, start_date, end_date, s_time, e_time


@frappe.whitelist(methods=["GET", "POST"])
def preview_recurring_amenity_booking(
	amenity=None,
	series_start_date=None,
	series_end_date=None,
	start_time=None,
	end_time=None,
	weekdays=None,
	week_mon=None,
	week_tue=None,
	week_wed=None,
	week_thu=None,
	week_fri=None,
	week_sat=None,
	week_sun=None,
	lease=None,
	property_unit=None,
):
	"""Return free vs conflict dates for a proposed recurring series (no writes)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"amenity": amenity,
				"series_start_date": series_start_date,
				"series_end_date": series_end_date,
				"start_time": start_time,
				"end_time": end_time,
				"weekdays": weekdays,
				"week_mon": week_mon,
				"week_tue": week_tue,
				"week_wed": week_wed,
				"week_thu": week_thu,
				"week_fri": week_fri,
				"week_sat": week_sat,
				"week_sun": week_sun,
				"lease": lease,
				"property_unit": property_unit,
			}
		)
		amenity_name = (payload.get("amenity") or "").strip()
		if not amenity_name or not frappe.db.exists("Amenity", amenity_name):
			return {"status": "error", "message": "Valid amenity is required"}

		amenity_doc = frappe.get_doc("Amenity", amenity_name)
		is_staff = _is_amenity_staff()
		if not amenity_doc.is_active:
			return {"status": "error", "message": f"{amenity_doc.amenity_name} is currently inactive"}
		if not cint(amenity_doc.is_published) and not is_staff:
			return {"status": "error", "message": _("Amenity is not published yet")}

		if not is_staff:
			from propms.api.v1.amenities.audience import assert_amenity_allowed_for_property

			denied = assert_amenity_allowed_for_property(
				amenity_doc,
				lease=payload.get("lease"),
				property_name=payload.get("property_unit"),
			)
			if denied:
				return {"status": "error", "message": denied}

		from propms.api.v1.amenities.lifecycle import (
			reconcile_stale_open_amenity_requests,
			reconcile_stale_pending_amenity_bookings,
		)

		reconcile_stale_pending_amenity_bookings()
		reconcile_stale_open_amenity_requests()

		weekdays, start_date, end_date, s_time, e_time = _validate_series_inputs(
			amenity_doc, payload, is_staff=is_staff
		)
		free, conflicts, s_time, e_time = _build_occurrence_plan(
			amenity_doc, start_date, end_date, weekdays, s_time, e_time
		)
		return {
			"status": "success",
			"amenity": amenity_doc.name,
			"amenity_name": amenity_doc.amenity_name,
			"allow_recurring": cint(getattr(amenity_doc, "allow_recurring", 1)),
			"max_series_days": cint(getattr(amenity_doc, "max_series_days", None) or 120),
			"requires_approval": _amenity_requires_approval(amenity_doc),
			"series_start_date": str(start_date),
			"series_end_date": str(end_date),
			"start_time": s_time,
			"end_time": e_time,
			"weekdays": sorted(list(weekdays)),
			"weekday_labels": [WEEKDAY_NAMES[w] for w in sorted(weekdays)],
			"free": free,
			"conflicts": conflicts,
			"free_count": len(free),
			"conflict_count": len(conflicts),
			"max_occurrences": MAX_OCCURRENCES,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "preview_recurring_amenity_booking")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def create_recurring_amenity_booking(
	amenity=None,
	series_start_date=None,
	series_end_date=None,
	start_time=None,
	end_time=None,
	weekdays=None,
	week_mon=None,
	week_tue=None,
	week_wed=None,
	week_thu=None,
	week_fri=None,
	week_sat=None,
	week_sun=None,
	guests_count=1,
	notes=None,
	lease=None,
	property_unit=None,
):
	"""Create series + Open Requests per free occurrence (auto-approve → Bookings)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"amenity": amenity,
				"series_start_date": series_start_date,
				"series_end_date": series_end_date,
				"start_time": start_time,
				"end_time": end_time,
				"weekdays": weekdays,
				"week_mon": week_mon,
				"week_tue": week_tue,
				"week_wed": week_wed,
				"week_thu": week_thu,
				"week_fri": week_fri,
				"week_sat": week_sat,
				"week_sun": week_sun,
				"guests_count": guests_count,
				"notes": notes,
				"lease": lease,
				"property_unit": property_unit,
			}
		)
		amenity_name = (payload.get("amenity") or "").strip()
		if not amenity_name or not frappe.db.exists("Amenity", amenity_name):
			return {"status": "error", "message": "Valid amenity is required"}

		amenity_doc = frappe.get_doc("Amenity", amenity_name)
		is_staff = _is_amenity_staff()
		if not amenity_doc.is_active:
			return {"status": "error", "message": f"{amenity_doc.amenity_name} is currently inactive"}
		if not cint(amenity_doc.is_published) and not is_staff:
			return {"status": "error", "message": _("Amenity is not published yet")}

		current_user = frappe.session.user
		tenant_email = frappe.db.get_value("User", current_user, "email") or current_user
		auto_unit, auto_lease, auto_resident = _get_tenant_default_unit(tenant_email)
		prop_unit = payload.get("property_unit") or auto_unit or "Viva Towers Unit"
		lease_name = payload.get("lease") or auto_lease
		resident_name = auto_resident or frappe.db.get_value("User", current_user, "full_name") or current_user

		if not is_staff:
			from propms.api.v1.amenities.audience import assert_amenity_allowed_for_property

			denied = assert_amenity_allowed_for_property(
				amenity_doc,
				lease=lease_name,
				property_name=prop_unit if prop_unit != "Viva Towers Unit" else None,
			)
			if denied:
				return {"status": "error", "message": denied}

		from propms.api.v1.amenities.lifecycle import (
			reconcile_stale_open_amenity_requests,
			reconcile_stale_pending_amenity_bookings,
		)

		reconcile_stale_pending_amenity_bookings()
		reconcile_stale_open_amenity_requests()

		weekdays, start_date, end_date, s_time, e_time = _validate_series_inputs(
			amenity_doc, payload, is_staff=is_staff
		)
		free, conflicts, s_time, e_time = _build_occurrence_plan(
			amenity_doc, start_date, end_date, weekdays, s_time, e_time
		)
		if not free:
			return {
				"status": "error",
				"message": _("No free dates in this series. Adjust days or date range."),
				"conflicts": conflicts,
				"conflict_count": len(conflicts),
			}

		auto = cint(getattr(amenity_doc, "auto_approval", 0))
		checks = _weekdays_to_checks(weekdays)
		initial_status = "Confirmed" if auto else "Pending"
		series = frappe.get_doc(
			{
				"doctype": AMENITY_BOOKING_SERIES,
				"amenity": amenity_doc.name,
				"status": initial_status,
				"series_start_date": str(start_date),
				"series_end_date": str(end_date),
				"start_time": s_time,
				"end_time": e_time,
				"tenant": current_user,
				"tenant_name": resident_name,
				"property_unit": prop_unit,
				"lease": lease_name,
				"guests_count": max(1, cint(payload.get("guests_count") or 1)),
				"notes": (payload.get("notes") or "").strip(),
				"booked_count": len(free),
				"conflict_count": len(conflicts),
				**checks,
			}
		)
		if initial_status == "Confirmed":
			series.approved_by = current_user
			series.approved_on = now_datetime()
		series.insert(ignore_permissions=True)

		request_ids = []
		booking_ids = []
		for row in free:
			# Re-check race (Bookings + Open Requests)
			conflict = find_conflicting_booking(
				amenity_doc.name, row["booking_date"], s_time, e_time, 0
			)
			if conflict:
				conflicts.append(
					{
						**row,
						"reason": "overlap_race",
						"conflict_booking_id": conflict.name,
						"label": "Booked",
					}
				)
				continue

			req = frappe.get_doc(
				{
					"doctype": AMENITY_BOOKING_REQUEST,
					"amenity": amenity_doc.name,
					"booking_date": row["booking_date"],
					"start_time": s_time,
					"end_time": e_time,
					"status": "Open",
					"tenant": current_user,
					"tenant_name": resident_name,
					"property_unit": prop_unit,
					"lease": lease_name,
					"guests_count": series.guests_count,
					"notes": series.notes,
					"series": series.name,
				}
			)
			req.insert(ignore_permissions=True)
			request_ids.append(req.name)

			if auto:
				booking_doc = _create_confirmed_booking_from_request(req)
				req.status = "Approved"
				req.booking = booking_doc.name
				req.approved_by = current_user
				req.approved_on = now_datetime()
				req.save(ignore_permissions=True)
				booking_ids.append(booking_doc.name)

		occurrence_count = len(request_ids)
		# Child sync during auto-approve saves series; reload before final stats
		series.reload()
		series.booked_count = len(booking_ids) if auto else occurrence_count
		series.conflict_count = len(conflicts)
		if not occurrence_count:
			series.status = "Cancelled"
			series.save(ignore_permissions=True)
			frappe.db.commit()
			return {
				"status": "error",
				"message": _("No free dates could be booked (all conflicted)."),
				"series_id": series.name,
				"conflicts": conflicts,
			}
		series.save(ignore_permissions=True)
		frappe.db.commit()

		# One staff notification for the whole series (not per occurrence)
		try:
			from propms.api.v1.amenities.notify import notify_amenity_series_booked

			notify_amenity_series_booked(series, amenity_doc)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "create_recurring.notify_series")

		if auto:
			message = _("Series created with {0} booking(s); {1} conflict(s) skipped.").format(
				len(booking_ids), len(conflicts)
			)
		else:
			message = _(
				"Series submitted with {0} request(s); {1} conflict(s) skipped."
			).format(len(request_ids), len(conflicts))

		return {
			"status": "success",
			"message": message,
			"series_id": series.name,
			"series_status": series.status,
			"request_ids": request_ids,
			"booking_ids": booking_ids,
			"booked_count": series.booked_count,
			"conflicts": conflicts,
			"conflict_count": len(conflicts),
			"requires_approval": _amenity_requires_approval(amenity_doc),
			"auto_approved": bool(auto),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "create_recurring_amenity_booking")
		return {"status": "error", "message": str(e)}


def _series_bookings(series_name, statuses=None):
	filters = {"series": series_name}
	if statuses:
		filters["status"] = ["in", statuses]
	return frappe.get_all(
		"Amenity Booking",
		filters=filters,
		fields=["name", "booking_date", "start_time", "status", "tenant"],
		order_by="booking_date asc",
	)


def _series_open_requests(series_name):
	return frappe.get_all(
		AMENITY_BOOKING_REQUEST,
		filters={"series": series_name, "status": "Open"},
		fields=["name", "booking_date", "start_time", "status", "tenant"],
		order_by="booking_date asc",
	)


@frappe.whitelist(methods=["POST"])
def approve_amenity_booking_series(series_id=None):
	"""Staff: approve all Open Requests (and legacy Pending bookings) in a series."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can approve series"), frappe.PermissionError)

		payload = _parse_request_payload({"series_id": series_id})
		sid = (payload.get("series_id") or "").strip()
		if not sid or not frappe.db.exists(AMENITY_BOOKING_SERIES, sid):
			return {"status": "error", "message": f"Series {sid} not found"}

		# Lock series row so concurrent approve/reject cannot both proceed
		series = frappe.get_doc(AMENITY_BOOKING_SERIES, sid, for_update=True)
		if series.status not in ("Pending",):
			return {
				"status": "error",
				"message": f"Only Pending series can be approved (current: {series.status})",
			}

		from propms.api.v1.amenities.staff_actions import _fulfill_open_request

		open_rows = _series_open_requests(sid)
		pending_rows = _series_bookings(sid, statuses=["Pending"])
		if not open_rows and not pending_rows:
			return {
				"status": "error",
				"message": _("No Open requests or Pending bookings to approve"),
				"series_id": sid,
			}

		approved = []
		failed = []
		for row in open_rows:
			result = _fulfill_open_request(row.name, notify=False, commit=False)
			if result.get("status") == "success":
				approved.append(result["booking_id"])
			else:
				failed.append(
					{
						"request_id": row.name,
						"message": result.get("message") or "approve failed",
					}
				)

		# Legacy migration: Pending Amenity Bookings linked to series
		for row in pending_rows:
			doc = frappe.get_doc("Amenity Booking", row.name, for_update=True)
			if doc.status != "Pending":
				failed.append(
					{
						"booking_id": doc.name,
						"message": f"Only Pending bookings can be approved (current: {doc.status})",
					}
				)
				continue
			s = _parse_time_str(doc.start_time).strftime("%H:%M:%S")
			e = _parse_time_str(doc.end_time).strftime("%H:%M:%S")
			conflict = find_conflicting_booking(
				doc.amenity, doc.booking_date, s, e, 0, exclude_name=doc.name
			)
			if conflict:
				failed.append({"booking_id": doc.name, "message": f"overlaps {conflict.name}"})
				continue
			doc.status = "Confirmed"
			doc.approved_by = frappe.session.user
			doc.approved_on = now_datetime()
			doc.save(ignore_permissions=True)
			approved.append(doc.name)

		# All-or-nothing: any failure rolls back so series stays Pending / re-approvable
		if failed:
			frappe.db.rollback()
			return {
				"status": "error",
				"message": _("Series approve aborted; {0} occurrence(s) failed").format(
					len(failed)
				),
				"series_id": sid,
				"failed": failed,
			}

		# Fulfill syncs child rows onto series; reload before status update
		series.reload()
		series.booked_count = len(approved) or series.booked_count
		series.status = "Confirmed"
		series.approved_by = frappe.session.user
		series.approved_on = now_datetime()
		series.save(ignore_permissions=True)
		frappe.db.commit()

		try:
			from propms.api.v1.amenities.notify import notify_amenity_series_approved

			notify_amenity_series_approved(series)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "approve_series.notify")

		return {
			"status": "success",
			"message": _("Series approved ({0} booking(s))").format(len(approved)),
			"series_id": sid,
			"approved_booking_ids": approved,
			"failed": [],
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "approve_amenity_booking_series")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def reject_amenity_booking_series(series_id=None, rejection_reason=None):
	"""Staff: reject all Open Requests (and legacy Pending bookings) in a series."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can reject series"), frappe.PermissionError)

		payload = _parse_request_payload(
			{"series_id": series_id, "rejection_reason": rejection_reason}
		)
		sid = (payload.get("series_id") or "").strip()
		reason = (payload.get("rejection_reason") or "").strip()
		if not sid or not frappe.db.exists(AMENITY_BOOKING_SERIES, sid):
			return {"status": "error", "message": f"Series {sid} not found"}
		if not reason:
			return {"status": "error", "message": "rejection_reason is required"}

		series = frappe.get_doc(AMENITY_BOOKING_SERIES, sid, for_update=True)
		if series.status != "Pending":
			return {
				"status": "error",
				"message": f"Only Pending series can be rejected (current: {series.status})",
			}

		rejected_requests = []
		for row in _series_open_requests(sid):
			req = frappe.get_doc(AMENITY_BOOKING_REQUEST, row.name, for_update=True)
			if req.status != "Open":
				continue
			req.status = "Rejected"
			req.rejection_reason = reason
			req.save(ignore_permissions=True)
			rejected_requests.append(req.name)

		rejected_bookings = []
		for row in _series_bookings(sid, statuses=["Pending"]):
			doc = frappe.get_doc("Amenity Booking", row.name, for_update=True)
			if doc.status != "Pending":
				continue
			doc.status = "Rejected"
			doc.rejection_reason = reason
			doc.save(ignore_permissions=True)
			rejected_bookings.append(doc.name)

		series.reload()
		series.status = "Rejected"
		series.rejection_reason = reason
		series.save(ignore_permissions=True)
		frappe.db.commit()

		try:
			from propms.api.v1.amenities.notify import notify_amenity_series_rejected

			notify_amenity_series_rejected(series)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "reject_series.notify")

		return {
			"status": "success",
			"message": _("Series rejected ({0} request(s), {1} booking(s))").format(
				len(rejected_requests), len(rejected_bookings)
			),
			"series_id": sid,
			"rejected_request_ids": rejected_requests,
			"rejected_booking_ids": rejected_bookings,
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "reject_amenity_booking_series")
		return {"status": "error", "message": str(e)}


def _weekday_labels_from_series(series):
	labels = []
	for field, wd in WEEKDAY_KEYS:
		if cint(getattr(series, field, 0)):
			labels.append(WEEKDAY_NAMES[wd])
	return labels


def _next_occurrence_for_series(series_name):
	"""Next upcoming Open request or Confirmed booking date for hub cards."""
	today = getdate(nowdate())
	now = now_datetime()
	candidates = []

	for row in frappe.get_all(
		AMENITY_BOOKING_REQUEST,
		filters={"series": series_name, "status": "Open"},
		fields=["booking_date", "start_time", "name"],
		order_by="booking_date asc, start_time asc",
		limit=5,
	):
		d = getdate(row.booking_date)
		if d < today:
			continue
		if d == today:
			start_dt = get_datetime(f"{row.booking_date} {row.start_time}")
			if start_dt <= now:
				continue
		candidates.append(
			{
				"kind": "request",
				"id": row.name,
				"booking_date": str(row.booking_date),
				"start_time": str(row.start_time or ""),
			}
		)
		break

	for row in frappe.get_all(
		"Amenity Booking",
		filters={"series": series_name, "status": "Confirmed"},
		fields=["booking_date", "start_time", "name"],
		order_by="booking_date asc, start_time asc",
		limit=20,
	):
		d = getdate(row.booking_date)
		if d < today:
			continue
		if d == today:
			start_dt = get_datetime(f"{row.booking_date} {row.start_time}")
			if start_dt <= now:
				continue
		candidates.append(
			{
				"kind": "booking",
				"id": row.name,
				"booking_date": str(row.booking_date),
				"start_time": str(row.start_time or ""),
			}
		)
		break

	if not candidates:
		return None
	candidates.sort(key=lambda x: (x["booking_date"], x["start_time"]))
	return candidates[0]


def serialize_series_card(series_doc_or_dict):
	"""Hub card shape for one Amenity Booking Series."""
	if isinstance(series_doc_or_dict, str):
		s = frappe.get_doc(AMENITY_BOOKING_SERIES, series_doc_or_dict)
	elif hasattr(series_doc_or_dict, "as_dict"):
		s = series_doc_or_dict
	else:
		s = frappe._dict(series_doc_or_dict)

	amenity_meta = (
		frappe.db.get_value(
			"Amenity",
			s.amenity,
			["amenity_name", "category", "cover_image", "floor"],
			as_dict=True,
		)
		or {}
	)
	next_occ = _next_occurrence_for_series(s.name)
	weekday_labels = _weekday_labels_from_series(s)
	return {
		"kind": "series",
		"series_id": s.name,
		"name": s.name,
		"amenity": s.amenity,
		"amenity_name": amenity_meta.get("amenity_name") or s.amenity,
		"category": amenity_meta.get("category"),
		"cover_image": amenity_meta.get("cover_image"),
		"floor": amenity_meta.get("floor"),
		"status": s.status,
		"display_status": s.status,
		"series_start_date": str(s.series_start_date or ""),
		"series_end_date": str(s.series_end_date or ""),
		"start_time": str(s.start_time or ""),
		"end_time": str(s.end_time or ""),
		"weekday_labels": weekday_labels,
		"weekdays_label": " · ".join(weekday_labels),
		"guests_count": cint(s.guests_count) or 1,
		"tenant": s.tenant,
		"tenant_name": s.tenant_name,
		"property_unit": s.property_unit,
		"lease": s.lease,
		"notes": s.notes,
		"booked_count": cint(s.booked_count) or 0,
		"conflict_count": cint(s.conflict_count) or 0,
		"next_occurrence": next_occ,
		"detail_api": "propms.api.mobile.get_amenity_booking_series",
		"creation": str(getattr(s, "creation", "") or ""),
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_amenity_booking_series(series_id=None):
	"""Series detail for mobile: header + chronological children (requests + bookings)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"series_id": series_id})
		sid = (payload.get("series_id") or "").strip()
		if not sid or not frappe.db.exists(AMENITY_BOOKING_SERIES, sid):
			return {"status": "error", "message": f"Series {sid or '—'} not found"}

		series = frappe.get_doc(AMENITY_BOOKING_SERIES, sid)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		if not is_staff and series.tenant != current_user:
			frappe.throw(_("Not permitted to view this series"), frappe.PermissionError)

		card = serialize_series_card(series)

		req_rows = frappe.get_all(
			AMENITY_BOOKING_REQUEST,
			filters={"series": sid},
			fields=[
				"name",
				"booking_date",
				"start_time",
				"end_time",
				"status",
				"guests_count",
				"booking",
				"rejection_reason",
				"notes",
				"creation",
			],
			order_by="booking_date asc, start_time asc",
			ignore_permissions=True,
		)
		booking_rows = frappe.get_all(
			"Amenity Booking",
			filters={"series": sid},
			fields=[
				"name",
				"booking_date",
				"start_time",
				"end_time",
				"status",
				"guests_count",
				"cancellation_reason",
				"rejection_reason",
				"notes",
				"creation",
			],
			order_by="booking_date asc, start_time asc",
			ignore_permissions=True,
		)

		# Prefer booking row when request is Approved (avoid duplicate cards)
		booking_by_date = {}
		for b in booking_rows:
			key = (str(b.booking_date), str(b.start_time or ""))
			booking_by_date[key] = b

		occurrences = []
		seen_booking_names = set()
		for r in req_rows:
			key = (str(r.booking_date), str(r.start_time or ""))
			linked = booking_by_date.get(key)
			if r.status == "Approved" and linked:
				occurrences.append(
					{
						"kind": "booking",
						"booking_id": linked.name,
						"request_id": r.name,
						"booking_date": str(linked.booking_date),
						"start_time": str(linked.start_time or ""),
						"end_time": str(linked.end_time or ""),
						"status": linked.status,
						"display_status": linked.status,
						"guests_count": linked.guests_count,
						"cancellation_reason": linked.cancellation_reason,
						"rejection_reason": linked.rejection_reason,
						"notes": linked.notes,
					}
				)
				seen_booking_names.add(linked.name)
			else:
				occurrences.append(
					{
						"kind": "request",
						"request_id": r.name,
						"booking_id": r.booking or None,
						"booking_date": str(r.booking_date),
						"start_time": str(r.start_time or ""),
						"end_time": str(r.end_time or ""),
						"status": r.status,
						"display_status": "Pending" if r.status == "Open" else r.status,
						"guests_count": r.guests_count,
						"rejection_reason": r.rejection_reason,
						"notes": r.notes,
					}
				)

		for b in booking_rows:
			if b.name in seen_booking_names:
				continue
			occurrences.append(
				{
					"kind": "booking",
					"booking_id": b.name,
					"request_id": None,
					"booking_date": str(b.booking_date),
					"start_time": str(b.start_time or ""),
					"end_time": str(b.end_time or ""),
					"status": b.status,
					"display_status": b.status,
					"guests_count": b.guests_count,
					"cancellation_reason": b.cancellation_reason,
					"rejection_reason": b.rejection_reason,
					"notes": b.notes,
				}
			)

		occurrences.sort(
			key=lambda x: (x.get("booking_date") or "", x.get("start_time") or "")
		)

		return {
			"status": "success",
			"is_staff": is_staff,
			"series": card,
			"occurrences": occurrences,
			"occurrence_count": len(occurrences),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_amenity_booking_series")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def cancel_remaining_amenity_booking_series(series_id=None, cancellation_reason=None):
	"""Cancel remaining Open requests + future Confirmed bookings in a series.

	Past Completed / already Cancelled rows are left alone. Series becomes Cancelled
	when no Open/Confirmed future rows remain.
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{"series_id": series_id, "cancellation_reason": cancellation_reason}
		)
		sid = (payload.get("series_id") or "").strip()
		reason = (payload.get("cancellation_reason") or "").strip() or None
		if not sid or not frappe.db.exists(AMENITY_BOOKING_SERIES, sid):
			return {"status": "error", "message": f"Series {sid or '—'} not found"}

		series = frappe.get_doc(AMENITY_BOOKING_SERIES, sid, for_update=True)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		if not is_staff and series.tenant != current_user:
			frappe.throw(_("Not permitted to cancel this series"), frappe.PermissionError)

		if series.status in ("Cancelled", "Rejected"):
			return {
				"status": "error",
				"message": _("Series is already {0}").format(series.status),
			}

		from propms.api.v1.amenities.user_bookings import (
			_cancel_booking_doc,
			_cancel_open_request,
		)

		cancelled_requests = []
		cancelled_bookings = []
		failed = []

		for row in _series_open_requests(sid):
			req = frappe.get_doc(AMENITY_BOOKING_REQUEST, row.name)
			result = _cancel_open_request(
				req, reason, current_user, is_staff, notify=False, commit=False
			)
			if result.get("status") == "success":
				cancelled_requests.append(row.name)
			else:
				failed.append({"request_id": row.name, "message": result.get("message")})

		today = getdate(nowdate())
		now = now_datetime()
		for row in _series_bookings(sid, statuses=["Confirmed", "Pending"]):
			doc = frappe.get_doc("Amenity Booking", row.name)
			# Only cancel remaining (today future start or later dates)
			d = getdate(doc.booking_date)
			if d < today:
				continue
			if d == today:
				start_dt = get_datetime(f"{doc.booking_date} {doc.start_time}")
				if start_dt <= now:
					continue
			result = _cancel_booking_doc(
				doc, reason, current_user, is_staff, notify=False, commit=False
			)
			if result.get("status") == "success":
				cancelled_bookings.append(doc.name)
			else:
				failed.append({"booking_id": doc.name, "message": result.get("message")})

		series.reload()
		# Mark series Cancelled if nothing actionable remains
		still_open = _series_open_requests(sid)
		still_active = _series_bookings(sid, statuses=["Confirmed", "Pending"])
		future_active = []
		for row in still_active:
			d = getdate(row.booking_date)
			if d < today:
				continue
			if d == today:
				start_dt = get_datetime(f"{row.booking_date} {row.start_time}")
				if start_dt <= now:
					continue
			future_active.append(row)

		if not still_open and not future_active:
			series.status = "Cancelled"
			if reason:
				existing = (series.notes or "").strip()
				note = f"Cancelled remaining: {reason}"
				series.notes = f"{existing}\n{note}".strip() if existing else note
			series.save(ignore_permissions=True)

		frappe.db.commit()

		try:
			from propms.api.v1.amenities.notify import notify_amenity_series_cancelled

			notify_amenity_series_cancelled(series, cancelled_by=current_user)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "cancel_remaining_series.notify")

		return {
			"status": "success",
			"message": _("Cancelled {0} request(s) and {1} booking(s)").format(
				len(cancelled_requests), len(cancelled_bookings)
			),
			"series_id": sid,
			"series_status": series.status,
			"cancelled_request_ids": cancelled_requests,
			"cancelled_booking_ids": cancelled_bookings,
			"failed": failed,
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "cancel_remaining_amenity_booking_series")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_my_amenity_series(status="all", page=1, page_length=20, amenity=None):
	"""List Amenity Booking Series cards for tenant (or all for staff)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"status": status,
				"page": page,
				"page_length": page_length,
				"amenity": amenity,
			}
		)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		target = (payload.get("status") or "all").strip().lower()
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 20)))
		offset = (page_num - 1) * page_len

		filters = {}
		if not is_staff:
			filters["tenant"] = current_user
		amenity_name = (payload.get("amenity") or "").strip()
		if amenity_name:
			filters["amenity"] = amenity_name
		if target in ("pending", "confirmed", "cancelled", "rejected"):
			filters["status"] = target.title() if target != "pending" else "Pending"
			if target == "confirmed":
				filters["status"] = "Confirmed"
			elif target == "cancelled":
				filters["status"] = "Cancelled"
			elif target == "rejected":
				filters["status"] = "Rejected"

		rows = frappe.get_all(
			AMENITY_BOOKING_SERIES,
			filters=filters,
			fields=[
				"name",
				"amenity",
				"status",
				"series_start_date",
				"series_end_date",
				"start_time",
				"end_time",
				"week_mon",
				"week_tue",
				"week_wed",
				"week_thu",
				"week_fri",
				"week_sat",
				"week_sun",
				"tenant",
				"tenant_name",
				"property_unit",
				"lease",
				"guests_count",
				"notes",
				"booked_count",
				"conflict_count",
				"creation",
			],
			order_by="series_start_date desc, creation desc",
			ignore_permissions=True,
		)
		cards = [serialize_series_card(r) for r in rows]
		return {
			"status": "success",
			"is_staff": is_staff,
			"page": page_num,
			"page_length": page_len,
			"total": len(cards),
			"series": cards[offset : offset + page_len],
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_my_amenity_series")
		return {"status": "error", "message": str(e)}
