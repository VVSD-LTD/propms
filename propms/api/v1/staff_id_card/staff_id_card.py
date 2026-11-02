# -*- coding: utf-8 -*-
"""Staff ID Card mobile + desk API (submittable DocType)."""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import cint, getdate, now_datetime, today

from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit, _parse_request_payload
from propms.api.v1.staff_id_card.notify import (
	notify_staff_id_approved,
	notify_staff_id_rejected,
	notify_staff_id_submitted,
)
from propms.property_management_solution.doctype.staff_id_card.staff_id_card import (
	FREE_CARD_LIMIT,
	RESIDENT_JOB_ROLE,
	STAFF_JOB_ROLES,
	VALID_APPLICANT_TYPES,
	VALID_GENDERS,
	VALID_ROLES,
	count_approved_cards,
	free_slots_remaining,
)

DOCTYPE = "Staff ID Card"
# Business status labels (Draft unpaid → pay → Submitted)
VALID_STATUSES = ("Draft", "Submitted", "Approved", "Rejected", "Cancelled")
DOCSTATUS_LABEL = {0: "Draft", 1: "Submitted", 2: "Cancelled"}


def _status_filters(status_key):
	"""Map API status filter → DB filters for submittable Staff ID Card."""
	st = (status_key or "all").strip().lower()
	if not st or st == "all":
		# Hide cancelled unless explicitly requested; include drafts + submitted
		return {"docstatus": ["<", 2]}
	mapped = {
		"draft": {"status": "Draft", "docstatus": 0},
		"submitted": {"status": "Submitted", "docstatus": 1},
		"pending": {"status": "Submitted", "docstatus": 1},
		"approved": {"status": "Approved", "docstatus": 1},
		"rejected": {"status": "Rejected", "docstatus": 1},
		"cancelled": {"status": "Cancelled", "docstatus": 2},
		"canceled": {"status": "Cancelled", "docstatus": 2},
	}
	if st in mapped:
		return mapped[st]
	# Fallback: treat as exact status string
	return {"status": status_key}


def _serialize(doc_or_dict, is_staff=False):
	as_dict_fn = getattr(doc_or_dict, "as_dict", None)
	if callable(as_dict_fn):
		d = as_dict_fn()
	else:
		d = frappe._dict(doc_or_dict)

	role_label = d.get("job_role") or ""
	if role_label == "Other" and d.get("job_role_other"):
		role_label = d.get("job_role_other")

	docstatus = cint(d.get("docstatus"))
	status = d.get("status") or ""

	out = {
		"name": d.get("name"),
		"card_id": d.get("name"),
		"status": status,
		"docstatus": docstatus,
		"docstatus_label": DOCSTATUS_LABEL.get(docstatus, str(docstatus)),
		"is_draft": 1 if docstatus == 0 else 0,
		"is_submitted": 1 if docstatus == 1 else 0,
		"is_cancelled": 1 if docstatus == 2 else 0,
		"payment_status": d.get("payment_status") or "Unpaid",
		"paid_amount": d.get("paid_amount"),
		"sales_invoice": d.get("sales_invoice"),
		"selcom_order_id": d.get("selcom_order_id"),
		"paid_on": str(d.get("paid_on") or "") or None,
		"can_pay": 1
		if docstatus == 0
		and status == "Draft"
		and (d.get("payment_status") or "Unpaid") in ("Unpaid", "Failed", "Pending")
		else 0,
		"can_cancel": 1
		if (docstatus == 1 and status == "Submitted")
		or (
			docstatus == 0
			and status == "Draft"
			and (d.get("payment_status") or "Unpaid") in ("Unpaid", "Failed")
		)
		else 0,
		"can_approve": 1
		if is_staff
		and docstatus == 1
		and status == "Submitted"
		and (d.get("payment_status") or "") == "Paid"
		else 0,
		"can_reject": 1 if is_staff and docstatus == 1 and status == "Submitted" else 0,
		"tenant": d.get("tenant"),
		"tenant_name": d.get("tenant_name"),
		"property_unit": d.get("property_unit"),
		"lease": d.get("lease"),
		"resident_mobile": d.get("resident_mobile"),
		"resident_email": d.get("resident_email"),
		"on_behalf": 1 if cint(d.get("on_behalf")) else 0,
		"submitted_by": d.get("submitted_by"),
		"applicant_type": d.get("applicant_type") or "Staff",
		"staff_full_name": d.get("staff_full_name"),
		"full_name": d.get("staff_full_name"),
		"nationality": d.get("nationality"),
		"gender": d.get("gender"),
		"date_of_birth": str(d.get("date_of_birth") or "") or None,
		"staff_mobile": d.get("staff_mobile"),
		"mobile": d.get("staff_mobile"),
		"job_role": d.get("job_role"),
		"job_role_other": d.get("job_role_other"),
		"job_role_label": role_label,
		"passport_photo": d.get("passport_photo"),
		"id_copy": d.get("id_copy"),
		"other_document": d.get("other_document"),
		"declaration_accepted": 1 if cint(d.get("declaration_accepted")) else 0,
		"is_paid_issuance": 1 if cint(d.get("is_paid_issuance")) else 0,
		"fee_note": d.get("fee_note"),
		"quota_info": d.get("quota_info"),
		"quota_enforced": 0,  # free-quota validation paused
		"rejection_reason": d.get("rejection_reason"),
		"scanned_id_image": d.get("scanned_id_image"),
		"creation": str(d.get("creation") or ""),
		"modified": str(d.get("modified") or ""),
		"is_digital_card": 1 if status == "Approved" and docstatus == 1 else 0,
	}

	# Digital card face (tenant + staff when approved)
	if status == "Approved" and docstatus == 1:
		out["digital_card"] = {
			"id_card_no": d.get("id_card_no"),
			"issue_date": str(d.get("issue_date") or "") or None,
			"staff_full_name": d.get("staff_full_name"),
			"job_role_label": role_label,
			"property_unit": d.get("property_unit"),
			"passport_photo": d.get("passport_photo"),
			"scanned_id_image": d.get("scanned_id_image"),
			"tenant_name": d.get("tenant_name"),
		}
		out["id_card_no"] = d.get("id_card_no")
		out["issue_date"] = str(d.get("issue_date") or "") or None
	else:
		out["digital_card"] = None
		out["id_card_no"] = None
		out["issue_date"] = None

	if is_staff:
		out["approved_by"] = d.get("approved_by")
		out["approved_on"] = str(d.get("approved_on") or "") or None

	return out


