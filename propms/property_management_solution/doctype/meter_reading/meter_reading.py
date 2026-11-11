# -*- coding: utf-8 -*-
# Copyright (c) 2019, Aakvatech and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt


class MeterReading(Document):
    def validate(self):
        for row in self.get("meter_reading_detail") or []:
            current = flt(row.current_meter_reading)
            previous = flt(row.previous_meter_reading)

            if current < previous:
                frappe.throw(
                    _(
                        "Row {0}: Current Meter Reading must be greater than Previous Reading"
                    ).format(row.idx)
                )

            if current == previous and not cint(row.do_not_create_invoice):
                frappe.throw(
                    _(
                        "Row {0}: Same meter reading as previous requires Do not create invoice to be ticked"
                    ).format(row.idx)
                )

            row.reading_difference = current - previous
