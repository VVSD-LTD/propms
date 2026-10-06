# -*- coding: utf-8 -*-
"""Tests for manual electricity POS → Afritrack top-up guards and enqueue."""

from __future__ import unicode_literals

import unittest
from unittest.mock import MagicMock, patch

import frappe


def _fake_doc(**kwargs):
	items = kwargs.pop("items", None)
	d = MagicMock()
	defaults = {
		"name": "SI-TEST-ELEC",
		"docstatus": 1,
		"is_pos": 1,
		"lease_item": "Electricity",
		"meter_number": "92114710087",
		"selcom_order_id": None,
	}
	defaults.update(kwargs)
	for k, v in defaults.items():
		setattr(d, k, v)
	if items is None:
		items = [MagicMock(item_code="Electricity - TANESCO")]
	d.items = items
	return d


class TestManualElectricityPosGuards(unittest.TestCase):
	def test_non_electricity_skipped(self):
		from propms.api.v1.electricity.manual_pos_topup import is_manual_electricity_pos_candidate

		doc = _fake_doc(lease_item="Water", items=[MagicMock(item_code="DRINKING WATER")])
		self.assertFalse(is_manual_electricity_pos_candidate(doc))

	def test_non_pos_skipped(self):
		from propms.api.v1.electricity.manual_pos_topup import is_manual_electricity_pos_candidate

		doc = _fake_doc(is_pos=0)
		self.assertFalse(is_manual_electricity_pos_candidate(doc))

	def test_missing_meter_skipped(self):
		from propms.api.v1.electricity.manual_pos_topup import is_manual_electricity_pos_candidate

		doc = _fake_doc(meter_number="")
		self.assertFalse(is_manual_electricity_pos_candidate(doc))

	def test_selcom_mobile_skipped(self):
		from propms.api.v1.electricity.manual_pos_topup import is_manual_electricity_pos_candidate

		doc = _fake_doc(selcom_order_id="ORD-ABC")
		self.assertFalse(is_manual_electricity_pos_candidate(doc))

	def test_manual_pos_candidate_ok(self):
		from propms.api.v1.electricity.manual_pos_topup import is_manual_electricity_pos_candidate

		doc = _fake_doc()
		self.assertTrue(is_manual_electricity_pos_candidate(doc))

	def test_mixed_cart_still_candidate(self):
		from propms.api.v1.electricity.manual_pos_topup import is_manual_electricity_pos_candidate

		doc = _fake_doc(
			items=[
				MagicMock(item_code="Electricity - TANESCO"),
				MagicMock(item_code="DRINKING WATER"),
			]
		)
		self.assertTrue(is_manual_electricity_pos_candidate(doc))

	@patch("propms.api.v1.electricity.manual_pos_topup.has_electricity_item_amounts", return_value=True)
	@patch("propms.api.v1.electricity.manual_pos_topup.run_manual_electricity_pos_topup")
	def test_enqueue_runs_in_test(self, mock_run, mock_amounts):
		from propms.api.v1.electricity.manual_pos_topup import enqueue_manual_electricity_pos_topup

		frappe.flags.in_test = True
		doc = _fake_doc()
		enqueue_manual_electricity_pos_topup(doc)
		mock_run.assert_called_once_with(doc.name)

	@patch("propms.api.v1.electricity.manual_pos_topup.has_electricity_item_amounts", return_value=True)
	@patch("propms.api.v1.electricity.manual_pos_topup.run_manual_electricity_pos_topup")
	def test_enqueue_skips_non_electricity(self, mock_run, mock_amounts):
		from propms.api.v1.electricity.manual_pos_topup import enqueue_manual_electricity_pos_topup

		doc = _fake_doc(lease_item="POS Store", items=[MagicMock(item_code="Maintenance Fee")])
		enqueue_manual_electricity_pos_topup(doc)
		mock_run.assert_not_called()


class TestManualElectricityPosRun(unittest.TestCase):
	@patch("propms.api.v1.electricity.vendor.purchase_electricity_token")
	@patch("propms.api.v1.electricity.manual_pos_topup.should_enqueue_manual_topup", return_value=True)
	def test_run_calls_vendor(self, mock_should, mock_purchase):
		from propms.api.v1.electricity.manual_pos_topup import run_manual_electricity_pos_topup

		mock_purchase.return_value = {"status": "success"}
		with patch("frappe.db.exists", return_value=True), patch(
			"frappe.get_doc", return_value=_fake_doc()
		):
			result = run_manual_electricity_pos_topup("SI-TEST-ELEC")
		mock_purchase.assert_called_once_with("SI-TEST-ELEC", payment_transaction=None)
		self.assertEqual(result["status"], "success")

	@patch("propms.api.v1.electricity.vendor.purchase_electricity_token")
	def test_run_skips_when_guards_fail(self, mock_purchase):
		from propms.api.v1.electricity.manual_pos_topup import run_manual_electricity_pos_topup

		with patch("frappe.db.exists", return_value=True), patch(
			"frappe.get_doc", return_value=_fake_doc(items=[MagicMock(item_code="DRINKING WATER")])
		):
			result = run_manual_electricity_pos_topup("SI-WATER")
		mock_purchase.assert_not_called()
		self.assertEqual(result["status"], "skipped")


