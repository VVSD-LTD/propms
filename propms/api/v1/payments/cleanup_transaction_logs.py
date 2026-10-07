# -*- coding: utf-8 -*-
"""Scheduled cleanup for Selcom Payment Transaction Log.

Deletes:
  - all Cancelled rows (terminal, no settle value)
  - Pending rows older than 1 day (covers late-night checkouts; keeps DB lean)
"""

from __future__ import unicode_literals

import frappe
from frappe.utils import add_to_date, now_datetime


DOCTYPE = "Selcom Payment Transaction Log"
BATCH_SIZE = 200


def cleanup_selcom_payment_transaction_logs():
	"""Daily job: purge Cancelled + stale Pending transaction logs."""
	if not frappe.db.exists("DocType", DOCTYPE):
		return {"status": "skipped", "reason": "doctype_missing"}

	cutoff = add_to_date(now_datetime(), days=-1)
	cancelled = _delete_by_filters({"status": "Cancelled"})
	pending = _delete_by_filters(
		{
			"status": "Pending",
			"creation": ("<", cutoff),
		}
	)
	return {
		"status": "success",
		"cancelled_deleted": cancelled,
		"pending_deleted": pending,
		"pending_older_than": str(cutoff),
	}


def _delete_by_filters(filters):
	"""Delete matching docs in batches (also clears Versions via delete_doc)."""
	deleted = 0
	while True:
		names = frappe.get_all(
			DOCTYPE,
			filters=filters,
			pluck="name",
			limit=BATCH_SIZE,
			order_by="creation asc",
		)
		if not names:
			break
		for name in names:
			try:
				frappe.delete_doc(
					DOCTYPE,
					name,
					force=1,
					ignore_permissions=True,
					delete_permanently=True,
				)
				deleted += 1
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					"cleanup_selcom_payment_transaction_logs",
				)
		frappe.db.commit()
	return deleted
