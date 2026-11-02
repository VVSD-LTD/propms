# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.model.document import Document


class FeedbackDepartment(Document):
	def before_validate(self):
		if self.department_name:
			self.department_name = self.department_name.strip()
		if self.sort_order is None:
			self.sort_order = 10


def get_active_feedback_departments():
	"""Active departments for mobile pickers."""
	return frappe.get_all(
		"Feedback Department",
		filters={"is_active": 1},
		fields=["name", "department_name", "description", "sort_order"],
		order_by="sort_order asc, department_name asc",
		ignore_permissions=True,
	)
