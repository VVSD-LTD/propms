# -*- coding: utf-8 -*-
"""One-shot baseline snapshot for electricity purchase duplicate observation."""
from __future__ import unicode_literals

import json
from pathlib import Path

import frappe
from frappe.utils import now_datetime


def _find_baraka(units):
	rows = units
	if isinstance(units, dict):
		rows = units.get("units") or units.get("data") or units.get("result") or []
	if not isinstance(rows, list):
		return None
	for u in rows:
		if not isinstance(u, dict):
			continue
		if str(u.get("meter_id")) == "308" or str(u.get("meter_serial")) == "92114710087":
			return u
	return None


def execute(phase="PRE"):
	frappe.set_user("Administrator")
	ts = str(now_datetime())
	phase = (phase or "PRE").upper()
	settings = frappe.get_single("Afritrack Settings")
	meter_serial = settings.allowed_meter_serial or "92114710087"

	meter_row = None
	if frappe.db.exists("Meter", meter_serial):
		fields = ["name"]
		if frappe.get_meta("Meter").has_field("trackspm_meter_id"):
			fields.append("trackspm_meter_id")
		meter_row = frappe.db.get_value("Meter", meter_serial, fields, as_dict=True)

	api_meter = None
	api_status = None
	api_errors = []

	# IMPORTANT: do NOT call TrackSPM live APIs during observe (read-only DB only).
	# Live units/list polls confused ops when Afritrack balances changed externally.
	# Pass api_meter from caller or leave None; compare PropMS docs for purchases.
	api_errors.append("TrackSPM live fetch disabled in observe — DB-only snapshot")

	txns = frappe.get_all(
		"Selcom Payment Transaction Log",
		filters={
			"payment_workflow": "electricity_pos",
			"creation": [">=", "2026-10-01 00:00:00"],
		},
		fields=[
			"name",
			"creation",
			"order_id",
			"status",
			"amount",
			"selcom_reference",
			"sales_invoice",
			"sales_order",
		],
		order_by="creation desc",
		limit=30,
	)

	logs = frappe.get_all(
		"Afritrack Top-up Log",
		filters={"meter_id": ["in", ["308", 308]]},
		fields=[
			"name",
			"creation",
			"status",
			"sales_invoice",
			"payment_transaction",
			"tanesco_amount",
			"generator_amount",
			"wt_id_t1",
			"wt_id_t2",
			"error_message",
		],
		order_by="creation desc",
		limit=30,
	)

	si_fields = ["name", "creation", "grand_total", "docstatus", "status", "customer"]
	si_meta = frappe.get_meta("Sales Invoice")
	for f in ("meter_number", "selcom_order_id", "remarks", "lease_item"):
		if si_meta.has_field(f):
			si_fields.append(f)

	si_filters = {"creation": [">=", "2026-10-01 00:00:00"]}
	if si_meta.has_field("lease_item"):
		si_filters["lease_item"] = "Electricity"

	sis = frappe.get_all(
		"Sales Invoice",
		filters=si_filters,
		fields=si_fields,
		order_by="creation desc",
		limit=40,
	)

	dup_info = []
	if si_meta.has_field("selcom_order_id"):
		dup_info = frappe.db.sql(
			"""
			SELECT selcom_order_id, COUNT(*) AS cnt, GROUP_CONCAT(name) AS invoices
			FROM `tabSales Invoice`
			WHERE IFNULL(selcom_order_id, '') != '' AND docstatus < 2
			GROUP BY selcom_order_id
			HAVING COUNT(*) > 1
			ORDER BY cnt DESC
			LIMIT 20
			""",
			as_dict=True,
		)

	# Also count SIs that share same Order: tag in remarks (legacy)
	remark_dups = frappe.db.sql(
		"""
		SELECT remarks, COUNT(*) AS cnt, GROUP_CONCAT(name) AS invoices
		FROM `tabSales Invoice`
		WHERE creation >= '2026-10-01'
		  AND docstatus < 2
		  AND remarks LIKE '%%Order: ORD-%%'
		GROUP BY remarks
		HAVING COUNT(*) > 1
		LIMIT 20
		""",
		as_dict=True,
	)

	snapshot = {
		"phase": phase,
		"captured_at": ts,
		"purpose": "PRE/POST observation for 3 live scenarios: TANESCO 100 / Generator 250 / Both 100+250"
		if phase == "PRE"
		else "POST-purchase observation (electricity scenarios)",
		"expected_purchase": {
			"scenarios": [
				{"id": 1, "name": "TANESCO only", "tanesco_amount": 100, "generator_amount": 0, "total": 100},
				{"id": 2, "name": "Generator only", "tanesco_amount": 0, "generator_amount": 250, "total": 250},
				{"id": 3, "name": "Both", "tanesco_amount": 100, "generator_amount": 250, "total": 350},
			]
		},
		"pass_criteria": {
			"exactly_one_new_si_per_payment": True,
			"exactly_one_new_topup_log_per_payment": True,
			"no_selcom_order_id_duplicates": True,
			"api_t1_units_per_100_tzs": round(100 / 330.40, 4),
			"api_t2_units_per_250_tzs": round(250 / 4000.0, 4),
		},
		"meter": {
			"serial": meter_serial,
			"trackspm_meter_id": (meter_row or {}).get("trackspm_meter_id"),
			"afritrack_enabled": int(settings.enabled or 0),
			"restrict_allowlist": int(getattr(settings, "restrict_purchases_to_allowlist", 0) or 0),
			"allowed_meter_serial": getattr(settings, "allowed_meter_serial", None),
		},
		"api_meter_from_units_list": api_meter,
		"api_meter_status_endpoint": api_status,
		"api_errors": api_errors,
		"system_baseline": {
			"electricity_txn_count_since_oct1": len(txns),
			"electricity_success_txn_count": len([t for t in txns if t.status == "Success"]),
			"topup_log_count_meter_308": len(logs),
			"electricity_si_count_since_oct1": len(sis),
			"selcom_order_id_duplicate_groups": dup_info,
			"remarks_order_duplicate_groups": remark_dups,
			"known_txn_names": [t.name for t in txns],
			"known_log_names": [L.name for L in logs],
			"known_si_names": [s.name for s in sis],
			"recent_electricity_txns": txns,
			"recent_topup_logs": logs,
			"recent_electricity_sis": sis,
		},
	}

	suffix = "pre" if phase == "PRE" else "post"
	docs_pkg = Path(frappe.get_app_path("propms", "docs"))
	docs_pkg.mkdir(parents=True, exist_ok=True)
	out = docs_pkg / f"electricity_purchase_baseline_2026-10-02_{suffix}.json"
	out.parent.mkdir(parents=True, exist_ok=True)
	out.write_text(json.dumps(snapshot, indent=2, default=str))

	# Mirror into apps/propms/docs for easy browsing
	try:
		mirror = Path(frappe.get_app_path("propms")).parent / "docs" / out.name
		mirror.write_text(out.read_text())
	except Exception:
		pass

	# Also a short markdown checklist for humans (PRE only)
	md = docs_pkg / f"electricity_purchase_baseline_2026-10-02_{suffix}.md"
	am = api_meter or {}
	if phase == "PRE":
		md.write_text(
			"\n".join(
				[
					"# Electricity purchase baseline (PRE) — 2026-10-02",
					"",
					f"Captured: `{ts}`",
					"",
					"## Planned scenarios (in order)",
					"1. TANESCO only — **100** t1 / 0 t2 (total 100)",
					"2. Generator only — 0 t1 / **250** t2 (total 250)",
					"3. Both — **100** t1 / **250** t2 (total 350)",
					"",
					"## After each scenario — expect",
					"- **+1** Selcom Payment Transaction Log (Success)",
					"- **+1** Sales Invoice only (no duplicate for same order_id)",
					"- **+1** Afritrack Top-up Log matching that scenario’s split",
					"- Tell agent scenario # finished → POST observe before next",
					"",
					f"Full JSON: `{out.name}`",
					"",
				]
			)
		)
		try:
			(Path(frappe.get_app_path("propms")).parent / "docs" / md.name).write_text(md.read_text())
		except Exception:
			pass

	print("JSON:", out)
	print("phase:", phase)
	print("api t1/t2:", am.get("t1"), am.get("t2"), "values", am.get("t1_value"), am.get("t2_value"))
	print("counts txns/logs/sis:", len(txns), len(logs), len(sis))
	print("dups:", dup_info)
	return {"json": str(out), "api_meter": api_meter, "snapshot": snapshot}
