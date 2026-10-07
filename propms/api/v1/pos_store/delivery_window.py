# -*- coding: utf-8 -*-
"""Same-day delivery slots for Mobile POS Service qty products (e.g. drinking water).

Tenants pick one fixed 1-hour slot (e.g. 10:00–11:00), not a free-form from–to range.
Past slots for today are not bookable. Optional delivery notes stay on checkout.

Window open/close come from Mobile POS Service when the service has
requires_delivery_window. Legacy fallback: POS Services Settings / defaults.
"""

from __future__ import unicode_literals

from datetime import time

import frappe
from frappe import _
from frappe.utils import cint, get_time, getdate, now_datetime, today


DEFAULTS = {
	"open_time": "08:00:00",
	"close_time": "18:00:00",
}

# Product rule: every bookable slot is exactly one hour.
SLOT_DURATION_MINS = 60

MOBILE_POS_SERVICE = "Mobile POS Service"


def _time_to_mins(t):
	t = get_time(t) if not isinstance(t, time) else t
	return t.hour * 60 + t.minute


def _mins_to_time_str(mins):
	mins = int(mins) % (24 * 60)
	return f"{mins // 60:02d}:{mins % 60:02d}:00"


def _fmt_label(start_m, end_m):
	return f"{_mins_to_time_str(start_m)[:5]} – {_mins_to_time_str(end_m)[:5]}"


def _settings_dict(open_t, close_t):
	return {
		"open_time": get_time(open_t or DEFAULTS["open_time"]),
		"close_time": get_time(close_t or DEFAULTS["close_time"]),
		"slot_duration_mins": SLOT_DURATION_MINS,
	}


def get_delivery_settings_for_service(service_name=None, item_code=None):
	"""Load open/close from a Mobile POS Service (qty + requires_delivery_window)."""
	svc = None
	if service_name and frappe.db.exists("DocType", MOBILE_POS_SERVICE):
		if frappe.db.exists(MOBILE_POS_SERVICE, service_name):
			svc = frappe.get_cached_doc(MOBILE_POS_SERVICE, service_name)
	if not svc and item_code and frappe.db.exists("DocType", MOBILE_POS_SERVICE):
		name = frappe.db.get_value(
			MOBILE_POS_SERVICE,
			{"purchase_mode": "qty", "item": item_code, "enabled": 1},
			"name",
		)
		if name:
			svc = frappe.get_cached_doc(MOBILE_POS_SERVICE, name)

	if svc and cint(getattr(svc, "requires_delivery_window", 0)):
		return _settings_dict(
			getattr(svc, "delivery_open_time", None),
			getattr(svc, "delivery_close_time", None),
		)

	return get_water_delivery_settings()


def get_water_delivery_settings():
	"""Legacy fallback: first qty service with delivery window, else Settings, else defaults."""
	if frappe.db.exists("DocType", MOBILE_POS_SERVICE) and frappe.db.has_column(
		MOBILE_POS_SERVICE, "requires_delivery_window"
	):
		name = frappe.db.get_value(
			MOBILE_POS_SERVICE,
			{"purchase_mode": "qty", "requires_delivery_window": 1, "enabled": 1},
			"name",
			order_by="sort_order asc, modified asc",
		)
		if name:
			svc = frappe.get_cached_doc(MOBILE_POS_SERVICE, name)
			return _settings_dict(svc.delivery_open_time, svc.delivery_close_time)

	open_t = DEFAULTS["open_time"]
	close_t = DEFAULTS["close_time"]
	doc = None
	if frappe.db.exists("DocType", "POS Services Settings"):
		doc = frappe.get_single("POS Services Settings")
	elif frappe.db.exists("DocType", "Mobile App Settings"):
		doc = frappe.get_single("Mobile App Settings")
	if doc:
		open_t = getattr(doc, "water_delivery_open_time", None) or open_t
		close_t = getattr(doc, "water_delivery_close_time", None) or close_t
	return _settings_dict(open_t, close_t)


