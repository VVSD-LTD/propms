# -*- coding: utf-8 -*-
"""Desk / manual electricity POS Sales Invoice → Afritrack top-up.

Mobile Selcom settle already tops up (has selcom_order_id). This path covers
cashiers who create a Paid POS electricity SI in Desk with no Payment Entry.
"""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import cint, flt

from propms.api.v1.electricity.electricity import (
	_invoice_split_amounts,
	get_electricity_catalog,
	resolve_electricity_meter,
)


def doc_has_electricity_items(doc):
	"""True if SI items include configured TANESCO and/or Generator lines."""
	codes = get_electricity_catalog()["item_codes"]
	for it in getattr(doc, "items", None) or []:
		if (getattr(it, "item_code", None) or "") in codes:
			return True
	return False


def get_lease_from_invoice(doc):
	"""Prefer lease, fall back to lease_name (both exist on some forms)."""
	lease = (getattr(doc, "lease", None) or "").strip()
	if lease:
		return lease
	return (getattr(doc, "lease_name", None) or "").strip()


def resolve_autofill_from_lease(lease_name):
	"""Return {lease_item, meter_number, property} for a Lease, or raise."""
	if not lease_name or not frappe.db.exists("Lease", lease_name):
		frappe.throw(_("Lease {0} not found").format(lease_name))

	property_name = frappe.db.get_value("Lease", lease_name, "property")
	if not property_name:
		frappe.throw(_("Lease {0} has no Property").format(lease_name))

	meter_number = resolve_electricity_meter(property_name)
	# POS Customer on Lease (fieldname `customer`), not lease_customer
	pos_customer = None
	if frappe.get_meta("Lease").has_field("customer"):
		pos_customer = frappe.db.get_value("Lease", lease_name, "customer")
	return {
		"lease": lease_name,
		"property": property_name,
		"lease_item": get_electricity_catalog()["lease_item"],
		"meter_number": meter_number,
		"customer": pos_customer,
		"pos_customer": pos_customer,
	}


def _clear_electricity_autofill_fields(doc):
	"""Clear Desk-auto fields (never touch mobile invoices with selcom_order_id)."""
	if getattr(doc, "selcom_order_id", None):
		return
	if doc.meta.has_field("lease_item") and (getattr(doc, "lease_item", None) or "") == get_electricity_catalog()["lease_item"]:
		doc.lease_item = None
	if doc.meta.has_field("meter_number"):
		doc.meter_number = None


def autofill_electricity_from_lease(doc, throw_on_error=False):
	"""Set lease_item + meter_number from Lease when electricity items are present.

	Meter is never typed by cashiers — resolved from Property Meter Reading
	via the same helper as mobile purchase.

	If Lease is cleared or electricity items are removed, clears those fields.
	"""
	if not doc or cint(getattr(doc, "docstatus", 0)) != 0:
		# Only autofill on drafts (submitted docs are immutable for these fields)
		return None

	# Mobile already stamped meter + selcom_order_id
	if getattr(doc, "selcom_order_id", None):
		return None

	if not doc_has_electricity_items(doc):
		_clear_electricity_autofill_fields(doc)
		return None

	lease = get_lease_from_invoice(doc)
	if not lease:
		_clear_electricity_autofill_fields(doc)
		if throw_on_error and cint(getattr(doc, "is_pos", 0)):
			frappe.throw(
				_("Select a Lease so Lease Item and Meter Number can be set for electricity")
			)
		return None

	try:
		info = resolve_autofill_from_lease(lease)
	except Exception:
		_clear_electricity_autofill_fields(doc)
		if throw_on_error:
			raise
		frappe.log_error(frappe.get_traceback(), "Electricity Autofill From Lease")
		return None

	if doc.meta.has_field("lease_item"):
		doc.lease_item = info["lease_item"]
	if doc.meta.has_field("meter_number"):
		doc.meter_number = info["meter_number"]
	# Bill POS Customer (Lease.customer), not Lease Customer
	if info.get("customer"):
		doc.customer = info["customer"]
	elif throw_on_error and cint(getattr(doc, "is_pos", 0)):
		frappe.throw(
			_("Lease {0} has no POS Customer. Set POS Customer on the Lease for electricity.").format(
				lease
			)
		)
	# Keep lease / lease_name in sync when only one was set
	if doc.meta.has_field("lease") and not getattr(doc, "lease", None):
		doc.lease = lease
	if doc.meta.has_field("lease_name") and not getattr(doc, "lease_name", None):
		doc.lease_name = lease

	return info


def on_sales_invoice_validate(doc, method=None):
	"""Desk: auto-fill electricity lease_item + meter from Lease before save/submit."""
	throw = bool(cint(getattr(doc, "is_pos", 0)) and doc_has_electricity_items(doc))
	autofill_electricity_from_lease(doc, throw_on_error=throw)


@frappe.whitelist()
def get_electricity_autofill_for_lease(lease=None, lease_name=None):
	"""Form helper: Lease → lease_item + meter_number."""
	lease_ref = (lease or lease_name or "").strip()
	if not lease_ref:
		frappe.throw(_("Lease is required"))
	return resolve_autofill_from_lease(lease_ref)


