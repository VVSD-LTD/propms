# -*- coding: utf-8 -*-
"""Afritrack TrackSPM HTTP client.

Reads credentials from Afritrack Settings. Never logs password or full Bearer token.
Do not call create_utility_bill from unit tests without mocking requests.
"""

from __future__ import unicode_literals

import frappe
from frappe.utils import cint, flt

TOKEN_CACHE_KEY = "afritrack_trackspm_access_token"
TOKEN_CACHE_TTL = 3500  # seconds; tokens typically last ~1h
UNITS_LIST_CACHE_PREFIX = "afritrack_units_list_"
UNITS_LIST_CACHE_TTL = 90  # seconds — balance/status freshness vs API load


class TrackSPMError(Exception):
	"""Raised when TrackSPM returns an error or unexpected payload."""

	def __init__(self, message, response=None):
		super(TrackSPMError, self).__init__(message)
		self.response = response


def get_settings():
	return frappe.get_single("Afritrack Settings")


def _base_url(settings=None):
	settings = settings or get_settings()
	url = (settings.base_url or "https://v1.api.trackspm.com").rstrip("/")
	return url


def _auth_headers(token):
	return {
		"Accept": "application/json, text/plain, */*",
		"Authorization": "Bearer {0}".format(token),
	}


def clear_token_cache():
	frappe.cache().delete_value(TOKEN_CACHE_KEY)


def login(force=False):
	"""POST /authentication/login — cache access_token."""
	if not force:
		cached = frappe.cache().get_value(TOKEN_CACHE_KEY)
		if cached:
			return cached

	settings = get_settings()
	username = settings.username
	password = settings.get_password("password") if settings.password else None
	if not username or not password:
		raise TrackSPMError("Afritrack Settings username/password not configured")

	url = "{0}/authentication/login".format(_base_url(settings))
	# TrackSPM accepts form or JSON; use form for compatibility with email examples
	import requests

	resp = requests.post(
		url,
		data={"username": username, "password": password},
		headers={"Accept": "application/json, text/plain, */*"},
		timeout=60,
	)
	try:
		payload = resp.json()
	except Exception:
		raise TrackSPMError(
			"TrackSPM login failed: HTTP {0} non-JSON body".format(resp.status_code),
			response=resp.text[:500] if resp.text else None,
		)

	if resp.status_code >= 400 or payload.get("error"):
		raise TrackSPMError(
			"TrackSPM login failed: {0}".format(
				payload.get("messages") or payload.get("message") or resp.status_code
			),
			response=payload,
		)

	data = payload.get("data") or {}
	token = data.get("access_token") or data.get("token")
	if not token and isinstance(data, str):
		token = data
	if not token:
		raise TrackSPMError("TrackSPM login: access_token missing", response=payload)

	frappe.cache().set_value(TOKEN_CACHE_KEY, token, expires_in_sec=TOKEN_CACHE_TTL)
	return token


def _request(method, path, *, params=None, data=None, files=None, retry_auth=True):
	"""Authenticated JSON request; re-login once on 401."""
	import requests

	token = login()
	url = "{0}{1}".format(_base_url(), path if path.startswith("/") else "/" + path)
	headers = _auth_headers(token)

	resp = requests.request(
		method,
		url,
		params=params,
		data=data,
		files=files,
		headers=headers,
		timeout=90,
	)

	if resp.status_code == 401 and retry_auth:
		clear_token_cache()
		return _request(method, path, params=params, data=data, files=files, retry_auth=False)

	try:
		payload = resp.json()
	except Exception:
		raise TrackSPMError(
			"TrackSPM {0} {1}: HTTP {2} non-JSON".format(method, path, resp.status_code),
			response=resp.text[:500] if resp.text else None,
		)

	if resp.status_code >= 400 or payload.get("error"):
		msgs = payload.get("messages") or payload.get("message") or payload
		raise TrackSPMError(
			"TrackSPM {0} {1}: {2}".format(method, path, msgs),
			response=payload,
		)
	return payload


def list_meters(property_id=None, tariffs=1):
	"""GET /units/list — operator/readonly helper; not used in tenant checkout."""
	settings = get_settings()
	pid = property_id or settings.property_id or "5"
	return _request(
		"GET",
		"/units/list",
		params={"tariffs": cint(tariffs), "property_id": pid},
	)


