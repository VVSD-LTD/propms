# -*- coding: utf-8 -*-
"""Electricity POS settle + TrackSPM load — mocked Selcom/TrackSPM (no live money).

Covers:
  - TANESCO only (t1)
  - Generator only (t2)
  - Both tariffs on one SI
  - Double settle (IPN + status poll) → exactly one Sales Invoice
  - Concurrent settle race → exactly one Sales Invoice
"""

from __future__ import unicode_literals

import json
import threading
import unittest
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from propms.api.v1.payments.workflows import WORKFLOW_ELECTRICITY_POS


SITE = getattr(frappe.local, "site", None) or "dev15-viva2.vvsdtz.com"


def _ensure_selcom_order_field():
	"""Unique Selcom order id on SI — hard DB guard against duplicate settles."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	if frappe.get_meta("Sales Invoice").has_field("selcom_order_id"):
		return
	create_custom_fields(
		{
			"Sales Invoice": [
				{
					"fieldname": "selcom_order_id",
					"label": "Selcom Order ID",
					"fieldtype": "Data",
					"insert_after": "meter_number",
					"unique": 1,
					"read_only": 1,
					"no_copy": 1,
					"description": "Selcom/Viva order_id — unique so one payment cannot create two SIs",
				}
			]
		},
		update=True,
	)
	frappe.clear_cache(doctype="Sales Invoice")


def _intent(tanesco=0, generator=0, order_suffix="TEST"):
	from propms.api.v1.electricity.electricity import (
		ITEM_TANESCO,
		ITEM_GENERATOR,
		units_from_amount,
		get_item_selling_rate,
	)

	lines = []
	for item_code, amt in ((ITEM_TANESCO, tanesco), (ITEM_GENERATOR, generator)):
		amt = flt(amt)
		if amt <= 0:
			continue
		rate, _ = get_item_selling_rate(item_code)
		lines.append(
			{
				"item_code": item_code,
				"qty": units_from_amount(amt, rate),
				"rate": rate,
				"amount_inclusive": amt,
			}
		)
	total = flt(tanesco) + flt(generator)
	return {
		"electricity_purchase": True,
		"payment_workflow": WORKFLOW_ELECTRICITY_POS,
		"customer": "BARAKA TRADING TANZANIA LTD",
		"company": "Virgin Plaza Ltd.",
		"lease": "B1904 (B2004 IN BUILDING)-194413",
		"property": "B1904 (B2004 IN BUILDING)",
		"cost_center": "B1904 (B2004 IN BUILDING) - VPL",
		"meter_number": "92114710087",
		"tanesco_amount": flt(tanesco),
		"generator_amount": flt(generator),
		"total_amount": total,
		"lines": lines,
		"price_list": "Standard Selling",
		"_order_suffix": order_suffix,
	}


def _make_txn(intent):
	suffix = frappe.generate_hash(length=8).upper()
	order_id = "ORD-UT-{0}-{1}".format(intent.get("_order_suffix") or "ELEC", suffix)
	raw = dict(intent)
	raw.pop("_order_suffix", None)
	raw["order_id"] = order_id
	txn = frappe.get_doc(
		{
			"doctype": "Selcom Payment Transaction Log",
			"order_id": order_id,
			"payment_workflow": WORKFLOW_ELECTRICITY_POS,
			"reference_doctype": "Lease",
			"reference_name": intent.get("lease"),
			"customer": intent["customer"],
			"amount": intent["total_amount"],
			"currency": "TZS",
			"payment_channel": "MOBILE_MONEY",
			"status": "Pending",
			"raw_request": json.dumps(raw),
		}
	)
	txn.insert(ignore_permissions=True)
	frappe.db.commit()
	return txn


def _count_sis_for_order(order_id):
	like_order = "%Order: {0}%".format(order_id)
	like_selcom = "%Paid via Selcom ({0})%".format(order_id)
	filters = {"docstatus": ("<", 2)}
	if frappe.get_meta("Sales Invoice").has_field("selcom_order_id"):
		by_field = frappe.get_all(
			"Sales Invoice",
			filters={"selcom_order_id": order_id, "docstatus": ("<", 2)},
			pluck="name",
		)
		if by_field:
			return by_field
	rows = frappe.db.sql(
		"""
		SELECT name FROM `tabSales Invoice`
		WHERE docstatus < 2
		  AND (remarks LIKE %s OR remarks LIKE %s)
		ORDER BY creation
		""",
		(like_order, like_selcom),
	)
	return [r[0] for r in rows]


def _cleanup(txn_name, invoice_names):
	for inv in invoice_names or []:
		if not inv or not frappe.db.exists("Sales Invoice", inv):
			continue
		docstatus = frappe.db.get_value("Sales Invoice", inv, "docstatus")
		try:
			if docstatus == 1:
				si = frappe.get_doc("Sales Invoice", inv)
				si.flags.ignore_permissions = True
				si.cancel()
			frappe.delete_doc("Sales Invoice", inv, force=1, ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "UT electricity settle cleanup SI")
		# Load logs unique on SI — delete if any
		for log in frappe.get_all("Afritrack Load Log", filters={"sales_invoice": inv}, pluck="name"):
			frappe.delete_doc("Afritrack Load Log", log, force=1, ignore_permissions=True)
	if txn_name and frappe.db.exists("Selcom Payment Transaction Log", txn_name):
		frappe.delete_doc("Selcom Payment Transaction Log", txn_name, force=1, ignore_permissions=True)
	frappe.db.commit()


class TestElectricitySettleMocked(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_selcom_order_field()
		if not frappe.db.exists("Customer", "BARAKA TRADING TANZANIA LTD"):
			raise unittest.SkipTest("BARAKA customer missing")
		if not frappe.db.exists("Item", "Electricity - TANESCO"):
			raise unittest.SkipTest("Electricity items missing")

	def setUp(self):
		self._created = []

	def tearDown(self):
		for txn_name, invoices in self._created:
			_cleanup(txn_name, invoices)

	def _settle(self, tanesco=0, generator=0, label="X"):
		from propms.api.v1.payments.handlers_electricity_pos import settle_electricity_pos

		intent = _intent(tanesco=tanesco, generator=generator, order_suffix=label)
		txn = _make_txn(intent)
		self._created.append([txn.name, []])

		trackspm_calls = []

		def fake_create(meter_id, tariff, amount, wallet_id=None):
			trackspm_calls.append({"meter_id": meter_id, "tariff": tariff, "amount": flt(amount)})
			return {"error": False, "wt_id": 90000 + len(trackspm_calls), "messages": ["mocked"]}

		with patch(
			"propms.api.v1.electricity.vendor.create_utility_bill", side_effect=fake_create
		), patch(
			"propms.api.v1.electricity.vendor.get_settings"
		) as mock_settings, patch(
			"propms.api.v1.electricity.vendor.resolve_trackspm_meter_id",
			return_value=("308", None, None),
		), patch(
			"propms.api.v1.electricity.vendor.cint_enabled", return_value=True
		):
			settings = frappe._dict(
				enabled=1,
				restrict_purchases_to_allowlist=0,
				allowed_meter_serial="92114710087",
				allowed_meter_id="308",
				wallet_id="112",
				property_id="5",
			)
			mock_settings.return_value = settings

			txn.reload()
			res = settle_electricity_pos(
				txn,
				txn.order_id,
				selcom_ref="MOCK-{0}".format(txn.order_id),
				amount=intent["total_amount"],
			)

		txn.reload()
		invoices = _count_sis_for_order(txn.order_id)
		self._created[-1][1] = list(set(invoices + ([txn.sales_invoice] if txn.sales_invoice else [])))
		return res, txn, trackspm_calls, invoices

	def test_tanesco_only_one_si_one_trackspm_t1(self):
		res, txn, calls, invoices = self._settle(tanesco=100, generator=0, label="T1")
		self.assertEqual(res.get("status"), "success", res)
		self.assertEqual(len(invoices), 1, invoices)
		self.assertEqual(txn.status, "Success")
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0]["tariff"], "t1")
		self.assertEqual(flt(calls[0]["amount"]), 100)

	def test_generator_only_one_si_one_trackspm_t2(self):
		res, txn, calls, invoices = self._settle(tanesco=0, generator=300, label="T2")
		self.assertEqual(res.get("status"), "success", res)
		self.assertEqual(len(invoices), 1, invoices)
		self.assertEqual(txn.status, "Success")
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0]["tariff"], "t2")
		self.assertEqual(flt(calls[0]["amount"]), 300)

	def test_both_tariffs_one_si_two_trackspm_calls(self):
		res, txn, calls, invoices = self._settle(tanesco=100, generator=200, label="BOTH")
		self.assertEqual(res.get("status"), "success", res)
		self.assertEqual(len(invoices), 1, invoices)
		self.assertEqual(txn.status, "Success")
		self.assertEqual(len(calls), 2)
		tariffs = sorted(c["tariff"] for c in calls)
		self.assertEqual(tariffs, ["t1", "t2"])
		by_t = {c["tariff"]: flt(c["amount"]) for c in calls}
		self.assertEqual(by_t["t1"], 100)
		self.assertEqual(by_t["t2"], 200)

	def test_double_settle_same_order_still_one_si(self):
		"""Simulate IPN then get_payment_status both calling settle."""
		from propms.api.v1.payments.handlers_electricity_pos import settle_electricity_pos

		intent = _intent(tanesco=0, generator=300, order_suffix="DBL")
		txn = _make_txn(intent)
		self._created.append([txn.name, []])
		calls = []

		def fake_create(meter_id, tariff, amount, wallet_id=None):
			calls.append({"tariff": tariff, "amount": flt(amount)})
			return {"error": False, "wt_id": 91000 + len(calls)}

		with patch(
			"propms.api.v1.electricity.vendor.create_utility_bill", side_effect=fake_create
		), patch(
			"propms.api.v1.electricity.vendor.get_settings",
			return_value=frappe._dict(enabled=1, restrict_purchases_to_allowlist=0, wallet_id="112"),
		), patch(
			"propms.api.v1.electricity.vendor.resolve_trackspm_meter_id",
			return_value=("308", None, None),
		), patch(
			"propms.api.v1.electricity.vendor.cint_enabled", return_value=True
		):
			r1 = settle_electricity_pos(frappe.get_doc("Selcom Payment Transaction Log", txn.name), txn.order_id, amount=300)
			r2 = settle_electricity_pos(frappe.get_doc("Selcom Payment Transaction Log", txn.name), txn.order_id, amount=300)

		self.assertEqual(r1.get("status"), "success", r1)
		self.assertEqual(r2.get("status"), "success", r2)
		self.assertTrue(r2.get("idempotent") or r2.get("invoice") == r1.get("invoice"), r2)

		invoices = _count_sis_for_order(txn.order_id)
		self._created[-1][1] = invoices
		self.assertEqual(len(invoices), 1, "double settle created {0}".format(invoices))
		# TrackSPM must not be charged twice for the same SI (idempotent vendor)
		self.assertEqual(len(calls), 1, calls)

	def test_concurrent_settle_race_one_si(self):
		"""Two threads settle the same Pending TXN — must produce one SI."""
		from propms.api.v1.payments.handlers_electricity_pos import settle_electricity_pos

		intent = _intent(tanesco=0, generator=300, order_suffix="RACE")
		txn = _make_txn(intent)
		self._created.append([txn.name, []])
		order_id = txn.order_id
		txn_name = txn.name
		results = [None, None]
		errors = [None, None]
		calls_lock = threading.Lock()
		calls = []

		def fake_create(meter_id, tariff, amount, wallet_id=None):
			with calls_lock:
				calls.append({"tariff": tariff, "amount": flt(amount)})
				n = len(calls)
			return {"error": False, "wt_id": 92000 + n}

		def worker(idx):
			import frappe as _frappe

			try:
				_frappe.init(site=SITE)
				_frappe.connect()
				_frappe.set_user("Administrator")
				with patch(
					"propms.api.v1.electricity.vendor.create_utility_bill", side_effect=fake_create
				), patch(
					"propms.api.v1.electricity.vendor.get_settings",
					return_value=_frappe._dict(
						enabled=1, restrict_purchases_to_allowlist=0, wallet_id="112"
					),
				), patch(
					"propms.api.v1.electricity.vendor.resolve_trackspm_meter_id",
					return_value=("308", None, None),
				), patch(
					"propms.api.v1.electricity.vendor.cint_enabled", return_value=True
				):
					doc = _frappe.get_doc("Selcom Payment Transaction Log", txn_name)
					results[idx] = settle_electricity_pos(doc, order_id, amount=300)
			except Exception as e:
				errors[idx] = str(e)
				_frappe.log_error(_frappe.get_traceback(), "UT concurrent settle")
			finally:
				_frappe.destroy()

		t1 = threading.Thread(target=worker, args=(0,))
		t2 = threading.Thread(target=worker, args=(1,))
		t1.start()
		t2.start()
		t1.join(timeout=120)
		t2.join(timeout=120)

		self.assertIsNone(errors[0], errors)
		self.assertIsNone(errors[1], errors)
		self.assertTrue(all(r and r.get("status") == "success" for r in results), results)

		# Reload in this connection
		frappe.connect()
		invoices = _count_sis_for_order(order_id)
		txn = frappe.get_doc("Selcom Payment Transaction Log", txn_name)
		self._created[-1][1] = invoices + ([txn.sales_invoice] if txn.sales_invoice else [])
		self.assertEqual(
			len(invoices),
			1,
			"concurrent settle created {0} invoices: {1}; results={2}".format(
				len(invoices), invoices, results
			),
		)
		self.assertLessEqual(len(calls), 1, "TrackSPM called too many times: {0}".format(calls))


if __name__ == "__main__":
	unittest.main()