@frappe.whitelist(methods=["GET", "POST"])
def get_staff_id_options():
	"""Form options + fee for current user. Not POS Store — ID Card module only."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	user = frappe.session.user
	is_staff = _is_amenity_staff(user)
	from propms.api.v1.staff_id_card.fee import fee_payload

	fee = fee_payload()
	user_full_name = frappe.db.get_value("User", user, "full_name") or user
	user_mobile = frappe.db.get_value("User", user, "mobile_no") or ""
	return {
		"status": "success",
		"display_label": "ID Card",
		"is_staff": is_staff,
		"applicant_types": [
			{"value": "Resident", "label": _("Myself (Resident)")},
			{"value": "Staff", "label": _("For Staff")},
		],
		"job_roles": list(STAFF_JOB_ROLES),
		"genders": list(VALID_GENDERS),
		"statuses": list(VALID_STATUSES),
		"is_submittable": 1,
		"docstatus_labels": DOCSTATUS_LABEL,
		"quota_enforced": 0,
		"free_card_limit": FREE_CARD_LIMIT,
		"approved_count": 0,
		"free_slots_remaining": None,
		"payment_required": fee["payment_required"],
		"fee_item": fee["fee_item"],
		"fee_item_name": fee.get("fee_item_name") or fee["fee_item"],
		"fee_amount": fee["fee_amount"],
		"currency": fee["currency"],
		"price_list": fee["price_list"],
		"paid_fee_tsh": fee["fee_amount"],
		"resident_defaults": {
			"full_name": user_full_name,
			"staff_full_name": user_full_name,
			"mobile": user_mobile,
			"staff_mobile": user_mobile,
			"job_role": RESIDENT_JOB_ROLE,
		},
		"rules": [
			_("A fee of Tshs {0}/= applies per ID Card application.").format(
				f"{fee['fee_amount']:,.0f}"
			),
			_("Pick up the physical ID at the management office after approval."),
			_("Return the card to management when no longer needed."),
			_("Cardholders must carry the ID card in the building."),
			_("Resident is liable for sponsored staff conduct and card misuse."),
		],
	}


@frappe.whitelist(methods=["POST"])
def create_staff_id_card(
	staff_full_name=None,
	nationality=None,
	gender=None,
	date_of_birth=None,
	staff_mobile=None,
	job_role=None,
	job_role_other=None,
	passport_photo=None,
	id_copy=None,
	other_document=None,
	declaration_accepted=None,
	property_unit=None,
	lease=None,
	resident_mobile=None,
	resident_email=None,
	applicant_type=None,
	full_name=None,
	mobile=None,
):
	"""Tenant: create Draft ID Card (Unpaid). Pay via checkout_staff_id_card."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"staff_full_name": staff_full_name,
				"full_name": full_name,
				"nationality": nationality,
				"gender": gender,
				"date_of_birth": date_of_birth,
				"staff_mobile": staff_mobile,
				"mobile": mobile,
				"job_role": job_role,
				"job_role_other": job_role_other,
				"passport_photo": passport_photo,
				"id_copy": id_copy,
				"other_document": other_document,
				"declaration_accepted": declaration_accepted,
				"property_unit": property_unit,
				"lease": lease,
				"resident_mobile": resident_mobile,
				"resident_email": resident_email,
				"applicant_type": applicant_type,
			}
		)

		current_user = frappe.session.user
		user_full_name = frappe.db.get_value("User", current_user, "full_name") or current_user
		user_mobile = frappe.db.get_value("User", current_user, "mobile_no") or ""

		atype = (payload.get("applicant_type") or "Staff").strip()
		if atype not in VALID_APPLICANT_TYPES:
			return {
				"status": "error",
				"message": _("applicant_type must be Resident or Staff"),
			}

		name = (
			payload.get("staff_full_name")
			or payload.get("full_name")
			or ""
		).strip()
		if atype == "Resident" and not name:
			name = (user_full_name or "").strip()
		if not name:
			return {"status": "error", "message": _("Full name is required")}
		nationality = (payload.get("nationality") or "").strip()
		if not nationality:
			return {"status": "error", "message": _("Nationality is required")}
		gender = (payload.get("gender") or "").strip()
		if gender not in VALID_GENDERS:
			return {"status": "error", "message": _("Gender must be Male or Female")}
		dob = payload.get("date_of_birth")
		if not dob:
			return {"status": "error", "message": _("Date of birth is required")}
		try:
			getdate(dob)
		except Exception:
			return {"status": "error", "message": _("Invalid date of birth")}
		staff_mobile = (
			payload.get("staff_mobile") or payload.get("mobile") or ""
		).strip()
		if atype == "Resident" and not staff_mobile:
			staff_mobile = (user_mobile or "").strip()
		if not staff_mobile:
			return {"status": "error", "message": _("Mobile is required")}

		if atype == "Resident":
			job_role = RESIDENT_JOB_ROLE
			job_other = None
		else:
			job_role = (payload.get("job_role") or "").strip()
			if job_role not in STAFF_JOB_ROLES:
				return {
					"status": "error",
					"message": _(
						"job_role must be Maid, Nanny, Driver, Cook, or Other"
					),
				}
			job_other = (payload.get("job_role_other") or "").strip()
			if job_role == "Other" and not job_other:
				return {
					"status": "error",
					"message": _("Please specify the Other role"),
				}

		photo = (payload.get("passport_photo") or "").strip()
		id_copy = (payload.get("id_copy") or "").strip()
		if not photo:
			return {"status": "error", "message": _("Passport-size photo is required")}
		if not id_copy:
			return {"status": "error", "message": _("ID copy is required")}
		if not cint(payload.get("declaration_accepted")):
			return {
				"status": "error",
				"message": _("You must accept the declaration and ID card rules"),
			}

		email = frappe.db.get_value("User", current_user, "email") or current_user
		auto_unit, auto_lease, auto_resident = _get_tenant_default_unit(email)

		prop_unit = (payload.get("property_unit") or auto_unit or "").strip() or None
		if not prop_unit:
			return {
				"status": "error",
				"message": _("No apartment linked to your account. Contact management."),
			}

		from propms.api.v1.staff_id_card.fee import fee_payload

		fee = fee_payload()

		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"status": "Draft",
				"payment_status": "Unpaid",
				"applicant_type": atype,
				"tenant": current_user,
				"tenant_name": auto_resident
				or user_full_name
				or current_user,
				"property_unit": prop_unit,
				"lease": payload.get("lease") or auto_lease,
				"resident_mobile": (payload.get("resident_mobile") or "").strip()
				or user_mobile,
				"resident_email": (payload.get("resident_email") or "").strip() or email,
				"staff_full_name": name,
				"nationality": nationality,
				"gender": gender,
				"date_of_birth": dob,
				"staff_mobile": staff_mobile,
				"job_role": job_role,
				"job_role_other": job_other if job_role == "Other" else None,
				"passport_photo": photo,
				"id_copy": id_copy,
				"other_document": (payload.get("other_document") or "").strip() or None,
				"declaration_accepted": 1,
				"on_behalf": 0,
				"submitted_by": current_user,
				"is_paid_issuance": 1,
			}
		)
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
		doc.reload()

		return {
			"status": "success",
			"message": _("ID Card draft saved. Pay the fee to submit to management."),
			"card_id": doc.name,
			"docstatus": cint(doc.docstatus),
			"payment_status": doc.payment_status,
			"applicant_type": doc.applicant_type,
			"can_pay": 1,
			"payment_required": 1,
			"fee_amount": fee["fee_amount"],
			"fee_item": fee["fee_item"],
			"fee_item_name": fee.get("fee_item_name") or fee["fee_item"],
			"currency": fee["currency"],
			"quota_enforced": 0,
			"card": _serialize(doc, is_staff=False),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "create_staff_id_card")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def checkout_staff_id_card(
	card_id=None, payment_method="MOBILE_MONEY", phone_number=None, amount=None
):
	"""Start Selcom for Staff ID fee; on success Paid POS SI + submit card."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		from propms.api.v1.payments.checkout import start_selcom_checkout
		from propms.api.v1.payments.workflows import WORKFLOW_STAFF_ID_POS
		from propms.api.v1.staff_id_card.fee import fee_payload, get_fee_item_code
		from propms.api.v1.water.water import _resolve_tenant_billing
		from frappe.utils import flt

		payload = _parse_request_payload(
			{
				"card_id": card_id,
				"payment_method": payment_method,
				"phone_number": phone_number,
				"amount": amount,
			}
		)
		cid = (payload.get("card_id") or "").strip()
		if not cid or not frappe.db.exists(DOCTYPE, cid):
			return {"status": "error", "message": _("ID Card not found")}

		doc = frappe.get_doc(DOCTYPE, cid)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		if not is_staff and doc.tenant != current_user:
			frappe.throw(_("Not permitted"), frappe.PermissionError)

		if cint(doc.docstatus) != 0 or (doc.status or "") != "Draft":
			return {
				"status": "error",
				"message": _("Only unpaid Draft applications can be paid"),
			}
		if (doc.payment_status or "") == "Paid":
			return {"status": "error", "message": _("This application is already paid")}

		fee = fee_payload()
		fee_amount = flt(fee["fee_amount"])
		pay_amount = flt(payload.get("amount")) if payload.get("amount") is not None else fee_amount
		if abs(pay_amount - fee_amount) > 0.05:
			return {
				"status": "error",
				"message": _("Payment amount must equal fee ({0})").format(fee_amount),
			}

		billing = _resolve_tenant_billing(doc.lease) or {}
		customer = billing.get("customer")
		if not customer and doc.lease:
			customer = frappe.db.get_value("Lease", doc.lease, "customer") or frappe.db.get_value(
				"Lease", doc.lease, "lease_customer"
			)
		if not customer:
			return {
				"status": "error",
				"message": _("Could not resolve billing customer for this lease"),
			}

		item_code = get_fee_item_code()
		doc.db_set("payment_status", "Pending", update_modified=True)
		frappe.db.commit()

		extra = {
			"staff_id_purchase": True,
			"payment_workflow": WORKFLOW_STAFF_ID_POS,
			"staff_id_card": doc.name,
			"lease": doc.lease or billing.get("lease"),
			"property": doc.property_unit or billing.get("property"),
			"customer": customer,
			"company": billing.get("company"),
			"cost_center": billing.get("cost_center"),
			"fee_item": item_code,
			"fee_amount": fee_amount,
			"price_list": fee.get("price_list"),
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
			reference_doctype=DOCTYPE,
			reference_name=doc.name,
			customer=customer,
			amount=pay_amount,
			currency="TZS",
			payment_workflow=WORKFLOW_STAFF_ID_POS,
			payment_method=(payload.get("payment_method") or "MOBILE_MONEY"),
			phone_number=payload.get("phone_number"),
			buyer_remarks=f"ID Card {doc.name}",
			merchant_remarks="Viva Towers ID Card",
			extra_raw_request=extra,
		)

		if isinstance(result, dict) and result.get("status") == "error":
			doc.db_set("payment_status", "Failed", update_modified=True)
			frappe.db.commit()

		if isinstance(result, dict):
			result["card_id"] = doc.name
			result["fee_amount"] = fee_amount
		return result
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "checkout_staff_id_card")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_my_staff_id_cards(status="all", page=1, page_length=20):
	"""Tenant: own cards. Staff: all.

	``status`` filter values: all | draft | submitted|pending | approved | rejected | cancelled
	Uses DocType status + docstatus (submittable).
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{"status": status, "page": page, "page_length": page_length}
		)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 20)))
		offset = (page_num - 1) * page_len

		filters = dict(_status_filters(payload.get("status")))
		if not is_staff:
			filters["tenant"] = current_user

		list_fields = [
			"name",
			"status",
			"docstatus",
			"applicant_type",
			"tenant",
			"tenant_name",
			"property_unit",
			"lease",
			"resident_mobile",
			"resident_email",
			"on_behalf",
			"submitted_by",
			"staff_full_name",
			"nationality",
			"gender",
			"date_of_birth",
			"staff_mobile",
			"job_role",
			"job_role_other",
			"passport_photo",
			"id_copy",
			"other_document",
			"declaration_accepted",
			"is_paid_issuance",
			"fee_note",
			"quota_info",
			"payment_status",
			"paid_amount",
			"sales_invoice",
			"selcom_order_id",
			"paid_on",
			"id_card_no",
			"issue_date",
			"scanned_id_image",
			"approved_by",
			"approved_on",
			"rejection_reason",
			"creation",
			"modified",
		]

		total = frappe.db.count(DOCTYPE, filters=filters)
		rows = frappe.get_all(
			DOCTYPE,
			filters=filters,
			fields=list_fields,
			order_by="creation desc",
			limit_start=offset,
			limit_page_length=page_len,
			ignore_permissions=True,
		)
		items = [_serialize(r, is_staff=is_staff) for r in rows]

		out = {
			"status": "success",
			"is_staff": is_staff,
			"page": page_num,
			"page_length": page_len,
			"total": total,
			"cards": items,
			"quota_enforced": 0,
		}
		# Quota counters paused — client has not approved yet.
		# if not is_staff:
		# 	out["free_slots_remaining"] = free_slots_remaining(current_user)
		# 	out["approved_count"] = count_approved_cards(current_user)
		# 	out["free_card_limit"] = FREE_CARD_LIMIT
		return out
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_my_staff_id_cards")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_staff_id_card_detail(card_id=None):
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"card_id": card_id})
		cid = (payload.get("card_id") or "").strip()
		if not cid or not frappe.db.exists(DOCTYPE, cid):
			return {"status": "error", "message": _("Staff ID Card not found")}

		doc = frappe.get_doc(DOCTYPE, cid)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		if not is_staff and doc.tenant != current_user:
			frappe.throw(_("Not permitted"), frappe.PermissionError)

		return {
			"status": "success",
			"is_staff": is_staff,
			"card": _serialize(doc, is_staff=is_staff),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_staff_id_card_detail")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def cancel_staff_id_card(card_id=None):
	"""Cancel Submitted request, or delete unpaid Draft."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"card_id": card_id})
		cid = (payload.get("card_id") or "").strip()
		if not cid or not frappe.db.exists(DOCTYPE, cid):
			return {"status": "error", "message": _("Staff ID Card not found")}

		doc = frappe.get_doc(DOCTYPE, cid, for_update=True)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		if not is_staff and doc.tenant != current_user:
			frappe.throw(_("Not permitted"), frappe.PermissionError)

		# Unpaid / failed Draft → delete
		if (
			cint(doc.docstatus) == 0
			and (doc.status or "") == "Draft"
			and (doc.payment_status or "Unpaid") in ("Unpaid", "Failed")
		):
			name = doc.name
			frappe.delete_doc(DOCTYPE, name, force=1, ignore_permissions=True)
			frappe.db.commit()
			return {
				"status": "success",
				"message": _("Draft application removed"),
				"card_id": name,
				"docstatus": 0,
			}

		if cint(doc.docstatus) != 1 or doc.status != "Submitted":
			return {
				"status": "error",
				"message": _(
					"Only unpaid Draft or Submitted requests can be cancelled "
					"(status={0}, payment={1}, docstatus={2})"
				).format(doc.status, doc.payment_status, cint(doc.docstatus)),
			}
		doc.cancel()
		frappe.db.commit()
		doc.reload()
		return {
			"status": "success",
			"message": _("Request cancelled"),
			"card_id": doc.name,
			"docstatus": cint(doc.docstatus),
			"card": _serialize(doc, is_staff=is_staff),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "cancel_staff_id_card")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def approve_staff_id_card(
	card_id=None, id_card_no=None, issue_date=None, scanned_id_image=None
):
	"""Staff: approve a Submitted (docstatus 1) card — updates allow_on_submit fields."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can approve ID cards"), frappe.PermissionError)

		payload = _parse_request_payload(
			{
				"card_id": card_id,
				"id_card_no": id_card_no,
				"issue_date": issue_date,
				"scanned_id_image": scanned_id_image,
			}
		)
		cid = (payload.get("card_id") or "").strip()
		if not cid or not frappe.db.exists(DOCTYPE, cid):
			return {"status": "error", "message": _("Staff ID Card not found")}

		card_no = (payload.get("id_card_no") or "").strip()
		if not card_no:
			return {"status": "error", "message": _("ID Card No. is required")}

		issue = payload.get("issue_date") or today()
		try:
			issue = getdate(issue)
		except Exception:
			return {"status": "error", "message": _("Invalid issue date")}

		scanned = (payload.get("scanned_id_image") or "").strip()
		if not scanned:
			return {"status": "error", "message": _("Scanned ID image is required")}

		dup = frappe.db.exists(
			DOCTYPE,
			{
				"id_card_no": card_no,
				"status": "Approved",
				"docstatus": 1,
				"name": ["!=", cid],
			},
		)
		if dup:
			return {
				"status": "error",
				"message": _("ID Card No. {0} is already issued").format(card_no),
			}

		doc = frappe.get_doc(DOCTYPE, cid, for_update=True)
		if cint(doc.docstatus) != 1 or doc.status != "Submitted":
			return {
				"status": "error",
				"message": _(
					"Only Submitted requests can be approved (status={0}, docstatus={1})"
				).format(doc.status, cint(doc.docstatus)),
			}
		if (doc.payment_status or "") != "Paid":
			return {
				"status": "error",
				"message": _("ID Card must be Paid before it can be approved"),
			}

		doc.status = "Approved"
		doc.id_card_no = card_no
		doc.issue_date = issue
		doc.scanned_id_image = scanned
		doc.approved_by = frappe.session.user
		doc.approved_on = now_datetime()
		doc.rejection_reason = None
		doc.apply_approval_update()
		frappe.db.commit()
		doc.reload()

		try:
			notify_staff_id_approved(doc)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "approve_staff_id_card.notify")

		return {
			"status": "success",
			"message": _("ID Card approved"),
			"card_id": doc.name,
			"docstatus": cint(doc.docstatus),
			"card": _serialize(doc, is_staff=True),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "approve_staff_id_card")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def reject_staff_id_card(card_id=None, rejection_reason=None):
	"""Staff: reject a Submitted (docstatus 1) request."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can reject ID cards"), frappe.PermissionError)

		payload = _parse_request_payload(
			{"card_id": card_id, "rejection_reason": rejection_reason}
		)
		cid = (payload.get("card_id") or "").strip()
		if not cid or not frappe.db.exists(DOCTYPE, cid):
			return {"status": "error", "message": _("Staff ID Card not found")}

		reason = (payload.get("rejection_reason") or "").strip()
		if not reason:
			return {"status": "error", "message": _("Rejection reason is required")}

		doc = frappe.get_doc(DOCTYPE, cid, for_update=True)
		if cint(doc.docstatus) != 1 or doc.status != "Submitted":
			return {
				"status": "error",
				"message": _(
					"Only Submitted requests can be rejected (status={0}, docstatus={1})"
				).format(doc.status, cint(doc.docstatus)),
			}
		doc.status = "Rejected"
		doc.rejection_reason = reason
		doc.apply_approval_update()
		frappe.db.commit()
		doc.reload()

		try:
			notify_staff_id_rejected(doc)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "reject_staff_id_card.notify")

		return {
			"status": "success",
			"message": _("ID Card request rejected"),
			"card_id": doc.name,
			"docstatus": cint(doc.docstatus),
			"card": _serialize(doc, is_staff=True),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "reject_staff_id_card")
		return {"status": "error", "message": str(e)}
