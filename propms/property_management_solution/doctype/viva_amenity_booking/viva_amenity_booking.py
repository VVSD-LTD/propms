# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


class VivaAmenityBooking(Document):
	def validate(self):
		if not self.tenant and frappe.session.user != "Guest":
			self.tenant = frappe.session.user
		if self.tenant and not self.tenant_name:
			self.tenant_name = frappe.db.get_value("User", self.tenant, "full_name") or self.tenant

		# Auto-complete status if past end time
		if self.status == "Confirmed" and self.booking_date and self.end_time:
			try:
				booking_end_dt = get_datetime(f"{self.booking_date} {self.end_time}")
				if booking_end_dt < now_datetime():
					self.status = "Completed"
			except Exception:
				pass
