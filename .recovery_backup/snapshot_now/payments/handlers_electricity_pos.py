# -*- coding: utf-8 -*-
"""Settle Selcom success for mobile electricity purchase (no Sales Order).

Flow:
  1. Claim Selcom Payment Transaction Log (Processing) — blocks concurrent IPN/status poll
  2. Create Paid POS Sales Invoice (is_pos + mobile_cart_pos_profile)
  3. Bind SI on TXN before submit finishes races
  4. Allocate paid amount to profile default Mode of Payment and submit
  5. Mark TXN Success, then TrackSPM load (idempotent per Afritrack Load Log)

Idempotency: claim-before-create. FOR UPDATE alone is not enough because SI
submit commits and releases the row lock.
"""

from __future__ import unicode_literals

import json

import frappe
from frappe.utils import flt, today, now
from propms.api.v1.payments.workflows import (
	WORKFLOW_ELECTRICITY_POS,
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
		"payment_workflow": WORKFLOW_ELECTRICITY_POS,
		"amount": flt(txn.amount) if txn else None,
		"currency": (txn.currency if txn else None) or "TZS",
		"payment_entry": None,
		"idempotent": True,
	}


def settle_electricity_pos(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
	"""Create paid POS Sales Invoice from electricity checkout intent."""
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
			return settlement_in_progress_response(txn, order_key, WORKFLOW_ELECTRICITY_POS)

		try:
			return _settle_electricity_pos_locked(
				txn, order_key, selcom_ref=selcom_ref, amount=amount, raw_payload=raw_payload
			)
		finally:
			lock_cm.__exit__(None, None, None)
	except Exception as e:
		frappe.db.rollback()
		try:
			if txn and txn.name:
				txn.reload()
				si = (txn.sales_invoice or "").strip()
				if si and frappe.db.exists("Sales Invoice", si):
					frappe.db.set_value(
						"Selcom Payment Transaction Log",
						txn.name,
						{
							"status": "Processing",
							"error_message": ("Settle interrupted: {0}".format(str(e)))[:140],
						},
						update_modified=True,
					)
				else:
					frappe.db.set_value(
						"Selcom Payment Transaction Log",
						txn.name,
						{
							"status": "Pending",
							"error_message": ("Settle failed: {0}".format(str(e)))[:140],
						},
						update_modified=True,
					)
				frappe.db.commit()
		except Exception:
			pass
		frappe.log_error(frappe.get_traceback(), "Selcom Electricity POS Settlement Error")
		return {"status": "error", "message": f"Failed to settle electricity payment: {str(e)}"}
	finally:
		if original_user:
			frappe.set_user(original_user)


def _settle_electricity_pos_locked(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
	outcome, txn = claim_payment_settlement(txn)

	if outcome == CLAIM_ALREADY_SETTLED:
		return _already_settled_response(txn, order_id)

	if outcome == CLAIM_IN_PROGRESS:
		# Under advisory lock this is rare; treat as resume if SI exists for order
		existing = find_invoice_for_order(order_id or txn.order_id)
		if existing:
			bind_invoice_to_txn(txn, existing)
			invoice = frappe.get_doc("Sales Invoice", existing)
			mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
			txn.reload()
			_notify_electricity_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
			_run_vendor_load(invoice, txn)
			return _already_settled_response(txn, order_id)
		return settlement_in_progress_response(txn, order_id, WORKFLOW_ELECTRICITY_POS)

	if outcome == CLAIM_RESUME_INVOICE:
		invoice = frappe.get_doc("Sales Invoice", txn.sales_invoice)
		mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
		txn.reload()
		_notify_electricity_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
		_run_vendor_load(invoice, txn)
		return _already_settled_response(txn, order_id)

	# CLAIM_CLAIMED — exclusive owner under GET_LOCK; create SI once
	# Final guard: SI already created for this order by a prior crash
	existing = find_invoice_for_order(order_id or txn.order_id)
	if existing:
		bind_invoice_to_txn(txn, existing)
		invoice = frappe.get_doc("Sales Invoice", existing)
		if invoice.docstatus == 0:
			pos_profile_name = get_mobile_cart_pos_profile_name()
			pos_profile = frappe.get_doc("POS Profile", pos_profile_name)
			paid_amt = flt(amount) if amount else flt(txn.amount)
			allocate_to = flt(invoice.grand_total) if flt(invoice.grand_total) > 0 else paid_amt
			_allocate_pos_payment_amount(invoice, allocate_to, pos_profile)
			invoice.flags.ignore_permissions = True
			invoice.save(ignore_permissions=True)
			invoice.submit()
		mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
		txn.reload()
		_notify_electricity_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
		_run_vendor_load(invoice, txn)
		return _already_settled_response(txn, order_id)

	intent = _load_intent(txn)
	if not intent:
		_release_claim_to_pending(txn, "Missing electricity purchase intent")
		frappe.log_error(
			f"Missing electricity intent for order {order_id}",
			"Selcom Electricity POS Error",
		)
		return {
			"status": "error",
			"message": "Electricity purchase intent not found on transaction",
		}

	from propms.api.v1.electricity.electricity import (
		get_electricity_catalog,
		units_from_amount,
		get_item_selling_rate,
	)

	catalog = get_electricity_catalog()
	pos_profile_name = get_mobile_cart_pos_profile_name()
	pos_profile = frappe.get_doc("POS Profile", pos_profile_name)

	customer = intent.get("customer") or (txn.customer if txn else None)
	if not customer:
		_release_claim_to_pending(txn, "Customer missing")
		return {"status": "error", "message": "Customer missing on electricity payment intent"}

	company = intent.get("company") or pos_profile.company
	meter_number = intent.get("meter_number")
	lease = intent.get("lease")
	cost_center = intent.get("cost_center")

	lines = intent.get("lines") or []
	if not lines:
		tanesco = flt(intent.get("tanesco_amount"))
		generator = flt(intent.get("generator_amount"))
		lines = []
		for item_code, amt in (
			(catalog["item_tanesco"], tanesco),
			(catalog["item_generator"], generator),
		):
			if amt <= 0:
				continue
			rate, _ = get_item_selling_rate(item_code)
			lines.append(
				{
					"item_code": item_code,
					"qty": units_from_amount(amt, rate),
					"rate": rate,
					"amount_inclusive": amt,
				}
			)
	if not lines:
		_release_claim_to_pending(txn, "Empty allocation lines")
		return {"status": "error", "message": "Electricity allocation lines are empty"}

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
		invoice.lease_item = catalog["lease_item"]
	if meter_number and invoice.meta.has_field("meter_number"):
		invoice.meter_number = meter_number

	invoice.set_pos_fields()

	if lease and invoice.meta.has_field("lease"):
		invoice.lease = lease
	if invoice.meta.has_field("lease_item"):
		invoice.lease_item = catalog["lease_item"]
	if meter_number and invoice.meta.has_field("meter_number"):
		invoice.meter_number = meter_number
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

	invoice.remarks = (
		f"Mobile electricity purchase. Meter: {meter_number or '-'}. "
		f"Paid via Selcom ({selcom_ref or order_id}). "
		f"Order: {order_id or txn.order_id}."
	)

	order_key = (order_id or txn.order_id or "").strip()
	if order_key and invoice.meta.has_field("selcom_order_id"):
		invoice.selcom_order_id = order_key

	invoice.set_missing_values()
	invoice.calculate_taxes_and_totals()
	try:
		invoice.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Another worker already inserted SI for this Selcom order — resume it
		existing = find_invoice_for_order(order_key)
		if not existing:
			raise
		bind_invoice_to_txn(txn, existing)
		invoice = frappe.get_doc("Sales Invoice", existing)
		if invoice.docstatus == 0:
			paid_amt = flt(amount) if amount else (flt(txn.amount) if txn else 0)
			allocate_to = flt(invoice.grand_total) if flt(invoice.grand_total) > 0 else paid_amt
			_allocate_pos_payment_amount(invoice, allocate_to, pos_profile)
			invoice.flags.ignore_permissions = True
			invoice.flags.ignore_push_and_realtime = True
			invoice.save(ignore_permissions=True)
			invoice.submit()
		mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
		txn.reload()
		_notify_electricity_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
		_run_vendor_load(invoice, txn)
		return _already_settled_response(txn, order_id)
	except Exception as insert_err:
		# MariaDB duplicate key sometimes surfaces as other IntegrityError wrappers
		msg = str(insert_err).lower()
		if "duplicate" in msg or "unique" in msg:
			existing = find_invoice_for_order(order_key)
			if existing:
				bind_invoice_to_txn(txn, existing)
				invoice = frappe.get_doc("Sales Invoice", existing)
				mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
				txn.reload()
				_notify_electricity_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
				_run_vendor_load(invoice, txn)
				return _already_settled_response(txn, order_id)
		raise

	# Bind SI before submit so a concurrent settler resumes this invoice
	bind_invoice_to_txn(txn, invoice.name)
	invoice.reload()

	paid_amt = flt(amount) if amount else (flt(txn.amount) if txn else 0)
	allocate_to = flt(invoice.grand_total) if flt(invoice.grand_total) > 0 else paid_amt
	if allocate_to <= 0:
		_abandon_draft_invoice(txn, invoice, "Invoice grand total is zero")
		return {"status": "error", "message": "Invoice grand total is zero; cannot settle payment"}

	if invoice.docstatus == 0:
		_allocate_pos_payment_amount(invoice, allocate_to, pos_profile)
		invoice.flags.ignore_permissions = True
		invoice.flags.ignore_push_and_realtime = True
		invoice.save(ignore_permissions=True)
		invoice.submit()

	mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
	txn.reload()

	_notify_electricity_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
	_run_vendor_load(invoice, txn)

	return {
		"status": "success",
		"result": "SUCCESS",
		"message": "POS Sales Invoice created as Paid for electricity purchase",
		"invoice": invoice.name,
		"amount": flt(invoice.grand_total),
		"currency": invoice.currency or "TZS",
		"payment_workflow": WORKFLOW_ELECTRICITY_POS,
		"pos_profile": pos_profile_name,
		"payment_entry": None,
		"meter_number": meter_number,
	}


def _run_vendor_load(invoice, txn):
	try:
		from propms.api.v1.electricity.vendor import purchase_electricity_token

		if getattr(invoice, "meter_number", None):
			purchase_electricity_token(
				invoice.name,
				payment_transaction=txn.name if txn else None,
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Electricity Vendor After POS Settle")


def _release_claim_to_pending(txn, error_message=None):
	"""Allow a later retry if we claimed but cannot create an SI."""
	if not txn:
		return
	vals = {"status": "Pending"}
	if error_message:
		vals["error_message"] = str(error_message)[:140]
	frappe.db.set_value("Selcom Payment Transaction Log", txn.name, vals, update_modified=True)
	frappe.db.commit()
	txn.reload()


def _abandon_draft_invoice(txn, invoice, error_message=None):
	"""Delete unbound draft SI and reopen TXN for a safe retry."""
	try:
		if invoice and invoice.docstatus == 0 and frappe.db.exists("Sales Invoice", invoice.name):
			frappe.delete_doc("Sales Invoice", invoice.name, force=1, ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Electricity POS abandon draft SI")
	if txn:
		vals = {"status": "Pending", "sales_invoice": None}
		if error_message:
			vals["error_message"] = str(error_message)[:140]
		frappe.db.set_value("Selcom Payment Transaction Log", txn.name, vals, update_modified=True)
		frappe.db.commit()
		txn.reload()


def _load_intent(txn):
	if not txn:
		return None
	raw = txn.raw_request
	if not raw:
		return None
	try:
		data = json.loads(raw) if isinstance(raw, str) else raw
	except Exception:
		return None
	if not isinstance(data, dict):
		return None
	if data.get("electricity_purchase") or data.get("payment_workflow") == WORKFLOW_ELECTRICITY_POS:
		return data
	if data.get("tanesco_amount") is not None or data.get("generator_amount") is not None:
		return data
	return None


def _notify_electricity_payment(invoice, order_id, selcom_ref, amount):
	try:
		realtime_payload = {
			"order_id": order_id,
			"invoice_name": invoice.name,
			"amount": amount,
			"currency": invoice.currency or "TZS",
			"status": "PAID",
			"payment_entry": None,
			"payment_workflow": WORKFLOW_ELECTRICITY_POS,
			"reference_no": str(selcom_ref or order_id),
			"timestamp": now(),
		}
		frappe.publish_realtime(
			event="payment_completed",
			message=realtime_payload,
			room=f"doc:Sales Invoice/{invoice.name}",
		)
		if invoice.contact_email:
			frappe.publish_realtime(
				event="payment_completed",
				message=realtime_payload,
				room=f"user:{invoice.contact_email}",
			)
	except Exception as e:
		frappe.log_error(f"WebSocket publish error on electricity POS: {str(e)}", "Selcom Webhook Realtime")

	try:
		target_user = None
		if invoice.contact_email and frappe.db.exists("User", invoice.contact_email):
			target_user = invoice.contact_email
		else:
			portal_user = frappe.db.get_value("Portal User", {"parent": invoice.customer}, "user")
			if portal_user:
				target_user = portal_user

		if target_user:
			tokens = frappe.db.get_all("Mobile Device Token", {"user": target_user}, pluck="token")
			if tokens:
				from propms.utils.fcm import send_to_tokens

				send_to_tokens(
					tokens=tokens,
					title="Electricity Payment Received",
					body=(
						f"Your payment of {invoice.currency or 'TZS'} {amount:,.0f} "
						f"for electricity was successful."
					),
					data={
						"type": "electricity_paid",
						"invoice": invoice.name,
						"order_id": order_id,
					},
				)
	except Exception as e:
		frappe.log_error(title="Selcom FCM Error", message=f"FCM electricity POS: {str(e)}")
