# -*- coding: utf-8 -*-
"""Settle Selcom success by creating Payment Entry(ies) against Sales Invoice(s).

Principal invoice first; any remainder (and full-pay intent) settles accrued
late-payment penalty via the existing ``create_penalty_invoice`` feature.

IPN + get_payment_status can arrive together — settlement runs under an advisory
lock so we never double-create principal PEs or skip the penalty PE.
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import flt, now
from propms.api.v1.payments.selcom_client import get_selcom_settings
from propms.api.v1.payments.settle_claim import (
	SettlementLockBusy,
	settlement_in_progress_response,
	settlement_lock,
)
from propms.api.v1.payments.workflows import WORKFLOW_SALES_INVOICE_PAYMENT


def settle_sales_invoice_payment(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
	"""Create/submit Payment Entry for txn.sales_invoice (+ penalty SI if needed)."""
	order_key = (order_id or (txn.order_id if txn else "") or "").strip()
	try:
		with settlement_lock(order_key, txn.name if txn else None, timeout=60):
			if txn:
				txn.reload()
			return _settle_sales_invoice_payment_locked(
				txn, order_key, selcom_ref=selcom_ref, amount=amount, raw_payload=raw_payload
			)
	except SettlementLockBusy:
		return settlement_in_progress_response(
			txn, order_key, WORKFLOW_SALES_INVOICE_PAYMENT
		)


def _find_existing_pe(ref_candidates, invoice_name=None):
	"""Submitted PE for this Selcom transid/order, optionally on a given SI."""
	for ref_query in ref_candidates:
		if not ref_query:
			continue
		if invoice_name:
			row = frappe.db.sql(
				"""
				SELECT pe.name
				FROM `tabPayment Entry` pe
				INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
				WHERE pe.docstatus = 1
				  AND pe.reference_no = %s
				  AND per.reference_doctype = 'Sales Invoice'
				  AND per.reference_name = %s
				ORDER BY pe.creation ASC
				LIMIT 1
				""",
				(ref_query, invoice_name),
			)
			if row:
				return row[0][0]
		else:
			name = frappe.db.get_value(
				"Payment Entry", {"reference_no": ref_query, "docstatus": 1}, "name"
			)
			if name:
				return name
	return None


def _ref_candidates(selcom_ref, order_id, txn):
	out = []
	for cand in (selcom_ref, order_id, getattr(txn, "selcom_reference", None) if txn else None):
		c = str(cand or "").strip()
		if c and c not in out:
			out.append(c)
	return out


def _settle_sales_invoice_payment_locked(
	txn, order_id, selcom_ref=None, amount=None, raw_payload=None
):
	invoice_name = txn.sales_invoice if txn else None
	if not invoice_name and order_id and "SINV" in order_id:
		parts = order_id.split("-")
		for p in parts:
			if p.startswith("SINV") or frappe.db.exists("Sales Invoice", p):
				invoice_name = p
				break

	if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
		frappe.log_error(
			f"Cannot resolve Sales Invoice for order: {order_id}", "Selcom Webhook Error"
		)
		return {"status": "error", "message": "Sales Invoice not found for this order"}

	from propms.api.v1.payments.invoice_amount import (
		ensure_submitted_penalty_invoice,
		invoice_due_breakdown,
		money,
		settle_penalty_portion,
		settle_principal_and_penalty_same_pe,
		split_payment_toward_principal_and_penalty,
	)

	# Already fully done (principal PE linked + no penalty left on this payment)
	due_now = invoice_due_breakdown(invoice_name)
	if (
		txn
		and txn.status == "Success"
		and txn.payment_entry
		and frappe.db.exists("Payment Entry", txn.payment_entry)
		and money(due_now.get("total_amount_due")) <= 0
	):
		return {
			"status": "success",
			"message": "Transaction already processed successfully",
			"order_id": order_id,
			"payment_entry": txn.payment_entry,
			"invoice": txn.sales_invoice,
			"payment_workflow": WORKFLOW_SALES_INVOICE_PAYMENT,
		}

	inv = frappe.get_doc("Sales Invoice", invoice_name)
	settings = get_selcom_settings()

	paid_amt = money(amount) if amount else (money(txn.amount) if txn else 0.0)
	if paid_amt <= 0 and txn:
		paid_amt = money(txn.amount)
	if paid_amt <= 0:
		paid_amt = money(invoice_due_breakdown(inv.name).get("total_amount_due"))

	ref_candidates = _ref_candidates(selcom_ref, order_id, txn)
	reference_no = str(selcom_ref or order_id)
	mode_of_payment = "Selcom" if frappe.db.exists("Mode of Payment", "Selcom") else None

	paid_to_account = settings.get("default_bank_account")
	if not paid_to_account or not frappe.db.exists("Account", paid_to_account):
		frappe.throw(
			_(
				"Please set <b>Default Bank Account</b> in "
				"<a href='/app/selcom-settings'>Selcom Settings</a>."
			),
			title=_("Bank Account Not Configured"),
		)

	inv.reload()
	split = split_payment_toward_principal_and_penalty(paid_amt, inv.name)
	to_principal = split["to_principal"]
	to_penalty = split["to_penalty"]
	outstanding_before = split["breakdown"]["principal_outstanding"]
	penalty_before = split["breakdown"]["penalty_amount_due"]

	# Reuse principal PE for this Selcom ref (idempotent) — do NOT return early;
	# full-pay must still settle the penalty slice.
	pe_name = _find_existing_pe(ref_candidates, invoice_name=inv.name)
	# Also match PE that used order_id / transid without invoice filter
	if not pe_name:
		pe_name = _find_existing_pe(ref_candidates, invoice_name=None)
		# Only accept if it actually points at this invoice
		if pe_name:
			linked = frappe.db.exists(
				"Payment Entry Reference",
				{
					"parent": pe_name,
					"reference_doctype": "Sales Invoice",
					"reference_name": inv.name,
				},
			)
			if not linked:
				pe_name = None

	if to_principal <= 0 and to_penalty <= 0 and pe_name:
		if txn:
			_mark_txn_success(txn, inv, reference_no, pe_name, raw_payload)
		return {
			"status": "success",
			"message": _("Invoice already settled; Payment Entry linked"),
			"payment_entry": pe_name,
			"invoice": inv.name,
			"amount": paid_amt,
			"payment_workflow": WORKFLOW_SALES_INVOICE_PAYMENT,
		}

	if to_principal <= 0 and to_penalty <= 0:
		if txn and txn.status != "Success":
			_mark_txn_success(txn, inv, reference_no, pe_name, raw_payload)
			txn.error_message = _("No outstanding left to allocate")
			txn.save(ignore_permissions=True)
			frappe.db.commit()
		return {
			"status": "success",
			"message": _("Invoice already settled; no Payment Entry created"),
			"invoice": inv.name,
			"amount": 0,
			"outstanding_before": outstanding_before,
			"payment_workflow": WORKFLOW_SALES_INVOICE_PAYMENT,
			"allocated": 0,
		}

	original_user = frappe.session.user
	frappe.set_user("Administrator")
	penalty_settle = None

	try:
		inv.reload()
		pay_pen = money(to_penalty)

		# Idempotency: existing PE already clears penalty SI
		pen_si = (frappe.db.get_value("Sales Invoice", inv.name, "penalty_invoice") or "").strip()
		if pe_name and pay_pen > 0 and pen_si:
			already_on_pen = frappe.db.exists(
				"Payment Entry Reference",
				{
					"parent": pe_name,
					"reference_doctype": "Sales Invoice",
					"reference_name": pen_si,
				},
			)
			if already_on_pen:
				penalty_settle = {
					"penalty_invoice": pen_si,
					"payment_entry": pe_name,
					"amount": pay_pen,
				}
				pay_pen = 0.0
				to_principal = 0.0

		if pe_name and (to_principal > 0 or pay_pen > 0):
			# Raced poll already created a PE — finish leftover penalty only
			if pay_pen > 0:
				penalty_settle = settle_penalty_portion(
					inv.name,
					pay_pen,
					paid_to_account,
					reference_no=reference_no,
					mode_of_payment=mode_of_payment,
				)
			elif penalty_before > 0 and money(inv.outstanding_amount) <= 0:
				pen_name = ensure_submitted_penalty_invoice(inv.name)
				if pen_name:
					penalty_settle = {
						"penalty_invoice": pen_name,
						"payment_entry": None,
						"amount": 0,
					}
		elif not pe_name:
			# Desk order: main SI → PE (principal) → penalty SI → same PE clears penalty
			combined = settle_principal_and_penalty_same_pe(
				inv.name,
				to_principal=to_principal,
				to_penalty=pay_pen,
				paid_to_account=paid_to_account,
				reference_no=reference_no,
				mode_of_payment=mode_of_payment,
			)
			if combined:
				pe_name = combined.get("payment_entry") or pe_name
				if combined.get("penalty_invoice") or money(combined.get("amount") or 0) > 0:
					penalty_settle = combined
				elif penalty_before > 0 and pay_pen <= 0 and to_principal > 0:
					# Principal-only: still bill penalty SI for later (no pay nudge)
					pen_name = ensure_submitted_penalty_invoice(inv.name)
					if pen_name:
						penalty_settle = {
							"penalty_invoice": pen_name,
							"payment_entry": None,
							"amount": 0,
						}
	finally:
		if original_user:
			frappe.set_user(original_user)

	penalty_amt = money((penalty_settle or {}).get("amount") or 0)
	# Actual principal booked this run (0 if reused existing PE and nothing left)
	principal_booked = money(to_principal) if to_principal > 0 else 0.0
	if pe_name and to_principal <= 0:
		principal_booked = 0.0
	total_allocated = money(principal_booked + penalty_amt)
	if pe_name and money(paid_amt) > 0 and total_allocated <= 0:
		# Recovery path: PE existed; report paid_amt for API consumers
		total_allocated = money(paid_amt)

	if txn:
		_mark_txn_success(
			txn,
			inv,
			reference_no,
			pe_name or (penalty_settle or {}).get("payment_entry"),
			raw_payload,
		)
	else:
		txn = frappe.get_doc(
			{
				"doctype": "Selcom Payment Transaction Log",
				"order_id": order_id,
				"payment_workflow": WORKFLOW_SALES_INVOICE_PAYMENT,
				"reference_doctype": "Sales Invoice",
				"reference_name": inv.name,
				"sales_invoice": inv.name,
				"customer": inv.customer,
				"amount": total_allocated or paid_amt,
				"currency": inv.currency or "TZS",
				"payment_channel": "HOSTED",
				"status": "Success",
				"selcom_reference": reference_no,
				"payment_entry": pe_name or (penalty_settle or {}).get("payment_entry"),
				"ipn_payload": frappe.as_json(raw_payload) if raw_payload else None,
			}
		).insert(ignore_permissions=True)

	frappe.db.commit()

	inv.reload()
	remaining_principal = money(inv.outstanding_amount)
	due_after = invoice_due_breakdown(inv.name)
	remaining_total = due_after["total_amount_due"]
	is_partial = 1 if remaining_total > 0 else 0

	try:
		realtime_payload = {
			"order_id": order_id,
			"invoice_name": inv.name,
			"amount": total_allocated or paid_amt,
			"principal_amount": to_principal,
			"penalty_amount": money((penalty_settle or {}).get("amount") or 0),
			"penalty_invoice": (penalty_settle or {}).get("penalty_invoice"),
			"currency": inv.currency or "TZS",
			"status": "PAID" if remaining_total <= 0 else "PARTIAL",
			"is_partial": is_partial,
			"outstanding_remaining": remaining_principal,
			"total_remaining": remaining_total,
			"payment_entry": pe_name,
			"payment_workflow": WORKFLOW_SALES_INVOICE_PAYMENT,
			"reference_no": reference_no,
			"timestamp": now(),
		}
		frappe.publish_realtime(
			event="payment_completed",
			message=realtime_payload,
			room=f"doc:Sales Invoice/{inv.name}",
		)
		if inv.contact_email:
			frappe.publish_realtime(
				event="payment_completed",
				message=realtime_payload,
				room=f"user:{inv.contact_email}",
			)
	except Exception as e:
		frappe.log_error(
			f"WebSocket publish error on payment: {str(e)}", "Selcom Webhook Realtime"
		)

	try:
		target_user = None
		if inv.contact_email and frappe.db.exists("User", inv.contact_email):
			target_user = inv.contact_email
		else:
			portal_user = frappe.db.get_value("Portal User", {"parent": inv.customer}, "user")
			if portal_user:
				target_user = portal_user

		if target_user:
			tokens = frappe.db.get_all(
				"Mobile Device Token", {"user": target_user}, pluck="token"
			)
			if tokens:
				from propms.utils.fcm import send_to_tokens

				send_to_tokens(
					tokens=tokens,
					title="Payment Received",
					body=(
						f"Your payment of {inv.currency or 'TZS'} {money(total_allocated or paid_amt):,.0f} "
						f"for invoice {inv.name} was successfully received"
						+ (
							f". Remaining due: {remaining_total:,.0f}."
							if remaining_total > 0
							else ". Invoice and penalty are fully paid. Thank you!"
						)
					),
					data={
						"type": "invoice_partial_paid" if remaining_total > 0 else "invoice_paid",
						"invoice": inv.name,
						"order_id": order_id,
						"is_partial": str(is_partial),
						"outstanding_remaining": str(remaining_principal),
						"penalty_invoice": str(
							(penalty_settle or {}).get("penalty_invoice") or ""
						),
					},
				)
	except Exception as e:
		frappe.log_error(
			title="Selcom FCM Error",
			message=f"FCM notification error on payment: {str(e)}",
		)

	return {
		"status": "success",
		"result": "SUCCESS",
		"message": (
			"Partial payment applied; amount still due"
			if remaining_total > 0
			else "Payment applied; invoice and penalty settled"
		),
		"payment_entry": pe_name,
		"invoice": inv.name,
		"amount": money(total_allocated or paid_amt),
		"principal_amount": to_principal,
		"penalty_amount": money((penalty_settle or {}).get("amount") or 0),
		"outstanding_before": outstanding_before,
		"penalty_before": penalty_before,
		"outstanding_remaining": remaining_principal,
		"total_remaining": remaining_total,
		"is_partial": is_partial,
		"penalty_invoice": (penalty_settle or {}).get("penalty_invoice"),
		"penalty_payment_entry": (penalty_settle or {}).get("payment_entry"),
		"payment_workflow": WORKFLOW_SALES_INVOICE_PAYMENT,
	}


def _mark_txn_success(txn, inv, reference_no, pe_name, raw_payload):
	txn.status = "Success"
	txn.selcom_reference = reference_no
	if pe_name:
		txn.payment_entry = pe_name
	txn.sales_invoice = inv.name
	if not txn.reference_doctype:
		txn.reference_doctype = "Sales Invoice"
	if not txn.reference_name:
		txn.reference_name = inv.name
	if raw_payload:
		txn.ipn_payload = frappe.as_json(raw_payload)
	txn.error_message = None
	txn.save(ignore_permissions=True)
	frappe.db.commit()
