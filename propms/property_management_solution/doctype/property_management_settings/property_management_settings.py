# -*- coding: utf-8 -*-
# Copyright (c) 2019, Aakvatech and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document


class PropertyManagementSettings(Document):
    def before_save(self):
        if self.enable_due_invoice_email_sending:
            stopped = frappe.db.get_value("Scheduled Job Type", {"method": "propms.custom.custom.get_overdue_sales_invoices"}, "stopped")
            if stopped:
                frappe.db.set_value("Scheduled Job Type", {"method": "propms.custom.custom.get_overdue_sales_invoices"}, "stopped", 0)
        else:
            frappe.db.set_value("Scheduled Job Type", {"method": "propms.custom.custom.get_overdue_sales_invoices"}, "stopped", 1)
