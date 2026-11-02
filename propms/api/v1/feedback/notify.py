# -*- coding: utf-8 -*-
"""Feedback notifications (Notification Log + WebSocket + FCM)."""

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


def _feedback_payload(doc, event_type, extra=None):
	msg_plain = frappe.utils.strip_html(getattr(doc, "message", None) or "") or ""
	reply_plain = frappe.utils.strip_html(getattr(doc, "staff_reply", None) or "") or ""
	department = getattr(doc, "department", None) or ""
	out = {
		"type": event_type,
		"event": event_type,
		"notification_type": event_type,
		"feedback_id": doc.name,
		"subject": getattr(doc, "subject", None) or "",
		"department": department,
		"department_name": department,
		"status": getattr(doc, "status", None) or "",
		"tenant": getattr(doc, "tenant", None) or "",
		"tenant_name": getattr(doc, "tenant_name", None) or "",
		"property_unit": getattr(doc, "property_unit", None) or "",
		"lease": getattr(doc, "lease", None) or "",
		"message_preview": (msg_plain[:160] + ("…" if len(msg_plain) > 160 else "")),
		"has_staff_reply": 1 if reply_plain.strip() else 0,
		"staff_reply_preview": (reply_plain[:160] + ("…" if len(reply_plain) > 160 else "")),
		"timestamp": frappe.utils.now(),
		"route": "/feedback/{0}".format(doc.name),
	}
	if extra:
		out.update(extra)
	return out


def _publish_to_user(user, event_names, payload):
	if not user or user == "Guest":
		return
	fid = payload.get("feedback_id") or ""
	for ev in event_names:
		try:
			frappe.publish_realtime(
				event=ev, message=payload, user=user, after_commit=True
			)
		except Exception:
			pass
		for room in (f"user:{user}", f"user_{user}", f"feedback:{fid}"):
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


def _insert_notification_log(user, title, body, doc, from_user=None):
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
				"document_type": "Feedback",
				"document_name": doc.name,
				"from_user": from_user or frappe.session.user,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "feedback.notification_log")


def _enqueue_fcm(user, title, body, payload):
	if not user or user == "Guest":
		return
	try:
		frappe.enqueue(
			"propms.api.v1.feedback.notify.enqueue_feedback_push",
			queue="short",
			user=user,
			title=title,
			body=body,
			payload=payload,
		)
	except Exception:
		frappe.logger().error(frappe.get_traceback())


def notify_feedback_created(doc):
	"""Notify staff (WS + FCM + Notification Log) when tenant submits feedback."""
	payload = _feedback_payload(doc, "feedback_created")
	events = ("feedback_created", "new_feedback")
	title = _("New Feedback: {0}").format(doc.subject or doc.name)
	body = (frappe.utils.strip_html(doc.message or "") or "")[:200]

	staff_users = _staff_users(exclude_user=doc.tenant)
	_publish_to_staff(events, payload, staff_users)
	for user in staff_users:
		_insert_notification_log(
			user, title, body, doc, from_user=doc.tenant or frappe.session.user
		)
		_enqueue_fcm(user, title, body, payload)

	# Soft ack to tenant
	if doc.tenant and doc.tenant != "Guest":
		ack_title = _("Feedback submitted")
		ack_body = _("We received your feedback: {0}").format(doc.subject or doc.name)
		ack_payload = _feedback_payload(doc, "feedback_submitted_ack")
		_publish_to_user(doc.tenant, ("feedback_submitted_ack",), ack_payload)
		_insert_notification_log(doc.tenant, ack_title, ack_body, doc)
		_enqueue_fcm(doc.tenant, ack_title, ack_body, ack_payload)


def notify_feedback_status_or_reply(doc, previous_status=None):
	"""Notify tenant (WS + FCM + Notification Log) on staff reply / status change."""
	tenant = doc.tenant
	if not tenant or tenant == "Guest":
		return

	parts = []
	status_changed = bool(previous_status and previous_status != doc.status)
	if status_changed:
		parts.append(_("Status: {0} → {1}").format(previous_status, doc.status))

	reply_plain = frappe.utils.strip_html(doc.staff_reply or "")[:200]
	has_reply = bool(reply_plain.strip())
	if has_reply:
		parts.append(_("Reply: {0}").format(reply_plain))

	if not parts:
		return

	# Prefer a specific event when possible
	if has_reply and status_changed:
		event_type = "feedback_updated"
		events = ("feedback_updated", "feedback_reply", "feedback_status_changed")
	elif has_reply:
		event_type = "feedback_reply"
		events = ("feedback_reply", "feedback_updated")
	else:
		event_type = "feedback_status_changed"
		events = ("feedback_status_changed", "feedback_updated")

	payload = _feedback_payload(
		doc,
		event_type,
		extra={
			"previous_status": previous_status or "",
			"status_changed": 1 if status_changed else 0,
		},
	)
	title = _("Update on your Feedback: {0}").format(doc.subject or doc.name)
	body = "\n".join(parts)

	_publish_to_user(tenant, events, payload)
	_insert_notification_log(tenant, title, body, doc)
	_enqueue_fcm(tenant, title, body, payload)


@frappe.whitelist()
def enqueue_feedback_push(user, title=None, body=None, payload=None):
	"""Background worker: FCM push for Feedback events."""
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

		event_type = payload.get("type") or payload.get("event") or "feedback_updated"
		title = title or "Feedback update"
		body = body or ""

		data = {
			"click_action": "FLUTTER_NOTIFICATION_CLICK",
			"type": str(event_type),
			"notification_type": str(event_type),
			"update_type": str(event_type),
			"feedback_id": str(payload.get("feedback_id") or ""),
			"subject": str(payload.get("subject") or ""),
			"status": str(payload.get("status") or ""),
			"previous_status": str(payload.get("previous_status") or ""),
			"property_unit": str(payload.get("property_unit") or ""),
			"tenant": str(payload.get("tenant") or ""),
			"tenant_name": str(payload.get("tenant_name") or ""),
			"has_staff_reply": str(payload.get("has_staff_reply") or "0"),
			"user": str(user),
			"route": str(payload.get("route") or "/feedback"),
		}

		if tokens:
			from propms.api.v1.utils.fcm import send_to_tokens

			send_to_tokens(tokens=tokens, data=data, title=title, body=body)
		else:
			frappe.logger().warning(f"Feedback FCM: no device tokens for {user}")

		return {"status": "success", "user": user, "tokens": len(tokens or [])}
	except Exception as e:
		frappe.logger().error(f"Feedback FCM error for {user}: {e}")
		frappe.log_error(frappe.get_traceback(), "enqueue_feedback_push")
		return {"status": "error", "message": str(e)}
