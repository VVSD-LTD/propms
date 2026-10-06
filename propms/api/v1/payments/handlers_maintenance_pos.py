# -*- coding: utf-8 -*-
"""Settle Selcom success for mobile Maintenance POS catalog purchase (no Sales Order).

Flow:
  1. Read purchase intent from Selcom Payment Transaction Log.raw_request
  2. Create Paid POS Sales Invoice (is_pos + mobile_cart_pos_profile)
  3. Allocate paid amount to profile default Mode of Payment and submit
  4. No Payment Entry
"""

from __future__ import unicode_literals

import json

import frappe
from frappe import _
from frappe.utils import flt, today, now, cint
from propms.api.v1.payments.workflows import (
	WORKFLOW_MAINTENANCE_POS,
	get_mobile_cart_pos_profile_name,
)
from propms.api.v1.payments.handlers_sales_order_pos import _allocate_pos_payment_amount

ITEM_GROUP_MAINTENANCE_POS = "Maintenance POS"  # legacy label only; eligibility is settings-driven

def settle_maintenance_pos(txn, order_id, selcom_ref=None, amount=None, raw_payload=None):
	"""Create paid POS Sales Invoice from Maintenance POS checkout intent."""
	if not txn:
		return {"status": "error", "message": "Payment transaction is required"}

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

	original_user = frappe.session.user
	frappe.set_user("Administrator")
	order_key = order_id or txn.order_id

	try:
		try:
			lock_cm = settlement_lock(order_key, txn.name, timeout=60)
			lock_cm.__enter__()
		except SettlementLockBusy:
			return settlement_in_progress_response(txn, order_key, WORKFLOW_MAINTENANCE_POS)

		try:
			outcome, txn = claim_payment_settlement(txn)

			if outcome == CLAIM_ALREADY_SETTLED:
				return {
					"status": "success",
					"result": "SUCCESS",
					"message": "Transaction already processed successfully",
					"order_id": order_id,
					"invoice": txn.sales_invoice,
					"payment_workflow": WORKFLOW_MAINTENANCE_POS,
					"idempotent": True,
				}

			if outcome == CLAIM_IN_PROGRESS:
				existing = find_invoice_for_order(order_key)
				if existing:
					bind_invoice_to_txn(txn, existing)
					invoice = frappe.get_doc("Sales Invoice", existing)
					mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
					_notify_maintenance_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
					return {
						"status": "success",
						"result": "SUCCESS",
						"message": "Transaction already processed successfully",
						"order_id": order_id,
						"invoice": invoice.name,
						"payment_workflow": WORKFLOW_MAINTENANCE_POS,
						"idempotent": True,
					}
				return settlement_in_progress_response(txn, order_id, WORKFLOW_MAINTENANCE_POS)

			if outcome == CLAIM_RESUME_INVOICE:
				invoice = frappe.get_doc("Sales Invoice", txn.sales_invoice)
				mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
				_notify_maintenance_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
				return {
					"status": "success",
					"result": "SUCCESS",
					"message": "Transaction already processed successfully",
					"order_id": order_id,
					"invoice": invoice.name,
					"payment_workflow": WORKFLOW_MAINTENANCE_POS,
					"idempotent": True,
				}

			existing = find_invoice_for_order(order_key)
			if existing:
				bind_invoice_to_txn(txn, existing)
				invoice = frappe.get_doc("Sales Invoice", existing)
				mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)
				_notify_maintenance_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))
				return {
					"status": "success",
					"result": "SUCCESS",
					"message": "Transaction already processed successfully",
					"order_id": order_id,
					"invoice": invoice.name,
					"payment_workflow": WORKFLOW_MAINTENANCE_POS,
					"idempotent": True,
				}

			intent = _load_intent(txn)
			if not intent:
				frappe.db.set_value("Selcom Payment Transaction Log", txn.name, "status", "Pending")
				frappe.db.commit()
				frappe.log_error(f"Missing maintenance POS intent for order {order_id}", "Selcom Maintenance POS Error")
				return {"status": "error", "message": "Maintenance POS purchase intent not found on transaction"}

			pos_profile_name = get_mobile_cart_pos_profile_name()
			pos_profile = frappe.get_doc("POS Profile", pos_profile_name)

			customer = intent.get("customer") or (txn.customer if txn else None)
			if not customer:
				frappe.db.set_value("Selcom Payment Transaction Log", txn.name, "status", "Pending")
				frappe.db.commit()
				return {"status": "error", "message": "Customer missing on Maintenance POS payment intent"}

			company = intent.get("company") or pos_profile.company
			lease = intent.get("lease")
			cost_center = intent.get("cost_center")
			lines = intent.get("lines") or []
			if not lines:
				frappe.db.set_value("Selcom Payment Transaction Log", txn.name, "status", "Pending")
				frappe.db.commit()
				return {"status": "error", "message": "Maintenance POS purchase lines are empty"}

			for line in lines:
				item_code = line.get("item_code")
				ok, msg = _validate_mobile_pos_item(item_code)
				if not ok:
					frappe.db.set_value("Selcom Payment Transaction Log", txn.name, "status", "Pending")
					frappe.db.commit()
					return {"status": "error", "message": msg}

			has_stock = any(
				cint(frappe.db.get_value("Item", ln.get("item_code"), "is_stock_item")) for ln in lines
			)

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
			invoice.update_stock = 1 if has_stock else 0
			if cost_center:
				invoice.cost_center = cost_center
			if lease and invoice.meta.has_field("lease"):
				invoice.lease = lease
			if invoice.meta.has_field("lease_item"):
				first_name = frappe.db.get_value("Item", lines[0].get("item_code"), "item_name") or "POS Store"
				invoice.lease_item = (first_name or "POS Store")[:140]

			invoice.set_pos_fields()

			if lease and invoice.meta.has_field("lease"):
				invoice.lease = lease
			if invoice.meta.has_field("lease_item") and not invoice.lease_item:
				invoice.lease_item = "POS Store"
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

			item_labels = ", ".join(ln.get("item_code") for ln in lines)
			invoice.remarks = (
				f"Mobile POS Store purchase ({item_labels}). "
				f"Paid via Selcom ({selcom_ref or order_id}). "
				f"Order: {order_id or txn.order_id}."
			)

			from propms.api.v1.water.water import _is_water_item

			d_start = intent.get("delivery_time_start")
			d_end = intent.get("delivery_time_end")
			d_date = intent.get("delivery_date") or today()
			d_notes = (intent.get("delivery_instructions") or "").strip()

			if d_start and d_end:
				if invoice.meta.has_field("delivery_date"):
					invoice.delivery_date = d_date
				if invoice.meta.has_field("delivery_time_start"):
					invoice.delivery_time_start = d_start
				if invoice.meta.has_field("delivery_time_end"):
					invoice.delivery_time_end = d_end
				if invoice.meta.has_field("delivery_instructions") and d_notes:
					invoice.delivery_instructions = d_notes

				start_disp = str(d_start)[:5]
				end_disp = str(d_end)[:5]
				invoice.remarks = (
					(invoice.remarks or "")
					+ f" Delivery window: {d_date} {start_disp}–{end_disp}."
				)
				if d_notes:
					invoice.remarks += f" Instructions: {d_notes}"
			else:
				waterish = any(_is_water_item(ln.get("item_code")) for ln in lines)
				if waterish:
					frappe.log_error(
						f"Water POS settle missing delivery window for order {order_id}",
						"Water Delivery Window",
					)
					invoice.remarks = (invoice.remarks or "") + " Delivery window missing."

			invoice.set_missing_values()
			invoice.calculate_taxes_and_totals()
			invoice.insert(ignore_permissions=True)
			bind_invoice_to_txn(txn, invoice.name)
			invoice.reload()

			paid_amt = flt(amount) if amount else (flt(txn.amount) if txn else 0)
			allocate_to = flt(invoice.grand_total) if flt(invoice.grand_total) > 0 else paid_amt
			if allocate_to <= 0:
				return {"status": "error", "message": "Invoice grand total is zero; cannot settle payment"}

			if invoice.docstatus == 0:
				_allocate_pos_payment_amount(invoice, allocate_to, pos_profile)
				invoice.flags.ignore_permissions = True
				invoice.flags.ignore_push_and_realtime = True
				invoice.save(ignore_permissions=True)
				invoice.submit()

			mark_txn_success(txn.name, invoice.name, selcom_ref, order_id, raw_payload)

			_notify_maintenance_pos_payment(invoice, order_id, selcom_ref, flt(invoice.grand_total))

			return {
				"status": "success",
				"result": "SUCCESS",
				"message": "POS Sales Invoice created as Paid for Maintenance POS purchase",
				"invoice": invoice.name,
				"amount": flt(invoice.grand_total),
				"currency": invoice.currency or "TZS",
				"payment_workflow": WORKFLOW_MAINTENANCE_POS,
				"pos_profile": pos_profile_name,
				"payment_entry": None,
			}
		finally:
			lock_cm.__exit__(None, None, None)
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Selcom Maintenance POS Settlement Error")
		return {"status": "error", "message": f"Failed to settle Maintenance POS payment: {str(e)}"}
	finally:
		if original_user:
			frappe.set_user(original_user)