def is_manual_electricity_pos_candidate(doc):
	"""True when Desk electricity POS SI should attempt Afritrack (not mobile)."""
	if not doc:
		return False
	if cint(getattr(doc, "docstatus", 0)) != 1:
		return False
	if not cint(getattr(doc, "is_pos", 0)):
		return False
	if (getattr(doc, "lease_item", None) or "").strip() != LEASE_ITEM_ELECTRICITY:
		return False
	meter = (getattr(doc, "meter_number", None) or "").strip()
	if not meter:
		return False
	# Mobile settle already called vendor in the same request
	if getattr(doc, "selcom_order_id", None):
		return False
	return True


def has_electricity_item_amounts(invoice_name):
	tanesco, generator = _invoice_split_amounts(invoice_name)
	return flt(tanesco) > 0 or flt(generator) > 0


def should_enqueue_manual_topup(doc):
	if not is_manual_electricity_pos_candidate(doc):
		return False
	return has_electricity_item_amounts(doc.name)


def enqueue_manual_electricity_pos_topup(doc):
	"""Schedule vendor load after SI commit. Never raises to caller."""
	try:
		if not should_enqueue_manual_topup(doc):
			return
		invoice_name = doc.name
		# Sync in tests (no redis worker); production enqueues after commit
		if getattr(frappe.flags, "in_test", False):
			run_manual_electricity_pos_topup(invoice_name)
			return
		frappe.enqueue(
			"propms.api.v1.electricity.manual_pos_topup.run_manual_electricity_pos_topup",
			invoice_name=invoice_name,
			queue="short",
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Manual Electricity POS Top-up Enqueue")
		try:
			run_manual_electricity_pos_topup(doc.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Manual Electricity POS Top-up Fallback")

def run_manual_electricity_pos_topup(invoice_name):
	"""Worker / sync entry: Policy A — log failures, never cancel SI."""
	from propms.api.v1.electricity.vendor import purchase_electricity_token

	if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
		return {"status": "skipped", "reason": "invoice_not_found"}

	doc = frappe.get_doc("Sales Invoice", invoice_name)
	if not should_enqueue_manual_topup(doc):
		return {"status": "skipped", "reason": "not_manual_electricity_pos"}

	try:
		return purchase_electricity_token(invoice_name, payment_transaction=None)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Manual Electricity POS Top-up")
		return {"status": "error", "invoice": invoice_name}


def _topup_log_for_invoice(sales_invoice):
	name = frappe.db.get_value("Afritrack Top-up Log", {"sales_invoice": sales_invoice}, "name")
	if not name:
		return None
	return frappe.get_doc("Afritrack Top-up Log", name)


@frappe.whitelist()
def get_electricity_topup_status(sales_invoice):
	"""Form helper: whether Retry should show and current log status."""
	frappe.has_permission("Sales Invoice", "read", doc=sales_invoice, throw=True)

	if not frappe.db.exists("Sales Invoice", sales_invoice):
		return {"show_retry": False, "reason": "missing"}

	doc = frappe.get_doc("Sales Invoice", sales_invoice)
	# Show retry UI for any submitted electricity POS with meter (incl. mobile
	# if load failed) — not only "manual" without selcom_order_id.
	base_ok = (
		cint(doc.docstatus) == 1
		and cint(doc.is_pos)
		and (doc.lease_item or "").strip() == LEASE_ITEM_ELECTRICITY
		and bool((doc.meter_number or "").strip())
		and has_electricity_item_amounts(sales_invoice)
	)
	if not base_ok:
		return {
			"show_retry": False,
			"status": None,
			"load_log": None,
			"reason": "not_electricity_pos",
		}

	log = _topup_log_for_invoice(sales_invoice)
	if not log:
		return {
			"show_retry": True,
			"status": None,
			"load_log": None,
			"reason": "no_log",
		}

	success = (log.status or "") == "Success" and bool(log.wt_id_t1 or log.wt_id_t2)
	return {
		"show_retry": not success,
		"status": log.status,
		"load_log": log.name,
		"wt_id_t1": log.wt_id_t1,
		"wt_id_t2": log.wt_id_t2,
		"error_message": log.error_message,
		"reason": "success" if success else "needs_retry",
	}


@frappe.whitelist()
def retry_manual_electricity_pos_topup(sales_invoice):
	"""Desk button: retry / first top-up for electricity POS SI."""
	frappe.has_permission("Sales Invoice", "write", doc=sales_invoice, throw=True)

	if not sales_invoice or not frappe.db.exists("Sales Invoice", sales_invoice):
		frappe.throw(_("Sales Invoice not found"))

	doc = frappe.get_doc("Sales Invoice", sales_invoice)
	if cint(doc.docstatus) != 1:
		frappe.throw(_("Submit the Sales Invoice before topping up the meter"))
	if (doc.lease_item or "").strip() != LEASE_ITEM_ELECTRICITY:
		frappe.throw(_("Only Electricity Sales Invoices can top up Afritrack"))
	if not cint(doc.is_pos):
		frappe.throw(_("Electricity top-up from Desk is only for POS invoices"))
	if not (doc.meter_number or "").strip():
		frappe.throw(_("Set Meter Number on the Sales Invoice before topping up"))
	if not has_electricity_item_amounts(sales_invoice):
		frappe.throw(
			_("Add {0} and/or {1} amounts before topping up").format(ITEM_TANESCO, ITEM_GENERATOR)
		)

	status = get_electricity_topup_status(sales_invoice)
	if not status.get("show_retry"):
		return {
			"status": "success",
			"idempotent": True,
			"message": _("Meter already topped up"),
			"load_log": status.get("load_log"),
		}

	from propms.api.v1.electricity.vendor import purchase_electricity_token

	result = purchase_electricity_token(sales_invoice, payment_transaction=None)
	return result
