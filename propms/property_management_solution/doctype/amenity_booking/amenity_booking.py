# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


class AmenityBooking(Document):
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

	def on_update(self):
		"""Keep Amenity Booking Series child row in sync on status/time changes."""
		if not self.series:
			return
		if not (
			self.has_value_changed("status")
			or self.has_value_changed("booking_date")
			or self.has_value_changed("start_time")
			or self.has_value_changed("end_time")
		):
			return
		# Skip when create path already synced immediately after insert
		if self.flags.get("skip_series_sync"):
			return
		try:
			from propms.api.v1.amenities.series_items import sync_series_booking_row

			sync_series_booking_row(self.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "AmenityBooking.on_update.sync_series")
