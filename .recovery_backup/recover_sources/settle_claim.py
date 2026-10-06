# -*- coding: utf-8 -*-
"""Idempotent settlement claim for Viva Payment Transaction.

ERPNext Sales Invoice insert/submit calls ``frappe.db.commit()``, which releases
InnoDB ``FOR UPDATE`` locks. Concurrent Selcom IPN + ``get_payment_status`` then
both create Paid invoices for the same order.

Fix: MySQL ``GET_LOCK`` held for the *entire* settle (survives commits), plus
atomic status claim and reuse of any SI already tagged with this order_id.
"""

from __future__ import unicode_literals

import re
from contextlib import contextmanager

import frappe
from frappe.utils import get_datetime, now_datetime, time_diff_in_seconds

# If a worker dies after claiming Processing, allow another settler to reclaim.
STALE_PROCESSING_SECONDS = 120

CLAIM_ALREADY_SETTLED = "already_settled"
CLAIM_RESUME_INVOICE = "resume_invoice"
CLAIM_IN_PROGRESS = "in_progress"
CLAIM_CLAIMED = "claimed"


def _lock_key(order_id, txn_name=None):
	raw = (order_id or txn_name or "unknown").strip()
	safe = re.sub(r"[^A-Za-z0-9_-]", "", raw)[:48]
	return "viva_settle_{0}".format(safe or "unknown")


@contextmanager
def settlement_lock(order_id, txn_name=None, timeout=60):
	"""Advisory lock that survives frappe.db.commit() during SI submit."""
	key = _lock_key(order_id, txn_name)
	got = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (key, int(timeout)))[0][0]
	if not int(got or 0):
		raise SettlementLockBusy(key)
	try:
		yield key
	finally:
		try:
			frappe.db.sql("SELECT RELEASE_LOCK(%s)", (key,))
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Viva Settle RELEASE_LOCK")


class SettlementLockBusy(Exception):
	def __init__(self, key):
		self.key = key
		super(SettlementLockBusy, self).__init__("Settlement lock busy: {0}".format(key))


def claim_payment_settlement(txn, stale_seconds=STALE_PROCESSING_SECONDS):
	"""Claim the right to settle one Viva Payment Transaction (call under settlement_lock).

	Returns ``(outcome, txn)`` where outcome is one of:
	- ``already_settled`` — Success + linked SI exists
	- ``resume_invoice`` — SI already linked; finish Success / vendor only
	- ``in_progress`` — another worker holds a fresh Processing claim
	- ``claimed`` — this worker owns settlement; safe to create SI
	"""
	if not txn or not txn.name:
		frappe.throw("Payment transaction is required to claim settlement")

	txn.reload()

	# Prefer any SI already created for this Selcom order (crash / race recovery)
	existing_si = find_invoice_for_order(txn.order_id)
	if existing_si and not _has_linked_invoice(txn):
		frappe.db.set_value(
			"Viva Payment Transaction",
			txn.name,
			{"sales_invoice": existing_si, "status": "Processing"},
			update_modified=True,
		)
		frappe.db.commit()
		txn.reload()

	if _has_linked_invoice(txn):
		if txn.status == "Success":
			return CLAIM_ALREADY_SETTLED, txn
		return CLAIM_RESUME_INVOICE, txn

	if txn.status == "Success":
		return CLAIM_ALREADY_SETTLED, txn

	if txn.status == "Processing" and not _is_stale_processing(txn, stale_seconds):
		return CLAIM_IN_PROGRESS, txn

	# Stale Processing with no SI — reopen so atomic claim can win
	if txn.status == "Processing" and _is_stale_processing(txn, stale_seconds):
		frappe.db.set_value(
			"Viva Payment Transaction",
			txn.name,
			"status",
			"Pending",
			update_modified=True,
		)
		frappe.db.commit()
		txn.reload()

	# Atomic compare-and-set: only one connection can flip Pending → Processing
	now = now_datetime()
	user = frappe.session.user or "Administrator"
	frappe.db.sql(
		"""
		UPDATE `tabViva Payment Transaction`
		SET status = 'Processing', modified = %s, modified_by = %s
		WHERE name = %s
		  AND status IN ('Pending', 'Failed')
		  AND IFNULL(sales_invoice, '') = ''
		""",
		(now, user, txn.name),
	)
	frappe.db.commit()
	txn.reload()

	if _has_linked_invoice(txn):
		return CLAIM_RESUME_INVOICE, txn
	if txn.status == "Success":
		return CLAIM_ALREADY_SETTLED, txn
	if txn.status != "Processing":
		return CLAIM_IN_PROGRESS, txn

	return CLAIM_CLAIMED, txn


def bind_invoice_to_txn(txn, invoice_name):
	"""Link SI as soon as it exists (before/during submit) so peers resume it."""
	if not txn or not invoice_name:
		return
	frappe.db.set_value(
		"Viva Payment Transaction",
		txn.name,
		{
			"sales_invoice": invoice_name,
			"status": "Processing",
		},
		update_modified=True,
	)
	frappe.db.commit()
	txn.reload()


def mark_txn_success(txn_name, invoice_name, selcom_ref=None, order_id=None, raw_payload=None):
	"""Mark Success via db_set only — avoids TimestampMismatchError after bind commits."""
	vals = {
		"status": "Success",
		"sales_invoice": invoice_name,
		"selcom_reference": str(selcom_ref or order_id or ""),
		"payment_entry": None,
		"error_message": None,
	}
	if raw_payload is not None:
		vals["ipn_payload"] = frappe.as_json(raw_payload)
	frappe.db.set_value("Viva Payment Transaction", txn_name, vals, update_modified=True)
	frappe.db.commit()


def find_invoice_for_order(order_id):
	"""Find an existing non-cancelled SI created for this Selcom order_id."""
	order_id = (order_id or "").strip()
	if not order_id:
		return None
	if frappe.get_meta("Sales Invoice").has_field("selcom_order_id"):
		by_field = frappe.db.get_value(
			"Sales Invoice",
			{"selcom_order_id": order_id, "docstatus": ("<", 2)},
			"name",
		)
		if by_field:
			return by_field
	# Prefer exact Order: tag (new settles); fall back to Paid via Selcom (order_id)
	like_order = "%Order: {0}%".format(order_id)
	like_selcom = "%Paid via Selcom ({0})%".format(order_id)
	row = frappe.db.sql(
		"""
		SELECT name FROM `tabSales Invoice`
		WHERE docstatus < 2
		  AND (remarks LIKE %s OR remarks LIKE %s)
		ORDER BY creation ASC
		LIMIT 1
		""",
		(like_order, like_selcom),
	)
	return row[0][0] if row else None


def _has_linked_invoice(txn):
	si = (txn.sales_invoice or "").strip()
	return bool(si and frappe.db.exists("Sales Invoice", si))


def _is_stale_processing(txn, stale_seconds):
	try:
		modified = get_datetime(txn.modified)
		age = time_diff_in_seconds(now_datetime(), modified)
		return age >= int(stale_seconds)
	except Exception:
		return False


def settlement_in_progress_response(txn, order_id, payment_workflow):
	return {
		"status": "success",
		"result": "PENDING",
		"message": "Settlement in progress",
		"order_id": order_id or (txn.order_id if txn else None),
		"invoice": txn.sales_invoice if txn else None,
		"payment_workflow": payment_workflow,
		"settlement_in_progress": True,
		"transaction_status": "Processing",
	}
