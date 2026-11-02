# -*- coding: utf-8 -*-
"""Tenant Feedback / Complaint mobile + desk API."""

from __future__ import unicode_literals

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.feedback.notify import (
	notify_feedback_created,
	notify_feedback_status_or_reply,
)
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit, _parse_request_payload

FEEDBACK_DOCTYPE = "Feedback"

VALID_STATUSES = ("Open", "Closed")

# Map old Job-Card-style statuses (and aliases) → Open / Closed
_STATUS_ALIASES = {
	"open": "Open",
	"closed": "Closed",
	"in progress": "Open",
	"in_progress": "Open",
	"resolved": "Closed",
	"cancelled": "Closed",
	"canceled": "Closed",
}


def _normalize_status(raw):
	if raw is None:
		return None
	s = str(raw).strip()
	if not s:
		return None
	if s in VALID_STATUSES:
		return s
	return _STATUS_ALIASES.get(s.lower())


def _parse_attachments(raw):
	"""Accept list of file_url strings or dicts {file_url, file_name}."""
	if raw is None or raw == "":
		return []
	if isinstance(raw, str):
		raw = raw.strip()
		if not raw:
			return []
		try:
			raw = json.loads(raw)
		except Exception:
			# single URL
			return [{"file_url": raw, "file_name": raw.rsplit("/", 1)[-1]}]
	if not isinstance(raw, (list, tuple)):
		return []
	out = []
	for item in raw:
		if isinstance(item, str) and item.strip():
			url = item.strip()
			out.append({"file_url": url, "file_name": url.rsplit("/", 1)[-1]})
		elif isinstance(item, dict) and item.get("file_url"):
			url = str(item.get("file_url")).strip()
			out.append(
				{
					"file_url": url,
					"file_name": (item.get("file_name") or url.rsplit("/", 1)[-1]),
				}
			)
	return out


def _serialize_feedback(doc_or_dict, include_message=True, include_status=False):
	# frappe._dict __getattr__ returns None for missing keys, so hasattr(as_dict) is True
	# but not callable — only Document.as_dict() is safe.
	as_dict_fn = getattr(doc_or_dict, "as_dict", None)
	if callable(as_dict_fn):
		d = as_dict_fn()
	else:
		d = frappe._dict(doc_or_dict)

	attachments = []
	child_rows = None
	if callable(as_dict_fn):
		child_rows = getattr(doc_or_dict, "attachments", None)
	elif isinstance(d.get("attachments"), (list, tuple)):
		child_rows = d.get("attachments")

	if child_rows:
		for row in child_rows:
			if isinstance(row, dict):
				attachments.append(
					{
						"file_url": row.get("file_url"),
						"file_name": row.get("file_name"),
					}
				)
			else:
				attachments.append(
					{
						"file_url": row.file_url,
						"file_name": row.file_name,
					}
				)
	elif d.get("name"):
		attachments = frappe.get_all(
			"Feedback Attachment",
			filters={"parent": d.name, "parenttype": FEEDBACK_DOCTYPE},
			fields=["file_url", "file_name"],
			order_by="idx asc",
			ignore_permissions=True,
		)

	msg = d.get("message")
	plain = frappe.utils.strip_html(msg or "") if msg else ""
	staff_reply = d.get("staff_reply")
	staff_plain = frappe.utils.strip_html(staff_reply or "") if staff_reply else ""

	department = d.get("department") or ""
	department_name = department
	if department:
		department_name = (
			frappe.db.get_value("Feedback Department", department, "department_name")
			or department
		)

	out = {
		"name": d.get("name"),
		"feedback_id": d.get("name"),
		"subject": d.get("subject"),
		"department": department or None,
		"department_name": department_name or None,
		"tenant": d.get("tenant"),
		"tenant_name": d.get("tenant_name"),
		"property_unit": d.get("property_unit"),
		"lease": d.get("lease"),
		"staff_reply": staff_reply,
		"has_staff_reply": 1 if staff_plain.strip() else 0,
		"attachments": attachments,
		"attachment_count": len(attachments),
		"creation": str(d.get("creation") or ""),
		"modified": str(d.get("modified") or ""),
		"message_preview": (plain[:160] + ("…" if len(plain) > 160 else "")),
	}
	# Status / resolution is staff workflow only (not a Job Card for tenants)
	if include_status:
		out["status"] = d.get("status")
		out["resolved_by"] = d.get("resolved_by")
		out["resolved_on"] = str(d.get("resolved_on") or "") or None
	if include_message:
		out["message"] = msg
		out["message_plain"] = plain
		if staff_plain:
			out["staff_reply_plain"] = staff_plain
	return out


