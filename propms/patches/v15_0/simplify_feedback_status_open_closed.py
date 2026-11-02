# -*- coding: utf-8 -*-
"""Remap Feedback statuses to Open / Closed only."""

from __future__ import unicode_literals

import frappe


def execute():
	if not frappe.db.exists("DocType", "Feedback"):
		return
	if not frappe.db.has_column("Feedback", "status"):
		return

	# Active / in-flight → Open
	frappe.db.sql(
		"""
		UPDATE `tabFeedback`
		SET status = 'Open'
		WHERE status IN ('In Progress', 'Open')
		"""
	)
	# Finished / withdrawn → Closed
	frappe.db.sql(
		"""
		UPDATE `tabFeedback`
		SET status = 'Closed'
		WHERE status IN ('Resolved', 'Cancelled', 'Canceled', 'Closed')
		   OR IFNULL(status, '') NOT IN ('Open', 'Closed')
		"""
	)
	# Anything still not Open/Closed (edge junk) → Open
	frappe.db.sql(
		"""
		UPDATE `tabFeedback`
		SET status = 'Open'
		WHERE IFNULL(status, '') NOT IN ('Open', 'Closed')
		"""
	)
	frappe.db.commit()
