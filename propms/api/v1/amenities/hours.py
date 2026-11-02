# -*- coding: utf-8 -*-
"""Per-day + public-holiday operating hours for Amenity."""

from __future__ import unicode_literals

from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, getdate

WEEKDAY_NAMES = (
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
	"Sunday",
)

HOLIDAY_MODES = (
	"Closed",
	"Same as Saturday",
	"Same as Sunday",
	"Custom",
)


def _parse_time_str(val):
	"""Parse time / timedelta / datetime.time into datetime.time."""
	from frappe.utils import get_time

	if not val:
		return get_time("00:00:00")
	if isinstance(val, timedelta):
		total_seconds = int(val.total_seconds()) % (24 * 3600)
		hours = total_seconds // 3600
		minutes = (total_seconds % 3600) // 60
		seconds = total_seconds % 60
		return datetime.strptime(f"{hours:02d}:{minutes:02d}:{seconds:02d}", "%H:%M:%S").time()
	if isinstance(val, str):
		try:
			return datetime.strptime(val[:8], "%H:%M:%S").time()
		except Exception:
			try:
				return datetime.strptime(val[:5], "%H:%M").time()
			except Exception:
				return get_time(val)
	if hasattr(val, "strftime"):
		return val
	return get_time(val)


def _time_hms(val):
	return _parse_time_str(val).strftime("%H:%M:%S")


def serialize_day_hours(amenity_doc):
	"""List of day-hour rows for mobile / desk APIs."""
	rows = getattr(amenity_doc, "day_hours", None) or []
	out = []
	for row in rows:
		out.append(
			{
				"day_of_week": row.day_of_week,
				"is_open": 1 if cint(row.is_open) else 0,
				"open_time": _time_hms(row.open_time) if row.open_time else None,
				"close_time": _time_hms(row.close_time) if row.close_time else None,
			}
		)
	order = {name: i for i, name in enumerate(WEEKDAY_NAMES)}
	out.sort(key=lambda r: order.get(r.get("day_of_week") or "", 99))
	return out


def summary_open_close(day_hours_rows=None, amenity_doc=None):
	"""Earliest open / latest close across open days (for mobile list compat)."""
	rows = day_hours_rows
	if rows is None and amenity_doc is not None:
		rows = serialize_day_hours(amenity_doc)
	opens = []
	closes = []
	for row in rows or []:
		if not cint(row.get("is_open") if isinstance(row, dict) else getattr(row, "is_open", 0)):
			continue
		o = row.get("open_time") if isinstance(row, dict) else row.open_time
		c = row.get("close_time") if isinstance(row, dict) else row.close_time
		if o and c:
			opens.append(_time_hms(o))
			closes.append(_time_hms(c))
	if not opens:
		return "06:00:00", "22:00:00"
	return min(opens), max(closes)


def serialize_holiday_hours(amenity_doc):
	"""Holiday config for mobile clients."""
	hl = (getattr(amenity_doc, "holiday_list", None) or "").strip() or None
	mode = (getattr(amenity_doc, "holiday_hours", None) or "").strip()
	if mode not in HOLIDAY_MODES:
		mode = ""
	return {
		"holiday_list": hl,
		"holiday_hours": mode or None,
		"holiday_open_time": (
			_time_hms(amenity_doc.holiday_open_time)
			if hl and mode == "Custom" and getattr(amenity_doc, "holiday_open_time", None)
			else None
		),
		"holiday_close_time": (
			_time_hms(amenity_doc.holiday_close_time)
			if hl and mode == "Custom" and getattr(amenity_doc, "holiday_close_time", None)
			else None
		),
	}


