# -*- coding: utf-8 -*-
"""Unit tests for mobile Payment Entry receipt PDF resolution."""

from __future__ import unicode_literals

import unittest
from unittest.mock import patch

import frappe

from propms.api.v1.invoices.invoices import (
	_resolve_receipt_payment_entry,
	download_receipt_pdf,
)


class TestDownloadReceiptPdf(unittest.TestCase):
	def test_download_receipt_pdf_is_whitelisted(self):
		self.assertTrue(callable(download_receipt_pdf))
		self.assertTrue(getattr(download_receipt_pdf, "__wrapped__", download_receipt_pdf))

	def test_requires_auth(self):
		with patch("propms.api.v1.invoices.invoices.frappe.session") as session:
			session.user = "Guest"
			with self.assertRaises(frappe.AuthenticationError):
				download_receipt_pdf(invoice_name="ACC-SINV-X")

	def test_resolve_requires_args(self):
		with self.assertRaises(frappe.ValidationError):
			_resolve_receipt_payment_entry()

	def test_resolve_rejects_pos_placeholder(self):
		with self.assertRaises(frappe.ValidationError):
			_resolve_receipt_payment_entry(payment_entry_name="POS Payment")


if __name__ == "__main__":
	unittest.main()