def build_delivery_slots(settings=None, now=None, delivery_date=None):
	"""Build same-day 1-hour slots; mark past (already started) as unavailable."""
	settings = settings or get_water_delivery_settings()
	now = now or now_datetime()
	if isinstance(now, str):
		from frappe.utils import get_datetime

		now = get_datetime(now)

	today_d = getdate(now)
	req_date = getdate(delivery_date) if delivery_date else today_d
	open_t = settings["open_time"] if isinstance(settings["open_time"], time) else get_time(settings["open_time"])
	close_t = settings["close_time"] if isinstance(settings["close_time"], time) else get_time(settings["close_time"])
	slot_mins = cint(settings.get("slot_duration_mins") or SLOT_DURATION_MINS)
	open_m = _time_to_mins(open_t)
	close_m = _time_to_mins(close_t)

	slots = []
	if close_m <= open_m or slot_mins <= 0:
		return slots

	# Only same-day slots are offered.
	if req_date != today_d:
		return slots

	now_m = now.hour * 60 + now.minute
	if now.second > 0 or now.microsecond > 0:
		now_m += 1

	start_m = open_m
	while start_m + slot_mins <= close_m:
		end_m = start_m + slot_mins
		# Slot already started (or finished) → not bookable.
		available = start_m >= now_m
		slots.append(
			{
				"start": _mins_to_time_str(start_m),
				"end": _mins_to_time_str(end_m),
				"label": _fmt_label(start_m, end_m),
				"available": available,
			}
		)
		start_m += slot_mins

	return slots


def serialize_delivery_window_for_api(
	settings=None, delivery_date=None, now=None, service_name=None, item_code=None
):
	"""API payload for mobile: list of 1-hour slots (past ones marked unavailable)."""
	if settings is None and (service_name or item_code):
		settings = get_delivery_settings_for_service(service_name=service_name, item_code=item_code)
	settings = settings or get_water_delivery_settings()
	now = now or now_datetime()
	od = delivery_date or today()
	all_slots = build_delivery_slots(settings=settings, now=now, delivery_date=od)
	bookable = [s for s in all_slots if s.get("available")]
	return {
		"same_day_only": True,
		"slot_duration_mins": cint(settings.get("slot_duration_mins") or SLOT_DURATION_MINS),
		"open_time": settings["open_time"].strftime("%H:%M:%S")
		if hasattr(settings["open_time"], "strftime")
		else str(settings["open_time"]),
		"close_time": settings["close_time"].strftime("%H:%M:%S")
		if hasattr(settings["close_time"], "strftime")
		else str(settings["close_time"]),
		"delivery_date": str(getdate(od)),
		# Primary picker list: only slots the tenant can still choose.
		"slots": bookable,
		# Full day for UIs that want to show disabled past slots.
		"all_slots": all_slots,
		"mobile_pos_service": service_name or None,
		"item_code": item_code or None,
	}


def validate_delivery_window(
	start, end=None, settings=None, now=None, delivery_date=None, service_name=None, item_code=None
):
	"""Validate chosen 1-hour slot. If only start is sent, end = start + 60 mins."""
	if settings is None and (service_name or item_code):
		settings = get_delivery_settings_for_service(service_name=service_name, item_code=item_code)
	settings = settings or get_water_delivery_settings()
	now = now or now_datetime()
	if isinstance(now, str):
		from frappe.utils import get_datetime

		now = get_datetime(now)

	today_d = getdate(now)
	req_date = getdate(delivery_date) if delivery_date else today_d
	if req_date != today_d:
		frappe.throw(_("Delivery is same-day only"), frappe.ValidationError)

	if not start:
		frappe.throw(_("Please choose a delivery time slot"), frappe.ValidationError)

	start_t = get_time(start)
	slot_mins = cint(settings.get("slot_duration_mins") or SLOT_DURATION_MINS)
	if end:
		end_t = get_time(end)
	else:
		# Client sent start only — compute the fixed 1-hour end.
		end_m = _time_to_mins(start_t) + slot_mins
		end_t = get_time(_mins_to_time_str(end_m))

	start_m = _time_to_mins(start_t)
	end_m = _time_to_mins(end_t)
	if end_m - start_m != slot_mins:
		frappe.throw(
			_("Delivery slot must be exactly {0} minutes").format(slot_mins),
			frappe.ValidationError,
		)

	bookable = {
		(s["start"], s["end"])
		for s in build_delivery_slots(settings=settings, now=now, delivery_date=req_date)
		if s.get("available")
	}
	chosen = (_mins_to_time_str(start_m), _mins_to_time_str(end_m))
	if chosen not in bookable:
		frappe.throw(
			_("That delivery slot is not available. Please choose another time."),
			frappe.ValidationError,
		)

	return {
		"delivery_date": str(today_d),
		"delivery_time_start": chosen[0],
		"delivery_time_end": chosen[1],
	}
