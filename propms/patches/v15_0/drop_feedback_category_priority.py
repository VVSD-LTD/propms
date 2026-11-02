# -*- coding: utf-8 -*-
"""Drop Feedback.category and Feedback.priority (simplified tenant form)."""

from __future__ import unicode_literals

import frappe


def execute():
	if not frappe.db.exists("DocType", "Feedback"):
		return
	for col in ("category", "priority"):
		if frappe.db.has_column("Feedback", col):
			frappe.db.sql(f"ALTER TABLE `tabFeedback` DROP COLUMN `{col}`")
	frappe.db.commit()
