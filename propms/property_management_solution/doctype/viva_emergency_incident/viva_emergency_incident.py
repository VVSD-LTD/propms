# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class VivaEmergencyIncident(Document):
	def validate(self):
		if not self.reported_at:
			self.reported_at = now_datetime()
		if not self.reporter and frappe.session.user != "Guest":
			self.reporter = frappe.session.user
		if self.reporter and not self.reporter_name:
			self.reporter_name = frappe.db.get_value("User", self.reporter, "full_name") or self.reporter
		if self.reporter and not self.reporter_phone:
			self.reporter_phone = frappe.db.get_value("User", self.reporter, "mobile_no") or ""
