# Copyright (c) 2026, VV Systems Developer LTD and contributors

import frappe
from frappe.utils import flt


def _source(**kwargs):
	data = {
		"doctype": "Sales Invoice",
		"name": "ACC-SINV-TEST",
		"docstatus": 1,
		"penalty_paid": 1,
		"penalty_invoice": None,
		"outstanding_penalty_amount": 1500,
		"total_penalty_amount": 1500,
		"customer": "Customer A",
		"company": "Virgin Plaza Ltd.",
		"currency": "TZS",
		"selling_price_list": "Standard Selling",
		"cost_center": "Main - VPL",
	}
	data.update(kwargs)
	return frappe._dict(data)


def _settings(item="Late Payment Interest"):
	return frappe._dict(penalty_item=item)


class TestCreatePenaltyInvoice:
	def test_create_function_exists(self):
		from propms.custom.sales_invoice_penalty import create_penalty_invoice

		assert callable(create_penalty_invoice)

	def test_payload_uses_settings_item_and_outstanding_penalty(self):
		from propms.custom.sales_invoice_penalty import penalty_invoice_payload

		payload = penalty_invoice_payload(_source(), _settings("PENALTY-ITEM"))
		assert payload["doctype"] == "Sales Invoice"
		assert payload["customer"] == "Customer A"
		assert payload["company"] == "Virgin Plaza Ltd."
		assert payload["currency"] == "TZS"
		assert payload["update_stock"] == 0
		assert len(payload["items"]) == 1
		row = payload["items"][0]
		assert row["item_code"] == "PENALTY-ITEM"
		assert row["qty"] == 1
		assert flt(row["rate"]) == 1500
		assert row["cost_center"] == "Main - VPL"
		assert "ACC-SINV-TEST" in payload["remarks"]
		assert payload["taxes_and_charges"] == "Incl VAT TZ - VPL"

	def test_payload_uses_company_default_tax_template(self):
		from propms.custom.sales_invoice_penalty import penalty_invoice_payload

		source = _source(taxes_and_charges="Tanzania Tax - VPL")
		payload = penalty_invoice_payload(source, _settings("PENALTY-ITEM"))
		assert payload["taxes_and_charges"] == "Incl VAT TZ - VPL"

	def test_payload_falls_back_to_source_tax_template(self):
		from propms.custom.sales_invoice_penalty import penalty_invoice_payload

		source = _source(
			company="Company Without Default Tax",
			taxes_and_charges="Tanzania Tax - VPL",
		)
		payload = penalty_invoice_payload(source, _settings("PENALTY-ITEM"))
		assert payload["taxes_and_charges"] == "Tanzania Tax - VPL"

	def test_payload_keeps_company_currency_without_exchange_rate(self):
		from propms.custom.sales_invoice_penalty import penalty_invoice_payload

		payload = penalty_invoice_payload(_source(currency="TZS", conversion_rate=1), _settings())
		assert "conversion_rate" not in payload

	def test_payload_copies_foreign_exchange_rate(self):
		from propms.custom.sales_invoice_penalty import penalty_invoice_payload

		source = _source(
			currency="USD",
			conversion_rate=2650,
			price_list_currency="USD",
			plc_conversion_rate=2650,
		)
		payload = penalty_invoice_payload(source, _settings())
		assert payload["currency"] == "USD"
		assert flt(payload["conversion_rate"]) == 2650
		assert flt(payload["plc_conversion_rate"]) == 2650

	def test_refuses_when_penalty_paid_is_off(self):
		from propms.custom.sales_invoice_penalty import assert_can_create_penalty_invoice

		try:
			assert_can_create_penalty_invoice(_source(penalty_paid=0), _settings())
		except frappe.ValidationError as exc:
			assert "Penalty Paid" in str(exc)
			return
		raise AssertionError("expected ValidationError")

	def test_refuses_when_penalty_invoice_already_set(self):
		from propms.custom.sales_invoice_penalty import assert_can_create_penalty_invoice

		try:
			assert_can_create_penalty_invoice(
				_source(penalty_invoice="ACC-SINV-PENALTY"),
				_settings(),
			)
		except frappe.ValidationError as exc:
			assert "ACC-SINV-PENALTY" in str(exc)
			return
		raise AssertionError("expected ValidationError")

	def test_refuses_when_penalty_item_missing(self):
		from propms.custom.sales_invoice_penalty import assert_can_create_penalty_invoice

		try:
			assert_can_create_penalty_invoice(_source(), _settings(item=None))
		except frappe.ValidationError as exc:
			assert "Penalty Item" in str(exc)
			return
		raise AssertionError("expected ValidationError")

	def test_refuses_when_no_penalty_amount(self):
		from propms.custom.sales_invoice_penalty import assert_can_create_penalty_invoice

		try:
			assert_can_create_penalty_invoice(
				_source(outstanding_penalty_amount=0, total_penalty_amount=0),
				_settings(),
			)
		except frappe.ValidationError as exc:
			assert "penalty amount" in str(exc).lower()
			return
		raise AssertionError("expected ValidationError")


def execute():
	cases = [
		"test_create_function_exists",
		"test_payload_uses_settings_item_and_outstanding_penalty",
		"test_payload_uses_company_default_tax_template",
		"test_payload_falls_back_to_source_tax_template",
		"test_payload_keeps_company_currency_without_exchange_rate",
		"test_payload_copies_foreign_exchange_rate",
		"test_refuses_when_penalty_paid_is_off",
		"test_refuses_when_penalty_invoice_already_set",
		"test_refuses_when_penalty_item_missing",
		"test_refuses_when_no_penalty_amount",
	]
	runner = TestCreatePenaltyInvoice()
	for name in cases:
		getattr(runner, name)()
		print(f"PASS: {name}")
	print("ALL UNIT CHECKS PASSED")
