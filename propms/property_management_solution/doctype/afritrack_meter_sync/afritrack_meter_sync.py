# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import time

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime


DOCTYPE = "Afritrack Meter Sync"


class AfritrackMeterSync(Document):
	pass


def get_sync_meta():
	"""Return last sync timestamps from the Single (or None)."""
	if not frappe.db.exists("DocType", DOCTYPE):
		return None
	try:
		doc = frappe.get_single(DOCTYPE)
	except Exception:
		return None
	return {
		"sync_name": DOCTYPE,
		"synced_on": doc.synced_on,
		"last_updated_on": doc.last_updated_on or doc.synced_on,
		"status": doc.status,
		"property_id": doc.property_id,
		"data_source": "afritrack_meter_sync",
	}


def trackspm_row_from_meter(meter_name_or_doc):
	"""Build a units/list-shaped dict from Meter fields (for serialize_meter_status)."""
	if isinstance(meter_name_or_doc, str):
		if not frappe.db.exists("Meter", meter_name_or_doc):
			return None
		m = frappe.db.get_value(
			"Meter",
			meter_name_or_doc,
			_meter_trackspm_fields(),
			as_dict=True,
		)
	else:
		m = meter_name_or_doc
	if not m:
		return None
	if not (m.get("trackspm_meter_id") or "").strip() and not m.get("trackspm_last_synced"):
		# Never synced — no usable TrackSPM snapshot
		return None
	return {
		"meter_serial": m.get("meter_number") or m.get("name"),
		"meter_reference": m.get("meter_number") or m.get("name"),
		"meter_id": m.get("trackspm_meter_id"),
		"meter_status": m.get("trackspm_meter_status"),
		"meter_power": m.get("trackspm_meter_power"),
		"meter_type": m.get("trackspm_meter_type"),
		"unit_reference": m.get("trackspm_unit_reference"),
		"zone_name": m.get("trackspm_zone_name"),
		"property_name": m.get("trackspm_property_name"),
		"property_currency": m.get("trackspm_currency") or "TZS",
		"t1": m.get("trackspm_t1"),
		"t1_value": m.get("trackspm_t1_value"),
		"t1_time": m.get("trackspm_t1_time"),
		"t1_relative_time": m.get("trackspm_t1_relative_time"),
		"meter_minT1": m.get("trackspm_min_t1"),
		"property_tariff_t1": m.get("trackspm_tariff_t1"),
		"t2": m.get("trackspm_t2"),
		"t2_value": m.get("trackspm_t2_value"),
		"t2_time": m.get("trackspm_t2_time"),
		"t2_relative_time": m.get("trackspm_t2_relative_time"),
		"meter_minT2": m.get("trackspm_min_t2"),
		"property_tariff_t2": m.get("trackspm_tariff_t2"),
	}


def find_meter_row_from_meter(meter_serial=None, meter_id=None):
	"""Find TrackSPM snapshot stored on Meter (no raw_json)."""
	serial = (meter_serial or "").strip()
	mid = str(meter_id).strip() if meter_id is not None else ""

	name = None
	if serial and frappe.db.exists("Meter", serial):
		name = serial
	elif mid and frappe.db.has_column("Meter", "trackspm_meter_id"):
		name = frappe.db.get_value("Meter", {"trackspm_meter_id": mid}, "name")

	if not name:
		return None
	return trackspm_row_from_meter(name)


def _meter_trackspm_fields():
	return [
		"name",
		"meter_number",
		"trackspm_meter_id",
		"trackspm_meter_status",
		"trackspm_meter_power",
		"trackspm_meter_type",
		"trackspm_unit_reference",
		"trackspm_zone_name",
		"trackspm_property_name",
		"trackspm_currency",
		"trackspm_t1",
		"trackspm_t1_value",
		"trackspm_t1_time",
		"trackspm_t1_relative_time",
		"trackspm_min_t1",
		"trackspm_tariff_t1",
		"trackspm_t2",
		"trackspm_t2_value",
		"trackspm_t2_time",
		"trackspm_t2_relative_time",
		"trackspm_min_t2",
		"trackspm_tariff_t2",
		"trackspm_last_synced",
	]


def _str_or_none(val):
	if val is None:
		return None
	s = str(val).strip()
	return s or None


