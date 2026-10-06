# -*- coding: utf-8 -*-
"""Settle Selcom success for generic POS Amount Service purchases (Cooking Gas, etc.).

Same Paid POS SI pattern as electricity / maintenance — no TrackSPM top-up.
Electricity continues to use electricity_pos (settlement + Afritrack).
"""

from __future__ import unicode_literals

import json

import frappe
from frappe.utils import flt, today, now
from propms.api.v1.payments.workflows import (
	WORKFLOW_AMOUNT_POS,
	get_mobile_cart_pos_profile_name,
)
from propms.api.v1.payments.handlers_sales_order_pos import _allocate_pos_payment_amount
from propms.api.v1.payments.settle_claim import (
	CLAIM_ALREADY_SETTLED,
	CLAIM_IN_PROGRESS,
	CLAIM_RESUME_INVOICE,
	SettlementLockBusy,
	bind_invoice_to_txn,
	claim_payment_settlement,
	find_invoice_for_order,
	mark_txn_success,
	settlement_in_progress_response,
	settlement_lock,
)


def _already_settled_response(txn, order_id):
	return {
		"status": "success",
		"result": "SUCCESS",
		"message": "Transaction already processed successfully",
		"order_id": order_id or (txn.order_id if txn else None),
		"invoice": txn.sales_invoice,
		"payment_workflow": WORKFLOW_AMOUNT_POS,
		"amount": flt(txn.amount) if txn else None,
		"currency": (txn.currency if txn else None) or "TZS",
		"payment_entry": None,
		"idempotent": True,
	}


