# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import hashlib
import json
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime, nowdate


class VisitorGatePass(Document):
	def before_insert(self):
		self.set_tenant_info()
		self.compute_valid_until()

	def after_insert(self):
		self.generate_qr_payload()
		self.db_set("qr_payload", self.qr_payload)

	def validate(self):
		self.set_tenant_info()
		self.compute_valid_until()
		if self.name:
			self.generate_qr_payload()


	def set_tenant_info(self):
		if not self.tenant and frappe.session.user != "Guest":
			self.tenant = frappe.session.user
		if self.tenant and not self.tenant_name:
			self.tenant_name = frappe.db.get_value("User", self.tenant, "full_name") or self.tenant

	def compute_valid_until(self):
		if not self.expected_arrival_date:
			self.expected_arrival_date = nowdate()

		arrival_time = self.expected_arrival_time or "00:00:00"
		arrival_dt_str = f"{self.expected_arrival_date} {arrival_time}"

		try:
			arrival_dt = get_datetime(arrival_dt_str)
		except Exception:
			arrival_dt = now_datetime()

		validity = self.validity_type or "One-Time Entry"
		if validity == "One-Time Entry":
			# Valid until end of the expected arrival day (23:59:59)
			self.valid_until = get_datetime(f"{self.expected_arrival_date} 23:59:59")
		elif validity == "Full Day":
			# Valid for 24 hours from expected arrival time
			self.valid_until = add_to_date(arrival_dt, hours=24)
		elif validity == "48h Stay":
			# Valid for 48 hours from expected arrival time
			self.valid_until = add_to_date(arrival_dt, hours=48)
		else:
			self.valid_until = get_datetime(f"{self.expected_arrival_date} 23:59:59")

	def generate_qr_payload(self):
		secret_token = hashlib.sha256(
			f"{self.name or 'TEMP'}-{self.tenant}-{self.expected_arrival_date}-{frappe.generate_hash(length=8)}".encode()
		).hexdigest()[:16]

		payload = {
			"pass_id": self.name,
			"visitor_name": self.visitor_name,
			"visitor_type": self.visitor_type,
			"property_unit": self.property_unit,
			"expected_arrival_date": str(self.expected_arrival_date),
			"token": secret_token,
		}
		self.qr_payload = json.dumps(payload)
