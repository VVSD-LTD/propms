# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class VivaSelcomSettings(Document):
    def onload(self):
        self.set_ipn_callback_url()

    def validate(self):
        self.set_ipn_callback_url()

    def set_ipn_callback_url(self):
        self.ipn_callback_url = frappe.utils.get_url(
            "/api/method/propms.api.v1.payments.selcom_ipn_webhook"
        )

