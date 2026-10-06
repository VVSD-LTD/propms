# -*- coding: utf-8 -*-
"""Vendor electricity token purchase via Afritrack TrackSPM.

Complete flow:
  SI.meter_number → Meter.trackspm_meter_id (from units/list sync)
  → create_utility_bill(t1/t2) → Afritrack Top-up Log

Optional test restriction: when Restrict Purchases to Allowlist is on,
only Afritrack Settings.allowed_meter_serial may purchase (BARAKA test meter).

Policy A: Paid Sales Invoice always stands; failures recorded on Top-up Log.
"""

from __future__ import unicode_literals

import json

import frappe
from frappe.utils import cint, flt, now_datetime

from propms.api.v1.electricity.electricity import (
	_invoice_split_amounts,
	get_electricity_catalog,
	invoice_foreign_item_codes,
)
from propms.api.v1.electricity.trackspm import TrackSPMError, create_utility_bill, get_settings


def _format_trackspm_response(payload):
	"""Pretty-print TrackSPM response (full body including messages + wt_id)."""
	if payload is None:
		return ""
	if isinstance(payload, dict) and payload.get("raw") is not None:
		payload = payload.get("raw")
	try:
		return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
	except Exception:
		return str(payload)


def purchase_electricity_token(invoice_name, payment_transaction=None, force_all=False):
	"""Called after successful electricity payment. Loads t1/t2 on TrackSPM.

	Security: TrackSPM only receives amounts for POS Amount Service (Electricity)
	catalog items. Other SI lines are ignored (mixed carts allowed).
	"""
	if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
		return {"status": "skipped", "reason": "invoice_not_found"}

	meter = None
	if frappe.get_meta("Sales Invoice").has_field("meter_number"):
		meter = frappe.db.get_value("Sales Invoice", invoice_name, "meter_number")

	catalog = get_electricity_catalog()
	foreign = invoice_foreign_item_codes(invoice_name)
	tanesco_amount, generator_amount = _invoice_split_amounts(invoice_name)

	# Gate on catalog electricity amounts — not lease_item purity / item exclusivity
	if flt(tanesco_amount) <= 0 and flt(generator_amount) <= 0:
		return {
			"status": "skipped",
			"reason": "no_catalog_electricity_amounts",
			"foreign_items_ignored": foreign,
			"message": "Invoice has no TANESCO/Generator catalog amounts — nothing sent to TrackSPM",
		}

	log = _get_or_create_log(
		invoice_name,
		meter_serial=meter,
		tanesco_amount=tanesco_amount,
		generator_amount=generator_amount,
		payment_transaction=payment_transaction,
	)
	if foreign:
		# Audit only — never top up non-catalog lines
		note = "Ignored non-electricity SI items (not sent to TrackSPM): {0}".format(
			", ".join(foreign)
		)
		existing = (log.error_message or "").strip()
		if note not in existing:
			log.error_message = (existing + "\n" + note).strip() if existing else note
			log.save(ignore_permissions=True)
			frappe.db.commit()


	# Idempotent: never re-buy when this SI already loaded successfully
	if (log.status or "") == "Success" and (log.wt_id_t1 or log.wt_id_t2) and not force_all:
		return {
			"status": "success",
			"idempotent": True,
			"invoice": invoice_name,
			"meter_number": meter,
			"load_log": log.name,
			"wt_id_t1": log.wt_id_t1,
			"wt_id_t2": log.wt_id_t2,
		}

	try:
		settings = get_settings()
	except Exception:
		_finalize_log(log, status="Skipped", error="Afritrack Settings missing")
		return {"status": "skipped", "reason": "settings_missing", "load_log": log.name}

	if not cint_enabled(settings):
		_finalize_log(log, status="Skipped", error="Afritrack integration disabled")
		return {
			"status": "skipped",
			"reason": "vendor_not_configured",
			"invoice": invoice_name,
			"meter_number": meter,
			"load_log": log.name,
		}

	meter = (meter or "").strip()
	meter_id, block_reason, block_error = resolve_trackspm_meter_id(meter, settings)
	if block_reason:
		_finalize_log(
			log,
			status="Blocked",
			error=block_error,
			meter_serial=meter,
			meter_id=meter_id,
		)
		frappe.logger("electricity_vendor").warning(
			"Blocked TrackSPM purchase invoice={0} meter={1} reason={2}".format(
				invoice_name, meter, block_reason
			)
		)
		return {
			"status": "blocked",
			"reason": block_reason,
			"invoice": invoice_name,
			"meter_number": meter,
			"load_log": log.name,
		}

	log.meter_serial = meter
	log.meter_id = meter_id
	log.tanesco_amount = tanesco_amount
	log.generator_amount = generator_amount

	return _run_purchases(
		log,
		meter_id=meter_id,
		tanesco_amount=tanesco_amount,
		generator_amount=generator_amount,
		force_all=force_all,
	)


