# -*- coding: utf-8 -*-
"""Amenity Flutter payment: rates, quote, Selcom checkout."""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, getdate, nowdate

from propms.api.v1.amenities.doctypes import AMENITY_BOOKING_REQUEST
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload
from propms.api.v1.amenities.slots import _parse_time_str

DEFAULT_AMENITY_ITEM = "Conference Hall Hire charges"


def get_amenity_payment_item_code():
	"""Item for paid amenity invoices — Mobile App Settings → Amenities."""
	item = None
	try:
		if frappe.db.exists("DocType", "Mobile App Settings"):
			meta = frappe.get_meta("Mobile App Settings")
			if meta.has_field("amenity_payment_item"):
				item = frappe.db.get_single_value("Mobile App Settings", "amenity_payment_item")
	except Exception:
		item = None
	item = (item or "").strip()
	return item or DEFAULT_AMENITY_ITEM


def serialize_payment_rates(amenity_doc):
	"""Rates for mobile catalog (Flutter ``rates`` array)."""
	rows = []
	for row in getattr(amenity_doc, "payment_rates", None) or []:
		hours = flt(row.duration_hours)
		seater = (row.seater_label or "").strip() or None
		mins = int(round(hours * 60)) if hours else 0
		label_parts = []
		if seater:
			label_parts.append(seater)
		if hours:
			# Prefer whole hours in label (1h / 4h / 8h)
			if abs(hours - int(hours)) < 0.01:
				label_parts.append(f"{int(hours)}h")
			else:
				label_parts.append(f"{mins}m")
		rows.append(
			{
				"name": row.name,
				"seater": seater,
				"seater_label": seater,  # alias
				"seats": cint(row.seats) if row.seats else None,
				"duration_hours": hours,
				"duration_mins": mins,
				"amount": flt(row.amount),
				"currency": "TZS",
				"label": " · ".join(label_parts) if label_parts else None,
			}
		)
	return rows


def get_rate_row_by_name(amenity_doc, rate_name):
	"""Resolve Amenity Rate child by docname (preferred Flutter path)."""
	rate_name = (rate_name or "").strip()
	if not rate_name:
		return None
	for row in getattr(amenity_doc, "payment_rates", None) or []:
		if (row.name or "").strip() == rate_name:
			return row
	# DB fallback (parent must match this amenity)
	parent = frappe.db.get_value("Amenity Rate", rate_name, "parent")
	if parent and parent == amenity_doc.name:
		# Return matching child from parent so callers see seater_label etc.
		amenity_doc.reload()
		for row in getattr(amenity_doc, "payment_rates", None) or []:
			if (row.name or "").strip() == rate_name:
				return row
	return None


def find_amenity_rate(amenity_doc, seater_label=None, duration_hours=None, amenity_rate=None):
	"""Match a payment rate row.

	Priority:
	1. ``amenity_rate`` child name (rates[].name) — alone is enough
	2. seater + duration_hours
	"""
	by_name = get_rate_row_by_name(amenity_doc, amenity_rate)
	if by_name:
		return by_name

	want_hours = flt(duration_hours)
	want_seater = (seater_label or "").strip().lower()
	rates = list(getattr(amenity_doc, "payment_rates", None) or [])
	if not rates or want_hours <= 0:
		return None

	matches = []
	for row in rates:
		if abs(flt(row.duration_hours) - want_hours) > 0.01:
			continue
		label = (row.seater_label or "").strip().lower()
		if want_seater:
			if label == want_seater:
				matches.append(row)
		else:
			# No seater: only blank-seater rows, or unique duration match below
			if not label:
				matches.append(row)

	if matches:
		return matches[0]

	if want_seater:
		return None

	# Duration-only when exactly one package for that duration
	by_hours = [r for r in rates if abs(flt(r.duration_hours) - want_hours) <= 0.01]
	if len(by_hours) == 1:
		return by_hours[0]
	return None


def end_time_from_start_and_hours(booking_date, start_time, duration_hours):
	"""Compute end_time (H:M:S) from start + duration hours."""
	s = _parse_time_str(start_time).strftime("%H:%M:%S")
	start_dt = get_datetime(f"{getdate(booking_date)} {s}")
	end_dt = add_to_date(start_dt, hours=flt(duration_hours))
	return end_dt.strftime("%H:%M:%S")


