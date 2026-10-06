# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import frappe
import unittest
from frappe.utils import flt


class TestElectricityHelpers(unittest.TestCase):
    def test_validate_allocation_ok(self):
        from propms.api.v1.electricity.electricity import validate_allocation

        validate_allocation(20000, 15000, 5000)

    def test_validate_allocation_mismatch(self):
        from propms.api.v1.electricity.electricity import validate_allocation

        with self.assertRaises(frappe.ValidationError):
            validate_allocation(20000, 10000, 5000)

    def test_units_from_inclusive_amount(self):
        from propms.api.v1.electricity.electricity import units_from_amount

        self.assertAlmostEqual(units_from_amount(50000, 330.4), 151.331719128, places=6)
        self.assertEqual(units_from_amount(0, 330.4), 0)
        self.assertEqual(units_from_amount(5000, 0), 0)