def resolve_trackspm_meter_id(meter_serial, settings=None):
	"""Resolve TrackSPM meter_id for a PropMS meter serial.

	Returns (meter_id, block_reason, error_message).
	block_reason is None when OK.
	"""
	settings = settings or get_settings()
	meter_serial = (meter_serial or "").strip()
	if not meter_serial:
		return None, "no_meter_on_invoice", "Sales Invoice has no meter_number"

	restrict = cint(getattr(settings, "restrict_purchases_to_allowlist", 0))
	allowed_serial = (getattr(settings, "allowed_meter_serial", None) or "").strip()
	allowed_meter_id = (getattr(settings, "allowed_meter_id", None) or "").strip()

	if restrict:
		if not allowed_serial:
			return (
				None,
				"blocked_no_allowlist",
				"Restrict Purchases is on but Allowed Meter Serial is empty",
			)
		if meter_serial != allowed_serial:
			return (
				None,
				"meter_not_allowlisted",
				"Meter {0} is not the allowlisted serial {1}".format(meter_serial, allowed_serial),
			)

	trackspm_id = None
	if frappe.db.exists("Meter", meter_serial):
		# Prefer meta field; fall back to raw column if Custom Field was wiped
		if frappe.get_meta("Meter").has_field("trackspm_meter_id"):
			trackspm_id = frappe.db.get_value("Meter", meter_serial, "trackspm_meter_id")
		elif frappe.db.has_column("Meter", "trackspm_meter_id"):
			trackspm_id = frappe.db.get_value("Meter", meter_serial, "trackspm_meter_id")
		if trackspm_id:
			trackspm_id = str(trackspm_id).strip()

	# Bootstrap: allowlisted test meter may use Settings.allowed_meter_id before sync
	if not trackspm_id and allowed_serial and meter_serial == allowed_serial and allowed_meter_id:
		trackspm_id = allowed_meter_id

	if not trackspm_id:
		return (
			None,
			"missing_trackspm_meter_id",
			"Meter {0} has no TrackSPM Meter ID — run Sync Meters from Afritrack Settings".format(
				meter_serial
			),
		)

	return trackspm_id, None, None


def retry_missing_tariffs(invoice_name):
	"""Retry only tariffs that still lack wt_id."""
	return purchase_electricity_token(invoice_name, force_all=False)


def cint_enabled(settings):
	return bool(getattr(settings, "enabled", 0))


def _get_or_create_log(
	invoice_name,
	meter_serial=None,
	tanesco_amount=0,
	generator_amount=0,
	payment_transaction=None,
):
	existing = frappe.db.get_value("Afritrack Top-up Log", {"sales_invoice": invoice_name}, "name")
	if existing:
		log = frappe.get_doc("Afritrack Top-up Log", existing)
		if payment_transaction and not log.payment_transaction:
			log.payment_transaction = payment_transaction
		return log

	log = frappe.get_doc(
		{
			"doctype": "Afritrack Top-up Log",
			"sales_invoice": invoice_name,
			"payment_transaction": payment_transaction,
			"meter_serial": meter_serial,
			"tanesco_amount": flt(tanesco_amount),
			"generator_amount": flt(generator_amount),
			"status": "Pending",
		}
	)
	log.insert(ignore_permissions=True)
	frappe.db.commit()
	return log


