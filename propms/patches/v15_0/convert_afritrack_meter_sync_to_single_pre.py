# -*- coding: utf-8 -*-
"""Before model sync: cache last Afritrack Meter Sync meta + raw_json; clear table rows."""

from __future__ import unicode_literals

import frappe


DOCTYPE = "Afritrack Meter Sync"
CACHE_META = "propms:ams_single_seed"
CACHE_RAW = "propms:ams_raw_json_seed"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	if not frappe.db.table_exists("tab{0}".format(DOCTYPE)):
		return

	_cache_last_row()
	try:
		frappe.db.sql("delete from `tab{0}`".format(DOCTYPE))
	except Exception:
		pass


def _cache_last_row():
	has_raw = frappe.db.has_column(DOCTYPE, "raw_json")
	raw_sel = ", raw_json" if has_raw else ""
	found = frappe.db.sql(
		"""
		select synced_on, last_updated_on, status, property_id, triggered_by,
			total_rows, meters_updated, meters_skipped, duration_ms, error_message
			{raw_sel}
		from `tab{doctype}`
		order by
			case when status = 'Success' then 0 when status = 'Partial' then 1 else 2 end,
			coalesce(last_updated_on, synced_on) desc
		limit 1
		""".format(
			doctype=DOCTYPE, raw_sel=raw_sel
		),
		as_dict=True,
	)
	if not found:
		return
	row = dict(found[0])
	raw = row.pop("raw_json", None) if has_raw else None
	frappe.cache().set_value(CACHE_META, row, expires_in_sec=7200)
	if raw:
		frappe.cache().set_value(CACHE_RAW, raw, expires_in_sec=7200)
