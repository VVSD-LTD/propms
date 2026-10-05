# -*- coding: utf-8 -*-
"""Exclusive amenity range math (buffer, gaps, step). No DB I/O except find_conflicting_booking."""

from __future__ import unicode_literals


def _parse_hms(val):
	s = str(val or "00:00:00").strip()
	if len(s) == 5:
		s = s + ":00"
	parts = s.split(":")
	h, m = int(parts[0]), int(parts[1])
	sec = int(parts[2]) if len(parts) > 2 else 0
	return h * 3600 + m * 60 + sec


def _format_hms(total_seconds):
	total_seconds = int(total_seconds)
	if total_seconds < 0:
		total_seconds = 0
	h = total_seconds // 3600
	m = (total_seconds % 3600) // 60
	s = total_seconds % 60
	return f"{h:02d}:{m:02d}:{s:02d}"


def ranges_overlap(start_a, end_a, start_b, end_b):
	"""True if [start_a, end_a) overlaps [start_b, end_b). Touching endpoints do not overlap."""
	a0, a1 = _parse_hms(start_a), _parse_hms(end_a)
	b0, b1 = _parse_hms(start_b), _parse_hms(end_b)
	return not (a1 <= b0 or b1 <= a0)


def expand_end_with_buffer(end_time, buffer_mins):
	buf = max(0, int(buffer_mins or 0))
	return _format_hms(_parse_hms(end_time) + buf * 60)


def is_time_on_step(time_str, step_mins):
	step = max(1, int(step_mins or 1))
	secs = _parse_hms(time_str)
	return (secs % (step * 60)) == 0


def compute_free_gaps(open_time, close_time, busy):
	"""busy items need start_time + end_with_buffer. Returns sorted free gaps inside open–close."""
	open_s, close_s = _parse_hms(open_time), _parse_hms(close_time)
	blocks = []
	for b in busy or []:
		blocks.append((_parse_hms(b["start_time"]), _parse_hms(b["end_with_buffer"])))
	blocks.sort()
	merged = []
	for s, e in blocks:
		s = max(s, open_s)
		e = min(e, close_s)
		if e <= s:
			continue
		if not merged or s > merged[-1][1]:
			merged.append([s, e])
		else:
			merged[-1][1] = max(merged[-1][1], e)
	gaps = []
	cursor = open_s
	for s, e in merged:
		if s > cursor:
			gaps.append({"start_time": _format_hms(cursor), "end_time": _format_hms(s)})
		cursor = max(cursor, e)
	if cursor < close_s:
		gaps.append({"start_time": _format_hms(cursor), "end_time": _format_hms(close_s)})
	return gaps


def find_conflicting_booking(
	amenity,
	booking_date,
	start_time,
	end_time,
	buffer_mins,
	exclude_name=None,
	exclude_request=None,
):
	"""DB helper: first conflicting Confirmed/Pending booking or Open request, or None.

	Returned dict includes ``source``: ``"booking"`` or ``"request"``.
	Pending bookings are still checked during the migration window.
	"""
	import frappe

	req_end_buf = expand_end_with_buffer(end_time, buffer_mins)

	booking_rows = frappe.get_all(
		"Amenity Booking",
		filters={
			"amenity": amenity,
			"booking_date": str(booking_date),
			"status": ["in", ["Pending", "Confirmed"]],
		},
		fields=["name", "start_time", "end_time", "tenant", "tenant_name", "status"],
		ignore_permissions=True,
	)
	for r in booking_rows or []:
		if exclude_name and r.name == exclude_name:
			continue
		other_end = expand_end_with_buffer(r.end_time, buffer_mins)
		if ranges_overlap(start_time, req_end_buf, r.start_time, other_end):
			r["source"] = "booking"
			return r

	if frappe.db.exists("DocType", "Amenity Booking Request"):
		req_rows = frappe.get_all(
			"Amenity Booking Request",
			filters={
				"amenity": amenity,
				"booking_date": str(booking_date),
				"status": "Open",
			},
			fields=["name", "start_time", "end_time", "tenant", "tenant_name", "status"],
			ignore_permissions=True,
		)
		for r in req_rows or []:
			if exclude_request and r.name == exclude_request:
				continue
			other_end = expand_end_with_buffer(r.end_time, buffer_mins)
			if ranges_overlap(start_time, req_end_buf, r.start_time, other_end):
				r["source"] = "request"
				return r
	return None
