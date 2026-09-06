# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime


class VivaCallLog(Document):
	def validate(self):
		if not self.started_at:
			self.started_at = now_datetime()

		if self.status in ["Ended", "Completed"] and self.connected_at and self.ended_at:
			try:
				c_dt = get_datetime(self.connected_at)
				e_dt = get_datetime(self.ended_at)
				if e_dt >= c_dt:
					self.duration_seconds = int((e_dt - c_dt).total_seconds())
			except Exception:
				pass
