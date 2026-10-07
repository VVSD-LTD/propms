# -*- coding: utf-8 -*-
"""Unit tests for Afritrack TrackSPM vendor layer (mocked HTTP — no live calls)."""

from __future__ import unicode_literals

import unittest
from unittest.mock import MagicMock, patch

import frappe


class TestTrackSPMClient(unittest.TestCase):
	def test_create_utility_bill_rejects_bad_tariff(self):
		from propms.api.v1.electricity.trackspm import TrackSPMError, create_utility_bill

		with self.assertRaises(TrackSPMError):
			create_utility_bill("308", "t3", 100)

	@patch("propms.api.v1.electricity.trackspm.list_meters")
	def test_sync_meters_updates_existing(self, mock_list):
		from propms.api.v1.electricity.trackspm import sync_meters_from_trackspm

		if not frappe.db.exists("DocType", "Meter"):
			self.skipTest("Meter DocType missing")
		if not frappe.get_meta("Meter").has_field("trackspm_meter_id"):
			self.skipTest("trackspm_meter_id not migrated")

		serial = "SYNC-TEST-SERIAL-999"
		if frappe.db.exists("Meter", serial):
			frappe.delete_doc("Meter", serial, force=1)

		frappe.get_doc(
			{"doctype": "Meter", "meter_number": serial, "status": "Active"}
		).insert(ignore_permissions=True)

		mock_list.return_value = {
			"error": False,
			"data": [{"meter_serial": serial, "meter_id": "777", "meter_reference": serial}],
		}

		with patch("propms.api.v1.electricity.trackspm.get_settings") as gs:
			settings = MagicMock()
			settings.property_id = "5"
			settings.db_set = MagicMock()
			gs.return_value = settings
			result = sync_meters_from_trackspm()

		self.assertEqual(result["updated"], 1)
		self.assertEqual(frappe.db.get_value("Meter", serial, "trackspm_meter_id"), "777")
		frappe.delete_doc("Meter", serial, force=1)


