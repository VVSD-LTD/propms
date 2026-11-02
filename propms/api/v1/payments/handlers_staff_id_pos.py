# -*- coding: utf-8 -*-
"""Settle Selcom success for Staff ID Card fee (Paid POS SI + submit card).

Not POS Store — dedicated workflow. Creates Paid POS Sales Invoice then submits
the Staff ID Card Draft so management can approve.
"""

from __future__ import unicode_literals

import json

import frappe
from frappe.utils import cint, flt, now_datetime, today

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
from propms.api.v1.payments.workflows import (
	WORKFLOW_STAFF_ID_POS,
	get_mobile_cart_pos_profile_name,
)
from propms.api.v1.payments.handlers_sales_order_pos import _allocate_pos_payment_amount
from propms.api.v1.staff_id_card.fee import get_fee_item_code


DOCTYPE = "Staff ID Card"


def settle_staff_id_pos(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
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
			return settlement_in_progress_response(txn, order_key, WORKFLOW_STAFF_ID_POS)

		try:
			return _settle_locked(
				txn, order_key, selcom_ref=selcom_ref, amount=amount, raw_payload=raw_payload
			)
		finally:
			lock_cm.__exit__(None, None, None)
	except Exception as e:
		frappe.db.rollback()
		try:
			if txn and txn.name:
				frappe.db.set_value(
					"Selcom Payment Transaction Log", txn.name, "status", "Pending"
				)
				frappe.db.commit()
		except Exception:
			pass
		frappe.log_error(frappe.get_traceback(), "Selcom Staff ID POS Error")
		return {"status": "error", "message": str(e)}
	finally:
		frappe.set_user(original_user)


def _already_settled_response(txn, order_id):
	return {
		"status": "success",
		"result": "SUCCESS",
		"message": "Transaction already processed successfully",
		"order_id": order_id or (txn.order_id if txn else None),
		"invoice": txn.sales_invoice,
		"payment_workflow": WORKFLOW_STAFF_ID_POS,
		"amount": flt(txn.amount) if txn else None,
		"currency": (txn.currency if txn else None) or "TZS",
		"payment_entry": None,
		"idempotent": True,
		"staff_id_card": (txn.reference_name if txn else None),
	}


def _settle_locked(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
	outcome, txn = claim_payment_settlement(txn)

	if outcome == CLAIM_ALREADY_SETTLED:
		return _already_settled_response(txn, order_id)

	if outcome == CLAIM_IN_PROGRESS:
		existing = find_invoice_for_order(order_id)
		if existing:
			bind_invoice_to_txn(txn, existing)
			invoice = frappe.get_doc("Sales Invoice", existing)
			mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
			_finalize_staff_id_card(txn, invoice, order_id, selcom_ref)
			_notify(invoice, order_id, selcom_ref, flt(invoice.grand_total), txn.reference_name)
			return _already_settled_response(txn, order_id)
		return settlement_in_progress_response(txn, order_id, WORKFLOW_STAFF_ID_POS)

	if outcome == CLAIM_RESUME_INVOICE:
		invoice = frappe.get_doc("Sales Invoice", txn.sales_invoice)
		if cint(invoice.docstatus) == 0:
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
		_finalize_staff_id_card(txn, invoice, order_id, selcom_ref)
		_notify(invoice, order_id, selcom_ref, flt(invoice.grand_total), txn.reference_name)
		return _already_settled_response(txn, order_id)

	existing = find_invoice_for_order(order_id)
	if existing:
		bind_invoice_to_txn(txn, existing)
		invoice = frappe.get_doc("Sales Invoice", existing)
		mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
		_finalize_staff_id_card(txn, invoice, order_id, selcom_ref)
		_notify(invoice, order_id, selcom_ref, flt(invoice.grand_total), txn.reference_name)
		return _already_settled_response(txn, order_id)

	intent = _load_intent(txn)
	if not intent:
		_release_claim_to_pending(txn, "Missing staff ID purchase intent")
		return {
			"status": "error",
			"message": "Staff ID purchase intent not found on transaction",
		}

	card_id = (intent.get("staff_id_card") or txn.reference_name or "").strip()
	if not card_id or not frappe.db.exists(DOCTYPE, card_id):
		_release_claim_to_pending(txn, "Staff ID Card missing")
		return {"status": "error", "message": "Staff ID Card not found for payment"}

	pos_profile_name = get_mobile_cart_pos_profile_name()
	pos_profile = frappe.get_doc("POS Profile", pos_profile_name)

	customer = intent.get("customer") or (txn.customer if txn else None)
	if not customer:
		_release_claim_to_pending(txn, "Customer missing")
		return {"status": "error", "message": "Customer missing on Staff ID payment intent"}

	company = intent.get("company") or pos_profile.company
	lease = intent.get("lease")
	cost_center = intent.get("cost_center")
	lines = intent.get("lines") or []
	if not lines:
		item_code = intent.get("fee_item") or get_fee_item_code()
		rate = flt(intent.get("fee_amount") or txn.amount)
		lines = [{"item_code": item_code, "qty": 1, "rate": rate, "amount": rate}]

	fee_item_code = intent.get("fee_item") or get_fee_item_code()

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
		invoice.lease_item = fee_item_code

	invoice.set_pos_fields()

	if lease and invoice.meta.has_field("lease"):
		invoice.lease = lease
	if cost_center:
		invoice.cost_center = cost_center

	invoice.items = []
	for line in lines:
		invoice.append(
			"items",
			{
				"item_code": line["item_code"],
				"qty": flt(line.get("qty") or 1),
				"rate": flt(line.get("rate")),
				"cost_center": cost_center,
			},
		)

	order_key = (order_id or txn.order_id or "").strip()
	invoice.remarks = (
		"Mobile ID Card fee for {0}. Paid via Selcom ({1}). Order: {2}.".format(
			card_id, selcom_ref or order_key, order_key
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
		_finalize_staff_id_card(txn, invoice, order_id, selcom_ref)
		_notify(invoice, order_id, selcom_ref, flt(invoice.grand_total), card_id)
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
	_finalize_staff_id_card(txn, invoice, order_id, selcom_ref)
	_notify(invoice, order_id, selcom_ref, flt(invoice.grand_total), card_id)
	return _already_settled_response(txn, order_id)


def _finalize_staff_id_card(txn, invoice, order_id, selcom_ref):
	card_id = (txn.reference_name or "").strip()
	if not card_id or not frappe.db.exists(DOCTYPE, card_id):
		return

	doc = frappe.get_doc(DOCTYPE, card_id)
	paid_amt = flt(invoice.grand_total) if invoice else flt(txn.amount)

	doc.db_set(
		{
			"payment_status": "Paid",
			"sales_invoice": invoice.name if invoice else None,
			"paid_amount": paid_amt,
			"paid_on": now_datetime(),
			"selcom_order_id": order_id or txn.order_id,
			"is_paid_issuance": 1,
		},
		update_modified=True,
	)
	doc.reload()

	# Submit Draft → Submitted (notifies staff)
	if cint(doc.docstatus) == 0:
		doc.payment_status = "Paid"
		doc.is_paid_issuance = 1
		doc.status = "Submitted"
		doc.flags.will_submit = True
		doc.flags.skip_submit_notify = False
		doc.flags.ignore_permissions = True
		try:
			doc.submit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Staff ID submit after payment")
			# Still leave Paid even if submit fails — support can submit desk-side
	frappe.db.commit()


def _load_intent(txn):
	raw = getattr(txn, "raw_request", None) or ""
	try:
		data = json.loads(raw) if isinstance(raw, str) else (raw or {})
	except Exception:
		data = {}
	if data.get("staff_id_purchase") or data.get("payment_workflow") == WORKFLOW_STAFF_ID_POS:
		return data
	extra = data.get("extra_raw_request") or data.get("extra") or {}
	if isinstance(extra, str):
		try:
			extra = json.loads(extra)
		except Exception:
			extra = {}
	if extra.get("staff_id_purchase") or extra.get("payment_workflow") == WORKFLOW_STAFF_ID_POS:
		return extra
	if data.get("staff_id_card") and data.get("lines"):
		return data
	return None


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


def _notify(invoice, order_id, selcom_ref, amount, card_id):
	try:
		frappe.publish_realtime(
			event="payment_success",
			message={
				"invoice": invoice.name if invoice else None,
				"order_id": order_id,
				"selcom_ref": selcom_ref,
				"amount": amount,
				"payment_workflow": WORKFLOW_STAFF_ID_POS,
				"type": "staff_id_paid",
				"staff_id_card": card_id,
			},
			after_commit=True,
		)
	except Exception:
		pass
