# -*- coding: utf-8 -*-
"""Seed Amenity Day Hours (Mon–Sun) from legacy open_time/close_time.

Court guidelines: weekdays 06:00–22:30, weekends 06:00–23:00.
"""

from __future__ import unicode_literals

import frappe

WEEKDAYS = (
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
	"Sunday",
)

# Amenity-specific weekend/weekday overrides (close_time). Open stays amenity open_time.
COURT_WEEKEND_CLOSE = "23:00:00"
COURT_WEEKDAY_CLOSE = "22:30:00"


def execute():
	if not frappe.db.exists("DocType", "Amenity"):
		return
	if not frappe.db.exists("DocType", "Amenity Day Hours"):
		return

	amenities = frappe.get_all("Amenity", fields=["name"], ignore_permissions=True)
	has_open = frappe.db.has_column("Amenity", "open_time")
	for row in amenities:
		existing = frappe.db.count(
			"Amenity Day Hours",
			{"parent": row.name, "parenttype": "Amenity"},
		)
		if existing:
			continue

		doc = frappe.get_doc("Amenity", row.name)
		default_open = "06:00:00"
		default_close = "22:00:00"
		if has_open:
			default_open = getattr(doc, "open_time", None) or default_open
			default_close = getattr(doc, "close_time", None) or default_close

		for day in WEEKDAYS:
			close = default_close
			if doc.name == "Court":
				if day in ("Saturday", "Sunday"):
					close = COURT_WEEKEND_CLOSE
				else:
					close = COURT_WEEKDAY_CLOSE
			doc.append(
				"day_hours",
				{
					"day_of_week": day,
					"is_open": 1,
					"open_time": default_open,
					"close_time": close,
				},
			)

		doc.flags.ignore_validate = True
		doc.save(ignore_permissions=True)

	frappe.db.commit()