class TestVendorAllowlist(unittest.TestCase):
	def setUp(self):
		if not frappe.db.exists("DocType", "Afritrack Settings"):
			self.skipTest("Afritrack Settings DocType not installed")
		if not frappe.db.exists("DocType", "Afritrack Top-up Log"):
			self.skipTest("Afritrack Top-up Log DocType not installed")

	def _mock_settings(self, **kwargs):
		s = MagicMock()
		s.enabled = kwargs.get("enabled", 1)
		s.restrict_purchases_to_allowlist = kwargs.get("restrict_purchases_to_allowlist", 1)
		s.allowed_meter_serial = kwargs.get("allowed_meter_serial", "TEST-SERIAL-ONLY")
		s.allowed_meter_id = kwargs.get("allowed_meter_id", "308")
		s.wallet_id = "112"
		s.property_id = "5"
		s.base_url = "https://v1.api.trackspm.com"
		s.username = "vivatowers"
		return s

	@patch("propms.api.v1.electricity.vendor.create_utility_bill")
	@patch("propms.api.v1.electricity.vendor.resolve_trackspm_meter_id")
	@patch("propms.api.v1.electricity.vendor.get_settings")
	@patch("propms.api.v1.electricity.vendor.invoice_foreign_item_codes")
	@patch("propms.api.v1.electricity.vendor._invoice_split_amounts")
	def test_mixed_foreign_items_still_tops_up_catalog(self, mock_split, mock_foreign, mock_settings, mock_resolve, mock_create):
		"""Mixed SI allowed — only catalog electricity amounts go to TrackSPM."""
		from propms.api.v1.electricity.vendor import purchase_electricity_token

		mock_settings.return_value = self._mock_settings()
		mock_foreign.return_value = ["DRINKING WATER"]
		mock_split.return_value = (660.0, 0.0)
		mock_resolve.return_value = ("312", None, None)
		mock_create.return_value = {"wt_id": 1001, "error": False}

		with patch("frappe.db.exists", return_value=True), patch(
			"frappe.db.get_value",
			side_effect=lambda *a, **k: _fake_get_value(*a, **k, meter="TEST-SERIAL-ONLY"),
		), patch("frappe.get_meta") as meta, patch(
			"propms.api.v1.electricity.vendor._get_or_create_log"
		) as mock_log, patch("propms.api.v1.electricity.vendor._finalize_log"):
			meta.return_value.has_field.return_value = True
			log = MagicMock()
			log.name = "AFL-TEST-MIXED"
			log.status = "Pending"
			log.wt_id_t1 = None
			log.wt_id_t2 = None
			log.error_message = None
			mock_log.return_value = log

			result = purchase_electricity_token("SI-FAKE-MIXED")

		self.assertEqual(result["status"], "success")
		mock_create.assert_called_once()
		self.assertEqual(mock_create.call_args.args[1], "t1")
		self.assertEqual(mock_create.call_args.args[2], 660.0)
		self.assertIn("DRINKING WATER", log.error_message or "")

	@patch("propms.api.v1.electricity.vendor.create_utility_bill")
	@patch("propms.api.v1.electricity.vendor.get_settings")
	@patch("propms.api.v1.electricity.vendor._invoice_split_amounts")
	def test_blocked_when_meter_not_allowlisted(self, mock_split, mock_settings, mock_create):
		from propms.api.v1.electricity.vendor import purchase_electricity_token

		mock_settings.return_value = self._mock_settings()
		mock_split.return_value = (660.0, 0.0)

		with patch("frappe.db.exists", return_value=True), patch(
			"frappe.db.get_value",
			side_effect=lambda *a, **k: _fake_get_value(*a, **k, meter="OTHER-METER"),
		), patch("frappe.get_meta") as meta, patch(
			"propms.api.v1.electricity.vendor._get_or_create_log"
		) as mock_log, patch("propms.api.v1.electricity.vendor._finalize_log") as mock_fin:
			meta.return_value.has_field.return_value = True
			log = MagicMock()
			log.name = "AFL-TEST-1"
			log.wt_id_t1 = None
			log.wt_id_t2 = None
			mock_log.return_value = log

			result = purchase_electricity_token("SI-FAKE-1")

		self.assertEqual(result["status"], "blocked")
		self.assertEqual(result["reason"], "meter_not_allowlisted")
		mock_create.assert_not_called()
		self.assertEqual(mock_fin.call_args.kwargs.get("status"), "Blocked")

	@patch("propms.api.v1.electricity.vendor.create_utility_bill")
	@patch("propms.api.v1.electricity.vendor.resolve_trackspm_meter_id")
	@patch("propms.api.v1.electricity.vendor.get_settings")
	@patch("propms.api.v1.electricity.vendor._invoice_split_amounts")
	def test_dual_tariff_two_requests(self, mock_split, mock_settings, mock_resolve, mock_create):
		from propms.api.v1.electricity.vendor import purchase_electricity_token

		mock_settings.return_value = self._mock_settings()
		mock_resolve.return_value = ("312", None, None)
		mock_split.return_value = (660.0, 4000.0)

		def _create(meter_id, tariff, amount, wallet_id=None):
			return {"wt_id": 1000 if tariff == "t1" else 2000, "error": False}

		mock_create.side_effect = _create

		with patch("frappe.db.exists", return_value=True), patch(
			"frappe.db.get_value",
			side_effect=lambda *a, **k: _fake_get_value(*a, **k, meter="TEST-SERIAL-ONLY"),
		), patch("frappe.get_meta") as meta, patch(
			"propms.api.v1.electricity.vendor._get_or_create_log"
		) as mock_log, patch("propms.api.v1.electricity.vendor._finalize_log"):
			meta.return_value.has_field.return_value = True
			log = MagicMock()
			log.name = "AFL-TEST-2"
			log.sales_invoice = "SI-FAKE-2"
			log.wt_id_t1 = None
			log.wt_id_t2 = None
			mock_log.return_value = log

			result = purchase_electricity_token("SI-FAKE-2")

		self.assertEqual(result["status"], "success")
		self.assertEqual(mock_create.call_count, 2)
		self.assertEqual([c.args[1] for c in mock_create.call_args_list], ["t1", "t2"])
		self.assertEqual(mock_create.call_args_list[0].args[0], "312")

	@patch("propms.api.v1.electricity.vendor.create_utility_bill")
	@patch("propms.api.v1.electricity.vendor.resolve_trackspm_meter_id")
	@patch("propms.api.v1.electricity.vendor.get_settings")
	@patch("propms.api.v1.electricity.vendor._invoice_split_amounts")
	def test_partial_when_one_tariff_fails(self, mock_split, mock_settings, mock_resolve, mock_create):
		from propms.api.v1.electricity.trackspm import TrackSPMError
		from propms.api.v1.electricity.vendor import purchase_electricity_token

		mock_settings.return_value = self._mock_settings()
		mock_resolve.return_value = ("312", None, None)
		mock_split.return_value = (660.0, 4000.0)

		def _create(meter_id, tariff, amount, wallet_id=None):
			if tariff == "t1":
				return {"wt_id": 111, "error": False}
			raise TrackSPMError("t2 boom")

		mock_create.side_effect = _create

		with patch("frappe.db.exists", return_value=True), patch(
			"frappe.db.get_value",
			side_effect=lambda *a, **k: _fake_get_value(*a, **k, meter="TEST-SERIAL-ONLY"),
		), patch("frappe.get_meta") as meta, patch(
			"propms.api.v1.electricity.vendor._get_or_create_log"
		) as mock_log, patch("propms.api.v1.electricity.vendor._finalize_log") as mock_fin, patch(
			"frappe.log_error"
		):
			meta.return_value.has_field.return_value = True
			log = MagicMock()
			log.name = "AFL-TEST-3"
			log.sales_invoice = "SI-FAKE-3"
			log.wt_id_t1 = None
			log.wt_id_t2 = None
			mock_log.return_value = log

			result = purchase_electricity_token("SI-FAKE-3")

		self.assertEqual(result["status"], "partial")
		self.assertEqual(mock_fin.call_args.kwargs.get("status"), "Partial")

	@patch("propms.api.v1.electricity.vendor.create_utility_bill")
	@patch("propms.api.v1.electricity.vendor.get_settings")
	@patch("propms.api.v1.electricity.vendor._invoice_split_amounts")
	def test_skipped_when_disabled(self, mock_split, mock_settings, mock_create):
		from propms.api.v1.electricity.vendor import purchase_electricity_token

		mock_settings.return_value = self._mock_settings(enabled=0)
		mock_split.return_value = (660.0, 0.0)

		with patch("frappe.db.exists", return_value=True), patch(
			"frappe.db.get_value",
			side_effect=lambda *a, **k: _fake_get_value(*a, **k, meter="TEST-SERIAL-ONLY"),
		), patch("frappe.get_meta") as meta, patch(
			"propms.api.v1.electricity.vendor._get_or_create_log"
		) as mock_log, patch("propms.api.v1.electricity.vendor._finalize_log"):
			meta.return_value.has_field.return_value = True
			log = MagicMock()
			log.name = "AFL-TEST-4"
			mock_log.return_value = log

			result = purchase_electricity_token("SI-FAKE-4")

		self.assertEqual(result["status"], "skipped")
		mock_create.assert_not_called()

	@patch("propms.api.v1.electricity.vendor.create_utility_bill")
	@patch("propms.api.v1.electricity.vendor.resolve_trackspm_meter_id")
	@patch("propms.api.v1.electricity.vendor.get_settings")
	@patch("propms.api.v1.electricity.vendor._invoice_split_amounts")
	def test_retry_skips_existing_wt_id(self, mock_split, mock_settings, mock_resolve, mock_create):
		from propms.api.v1.electricity.vendor import purchase_electricity_token

		mock_settings.return_value = self._mock_settings()
		mock_resolve.return_value = ("312", None, None)
		mock_split.return_value = (660.0, 4000.0)
		mock_create.return_value = {"wt_id": 2222, "error": False}

		with patch("frappe.db.exists", return_value=True), patch(
			"frappe.db.get_value",
			side_effect=lambda *a, **k: _fake_get_value(*a, **k, meter="TEST-SERIAL-ONLY"),
		), patch("frappe.get_meta") as meta, patch(
			"propms.api.v1.electricity.vendor._get_or_create_log"
		) as mock_log, patch("propms.api.v1.electricity.vendor._finalize_log"):
			meta.return_value.has_field.return_value = True
			log = MagicMock()
			log.name = "AFL-TEST-5"
			log.sales_invoice = "SI-FAKE-5"
			log.wt_id_t1 = "1111"
			log.wt_id_t2 = None
			mock_log.return_value = log

			result = purchase_electricity_token("SI-FAKE-5")

		self.assertEqual(mock_create.call_count, 1)
		self.assertEqual(mock_create.call_args.args[1], "t2")
		self.assertEqual(result["status"], "success")

	def test_resolve_uses_meter_trackspm_id_when_unrestricted(self):
		from propms.api.v1.electricity.vendor import resolve_trackspm_meter_id

		settings = self._mock_settings(restrict_purchases_to_allowlist=0)
		with patch("frappe.db.exists", return_value=True), patch(
			"frappe.get_meta"
		) as meta, patch("frappe.db.get_value", return_value="999"):
			meta.return_value.has_field.return_value = True
			mid, reason, err = resolve_trackspm_meter_id("ANY-SERIAL", settings)
		self.assertEqual(mid, "999")
		self.assertIsNone(reason)


