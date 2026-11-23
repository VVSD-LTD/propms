# -*- coding: utf-8 -*-
"""Single Payment Entry for principal + penalty; no pay-nudge on penalty SI."""

from __future__ import unicode_literals

import unittest

import frappe
from frappe.utils import flt


class TestPenaltyInvoiceNotifySkip(unittest.TestCase):
	def test_remarks_detect_penalty_reimbursement(self):
		from propms.api.v1.invoices.invoices import is_penalty_reimbursement_invoice

		doc = frappe._dict(
			name="ACC-SINV-PEN",
			remarks="Late payment penalty for ACC-SINV-SRC",
			items=[],
		)
		self.assertTrue(is_penalty_reimbursement_invoice(doc))

	def test_normal_invoice_not_penalty_reimbursement(self):
		from propms.api.v1.invoices.invoices import is_penalty_reimbursement_invoice

		doc = frappe._dict(
			name="ACC-SINV-NORMAL",
			remarks="No Remarks",
			items=[frappe._dict(item_code="Service Charge - Residential")],
		)
		self.assertFalse(is_penalty_reimbursement_invoice(doc))


class TestMultiInvoicePaymentEntryHelper(unittest.TestCase):
	def test_helper_exists(self):
		from propms.api.v1.payments.invoice_amount import create_payment_entry_for_allocations

		self.assertTrue(callable(create_payment_entry_for_allocations))


if __name__ == "__main__":
	unittest.main()
