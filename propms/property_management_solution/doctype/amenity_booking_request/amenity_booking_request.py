# -*- coding: utf-8 -*-
# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document


class AmenityBookingRequest(Document):
	def validate(self):
		if not self.tenant and frappe.session.user != "Guest":
			self.tenant = frappe.session.user
		if self.tenant and not self.tenant_name:
			self.tenant_name = frappe.db.get_value("User", self.tenant, "full_name") or self.tenant

		if self.status == "Rejected" and not self.rejection_reason:
			frappe.throw("Rejection Reason is required when status is Rejected")