class TestElectricityAutofillFromLease(unittest.TestCase):
	@patch("propms.api.v1.electricity.manual_pos_topup.resolve_electricity_meter", return_value="92114710087")
	@patch("frappe.db.exists", return_value=True)
	@patch("frappe.db.get_value")
	def test_resolve_autofill(self, mock_get, mock_exists, mock_meter):
		from propms.api.v1.electricity.manual_pos_topup import resolve_autofill_from_lease

		def _get_value(doctype, name, field):
			if field == "property":
				return "B1904 (B2004 IN BUILDING)"
			if field == "customer":
				return "POS PAYER LTD"
			return None

		mock_get.side_effect = _get_value
		with patch("frappe.get_meta") as mock_meta:
			mock_meta.return_value.has_field = MagicMock(return_value=True)
			info = resolve_autofill_from_lease("LEASE-1")
		self.assertEqual(info["lease_item"], "Electricity")
		self.assertEqual(info["meter_number"], "92114710087")
		self.assertEqual(info["property"], "B1904 (B2004 IN BUILDING)")
		self.assertEqual(info["customer"], "POS PAYER LTD")
		self.assertEqual(info["pos_customer"], "POS PAYER LTD")

	@patch("propms.api.v1.electricity.manual_pos_topup.resolve_autofill_from_lease")
	def test_autofill_sets_fields_when_electricity_items(self, mock_resolve):
		from propms.api.v1.electricity.manual_pos_topup import autofill_electricity_from_lease

		mock_resolve.return_value = {
			"lease": "LEASE-1",
			"property": "PROP-1",
			"lease_item": "Electricity",
			"meter_number": "92114710087",
			"customer": "POS PAYER LTD",
			"pos_customer": "POS PAYER LTD",
		}
		doc = _fake_doc(docstatus=0, lease="LEASE-1", lease_item=None, meter_number=None, customer=None)
		doc.meta = MagicMock()
		doc.meta.has_field = MagicMock(return_value=True)
		item = MagicMock(item_code="Electricity - TANESCO")
		doc.items = [item]

		info = autofill_electricity_from_lease(doc)
		self.assertEqual(doc.lease_item, "Electricity")
		self.assertEqual(doc.meter_number, "92114710087")
		self.assertEqual(doc.customer, "POS PAYER LTD")
		self.assertIsNotNone(info)

	def test_autofill_skips_without_electricity_items(self):
		from propms.api.v1.electricity.manual_pos_topup import autofill_electricity_from_lease

		doc = _fake_doc(docstatus=0, lease="LEASE-1", lease_item="Electricity", meter_number="OLD")
		doc.meta = MagicMock()
		doc.meta.has_field = MagicMock(return_value=True)
		item = MagicMock(item_code="Maintenance Fee")
		doc.items = [item]
		self.assertIsNone(autofill_electricity_from_lease(doc))
		self.assertIn(doc.lease_item, (None, ""))
		self.assertIn(doc.meter_number, (None, ""))

	def test_autofill_clears_when_lease_removed(self):
		from propms.api.v1.electricity.manual_pos_topup import autofill_electricity_from_lease

		doc = _fake_doc(
			docstatus=0,
			lease="",
			lease_name="",
			lease_item="Electricity",
			meter_number="92114710087",
		)
		doc.meta = MagicMock()
		doc.meta.has_field = MagicMock(return_value=True)
		doc.items = [MagicMock(item_code="Electricity - TANESCO")]
		self.assertIsNone(autofill_electricity_from_lease(doc))
		self.assertIn(doc.lease_item, (None, ""))
		self.assertIn(doc.meter_number, (None, ""))
	@patch("propms.api.v1.electricity.manual_pos_topup.has_electricity_item_amounts", return_value=True)
	@patch("propms.api.v1.electricity.manual_pos_topup._topup_log_for_invoice")
	def test_show_retry_when_failed(self, mock_log, mock_amounts):
		from propms.api.v1.electricity.manual_pos_topup import get_electricity_topup_status

		log = MagicMock(status="Failed", name="AFL-1", wt_id_t1=None, wt_id_t2=None, error_message="x")
		mock_log.return_value = log
		doc = _fake_doc()
		with patch("frappe.has_permission"), patch("frappe.db.exists", return_value=True), patch(
			"frappe.get_doc", return_value=doc
		):
			status = get_electricity_topup_status("SI-TEST-ELEC")
		self.assertTrue(status["show_retry"])
		self.assertEqual(status["status"], "Failed")

	@patch("propms.api.v1.electricity.manual_pos_topup.has_electricity_item_amounts", return_value=True)
	@patch("propms.api.v1.electricity.manual_pos_topup._topup_log_for_invoice")
	def test_hide_retry_when_success(self, mock_log, mock_amounts):
		from propms.api.v1.electricity.manual_pos_topup import get_electricity_topup_status

		log = MagicMock(
			status="Success",
			name="AFL-1",
			wt_id_t1="25908",
			wt_id_t2=None,
			error_message=None,
		)
		mock_log.return_value = log
		doc = _fake_doc()
		with patch("frappe.has_permission"), patch("frappe.db.exists", return_value=True), patch(
			"frappe.get_doc", return_value=doc
		):
			status = get_electricity_topup_status("SI-TEST-ELEC")
		self.assertFalse(status["show_retry"])
		self.assertEqual(status["reason"], "success")