def _finalize_log(
	log,
	status,
	error=None,
	meter_serial=None,
	meter_id=None,
	wt_id_t1=None,
	wt_id_t2=None,
	response_t1=None,
	response_t2=None,
):
	log.status = status
	log.last_attempt_on = now_datetime()
	if error is not None:
		log.error_message = (error or "")[:140]
	if meter_serial is not None:
		log.meter_serial = meter_serial
	if meter_id is not None:
		log.meter_id = meter_id
	if wt_id_t1 is not None:
		log.wt_id_t1 = str(wt_id_t1)
	if wt_id_t2 is not None:
		log.wt_id_t2 = str(wt_id_t2)
	if response_t1 is not None and log.meta.has_field("response_t1"):
		log.response_t1 = response_t1
	if response_t2 is not None and log.meta.has_field("response_t2"):
		log.response_t2 = response_t2
	log.flags.ignore_permissions = True
	log.save(ignore_permissions=True)
	frappe.db.commit()


def _run_purchases(log, meter_id, tanesco_amount, generator_amount, force_all=False):
	"""Execute t1/t2 purchases; skip tariffs that already have wt_id unless force_all."""
	errors = []
	need_t1 = flt(tanesco_amount) > 0
	need_t2 = flt(generator_amount) > 0

	do_t1 = need_t1 and (force_all or not log.wt_id_t1)
	do_t2 = need_t2 and (force_all or not log.wt_id_t2)

	if do_t1:
		try:
			result = create_utility_bill(meter_id, "t1", tanesco_amount)
			wt = result.get("wt_id")
			if wt is None:
				raise TrackSPMError("t1 purchase returned no wt_id", response=result)
			log.wt_id_t1 = str(wt)
			if log.meta.has_field("response_t1"):
				log.response_t1 = _format_trackspm_response(result)
		except Exception as e:
			errors.append("t1: {0}".format(e))
			if log.meta.has_field("response_t1"):
				resp = getattr(e, "response", None)
				log.response_t1 = _format_trackspm_response(resp if resp is not None else {"error": str(e)})
			frappe.log_error(frappe.get_traceback(), "Afritrack TrackSPM t1 purchase")

	if do_t2:
		try:
			result = create_utility_bill(meter_id, "t2", generator_amount)
			wt = result.get("wt_id")
			if wt is None:
				raise TrackSPMError("t2 purchase returned no wt_id", response=result)
			log.wt_id_t2 = str(wt)
			if log.meta.has_field("response_t2"):
				log.response_t2 = _format_trackspm_response(result)
		except Exception as e:
			errors.append("t2: {0}".format(e))
			if log.meta.has_field("response_t2"):
				resp = getattr(e, "response", None)
				log.response_t2 = _format_trackspm_response(resp if resp is not None else {"error": str(e)})
			frappe.log_error(frappe.get_traceback(), "Afritrack TrackSPM t2 purchase")

	t1_ok = (not need_t1) or bool(log.wt_id_t1)
	t2_ok = (not need_t2) or bool(log.wt_id_t2)

	if not need_t1 and not need_t2:
		status = "Skipped"
		err = "No TANESCO/Generator amounts on invoice"
	elif t1_ok and t2_ok:
		status = "Success"
		err = ""
	elif (need_t1 and log.wt_id_t1) or (need_t2 and log.wt_id_t2):
		status = "Partial"
		err = "; ".join(errors) if errors else "Partial TrackSPM load"
	else:
		status = "Failed"
		err = "; ".join(errors) if errors else "TrackSPM load failed"

	_finalize_log(log, status=status, error=err)

	frappe.logger("electricity_vendor").info(
		"TrackSPM purchase invoice={0} status={1} wt_t1={2} wt_t2={3}".format(
			log.sales_invoice, status, log.wt_id_t1, log.wt_id_t2
		)
	)

	return {
		"status": status.lower(),
		"invoice": log.sales_invoice,
		"load_log": log.name,
		"wt_id_t1": log.wt_id_t1,
		"wt_id_t2": log.wt_id_t2,
		"error": err or None,
	}
