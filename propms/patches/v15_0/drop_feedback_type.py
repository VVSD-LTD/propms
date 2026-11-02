# -*- coding: utf-8 -*-
"""Drop Feedback.feedback_type (Type Feedback/Complaint removed)."""

from __future__ import unicode_literals

import frappe


def execute():
	if not frappe.db.exists("DocType", "Feedback"):
		return
	if frappe.db.has_column("Feedback", "feedback_type"):
		frappe.db.sql("ALTER TABLE `tabFeedback` DROP COLUMN `feedback_type`")
	frappe.db.commit()
