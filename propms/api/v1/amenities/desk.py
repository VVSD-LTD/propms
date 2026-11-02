# -*- coding: utf-8 -*-
"""Desk helpers for Amenity Booking Request (staff form)."""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate

from propms.api.v1.amenities.series import WEEKDAY_KEYS, _iter_occurrence_dates
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit

MAX_DESK_OCCURRENCES = 60


@frappe.whitelist()
def get_resident_booking_defaults(user=None):
	"""Desk helper: unit / lease / display name for a resident User."""
	user = (user or "").strip()
	if not user or not frappe.db.exists("User", user):
		return {"status": "error", "message": _("Valid User is required")}

	email = frappe.db.get_value("User", user, "email") or user
	full_name = frappe.db.get_value("User", user, "full_name") or user
	unit, lease, resident = _get_tenant_default_unit(email)
	return {
		"status": "success",
		"tenant": user,
		"tenant_name": resident or full_name,
		"property_unit": unit,
		"lease": lease,
	}


@frappe.whitelist()
def generate_request_occurrence_dates(
	from_date=None,
	until_date=None,
	start_time=None,
	end_time=None,
	week_mon=None,
	week_tue=None,
	week_wed=None,
	week_thu=None,
	week_fri=None,
	week_sat=None,
	week_sun=None,
):
	"""Build occurrence rows for a Recurring desk request (From → Until + weekdays)."""
	if not from_date or not until_date:
		return {"status": "error", "message": _("From Date and Until Date are required")}
	if not start_time or not end_time:
		return {"status": "error", "message": _("Start Time and End Time are required")}

	start = getdate(from_date)
	end = getdate(until_date)
	if end < start:
		return {"status": "error", "message": _("Until Date must be on or after From Date")}

	payload = {
		"week_mon": week_mon,
		"week_tue": week_tue,
		"week_wed": week_wed,
		"week_thu": week_thu,
		"week_fri": week_fri,
		"week_sat": week_sat,
		"week_sun": week_sun,
	}
	weekdays = set()
	for field, wd in WEEKDAY_KEYS:
		if cint(payload.get(field)):
			weekdays.add(wd)
	if not weekdays:
		return {"status": "error", "message": _("Select at least one weekday")}

	from propms.api.v1.amenities.slots import _parse_time_str

	s_time = _parse_time_str(start_time).strftime("%H:%M:%S")
	e_time = _parse_time_str(end_time).strftime("%H:%M:%S")
	if e_time <= s_time:
		return {"status": "error", "message": _("End Time must be after Start Time")}

	rows = []
	for d in _iter_occurrence_dates(start, end, weekdays):
		rows.append(
			{
				"booking_date": str(d),
				"start_time": s_time,
				"end_time": e_time,
				"status": "Pending",
			}
		)
		if len(rows) >= MAX_DESK_OCCURRENCES:
			break

	if not rows:
		return {
			"status": "error",
			"message": _("No matching dates in that range for the selected weekdays"),
		}

	return {
		"status": "success",
		"occurrences": rows,
		"count": len(rows),
		"truncated": len(rows) >= MAX_DESK_OCCURRENCES,
		"max_occurrences": MAX_DESK_OCCURRENCES,
	}
