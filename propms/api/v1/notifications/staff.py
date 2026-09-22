# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

"""Staff APIs to create/submit Mobile Notifications from the mobile app."""

from __future__ import annotations

import json

import frappe
from frappe import _


DOCTYPE = "Mobile Notifications"
NOTIFICATION_STAFF_ROLES = (
	"Mobile Maintenance Manager",
	"Mobile Maintenance Officer",
)


def _require_notification_staff():
	"""Manager / Officer / System Manager only (not Technician / Tenant)."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)

	roles = frappe.get_roles(frappe.session.user)
	if "System Manager" in roles:
		return "System Manager"
	for role in NOTIFICATION_STAFF_ROLES:
		if role in roles:
			return role

	frappe.throw(_("Not permitted"), frappe.PermissionError)


def _parse_attachments(attachments):
	"""Normalize attachments from list or JSON string -> list[dict]."""
	if attachments is None or attachments == "":
		return []
	if isinstance(attachments, str):
		try:
			attachments = json.loads(attachments)
		except Exception:
			frappe.throw(_("Invalid attachments JSON"))
	if not isinstance(attachments, (list, tuple)):
		frappe.throw(_("attachments must be a list"))
	out = []
	for row in attachments:
		if not isinstance(row, dict):
			frappe.throw(_("Each attachment must be an object with title and attachment"))
		title = (row.get("title") or "").strip()
		file_url = (row.get("attachment") or row.get("file_url") or "").strip()
		if not title or not file_url:
			frappe.throw(_("Each attachment requires title and attachment (file URL)"))
		out.append({"title": title, "attachment": file_url})
	return out


def _serialize_preview(doc):
	recipients = [
		{"tenant": r.tenant, "read_status": getattr(r, "read_status", None) or "Unread"}
		for r in (doc.recipients or [])
		if getattr(r, "tenant", None)
	]
	attachments = [
		{"title": a.title, "attachment": a.attachment}
		for a in (doc.attachment or [])
		if getattr(a, "attachment", None)
	]
	return {
		"status": "success",
		"notification_id": doc.name,
		"docstatus": doc.docstatus,
		"subject": doc.subject,
		"message": doc.message,
		"company": doc.company,
		"property": doc.property,
		"customer": doc.customer,
		"category": getattr(doc, "category", None) or "Notice",
		"target_audience": getattr(doc, "target_audience", None) or "Everyone",
		"recipient_count": len(recipients),
		"recipients": recipients,
		"attachments": attachments,
	}


@frappe.whitelist(methods=["POST"])
def create_notification(
	subject=None,
	message=None,
	company=None,
	property=None,
	customer=None,
	category=None,
	target_audience=None,
	target_floor=None,
	target_unit=None,
	attachments=None,
):
	"""Create a draft Mobile Notification and return resolved recipients for preview."""
	_require_notification_staff()

	# Accept JSON body fields if form args empty
	if frappe.request and getattr(frappe.request, "is_json", False):
		payload = frappe.request.get_json(silent=True) or {}
		subject = subject if subject is not None else payload.get("subject")
		message = message if message is not None else payload.get("message")
		company = company if company is not None else payload.get("company")
		property = property if property is not None else payload.get("property")
		customer = customer if customer is not None else payload.get("customer")
		category = category if category is not None else payload.get("category")
		target_audience = (
			target_audience if target_audience is not None else payload.get("target_audience")
		)
		target_floor = target_floor if target_floor is not None else payload.get("target_floor")
		target_unit = target_unit if target_unit is not None else payload.get("target_unit")
		attachments = attachments if attachments is not None else payload.get("attachments")

	subject = (subject or "").strip()
	message = (message or "").strip()
	company = (company or "").strip() or None
	property = (property or "").strip() or None
	customer = (customer or "").strip() or None

	if not subject:
		return {"status": "error", "message": "subject is required"}
	if not message:
		return {"status": "error", "message": "message is required"}
	if not (company or property or customer):
		return {
			"status": "error",
			"message": "At least one of company, property, or customer is required",
		}

	att_rows = _parse_attachments(attachments)

	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"subject": subject,
			"message": message,
			"company": company,
			"property": property,
			"customer": customer,
			"category": category or "Notice",
			"target_audience": target_audience or "Everyone",
			"target_floor": target_floor,
			"target_unit": target_unit,
			"sender": frappe.session.user,
		}
	)
	for row in att_rows:
		doc.append("attachment", row)

	doc.insert(ignore_permissions=True)
	# before_save already ran fetch_users; reload child rows
	doc.reload()

	if not doc.recipients:
		frappe.delete_doc(DOCTYPE, doc.name, force=1, ignore_permissions=True)
		return {
			"status": "error",
			"message": "No recipients for these filters",
		}

	return _serialize_preview(doc)


@frappe.whitelist(methods=["POST"])
def submit_notification(notification_id=None):
	"""Submit a draft Mobile Notification (fires WebSocket + FCM via DocType hooks)."""
	_require_notification_staff()

	if frappe.request and getattr(frappe.request, "is_json", False):
		payload = frappe.request.get_json(silent=True) or {}
		notification_id = notification_id or payload.get("notification_id")

	notification_id = (notification_id or "").strip()
	if not notification_id:
		return {"status": "error", "message": "notification_id is required"}

	if not frappe.db.exists(DOCTYPE, notification_id):
		return {"status": "error", "message": "Notification not found"}

	doc = frappe.get_doc(DOCTYPE, notification_id)
	if doc.docstatus != 0:
		return {
			"status": "error",
			"message": "Notification is already submitted",
		}

	doc.submit(ignore_permissions=True)
	doc.reload()

	return {
		"status": "success",
		"notification_id": doc.name,
		"docstatus": doc.docstatus,
		"delivery": getattr(doc, "delivery", None) or "",
		"recipient_count": len(doc.recipients or []),
	}