@frappe.whitelist(methods=["GET", "POST"])
def get_feedback_departments():
	"""Active Feedback Departments for Flutter picker."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)

	from propms.property_management_solution.doctype.feedback_department.feedback_department import (
		get_active_feedback_departments,
	)

	rows = get_active_feedback_departments()
	departments = [
		{
			"name": r.name,
			"department": r.name,
			"department_name": r.department_name or r.name,
			"description": r.description or "",
			"sort_order": r.sort_order,
		}
		for r in rows
	]
	return {
		"status": "success",
		"departments": departments,
		"count": len(departments),
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_feedback_categories():
	"""Options for mobile. Statuses are staff-only. Includes departments."""
	is_staff = _is_amenity_staff()
	from propms.property_management_solution.doctype.feedback_department.feedback_department import (
		get_active_feedback_departments,
	)

	dept_rows = get_active_feedback_departments()
	departments = [
		{
			"name": r.name,
			"department": r.name,
			"department_name": r.department_name or r.name,
			"description": r.description or "",
			"sort_order": r.sort_order,
		}
		for r in dept_rows
	]
	out = {
		"status": "success",
		"is_staff": is_staff,
		# Removed fields — empty for older app builds
		"feedback_types": [],
		"categories": [],
		"priorities": [],
		"statuses": [],
		"departments": departments,
	}
	if is_staff:
		out["statuses"] = list(VALID_STATUSES)
	return out


def _resolve_department(raw):
	"""Accept Feedback Department name (or department_name)."""
	value = (raw or "").strip()
	if not value:
		return None
	if frappe.db.exists("Feedback Department", value):
		active = frappe.db.get_value("Feedback Department", value, "is_active")
		if not cint(active):
			return None
		return value
	# Match by department_name (same as name when autoname field:)
	found = frappe.db.get_value(
		"Feedback Department",
		{"department_name": value, "is_active": 1},
		"name",
	)
	return found


@frappe.whitelist(methods=["POST"])
def create_feedback(
	subject=None,
	message=None,
	feedback_type=None,
	category=None,
	priority=None,
	attachments=None,
	property_unit=None,
	lease=None,
	department=None,
):
	"""Tenant: create Feedback with optional attachments.

	``feedback_type`` / ``category`` / ``priority`` are accepted but ignored
	(removed from DocType) so older app builds keep working.
	``department`` is required (Feedback Department name).
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"subject": subject,
				"message": message,
				"attachments": attachments,
				"property_unit": property_unit,
				"lease": lease,
				"department": department,
			}
		)

		subj = (payload.get("subject") or "").strip()
		msg = (payload.get("message") or "").strip()
		if not subj:
			return {"status": "error", "message": _("Subject is required")}
		if not msg or not frappe.utils.strip_html(msg).strip():
			return {"status": "error", "message": _("Message is required")}

		dept = _resolve_department(payload.get("department"))
		if not dept:
			return {
				"status": "error",
				"message": _("Please select a valid department for this feedback"),
			}

		current_user = frappe.session.user
		email = frappe.db.get_value("User", current_user, "email") or current_user
		auto_unit, auto_lease, auto_resident = _get_tenant_default_unit(email)
		prop_unit = payload.get("property_unit") or auto_unit
		lease_name = payload.get("lease") or auto_lease
		resident = (
			auto_resident
			or frappe.db.get_value("User", current_user, "full_name")
			or current_user
		)

		rows = _parse_attachments(payload.get("attachments"))

		doc = frappe.get_doc(
			{
				"doctype": FEEDBACK_DOCTYPE,
				"subject": subj,
				"message": msg,
				"department": dept,
				"status": "Open",
				"tenant": current_user,
				"tenant_name": resident,
				"property_unit": prop_unit,
				"lease": lease_name,
				"attachments": rows,
			}
		)
		doc.flags.skip_feedback_notify = True
		doc.insert(ignore_permissions=True)
		frappe.db.commit()

		try:
			notify_feedback_created(doc)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "create_feedback.notify")

		return {
			"status": "success",
			"message": _("Your feedback was submitted"),
			"feedback_id": doc.name,
			"feedback": _serialize_feedback(doc, include_status=False),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "create_feedback")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_my_feedback(
	status="all",
	feedback_type=None,
	category=None,
	page=1,
	page_length=20,
):
	"""List own Feedback (tenant) or all (staff).

	Tenants: list what they sent (no status UI).
	Staff: optional filter Open / Closed only.
	``feedback_type`` / ``category`` are ignored (removed).
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"status": status,
				"page": page,
				"page_length": page_length,
			}
		)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 20)))
		offset = (page_num - 1) * page_len

		filters = {}
		if not is_staff:
			filters["tenant"] = current_user
		else:
			st = (payload.get("status") or "all").strip()
			if st and st.lower() != "all":
				norm = _normalize_status(st)
				if norm:
					filters["status"] = norm

		rows = frappe.get_all(
			FEEDBACK_DOCTYPE,
			filters=filters,
			fields=[
				"name",
				"subject",
				"department",
				"status",
				"tenant",
				"tenant_name",
				"property_unit",
				"lease",
				"message",
				"staff_reply",
				"resolved_by",
				"resolved_on",
				"creation",
				"modified",
			],
			order_by="creation desc",
			ignore_permissions=True,
		)
		total = len(rows)
		page_rows = rows[offset : offset + page_len]
		items = [
			_serialize_feedback(r, include_message=False, include_status=is_staff)
			for r in page_rows
		]

		return {
			"status": "success",
			"is_staff": is_staff,
			"page": page_num,
			"page_length": page_len,
			"total": total,
			"feedback": items,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_my_feedback")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_feedback_detail(feedback_id=None):
	"""Single Feedback detail for tenant (own) or staff."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"feedback_id": feedback_id})
		fid = (payload.get("feedback_id") or "").strip()
		if not fid or not frappe.db.exists(FEEDBACK_DOCTYPE, fid):
			return {"status": "error", "message": _("Feedback not found")}

		doc = frappe.get_doc(FEEDBACK_DOCTYPE, fid)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		if not is_staff and doc.tenant != current_user:
			frappe.throw(_("Not permitted"), frappe.PermissionError)

		return {
			"status": "success",
			"is_staff": is_staff,
			"feedback": _serialize_feedback(
				doc, include_message=True, include_status=is_staff
			),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_feedback_detail")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def cancel_feedback(feedback_id=None):
	"""Tenant/staff: close Open feedback (statuses are only Open / Closed)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"feedback_id": feedback_id})
		fid = (payload.get("feedback_id") or "").strip()
		if not fid or not frappe.db.exists(FEEDBACK_DOCTYPE, fid):
			return {"status": "error", "message": _("Feedback not found")}

		doc = frappe.get_doc(FEEDBACK_DOCTYPE, fid, for_update=True)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)
		if not is_staff and doc.tenant != current_user:
			frappe.throw(_("Not permitted"), frappe.PermissionError)
		if doc.status != "Open":
			return {
				"status": "error",
				"message": _("Only Open feedback can be closed (current: {0})").format(
					doc.status
				),
			}
		prev_status = doc.status
		doc.status = "Closed"
		doc.flags.skip_feedback_notify = True
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		try:
			notify_feedback_status_or_reply(doc, previous_status=prev_status)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "cancel_feedback.notify")

		return {
			"status": "success",
			"message": _("Feedback closed"),
			"feedback_id": doc.name,
			"feedback": _serialize_feedback(
				doc, include_status=_is_amenity_staff(current_user)
			),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "cancel_feedback")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def update_feedback_status(
	feedback_id=None, status=None, staff_reply=None, internal_notes=None, priority=None
):
	"""Staff: mark Open/Closed and/or reply.

	``priority`` is accepted but ignored (field removed).
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)
		if not _is_amenity_staff():
			frappe.throw(_("Only staff can update feedback status"), frappe.PermissionError)

		payload = _parse_request_payload(
			{
				"feedback_id": feedback_id,
				"status": status,
				"staff_reply": staff_reply,
				"internal_notes": internal_notes,
			}
		)
		fid = (payload.get("feedback_id") or "").strip()
		if not fid or not frappe.db.exists(FEEDBACK_DOCTYPE, fid):
			return {"status": "error", "message": _("Feedback not found")}

		doc = frappe.get_doc(FEEDBACK_DOCTYPE, fid, for_update=True)
		prev_status = doc.status

		new_status = _normalize_status(payload.get("status"))
		if payload.get("status") is not None and str(payload.get("status") or "").strip():
			if not new_status:
				return {
					"status": "error",
					"message": _("Status must be Open or Closed"),
				}
			doc.status = new_status

		if payload.get("staff_reply") is not None:
			doc.staff_reply = payload.get("staff_reply")
		if payload.get("internal_notes") is not None:
			doc.internal_notes = payload.get("internal_notes")

		if doc.status == "Closed":
			if not doc.resolved_by:
				doc.resolved_by = frappe.session.user
			if not doc.resolved_on:
				doc.resolved_on = now_datetime()

		doc.flags.skip_feedback_notify = True
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		try:
			notify_feedback_status_or_reply(doc, previous_status=prev_status)
			frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "update_feedback_status.notify")

		return {
			"status": "success",
			"message": _("Feedback updated"),
			"feedback_id": doc.name,
			"feedback": _serialize_feedback(doc, include_status=True),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(frappe.get_traceback(), "update_feedback_status")
		return {"status": "error", "message": str(e)}