def load_day_hours_map(amenity_doc_or_name):
	"""Return {day_name: {is_open, open_time, close_time}} from child table or DB."""
	rows = []
	if hasattr(amenity_doc_or_name, "day_hours"):
		rows = amenity_doc_or_name.day_hours or []
		name = amenity_doc_or_name.name
	else:
		name = amenity_doc_or_name

	if not rows and name and frappe.db.exists("DocType", "Amenity Day Hours"):
		rows = frappe.get_all(
			"Amenity Day Hours",
			filters={"parent": name, "parenttype": "Amenity"},
			fields=["day_of_week", "is_open", "open_time", "close_time"],
			order_by="idx asc",
			ignore_permissions=True,
		)

	by_day = {}
	for row in rows:
		day = getattr(row, "day_of_week", None) or (
			row.get("day_of_week") if isinstance(row, dict) else None
		)
		if not day:
			continue
		is_open = cint(
			getattr(row, "is_open", None) if not isinstance(row, dict) else row.get("is_open")
		)
		open_t = (
			getattr(row, "open_time", None) if not isinstance(row, dict) else row.get("open_time")
		)
		close_t = (
			getattr(row, "close_time", None) if not isinstance(row, dict) else row.get("close_time")
		)
		by_day[day] = {
			"is_open": 1 if is_open else 0,
			"open_time": _time_hms(open_t) if open_t else None,
			"close_time": _time_hms(close_t) if close_t else None,
		}
	return by_day


def resolve_holiday_list(amenity_doc):
	"""Only the Amenity's own Holiday List (no company fallback)."""
	hl = (getattr(amenity_doc, "holiday_list", None) or "").strip()
	if hl and frappe.db.exists("Holiday List", hl):
		return hl
	return None


def get_public_holiday(amenity_doc, booking_date):
	"""Return {is_holiday, description, holiday_list} for public holidays only.

	Ignores Holiday rows with weekly_off=1 (those are weekly offs already covered by day_hours).
	"""
	d = getdate(booking_date)
	hl = resolve_holiday_list(amenity_doc)
	if not hl:
		return {"is_holiday": False, "description": None, "holiday_list": None}

	row = frappe.db.get_value(
		"Holiday",
		{"parent": hl, "holiday_date": d, "weekly_off": 0},
		["holiday_date", "description"],
		as_dict=True,
	)
	if not row:
		return {"is_holiday": False, "description": None, "holiday_list": hl}
	return {
		"is_holiday": True,
		"description": row.description or "Public Holiday",
		"holiday_list": hl,
	}


def _hours_from_day_row(by_day, day_name, amenity_doc):
	row = by_day.get(day_name) if by_day else None
	if by_day:
		if not row or not row.get("is_open"):
			return {
				"is_open": False,
				"open_time": None,
				"close_time": None,
			}
		return {
			"is_open": True,
			"open_time": row.get("open_time") or "06:00:00",
			"close_time": row.get("close_time") or "22:00:00",
		}
	# No day_hours rows — safe defaults
	return {
		"is_open": True,
		"open_time": "06:00:00",
		"close_time": "22:00:00",
	}


def _as_amenity_doc(amenity_doc_or_name):
	"""Accept Amenity doc or name."""
	if hasattr(amenity_doc_or_name, "doctype"):
		return amenity_doc_or_name
	if isinstance(amenity_doc_or_name, str) and amenity_doc_or_name:
		return frappe.get_cached_doc("Amenity", amenity_doc_or_name)
	return amenity_doc_or_name


