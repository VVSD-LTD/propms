# -*- coding: utf-8 -*-
"""Same-day drinking-water delivery window helpers for POS Store."""

from __future__ import unicode_literals

from datetime import datetime, time, timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_time, getdate, now_datetime, today


DEFAULTS = {
	"open_time": "08:00:00",
	"close_time": "18:00:00",
	"min_window_mins": 120,
	"picker_step_mins": 30,
}


def _time_to_mins(t):
	t = get_time(t) if not isinstance(t, time) else t
	return t.hour * 60 + t.minute


def _mins_to_time_str(mins):
	mins = int(mins) % (24 * 60)
	return f"{mins // 60:02d}:{mins % 60:02d}:00"


def ceil_to_step(dt, step_mins):
	"""Ceil datetime to next picker step (same calendar day minutes)."""
	step = max(1, cint(step_mins))
	total = dt.hour * 60 + dt.minute
	if dt.second > 0 or dt.microsecond > 0:
		total += 1
	rem = total % step
	if rem:
		total += step - rem
	return dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=total)


def get_water_delivery_settings():
	"""Load settings from Mobile App Settings with defaults."""
	open_t = DEFAULTS["open_time"]
	close_t = DEFAULTS["close_time"]
	min_m = DEFAULTS["min_window_mins"]
	step = DEFAULTS["picker_step_mins"]
	if frappe.db.exists("DocType", "Mobile App Settings"):
		doc = frappe.get_single("Mobile App Settings")
		open_t = getattr(doc, "water_delivery_open_time", None) or open_t
		close_t = getattr(doc, "water_delivery_close_time", None) or close_t
		min_m = cint(getattr(doc, "water_delivery_min_window_mins", None) or min_m)
		step = cint(getattr(doc, "water_delivery_picker_step_mins", None) or step)
	return {
		"open_time": get_time(open_t),
		"close_time": get_time(close_t),
		"min_window_mins": max(1, min_m),
		"picker_step_mins": max(1, step),
	}


def serialize_delivery_window_for_api(settings=None, delivery_date=None):
	settings = settings or get_water_delivery_settings()
	od = delivery_date or today()
	return {
		"same_day_only": True,
		"open_time": settings["open_time"].strftime("%H:%M:%S")
		if hasattr(settings["open_time"], "strftime")
		else str(settings["open_time"]),
		"close_time": settings["close_time"].strftime("%H:%M:%S")
		if hasattr(settings["close_time"], "strftime")
		else str(settings["close_time"]),
		"min_window_mins": settings["min_window_mins"],
		"picker_step_mins": settings["picker_step_mins"],
		"delivery_date": str(getdate(od)),
	}


def validate_delivery_window(start, end, settings=None, now=None, delivery_date=None):
	"""Raise ValidationError if window is invalid. Returns normalized dict."""
	settings = settings or get_water_delivery_settings()
	now = now or now_datetime()
	if isinstance(now, str):
		from frappe.utils import get_datetime

		now = get_datetime(now)

	today_d = getdate(now)
	req_date = getdate(delivery_date) if delivery_date else today_d
	if req_date != today_d:
		frappe.throw(_("Delivery is same-day only"), frappe.ValidationError)

	if not start or not end:
		frappe.throw(_("delivery_time_start and delivery_time_end are required"), frappe.ValidationError)

	start_t = get_time(start)
	end_t = get_time(end)
	open_t = settings["open_time"] if isinstance(settings["open_time"], time) else get_time(settings["open_time"])
	close_t = settings["close_time"] if isinstance(settings["close_time"], time) else get_time(settings["close_time"])
	min_m = cint(settings["min_window_mins"])
	step = cint(settings["picker_step_mins"])

	start_m = _time_to_mins(start_t)
	end_m = _time_to_mins(end_t)
	open_m = _time_to_mins(open_t)
	close_m = _time_to_mins(close_t)

	if end_m <= start_m:
		frappe.throw(_("Delivery window end must be after start"), frappe.ValidationError)
	if (end_m - start_m) < min_m:
		frappe.throw(
			_("Delivery window must be at least {0} minutes").format(min_m),
			frappe.ValidationError,
		)
	if start_m < open_m or end_m > close_m:
		frappe.throw(
			_("Delivery window must be within {0}–{1}").format(
				_mins_to_time_str(open_m)[:5], _mins_to_time_str(close_m)[:5]
			),
			frappe.ValidationError,
		)

	earliest = ceil_to_step(now, step)
	if earliest.date() != now.date():
		frappe.throw(
			_("Delivery windows for today are closed; try again tomorrow."),
			frappe.ValidationError,
		)
	earliest_m = max(open_m, earliest.hour * 60 + earliest.minute)
	last_start_m = close_m - min_m
	if earliest_m > last_start_m:
		frappe.throw(
			_("Delivery windows for today are closed; try again tomorrow."),
			frappe.ValidationError,
		)
	if start_m < earliest_m:
		frappe.throw(
			_("Delivery window start must be at or after {0}").format(_mins_to_time_str(earliest_m)[:5]),
			frappe.ValidationError,
		)

	return {
		"delivery_date": str(today_d),
		"delivery_time_start": start_t.strftime("%H:%M:%S"),
		"delivery_time_end": end_t.strftime("%H:%M:%S"),
	}