def duration_hours_between(start_time, end_time):
	s = _parse_time_str(start_time)
	e = _parse_time_str(end_time)
	secs = (e.hour * 3600 + e.minute * 60 + e.second) - (
		s.hour * 3600 + s.minute * 60 + s.second
	)
	return flt(secs) / 3600.0


def assert_amenity_ready_for_payment(amenity_doc):
	"""Validate settings + rates when require_payment is on."""
	if not cint(getattr(amenity_doc, "require_payment", 0)):
		return None
	rates = list(getattr(amenity_doc, "payment_rates", None) or [])
	if not rates:
		return _("Amenity {0} requires payment but has no rates configured").format(
			amenity_doc.amenity_name
		)
	item = get_amenity_payment_item_code()
	if not item or not frappe.db.exists("Item", item):
		return _(
			"Amenity Payment Item is not set. Configure it under Mobile App Settings → Amenities."
		)
	return None


@frappe.whitelist(methods=["GET", "POST"])
def quote_amenity_booking(
	amenity=None,
	seater_label=None,
	duration_hours=None,
	start_time=None,
	booking_date=None,
	end_time=None,
	amenity_rate=None,
):
	"""Return package amount + computed end_time for Flutter checkout UI."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"amenity": amenity,
				"seater_label": seater_label,
				"duration_hours": duration_hours,
				"start_time": start_time,
				"booking_date": booking_date or nowdate(),
				"end_time": end_time,
				"amenity_rate": amenity_rate,
			}
		)
		name = (payload.get("amenity") or "").strip()
		if not name or not frappe.db.exists("Amenity", name):
			return {"status": "error", "message": _("Valid amenity is required")}

		doc = frappe.get_doc("Amenity", name)
		if not cint(getattr(doc, "require_payment", 0)):
			return {
				"status": "success",
				"require_payment": 0,
				"amount": 0.0,
				"currency": "TZS",
				"message": _("This amenity does not require payment"),
			}

		cfg_err = assert_amenity_ready_for_payment(doc)
		if cfg_err:
			return {"status": "error", "message": cfg_err}

		hours = flt(payload.get("duration_hours"))
		if hours <= 0 and payload.get("start_time") and payload.get("end_time"):
			hours = duration_hours_between(payload.get("start_time"), payload.get("end_time"))

		rate = find_amenity_rate(
			doc,
			payload.get("seater_label") or payload.get("seater"),
			hours,
			amenity_rate=payload.get("amenity_rate"),
		)
		if not rate:
			return {
				"status": "error",
				"message": _(
					"No rate found. Pass amenity_rate or seater + duration_hours."
				),
			}

		end = payload.get("end_time")
		if payload.get("start_time") and not end:
			end = end_time_from_start_and_hours(
				payload.get("booking_date") or nowdate(),
				payload.get("start_time"),
				flt(rate.duration_hours),
			)

		seater = (rate.seater_label or "").strip() or None
		hours = flt(rate.duration_hours)
		return {
			"status": "success",
			"require_payment": 1,
			"amenity": doc.name,
			"amenity_rate": rate.name,
			"seater": seater,
			"seater_label": seater,
			"seats": cint(rate.seats) if rate.seats else None,
			"duration_hours": hours,
			"duration_mins": int(round(hours * 60)),
			"amount": flt(rate.amount),
			"currency": "TZS",
			"item_code": get_amenity_payment_item_code(),
			"start_time": payload.get("start_time"),
			"end_time": end,
			"booking_date": str(getdate(payload.get("booking_date") or nowdate())),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "quote_amenity_booking")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def checkout_amenity_booking(
	request_id=None,
	payment_method="MOBILE_MONEY",
	phone_number=None,
	amount=None,
):
	"""Start Selcom for an unpaid amenity booking request (pay-to-confirm)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		from propms.api.v1.payments.checkout import start_selcom_checkout
		from propms.api.v1.payments.workflows import WORKFLOW_AMENITY_POS
		from propms.api.v1.water.water import _resolve_tenant_billing

		payload = _parse_request_payload(
			{
				"request_id": request_id,
				"payment_method": payment_method,
				"phone_number": phone_number,
				"amount": amount,
			}
		)
		rid = (payload.get("request_id") or "").strip()
		if not rid or not frappe.db.exists(AMENITY_BOOKING_REQUEST, rid):
			return {"status": "error", "message": _("Booking request not found")}

		req = frappe.get_doc(AMENITY_BOOKING_REQUEST, rid)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		if not is_staff and req.tenant != current_user:
			frappe.throw(_("Not permitted"), frappe.PermissionError)

		if req.status != "Open":
			return {
				"status": "error",
				"message": _("Only Open requests can be paid (current: {0})").format(req.status),
			}
		if not cint(getattr(req, "require_payment", 0)):
			return {"status": "error", "message": _("This booking does not require payment")}
		if (req.payment_status or "") == "Paid":
			return {"status": "error", "message": _("This booking is already paid")}

		amenity_doc = frappe.get_doc("Amenity", req.amenity)
		cfg_err = assert_amenity_ready_for_payment(amenity_doc)
		if cfg_err:
			return {"status": "error", "message": cfg_err}

		fee_amount = flt(req.amount)
		if fee_amount <= 0:
			return {"status": "error", "message": _("Booking amount is missing on the request")}

		pay_amount = (
			flt(payload.get("amount")) if payload.get("amount") is not None else fee_amount
		)
		if abs(pay_amount - fee_amount) > 0.05:
			return {
				"status": "error",
				"message": _("Payment amount must equal {0}").format(fee_amount),
			}

		billing = _resolve_tenant_billing(req.lease) or {}
		customer = billing.get("customer")
		if not customer and req.lease:
			customer = frappe.db.get_value("Lease", req.lease, "customer") or frappe.db.get_value(
				"Lease", req.lease, "lease_customer"
			)
		if not customer:
			return {
				"status": "error",
				"message": _("Could not resolve billing customer for this lease"),
			}

		item_code = get_amenity_payment_item_code()
		req.db_set("payment_status", "Pending", update_modified=True)
		frappe.db.commit()

		extra = {
			"amenity_purchase": True,
			"payment_workflow": WORKFLOW_AMENITY_POS,
			"amenity_booking_request": req.name,
			"amenity": req.amenity,
			"seater_label": req.seater_label,
			"duration_hours": flt(req.duration_hours),
			"lease": req.lease or billing.get("lease"),
			"property": req.property_unit or billing.get("property"),
			"customer": customer,
			"company": billing.get("company"),
			"cost_center": billing.get("cost_center"),
			"fee_item": item_code,
			"fee_amount": fee_amount,
			"lines": [
				{
					"item_code": item_code,
					"qty": 1,
					"rate": fee_amount,
					"amount": fee_amount,
				}
			],
		}

		result = start_selcom_checkout(
			reference_doctype=AMENITY_BOOKING_REQUEST,
			reference_name=req.name,
			customer=customer,
			amount=pay_amount,
			currency=req.currency or "TZS",
			payment_workflow=WORKFLOW_AMENITY_POS,
			payment_method=(payload.get("payment_method") or "MOBILE_MONEY"),
			phone_number=payload.get("phone_number"),
			buyer_remarks=f"Amenity {req.amenity} {req.name}",
			merchant_remarks="Viva Towers Amenity Booking",
			extra_raw_request=extra,
		)

		if isinstance(result, dict) and result.get("status") == "error":
			req.db_set("payment_status", "Failed", update_modified=True)
			frappe.db.commit()

		if isinstance(result, dict) and result.get("status") != "error":
			order_id = result.get("order_id")
			if order_id:
				req.db_set("selcom_order_id", order_id, update_modified=False)
				frappe.db.commit()

		if isinstance(result, dict):
			result["request_id"] = req.name
			result["amount"] = fee_amount
			result["require_payment"] = 1
			result["payment_status"] = frappe.db.get_value(
				AMENITY_BOOKING_REQUEST, req.name, "payment_status"
			)
		return result
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "checkout_amenity_booking")
		try:
			rid = (request_id or "").strip()
			if not rid:
				payload = _parse_request_payload({"request_id": request_id})
				rid = (payload.get("request_id") or "").strip()
			if rid and frappe.db.exists(AMENITY_BOOKING_REQUEST, rid):
				frappe.db.set_value(
					AMENITY_BOOKING_REQUEST,
					rid,
					"payment_status",
					"Failed",
					update_modified=True,
				)
				frappe.db.commit()
		except Exception:
			pass
		return {"status": "error", "message": str(e)}