def get_operating_hours_for_date(amenity_doc, booking_date):
	"""Return hours for a calendar date (weekday + public holiday override).

	Returns dict with:
	  is_open, open_time, close_time, day_of_week, source,
	  is_holiday, holiday_description, holiday_list
	"""
	amenity_doc = _as_amenity_doc(amenity_doc)
	d = getdate(booking_date)
	day_name = WEEKDAY_NAMES[d.weekday()]
	by_day = load_day_hours_map(amenity_doc)
	holiday = get_public_holiday(amenity_doc, d)
	mode = (getattr(amenity_doc, "holiday_hours", None) or "").strip()
	if mode not in HOLIDAY_MODES:
		mode = ""

	base = {
		"day_of_week": day_name,
		"is_holiday": 1 if holiday.get("is_holiday") else 0,
		"holiday_description": holiday.get("description"),
		"holiday_list": holiday.get("holiday_list"),
	}

	# Public holiday override only when list is set AND a mode is chosen
	if holiday.get("is_holiday") and mode:
		if mode == "Closed":
			return {
				**base,
				"is_open": False,
				"open_time": None,
				"close_time": None,
				"source": "holiday_closed",
			}
		if mode == "Same as Saturday":
			h = _hours_from_day_row(by_day, "Saturday", amenity_doc)
			return {**base, **h, "source": "holiday_saturday"}
		if mode == "Same as Sunday":
			h = _hours_from_day_row(by_day, "Sunday", amenity_doc)
			return {**base, **h, "source": "holiday_sunday"}
		if mode == "Custom":
			open_s = _time_hms(getattr(amenity_doc, "holiday_open_time", None) or "06:00:00")
			close_s = _time_hms(getattr(amenity_doc, "holiday_close_time", None) or "23:00:00")
			return {
				**base,
				"is_open": True,
				"open_time": open_s,
				"close_time": close_s,
				"source": "holiday_custom",
			}

	# Normal weekday / weekend from day_hours
	h = _hours_from_day_row(by_day, day_name, amenity_doc)
	source = "day_hours" if by_day else "legacy"
	return {**base, **h, "source": source}


def ensure_default_day_hours(doc):
	"""Ensure Amenity has one Mon–Sun row."""
	existing = {r.day_of_week: r for r in (doc.day_hours or []) if r.day_of_week}
	default_open = "06:00:00"
	default_close = "22:00:00"

	if not existing:
		doc.set("day_hours", [])
		for day in WEEKDAY_NAMES:
			doc.append(
				"day_hours",
				{
					"day_of_week": day,
					"is_open": 1,
					"open_time": default_open,
					"close_time": default_close,
				},
			)
		return

	for day in WEEKDAY_NAMES:
		if day not in existing:
			doc.append(
				"day_hours",
				{
					"day_of_week": day,
					"is_open": 1,
					"open_time": default_open,
					"close_time": default_close,
				},
			)


def validate_day_hours(doc):
	"""Validate child rows + holiday custom times."""
	ensure_default_day_hours(doc)
	seen = set()
	for row in doc.day_hours or []:
		day = row.day_of_week
		if not day:
			frappe.throw(_("Day is required on each Operating Hours row"))
		if day in seen:
			frappe.throw(_("Duplicate day in Operating Hours: {0}").format(day))
		seen.add(day)
		if not cint(row.is_open):
			continue
		if not row.open_time or not row.close_time:
			frappe.throw(_("Open and Close time required for {0}").format(day))
		o = _time_hms(row.open_time)
		c = _time_hms(row.close_time)
		if c <= o:
			frappe.throw(_("Close time must be after open time for {0}").format(day))

	# Clear holiday mode when no list is linked
	if not (doc.holiday_list or "").strip():
		doc.holiday_hours = None
		doc.holiday_open_time = None
		doc.holiday_close_time = None
	else:
		if doc.holiday_list and not frappe.db.exists("Holiday List", doc.holiday_list):
			frappe.throw(_("Holiday List {0} not found").format(doc.holiday_list))
		mode = (doc.holiday_hours or "").strip()
		if mode and mode not in HOLIDAY_MODES:
			frappe.throw(_("Invalid On Public Holiday option"))
		if mode == "Custom":
			if not doc.holiday_open_time or not doc.holiday_close_time:
				frappe.throw(_("Holiday Open and Close time are required for Custom holiday hours"))
			ho = _time_hms(doc.holiday_open_time)
			hc = _time_hms(doc.holiday_close_time)
			if hc <= ho:
				frappe.throw(_("Holiday Close time must be after Holiday Open time"))
		elif mode != "Custom":
			doc.holiday_open_time = None
			doc.holiday_close_time = None
