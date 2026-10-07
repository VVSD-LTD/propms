# -*- coding: utf-8 -*-
"""After model sync: seed Single + apply cached units/list onto Meter detail fields."""

from __future__ import unicode_literals

import json

import frappe
from frappe.utils import cint, now_datetime


DOCTYPE = "Afritrack Meter Sync"
CACHE_META = "propms:ams_single_seed"
CACHE_RAW = "propms:ams_raw_json_seed"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	if not cint(frappe.db.get_value("DocType", DOCTYPE, "issingle")):
		return

	_seed_single()
	_apply_cached_raw_to_meters()
	frappe.cache().delete_value(CACHE_META)
	frappe.cache().delete_value(CACHE_RAW)


def _seed_single():
	meta_row = frappe.cache().get_value(CACHE_META) or {}
	doc = frappe.get_single(DOCTYPE)
	for key in (
		"synced_on",
		"last_updated_on",
		"status",
		"property_id",
		"triggered_by",
		"total_rows",
		"meters_updated",
		"meters_skipped",
		"duration_ms",
		"error_message",
	):
		if meta_row.get(key) is not None:
			doc.set(key, meta_row.get(key))
	if not doc.status:
		doc.status = "Pending"
	doc.save(ignore_permissions=True)


def _apply_cached_raw_to_meters():
	raw = frappe.cache().get_value(CACHE_RAW)
	if not raw:
		return
	try:
		payload = json.loads(raw)
	except Exception:
		return
	data = payload.get("data") if isinstance(payload, dict) else None
	if not isinstance(data, list):
		return

	from propms.property_management_solution.doctype.afritrack_meter_sync.afritrack_meter_sync import (
		apply_trackspm_row_to_meter,
	)

	meta = frappe.cache().get_value(CACHE_META) or {}
	synced = meta.get("last_updated_on") or meta.get("synced_on") or now_datetime()
	for row in data:
		if not isinstance(row, dict):
			continue
		serial = (row.get("meter_serial") or row.get("meter_reference") or "").strip()
		if not serial:
			continue
		apply_trackspm_row_to_meter(serial, row, synced_on=synced)