def get_units_list_cached(property_id=None, tariffs=1, force_refresh=False):
	"""Prefer latest Afritrack Meter Sync JSON; optionally force live TrackSPM fetch.

	Normal mobile traffic should hit stored sync (15-min job), not TrackSPM per tenant.
	"""
	settings = get_settings()
	pid = str(property_id or settings.property_id or "5")

	if not force_refresh and frappe.db.exists("DocType", "Afritrack Meter Sync"):
		from propms.property_management_solution.doctype.afritrack_meter_sync.afritrack_meter_sync import (
			get_latest_units_list_payload,
		)

		stored = get_latest_units_list_payload(property_id=pid)
		if stored:
			return stored

	# Live fetch (+ short redis cache) when no sync yet or force_refresh
	cache_key = "{0}{1}_{2}".format(UNITS_LIST_CACHE_PREFIX, pid, cint(tariffs))
	if not force_refresh:
		cached = frappe.cache().get_value(cache_key)
		if cached:
			return cached
	payload = list_meters(property_id=pid, tariffs=tariffs)
	frappe.cache().set_value(cache_key, payload, expires_in_sec=UNITS_LIST_CACHE_TTL)
	return payload


def find_meter_row(meter_serial=None, meter_id=None, force_refresh=False):
	"""Find one meter row from units/list by serial and/or TrackSPM meter_id."""
	serial = (meter_serial or "").strip()
	mid = str(meter_id).strip() if meter_id is not None else ""
	if not serial and not mid:
		return None

	payload = get_units_list_cached(force_refresh=force_refresh)
	rows = payload.get("data") or []
	if not isinstance(rows, list):
		raise TrackSPMError("units/list data is not a list", response=payload)

	for row in rows:
		if not isinstance(row, dict):
			continue
		row_serial = (row.get("meter_serial") or row.get("meter_reference") or "").strip()
		row_id = str(row.get("meter_id") or "").strip()
		if mid and row_id == mid:
			return row
		if serial and row_serial == serial:
			return row
	return None


def serialize_meter_status(row, propms_serial=None):
	"""Normalize a TrackSPM units/list row for mobile clients."""
	if not row:
		return None
	return {
		"meter_serial": (row.get("meter_serial") or row.get("meter_reference") or propms_serial or "").strip(),
		"meter_id": str(row.get("meter_id") or "").strip() or None,
		"meter_status": row.get("meter_status"),
		"meter_power": row.get("meter_power"),
		"meter_type": row.get("meter_type"),
		"unit_reference": row.get("unit_reference"),
		"zone_name": row.get("zone_name"),
		"property_name": row.get("property_name"),
		"currency": row.get("property_currency") or "TZS",
		"tanesco": {
			"units": row.get("t1"),
			"t1_value": row.get("t1_value"),
			"refreshed_at": row.get("t1_time"),
			"relative_time": row.get("t1_relative_time"),
			"min_amount": row.get("meter_minT1"),
			"tariff": row.get("property_tariff_t1"),
		},
		"generator": {
			"units": row.get("t2"),
			"t2_value": row.get("t2_value"),
			"refreshed_at": row.get("t2_time"),
			"relative_time": row.get("t2_relative_time"),
			"min_amount": row.get("meter_minT2"),
			"tariff": row.get("property_tariff_t2"),
		},
	}


def create_utility_bill(meter_id, tariff, amount, wallet_id=None):
	"""POST /transactions/create_utility_bill (multipart form).

	tariff: 't1' (TANESCO/mains) or 't2' (Generator)
	"""
	tariff = (tariff or "").strip().lower()
	if tariff not in ("t1", "t2"):
		raise TrackSPMError("tariff must be t1 or t2, got {0}".format(tariff))

	amount = flt(amount)
	if amount <= 0:
		raise TrackSPMError("amount must be > 0")

	settings = get_settings()
	wallet_id = wallet_id or settings.wallet_id or "112"
	meter_id = str(meter_id).strip()
	if not meter_id:
		raise TrackSPMError("meter_id is required")

	# Multipart form fields as in Afritrack email
	form = {
		"meter_id": str(meter_id),
		"tariff": tariff,
		"amount": str(cint(amount) if amount == cint(amount) else amount),
		"wallet_id": str(wallet_id),
		"direct_purchase": "true",
		"payment_gateway": "1",
	}

	payload = _request("POST", "/transactions/create_utility_bill", data=form)
	wt_id = payload.get("wt_id")
	if wt_id is None and isinstance(payload.get("data"), dict):
		wt_id = payload["data"].get("wt_id")
	return {
		"error": False,
		"messages": payload.get("messages") or [],
		"wt_id": wt_id,
		"raw": payload,
	}


def sync_meters_from_trackspm(property_id=None, create_missing=False, triggered_by="Manual"):
	"""Fetch /units/list → Afritrack Meter Sync (full JSON) + update Meter IDs.

	create_missing=False (default): only update existing PropMS Meter docs.
	"""
	from propms.property_management_solution.doctype.afritrack_meter_sync.afritrack_meter_sync import (
		run_afritrack_meter_sync,
	)

	return run_afritrack_meter_sync(
		property_id=property_id,
		create_missing=create_missing,
		triggered_by=triggered_by or "Manual",
	)