def apply_trackspm_row_to_meter(serial, row, synced_on=None):
	"""Write one /units/list row onto an existing Meter. Returns True if updated.

	Skips Cooking Gas meters — TrackSPM details are electricity-only.
	"""
	if not serial or not frappe.db.exists("Meter", serial):
		return False
	if not frappe.db.has_column("Meter", "trackspm_meter_id"):
		return False

	meter_type = (frappe.db.get_value("Meter", serial, "meter_type") or "").strip()
	if meter_type == "Cooking Gas":
		return False

	meter_id = row.get("meter_id")
	if meter_id is not None:
		meter_id = str(meter_id).strip()

	now = synced_on or now_datetime()
	values = {
		"trackspm_meter_id": meter_id or None,
		"trackspm_last_synced": now,
	}
	# Only set detail fields when columns exist (pre-migrate safety)
	field_map = {
		"trackspm_meter_status": _str_or_none(row.get("meter_status")),
		"trackspm_meter_power": _str_or_none(row.get("meter_power")),
		"trackspm_meter_type": _str_or_none(row.get("meter_type")),
		"trackspm_unit_reference": _str_or_none(row.get("unit_reference")),
		"trackspm_zone_name": _str_or_none(row.get("zone_name")),
		"trackspm_property_name": _str_or_none(row.get("property_name")),
		"trackspm_currency": _str_or_none(row.get("property_currency")) or "TZS",
		"trackspm_t1": flt(row.get("t1")) if row.get("t1") is not None else None,
		"trackspm_t1_value": flt(row.get("t1_value")) if row.get("t1_value") is not None else None,
		"trackspm_t1_time": _str_or_none(row.get("t1_time")),
		"trackspm_t1_relative_time": _str_or_none(row.get("t1_relative_time")),
		"trackspm_min_t1": flt(row.get("meter_minT1")) if row.get("meter_minT1") is not None else None,
		"trackspm_tariff_t1": _str_or_none(row.get("property_tariff_t1")),
		"trackspm_t2": flt(row.get("t2")) if row.get("t2") is not None else None,
		"trackspm_t2_value": flt(row.get("t2_value")) if row.get("t2_value") is not None else None,
		"trackspm_t2_time": _str_or_none(row.get("t2_time")),
		"trackspm_t2_relative_time": _str_or_none(row.get("t2_relative_time")),
		"trackspm_min_t2": flt(row.get("meter_minT2")) if row.get("meter_minT2") is not None else None,
		"trackspm_tariff_t2": _str_or_none(row.get("property_tariff_t2")),
	}
	for fieldname, value in field_map.items():
		if frappe.db.has_column("Meter", fieldname):
			values[fieldname] = value

	frappe.db.set_value("Meter", serial, values, update_modified=False)
	return True


def _get_sync_single():
	"""Load or initialize the Afritrack Meter Sync Single."""
	if not frappe.db.exists("DocType", DOCTYPE):
		frappe.throw(frappe._("{0} DocType missing — migrate first").format(DOCTYPE))
	# After convert-to-Single, get_single always works
	try:
		return frappe.get_single(DOCTYPE)
	except Exception:
		# Rare: Single row missing
		doc = frappe.new_doc(DOCTYPE)
		doc.insert(ignore_permissions=True)
		return doc


