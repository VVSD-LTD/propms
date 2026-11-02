# -*- coding: utf-8 -*-
"""Staff ID Card notifications (Notification Log + WebSocket + FCM)."""

from __future__ import unicode_literals

import frappe
from frappe import _


STAFF_ROLES = (
	"System Manager",
	"Property Manager",
	"Mobile Maintenance Manager",
	"Mobile Maintenance Officer",
)


def _staff_users(exclude_user=None):
	users = set()
	for role in STAFF_ROLES:
		for row in frappe.get_all(
			"Has Role",
			filters={"role": role, "parenttype": "User"},
			fields=["parent"],
		):
			if row.parent and row.parent not in ("Guest", "Administrator"):
				users.add(row.parent)
	users.add("Administrator")
	if exclude_user:
		users.discard(exclude_user)
	return list(users)


def _card_payload(doc, event_type):
	role_label = doc.job_role or ""
	if role_label == "Other" and getattr(doc, "job_role_other", None):
		role_label = doc.job_role_other
	return {
		"type": event_type,
		"event": event_type,
		"notification_type": event_type,
		"card_id": doc.name,
		"status": getattr(doc, "status", None) or "",
		"applicant_type": getattr(doc, "applicant_type", None) or "Staff",
		"tenant": getattr(doc, "tenant", None) or "",
		"tenant_name": getattr(doc, "tenant_name", None) or "",
		"property_unit": getattr(doc, "property_unit", None) or "",
		"lease": getattr(doc, "lease", None) or "",
		"staff_full_name": getattr(doc, "staff_full_name", None) or "",
		"job_role": getattr(doc, "job_role", None) or "",
		"job_role_label": role_label,
		"id_card_no": getattr(doc, "id_card_no", None) or "",
		"issue_date": str(getattr(doc, "issue_date", None) or ""),
		"rejection_reason": getattr(doc, "rejection_reason", None) or "",
		"passport_photo": getattr(doc, "passport_photo", None) or "",
		"timestamp": frappe.utils.now(),
		"route": "/staff_id_cards/{0}".format(doc.name),
	}


def _publish_to_user(user, event_names, payload):
	if not user or user == "Guest":
		return
	for ev in event_names:
		try:
			frappe.publish_realtime(
				event=ev, message=payload, user=user, after_commit=True
			)
		except Exception:
			pass
		for room in (f"user:{user}", f"user_{user}", f"staff_id:{payload.get('card_id')}"):
			try:
				frappe.publish_realtime(
					event=ev, message=payload, room=room, after_commit=True
				)
			except Exception:
				pass


def _publish_to_staff(event_names, payload, staff_users):
	for ev in event_names:
		try:
			frappe.publish_realtime(event=ev, message=payload, after_commit=True)
		except Exception:
			pass
		for u in staff_users:
			_publish_to_user(u, (ev,), payload)


def _insert_notification_log(user, title, body, doc):
	if not user or user == "Guest":
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"subject": title,
				"email_content": body,
				"for_user": user,
				"type": "Alert",
				"document_type": "Staff ID Card",
				"document_name": doc.name,
				"from_user": frappe.session.user,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "staff_id_card.notification_log")


def _enqueue_fcm(user, title, body, payload):
	if not user or user == "Guest":
		return
	try:
		frappe.enqueue(
			"propms.api.v1.staff_id_card.notify.enqueue_staff_id_push",
			queue="short",
			user=user,
			title=title,
			body=body,
			payload=payload,
		)
	except Exception:
		frappe.logger().error(frappe.get_traceback())


def notify_staff_id_submitted(doc):
	"""Notify staff (desk + WS + FCM) and ack tenant."""
	payload = _card_payload(doc, "staff_id_submitted")
	events = ("staff_id_submitted", "new_staff_id_card")
	atype = getattr(doc, "applicant_type", None) or "Staff"
	title = _("New ID Card request: {0}").format(doc.staff_full_name or doc.name)
	body = _("{0} · {1} · {2} · Unit {3}").format(
		doc.tenant_name or doc.tenant,
		atype,
		doc.job_role or "",
		doc.property_unit or "—",
	)

	staff_users = _staff_users(exclude_user=doc.tenant)
	_publish_to_staff(events, payload, staff_users)
	for user in staff_users:
		_insert_notification_log(user, title, body, doc)
		_enqueue_fcm(user, title, body, payload)

	# Tenant acknowledgement
	if doc.tenant and doc.tenant != "Guest":
		tenant_title = _("ID Card request submitted")
		tenant_body = _(
			"Your request for {0} ({1}) was received. We will notify you when it is reviewed."
		).format(doc.staff_full_name, doc.job_role or "")
		tenant_payload = _card_payload(doc, "staff_id_submitted_ack")
		_publish_to_user(doc.tenant, ("staff_id_submitted_ack",), tenant_payload)
		_insert_notification_log(doc.tenant, tenant_title, tenant_body, doc)
		_enqueue_fcm(doc.tenant, tenant_title, tenant_body, tenant_payload)


