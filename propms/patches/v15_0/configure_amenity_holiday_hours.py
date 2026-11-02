# -*- coding: utf-8 -*-
"""Seed 2026 public holidays + configure Court to use Saturday hours on holidays."""

from __future__ import unicode_literals

import frappe
from frappe.utils import getdate

# Fixed Tanzania public holidays for 2026 (non-weekly). Eid dates approximate if needed later.
TZ_2026_PUBLIC = [
	("2026-01-01", "New Year's Day"),
	("2026-01-12", "Zanzibar Revolution Day"),
	("2026-04-03", "Good Friday"),
	("2026-04-05", "Easter Sunday"),
	("2026-04-06", "Easter Monday"),
	("2026-04-07", "Karume Day"),
	("2026-04-26", "Union Day"),
	("2026-05-01", "Labour Day"),
	("2026-07-07", "Saba Saba Day"),
	("2026-08-08", "Peasants' Day (Nane Nane)"),
	("2026-10-14", "Nyerere Day"),
	("2026-12-09", "Independence Day"),
	("2026-12-25", "Christmas Day"),
	("2026-12-26", "Boxing Day"),
]

HOLIDAY_LIST_NAME = "2026"


def _ensure_holiday_list():
	if not frappe.db.exists("DocType", "Holiday List"):
		return None

	if not frappe.db.exists("Holiday List", HOLIDAY_LIST_NAME):
		doc = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": HOLIDAY_LIST_NAME,
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
			}
		)
		doc.insert(ignore_permissions=True)
	else:
		doc = frappe.get_doc("Holiday List", HOLIDAY_LIST_NAME)

	existing = {
		str(getdate(r.holiday_date)): r
		for r in (doc.holidays or [])
		if r.holiday_date
	}
	changed = False
	for date_str, desc in TZ_2026_PUBLIC:
		if date_str in existing:
			# Prefer public holiday over weekly_off duplicate
			row = existing[date_str]
			if cint_safe(row.weekly_off):
				row.weekly_off = 0
				row.description = desc
				changed = True
			continue
		doc.append(
			"holidays",
			{
				"holiday_date": date_str,
				"description": desc,
				"weekly_off": 0,
			},
		)
		changed = True

	if changed:
		doc.save(ignore_permissions=True)
	return HOLIDAY_LIST_NAME


def cint_safe(v):
	try:
		return int(v or 0)
	except Exception:
		return 0


def execute():
	if not frappe.db.exists("DocType", "Amenity"):
		return
	if not frappe.db.has_column("Amenity", "holiday_hours"):
		return

	hl = _ensure_holiday_list()

	# Court guidelines: weekends & public holidays share later close
	if frappe.db.exists("Amenity", "Court"):
		frappe.db.set_value(
			"Amenity",
			"Court",
			{
				"holiday_list": hl,
				"holiday_hours": "Same as Saturday",
			},
			update_modified=False,
		)

	frappe.db.commit()
