# -*- coding: utf-8 -*-
"""Manual amenity booking endpoint check (bench execute)."""

from __future__ import unicode_literals

import json
from datetime import timedelta

import frappe
from frappe.utils import add_days, getdate, nowdate


def _dump(title, data):
	print("\n=== {} ===".format(title))
	if isinstance(data, dict):
		safe = {k: v for k, v in data.items() if k != "doc"}
		print(json.dumps(safe, default=str, indent=2)[:2500])
	else:
		print(data)


def _pick_slot(slots_resp, prefer_afternoon=True):
	avail = (
		slots_resp.get("available_slots")
		or slots_resp.get("slots")
		or slots_resp.get("data")
		or []
	)
	candidates = []
	for s in avail:
		if not isinstance(s, dict):
			continue
		if s.get("is_available") is False or s.get("available") is False:
			continue
		if s.get("is_past"):
			continue
		if int(s.get("booked_count") or 0) > 0:
			continue
		st = s.get("start_time") or s.get("start")
		et = s.get("end_time") or s.get("end")
		if st and et:
			candidates.append((str(st)[:8], str(et)[:8]))
	if prefer_afternoon:
		for st, et in candidates:
			if st >= "12:00:00":
				return st, et
	if candidates:
		return candidates[-1]
	return "18:00:00", "18:30:00"


def run():
	print("series_type column:", frappe.db.has_column("Amenity Booking Series", "series_type"))

	from propms.api.mobile import (
		approve_amenity_booking,
		create_amenity_booking,
		create_recurring_amenity_booking,
		get_amenity_booking_series,
		get_available_slots,
		get_my_amenity_bookings,
	)

	tenant = "baraka@vvsdtz.com"
	frappe.set_user(tenant)

	amenity = "Court"
	booking_date = str(add_days(getdate(nowdate()), 5))
	slots = get_available_slots(amenity=amenity, booking_date=booking_date)
	_dump("SLOTS", {"status": slots.get("status"), "keys": list(slots.keys())[:10]})
	start_time, end_time = _pick_slot(slots)
	print("picked", booking_date, start_time, end_time)

	# 1) One-time auto
	r = create_amenity_booking(
		amenity=amenity,
		booking_date=booking_date,
		start_time=start_time,
		end_time=end_time,
		guests_count=1,
		notes="API test one-time auto",
	)
	_dump("CREATE ONE-TIME AUTO", r)
	assert r.get("status") == "success", r
	assert r.get("series_id"), r
	assert r.get("series_type") == "One Time", r
	assert r.get("request_id"), r
	assert r.get("booking_id"), "auto amenity must create booking"
	assert r.get("auto_approved") is True, r

	s = frappe.db.get_value(
		"Amenity Booking Series",
		r["series_id"],
		["series_type", "status"],
		as_dict=True,
	)
	req = frappe.db.get_value(
		"Amenity Booking Request",
		r["request_id"],
		["status", "series", "booking"],
		as_dict=True,
	)
	b = frappe.db.get_value(
		"Amenity Booking",
		r["booking_id"],
		["status", "series"],
		as_dict=True,
	)
	_dump("DB CHAIN ONE-TIME", {"series": s, "request": req, "booking": b})
	assert s.series_type == "One Time"
	assert s.status == "Confirmed"
	assert req.status == "Approved" and req.series == r["series_id"]
	assert b.status == "Confirmed" and b.series == r["series_id"]

	det = get_amenity_booking_series(series_id=r["series_id"])
	_dump(
		"SERIES DETAIL",
		{
			"status": det.get("status"),
			"card_series_type": (det.get("series") or {}).get("series_type")
			if isinstance(det.get("series"), dict)
			else None,
			"keys": list(det.keys())[:12],
		},
	)
	assert det.get("status") == "success"

	hub = get_my_amenity_bookings(status="all", page=1, page_length=10)
	_dump(
		"HUB",
		{
			"status": hub.get("status"),
			"hub_len": len(hub.get("hub") or []),
			"first": (hub.get("hub") or [None])[0],
		},
	)
	assert hub.get("status") == "success"

	# 2) One-time pending + staff approve
	party = "Party Hall"
	frappe.set_user("Administrator")
	prev_auto = frappe.db.get_value("Amenity", party, "auto_approval")
	frappe.db.set_value("Amenity", party, "auto_approval", 0)
	frappe.db.commit()

	frappe.set_user(tenant)
	booking_date2 = str(add_days(getdate(nowdate()), 6))
	slots2 = get_available_slots(amenity=party, booking_date=booking_date2)
	st2, et2 = _pick_slot(slots2)
	r2 = create_amenity_booking(
		amenity=party,
		booking_date=booking_date2,
		start_time=st2,
		end_time=et2,
		guests_count=1,
		notes="API test one-time pending",
	)
	_dump("CREATE ONE-TIME PENDING", r2)
	assert r2.get("status") == "success", r2
	assert r2.get("series_id") and r2.get("request_id")
	assert r2.get("booking_id") in (None, "")
	assert r2.get("auto_approved") is False
	assert r2.get("series_type") == "One Time"

	s2 = frappe.db.get_value(
		"Amenity Booking Series",
		r2["series_id"],
		["series_type", "status"],
		as_dict=True,
	)
	assert s2.status == "Pending" and s2.series_type == "One Time"

	frappe.set_user("Administrator")
	apr = approve_amenity_booking(request_id=r2["request_id"])
	_dump("STAFF APPROVE", apr)
	assert apr.get("status") == "success", apr
	assert apr.get("booking_id")

	s2b = frappe.db.get_value(
		"Amenity Booking Series", r2["series_id"], ["status"], as_dict=True
	)
	req2b = frappe.db.get_value(
		"Amenity Booking Request",
		r2["request_id"],
		["status", "booking"],
		as_dict=True,
	)
	_dump("DB AFTER APPROVE", {"series": s2b, "request": req2b})
	assert s2b.status == "Confirmed"
	assert req2b.status == "Approved" and req2b.booking == apr["booking_id"]

	frappe.db.set_value("Amenity", party, "auto_approval", prev_auto)
	frappe.db.commit()

	# 3) Recurring
	frappe.set_user(tenant)
	start = getdate(nowdate()) + timedelta(days=14)
	end = start + timedelta(days=7)
	wd = start.weekday()
	keys = [
		"week_mon",
		"week_tue",
		"week_wed",
		"week_thu",
		"week_fri",
		"week_sat",
		"week_sun",
	]
	kwargs = {
		"amenity": "Court",
		"series_start_date": str(start),
		"series_end_date": str(end),
		"start_time": "17:00:00",
		"end_time": "17:30:00",
		"guests_count": 1,
		"notes": "API test recurring",
	}
	for i, k in enumerate(keys):
		kwargs[k] = 1 if i == wd else 0

	r3 = create_recurring_amenity_booking(**kwargs)
	_dump("CREATE RECURRING", r3)
	assert r3.get("status") == "success", r3
	assert r3.get("series_id")
	assert r3.get("series_type") == "Recurring"
	s3 = frappe.db.get_value(
		"Amenity Booking Series",
		r3["series_id"],
		["series_type", "status", "booked_count"],
		as_dict=True,
	)
	_dump("DB RECURRING", s3)
	assert s3.series_type == "Recurring"

	print("\nALL ASSERTIONS PASSED")
	return {"status": "ok"}
