# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import frappe
from frappe.utils import add_days, today


def execute():
	frappe.set_user("baraka@vvsdtz.com")
	from propms.api.v1.amenities.series import (
		create_recurring_amenity_booking,
		preview_recurring_amenity_booking,
	)

	start = today()
	end = str(add_days(today(), 60))  # ~2 months
	lease = "B1904 (B2004 IN BUILDING)-194413"
	prop = "B1904 (B2004 IN BUILDING)"

	created = create_recurring_amenity_booking(
		amenity="Sports Bar",
		series_start_date=start,
		series_end_date=end,
		start_time="15:00:00",
		end_time="18:00:00",
		weekdays=[5, 6],  # Sat, Sun
		guests_count=4,
		notes="UEFA / EPL weekend watch — BARAKA test series",
		lease=lease,
		property_unit=prop,
	)

	# Second preview as same tenant overlapping → should show conflicts
	preview = preview_recurring_amenity_booking(
		amenity="Sports Bar",
		series_start_date=start,
		series_end_date=end,
		start_time="15:00:00",
		end_time="18:00:00",
		weekdays=[5, 6],
		lease=lease,
		property_unit=prop,
	)

	return {
		"tenant": "baraka@vvsdtz.com",
		"customer": "BARAKA TRADING TANZANIA LTD",
		"lease": lease,
		"range": {"from": str(start), "to": end},
		"created": {
			"status": created.get("status"),
			"series_id": created.get("series_id"),
			"series_status": created.get("series_status"),
			"booked_count": created.get("booked_count"),
			"booking_ids_sample": (created.get("booking_ids") or [])[:5],
			"message": created.get("message"),
		},
		"rebook_preview": {
			"status": preview.get("status"),
			"free_count": preview.get("free_count"),
			"conflict_count": preview.get("conflict_count"),
			"conflicts_sample": (preview.get("conflicts") or [])[:3],
		},
	}