def notify_staff_id_approved(doc):
	"""Notify tenant that ID Card was approved (Notification Log + WebSocket + FCM)."""
	if not doc.tenant or doc.tenant == "Guest":
		return

	payload = _card_payload(doc, "staff_id_approved")
	events = ("staff_id_approved", "staff_id_card_approved")
	title = _("ID Card approved: {0}").format(doc.staff_full_name or doc.name)
	body = _(
		"ID Card No. {0} has been issued on {1}. You can now pick up the ID at the management office."
	).format(doc.id_card_no or "—", doc.issue_date or "")

	_publish_to_user(doc.tenant, events, payload)
	_insert_notification_log(doc.tenant, title, body, doc)
	_enqueue_fcm(doc.tenant, title, body, payload)


def notify_staff_id_rejected(doc):
	"""Notify tenant that ID Card was rejected (Notification Log + WebSocket + FCM)."""
	if not doc.tenant or doc.tenant == "Guest":
		return

	payload = _card_payload(doc, "staff_id_rejected")
	events = ("staff_id_rejected", "staff_id_card_rejected")
	title = _("ID Card request rejected: {0}").format(doc.staff_full_name or doc.name)
	body = doc.rejection_reason or _("Your ID Card request was rejected.")

	_publish_to_user(doc.tenant, events, payload)
	_insert_notification_log(doc.tenant, title, body, doc)
	_enqueue_fcm(doc.tenant, title, body, payload)


@frappe.whitelist()
def enqueue_staff_id_push(user, title=None, body=None, payload=None):
	"""Background worker: FCM push for Staff ID Card events."""
	try:
		if not user:
			return {"status": "no_user"}

		payload = payload or {}
		tokens = frappe.get_all(
			"User Device",
			filters={"user": user},
			order_by="creation desc",
			pluck="token",
			limit_page_length=5,
		)

		event_type = payload.get("type") or payload.get("event") or "staff_id_approved"
		title = title or "ID Card update"
		body = body or ""

		data = {
			"click_action": "FLUTTER_NOTIFICATION_CLICK",
			"type": str(event_type),
			"notification_type": str(event_type),
			"update_type": str(event_type),
			"card_id": str(payload.get("card_id") or ""),
			"status": str(payload.get("status") or ""),
			"applicant_type": str(payload.get("applicant_type") or "Staff"),
			"staff_full_name": str(payload.get("staff_full_name") or ""),
			"job_role": str(payload.get("job_role") or ""),
			"job_role_label": str(payload.get("job_role_label") or ""),
			"id_card_no": str(payload.get("id_card_no") or ""),
			"issue_date": str(payload.get("issue_date") or ""),
			"property_unit": str(payload.get("property_unit") or ""),
			"tenant": str(payload.get("tenant") or ""),
			"tenant_name": str(payload.get("tenant_name") or ""),
			"rejection_reason": str(payload.get("rejection_reason") or ""),
			"user": str(user),
			"route": str(payload.get("route") or "/staff_id_cards"),
		}

		if tokens:
			from propms.api.v1.utils.fcm import send_to_tokens

			send_to_tokens(tokens=tokens, data=data, title=title, body=body)
		else:
			frappe.logger().warning(f"Staff ID FCM: no device tokens for {user}")

		return {"status": "success", "user": user, "tokens": len(tokens or [])}
	except Exception as e:
		frappe.logger().error(f"Staff ID FCM error for {user}: {e}")
		frappe.log_error(frappe.get_traceback(), "enqueue_staff_id_push")
		return {"status": "error", "message": str(e)}