class TestMeterStatusSerialize(unittest.TestCase):
	def test_serialize_meter_status_shape(self):
		from propms.api.v1.electricity.trackspm import serialize_meter_status

		row = {
			"meter_id": "312",
			"meter_serial": "92114710087",
			"meter_status": "Active",
			"meter_power": "On",
			"meter_type": "Electricity",
			"t1": "0",
			"t2": "0",
			"t1_time": "2026-10-01 10:00:00",
			"t1_relative_time": "just now",
			"t2_time": "2026-10-01 10:00:00",
			"t2_relative_time": "just now",
			"meter_minT1": "100",
			"meter_minT2": "20",
			"property_tariff_t1": "330.40",
			"property_tariff_t2": "4000.00",
			"property_currency": "TZS",
			"unit_reference": "TEST VIVA API METER",
			"zone_name": "Basement (W1-3)",
			"property_name": "Viva Towers",
		}
		out = serialize_meter_status(row)
		self.assertEqual(out["meter_serial"], "92114710087")
		self.assertEqual(out["meter_power"], "On")
		self.assertEqual(out["tanesco"]["units"], "0")
		self.assertEqual(out["generator"]["min_amount"], "20")


def _fake_get_value(*args, **kwargs):
	meter = kwargs.pop("meter", "TEST-SERIAL-ONLY")
	if len(args) >= 3:
		field = args[2]
		if field == "lease_item":
			return "Electricity"
		if field == "meter_number":
			return meter
		if field == "trackspm_meter_id":
			return "312"
	return None