def settle_amount_pos(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
	"""Create paid POS Sales Invoice from amount-service checkout intent."""
	if not txn:
		return {"status": "error", "message": "Payment transaction is required"}

	original_user = frappe.session.user
	frappe.set_user("Administrator")
	order_key = order_id or txn.order_id

	try:
		try:
			lock_cm = settlement_lock(order_key, txn.name, timeout=60)
			lock_cm.__enter__()
		except SettlementLockBusy:
			return settlement_in_progress_response(txn, order_key, WORKFLOW_AMOUNT_POS)

		try:
			return _settle_amount_pos_locked(
				txn, order_key, selcom_ref=selcom_ref, amount=amount, raw_payload=raw_payload
			)
		finally:
			lock_cm.__exit__(None, None, None)
	except Exception as e:
		frappe.db.rollback()
		try:
			if txn and txn.name:
				frappe.db.set_value("Selcom Payment Transaction Log", txn.name, "status", "Pending")
				frappe.db.commit()
		except Exception:
			pass
		frappe.log_error(frappe.get_traceback(), "Selcom Amount POS Error")
		return {"status": "error", "message": str(e)}
	finally:
		frappe.set_user(original_user)


def _settle_amount_pos_locked(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
	outcome, txn = claim_payment_settlement(txn)

	if outcome == CLAIM_ALREADY_SETTLED:
		return _already_settled_response(txn, order_id)

	if outcome == CLAIM_IN_PROGRESS:
		existing = find_invoice_for_order(order_id)
		if existing:
			bind_invoice_to_txn(txn, existing)
			invoice = frappe.get_doc("Sales Invoice", existing)
			mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
			_notify_amount_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
			return _already_settled_response(txn, order_id)
		return settlement_in_progress_response(txn, order_id, WORKFLOW_AMOUNT_POS)

	if outcome == CLAIM_RESUME_INVOICE:
		invoice = frappe.get_doc("Sales Invoice", txn.sales_invoice)
		if cint_docstatus(invoice) == 0:
			pos_profile_name = get_mobile_cart_pos_profile_name()
			pos_profile = frappe.get_doc("POS Profile", pos_profile_name)
			paid_amt = flt(amount) if amount and flt(amount) > 0 else flt(txn.amount)
			allocate_to = flt(invoice.grand_total) if flt(invoice.grand_total) > 0 else paid_amt
			_allocate_pos_payment_amount(invoice, allocate_to, pos_profile)
			invoice.flags.ignore_permissions = True
			invoice.save(ignore_permissions=True)
			invoice.submit()
		mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
		txn.reload()
		_notify_amount_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
		return _already_settled_response(txn, order_id)

	existing = find_invoice_for_order(order_id)
	if existing:
		bind_invoice_to_txn(txn, existing)
		invoice = frappe.get_doc("Sales Invoice", existing)
		mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
		_notify_amount_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
		return _already_settled_response(txn, order_id)

	intent = _load_intent(txn)
	if not intent:
		_release_claim_to_pending(txn, "Missing amount service purchase intent")
		return {
			"status": "error",
			"message": "Amount service purchase intent not found on transaction",
		}

	pos_profile_name = get_mobile_cart_pos_profile_name()
	pos_profile = frappe.get_doc("POS Profile", pos_profile_name)

	customer = intent.get("customer") or (txn.customer if txn else None)
	if not customer:
		_release_claim_to_pending(txn, "Customer missing")
		return {"status": "error", "message": "Customer missing on amount payment intent"}

	company = intent.get("company") or pos_profile.company
	lease = intent.get("lease")
	cost_center = intent.get("cost_center")
	lease_item = (intent.get("lease_item") or intent.get("amount_service") or "Amount")[:140]
	lines = intent.get("lines") or []
	if not lines:
		_release_claim_to_pending(txn, "Empty allocation lines")
		return {"status": "error", "message": "Amount service allocation lines are empty"}

	invoice = frappe.new_doc("Sales Invoice")
	invoice.flags.ignore_permissions = True
	invoice.flags.ignore_mandatory = True
	invoice.flags.ignore_push_and_realtime = True
	invoice.customer = customer
	invoice.company = company
	invoice.is_pos = 1
	invoice.pos_profile = pos_profile_name
	invoice.posting_date = today()
	invoice.due_date = today()
	invoice.update_stock = 0
	if cost_center:
		invoice.cost_center = cost_center
	if lease and invoice.meta.has_field("lease"):
		invoice.lease = lease
	if invoice.meta.has_field("lease_item"):
		invoice.lease_item = lease_item

	invoice.set_pos_fields()

	if lease and invoice.meta.has_field("lease"):
		invoice.lease = lease
	if invoice.meta.has_field("lease_item"):
		invoice.lease_item = lease_item
	if cost_center:
		invoice.cost_center = cost_center

	invoice.items = []
	for line in lines:
		invoice.append(
			"items",
			{
				"item_code": line["item_code"],
				"qty": flt(line.get("qty")),
				"rate": flt(line.get("rate")),
				"cost_center": cost_center,
			},
		)

	order_key = (order_id or txn.order_id or "").strip()
	svc_name = intent.get("amount_service") or lease_item
	invoice.remarks = (
		"Mobile {0} purchase. Paid via Selcom ({1}). Order: {2}.".format(
			svc_name, selcom_ref or order_key, order_key
		)
	)
	if order_key and invoice.meta.has_field("selcom_order_id"):
		invoice.selcom_order_id = order_key

	invoice.set_missing_values()
	invoice.calculate_taxes_and_totals()
	try:
		invoice.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		existing = find_invoice_for_order(order_key)
		if not existing:
			raise
		bind_invoice_to_txn(txn, existing)
		invoice = frappe.get_doc("Sales Invoice", existing)
		mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
		_notify_amount_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
		return _already_settled_response(txn, order_id)

	bind_invoice_to_txn(txn, invoice.name)

	paid_amt = flt(amount) if amount and flt(amount) > 0 else flt(txn.amount)
	allocate_to = flt(invoice.grand_total) if flt(invoice.grand_total) > 0 else paid_amt
	_allocate_pos_payment_amount(invoice, allocate_to, pos_profile)
	invoice.flags.ignore_permissions = True
	invoice.save(ignore_permissions=True)
	invoice.submit()

	mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
	txn.reload()
	_notify_amount_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
	return _already_settled_response(txn, order_id)


def cint_docstatus(doc):
	from frappe.utils import cint

	return cint(getattr(doc, "docstatus", 0))


def _load_intent(txn):
	raw = getattr(txn, "raw_request", None) or ""
	try:
		data = json.loads(raw) if isinstance(raw, str) else (raw or {})
	except Exception:
		data = {}
	if data.get("amount_service_purchase") or data.get("payment_workflow") == WORKFLOW_AMOUNT_POS:
		return data
	# Nested under extra
	extra = data.get("extra_raw_request") or data.get("extra") or {}
	if isinstance(extra, str):
		try:
			extra = json.loads(extra)
		except Exception:
			extra = {}
	if extra.get("amount_service_purchase") or extra.get("payment_workflow") == WORKFLOW_AMOUNT_POS:
		return extra
	return data if data.get("lines") and data.get("amount_service") else None


def _release_claim_to_pending(txn, reason):
	try:
		frappe.db.set_value(
			"Selcom Payment Transaction Log",
			txn.name,
			{"status": "Pending", "error_message": reason},
		)
		frappe.db.commit()
	except Exception:
		pass


def _notify_amount_pos_payment(invoice, order_id, selcom_ref, amount):
	try:
		frappe.publish_realtime(
			event="payment_success",
			message={
				"invoice": invoice.name,
				"order_id": order_id,
				"selcom_ref": selcom_ref,
				"amount": amount,
				"payment_workflow": WORKFLOW_AMOUNT_POS,
				"type": "amount_service_paid",
			},
			user=f"doc:Sales Invoice/{invoice.name}",
			after_commit=True,
		)
	except Exception:
		pass