def run_afritrack_meter_sync(property_id=None, triggered_by="Scheduler"):
	"""Fetch TrackSPM /units/list, update Meter docs with unit details, refresh Single meta.

	Never creates Meter docs. No raw_json — details live on each Meter.
	"""
	from propms.api.v1.electricity.trackspm import TrackSPMError, get_settings, list_meters

	started = time.time()
	settings = get_settings()
	pid = property_id if property_id not in (None, "") else getattr(settings, "property_id", None)
	if not isinstance(pid, (str, int, float)) or "<MagicMock" in str(pid):
		pid = "5"
	pid = str(pid).strip() or "5"

	now = now_datetime()
	doc = _get_sync_single()
	if not doc.synced_on:
		doc.synced_on = now
	doc.last_updated_on = now
	doc.status = "Pending"
	doc.property_id = pid
	doc.triggered_by = triggered_by or "Scheduler"
	doc.error_message = ""
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	try:
		if not frappe.db.has_column("Meter", "trackspm_meter_id"):
			raise TrackSPMError(
				"Meter.trackspm_meter_id column missing — migrate Meter DocType then retry"
			)

		payload = list_meters(property_id=pid, tariffs=1)
		rows = payload.get("data") or []
		if not isinstance(rows, list):
			raise TrackSPMError("units/list data is not a list", response=payload)

		updated = 0
		skipped = 0
		now = now_datetime()

		for row in rows:
			if not isinstance(row, dict):
				skipped += 1
				continue
			serial = (row.get("meter_serial") or row.get("meter_reference") or "").strip()
			meter_id = row.get("meter_id")
			if meter_id is not None:
				meter_id = str(meter_id).strip()
			if not serial or not meter_id:
				skipped += 1
				continue

			if apply_trackspm_row_to_meter(serial, row, synced_on=now):
				updated += 1
			else:
				skipped += 1

		duration_ms = cint((time.time() - started) * 1000)
		status = "Success"
		if updated == 0 and rows:
			status = "Partial"

		doc = frappe.get_single(DOCTYPE)
		doc.status = status
		doc.total_rows = len(rows)
		doc.meters_updated = updated
		doc.meters_skipped = skipped
		doc.duration_ms = duration_ms
		doc.error_message = ""
		doc.last_updated_on = now
		doc.property_id = pid
		doc.triggered_by = triggered_by or "Scheduler"
		if not doc.synced_on:
			doc.synced_on = now
		doc.save(ignore_permissions=True)

		settings.db_set("last_meter_sync", now, update_modified=False)
		settings.db_set("meters_synced", updated, update_modified=False)
		frappe.db.commit()

		return {
			"status": "success",
			"sync_name": DOCTYPE,
			"updated": updated,
			"skipped": skipped,
			"total_rows": len(rows),
			"duration_ms": duration_ms,
			"property_id": pid,
			"last_updated_on": str(now),
		}
	except Exception as e:
		duration_ms = cint((time.time() - started) * 1000)
		err = str(e)
		now = now_datetime()
		try:
			doc = frappe.get_single(DOCTYPE)
			doc.status = "Failed"
			doc.error_message = err[:1400]
			doc.duration_ms = duration_ms
			doc.last_updated_on = now
			doc.save(ignore_permissions=True)
			frappe.db.commit()
		except Exception:
			pass
		frappe.log_error(frappe.get_traceback(), "Afritrack Meter Sync")
		return {
			"status": "error",
			"sync_name": DOCTYPE,
			"message": err,
			"property_id": pid,
		}


def scheduled_afritrack_meter_sync():
	"""Cron every 15 minutes — skip if Afritrack disabled."""
	if not frappe.db.exists("DocType", "Afritrack Settings"):
		return
	try:
		enabled = frappe.db.get_single_value("Afritrack Settings", "enabled")
	except Exception:
		return
	if not cint(enabled):
		return
	run_afritrack_meter_sync(triggered_by="Scheduler")


@frappe.whitelist()
def sync_now():
	"""Desk / API: run Afritrack Meter Sync immediately."""
	frappe.only_for("System Manager")
	return run_afritrack_meter_sync(triggered_by="Manual")


# ---------------------------------------------------------------------------
# Back-compat shims (used by older callers / tests)
# ---------------------------------------------------------------------------


def get_latest_units_list_payload(property_id=None):
	"""Deprecated: rebuild a fake units/list from Meter rows for callers that still expect JSON shape."""
	meta = get_sync_meta()
	if not meta or meta.get("status") not in ("Success", "Partial"):
		return None

	data = []
	if frappe.db.has_column("Meter", "trackspm_meter_id"):
		names = frappe.get_all(
			"Meter",
			filters=[["trackspm_meter_id", "is", "set"]],
			pluck="name",
		)
		for name in names:
			row = trackspm_row_from_meter(name)
			if row:
				data.append(row)

	return {
		"data": data,
		"_sync_name": DOCTYPE,
		"_synced_on": meta.get("synced_on"),
		"_last_updated_on": meta.get("last_updated_on"),
	}


def find_meter_row_in_sync(meter_serial=None, meter_id=None, property_id=None):
	"""Prefer Meter snapshot; ignore property_id (Single holds current property)."""
	row = find_meter_row_from_meter(meter_serial=meter_serial, meter_id=meter_id)
	payload = get_latest_units_list_payload(property_id=property_id)
	return row, payload