def _validate_mobile_pos_item(item_code):
	if not item_code or not frappe.db.exists("Item", item_code):
		return False, _("Item {0} not found").format(item_code)
	row = frappe.db.get_value(
		"Item",
		item_code,
		["disabled", "is_sales_item"],
		as_dict=True,
	)
	if not row:
		return False, _("Item {0} not found").format(item_code)
	if cint(row.disabled):
		return False, _("Item {0} is disabled").format(item_code)
	if not cint(row.is_sales_item):
		return False, _("Item {0} is not a sales item").format(item_code)

	from propms.api.v1.pos_store.pos_store import _is_item_enabled_in_settings

	if not _is_item_enabled_in_settings(item_code):
		return False, _("Item {0} is not enabled in POS Services Settings → POS Store Services").format(
			item_code
		)
	return True, None

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
	if data.get("maintenance_pos_purchase") or data.get("payment_workflow") == WORKFLOW_MAINTENANCE_POS:
		return data
	if data.get("lines"):
		return data
	return None


def _mark_txn_success(txn, invoice_name, selcom_ref, order_id, raw_payload):
	txn.status = "Success"
	txn.sales_invoice = invoice_name
	txn.selcom_reference = str(selcom_ref or order_id)
	txn.payment_entry = None
	if raw_payload:
		txn.ipn_payload = frappe.as_json(raw_payload)
	txn.save(ignore_permissions=True)
	frappe.db.commit()


def _notify_maintenance_pos_payment(invoice, order_id, selcom_ref, amount):
	try:
		realtime_payload = {
			"order_id": order_id,
			"invoice_name": invoice.name,
			"amount": amount,
			"currency": invoice.currency or "TZS",
			"status": "PAID",
			"payment_entry": None,
			"payment_workflow": WORKFLOW_MAINTENANCE_POS,
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
		frappe.log_error(f"WebSocket publish error on maintenance POS: {str(e)}", "Selcom Webhook Realtime")

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
					title="Store Payment Received",
					body=(
						f"Your payment of {invoice.currency or 'TZS'} {amount:,.0f} "
						f"for POS Store was successful."
					),
					data={
						"type": "pos_store_paid",
						"invoice": invoice.name,
						"order_id": order_id,
					},
				)
	except Exception as e:
		frappe.log_error(title="Selcom FCM Error", message=f"FCM maintenance POS: {str(e)}")
