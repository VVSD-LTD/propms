# -*- coding: utf-8 -*-
"""WebSocket + FCM notifications for amenity bookings (staff + tenant)."""

from __future__ import unicode_literals

import frappe
from propms.api.v1.amenities.list import _is_amenity_staff


# Roles that receive amenity booking push/websocket (same as ticket staff alerts)
NOTIFY_AMENITY_STAFF_ROLES = (
	"Mobile Maintenance Manager",
	"Mobile Maintenance Officer",
)


def _get_amenity_staff_recipients(exclude_user=None):
	"""Enabled mobile staff who should get amenity booking alerts.

	Uses Maintenance Users + Has Role for Officer / Manager / Property Manager.
	System Manager is intentionally excluded from push blast (too broad).
	"""
	recipients = set()
	roles = list(NOTIFY_AMENITY_STAFF_ROLES)

	# Maintenance Users rows (officer / manager)
	try:
		rows = frappe.get_all(
			"Maintenance Users",
			filters={
				"enabled": 1,
				"role": ["in", ["Mobile Maintenance Officer", "Mobile Maintenance Manager"]],
			},
			fields=["user_email", "user"],
		)
		for r in rows or []:
			if r.get("user_email"):
				recipients.add(r["user_email"])
			if r.get("user"):
				recipients.add(r["user"])
	except Exception:
		pass

	# Has Role assignments
	try:
		role_rows = frappe.get_all(
			"Has Role",
			filters={"role": ["in", roles], "parenttype": "User"},
			fields=["parent"],
		)
		for r in role_rows or []:
			user_id = r.get("parent")
			if not user_id or user_id in ("Guest", "Administrator"):
				continue
			if not frappe.db.get_value("User", user_id, "enabled"):
				continue
			user_email = frappe.db.get_value("User", user_id, "email") or user_id
			recipients.add(user_email)
			recipients.add(user_id)
	except Exception:
		pass

	exclude = set()
	if exclude_user:
		exclude.add(exclude_user)
		email = frappe.db.get_value("User", exclude_user, "email")
		if email:
			exclude.add(email)

	return {u for u in recipients if u and u not in exclude}


def _staff_broadcast_rooms():
	return {
		"amenities",
		"amenity_bookings",
		"support_team",
		"staff",
		"all_staff",
		"maintenance",
		"management",
		"role:Mobile Maintenance Officer",
		"role:Mobile Maintenance Manager",
		"role:Property Manager",
	}


def _booking_payload(booking_doc, amenity_doc=None, event_type="amenity_booked"):
	amenity_name = getattr(booking_doc, "amenity", None) or ""
	amenity_label = amenity_name
	if amenity_doc:
		amenity_label = getattr(amenity_doc, "amenity_name", None) or amenity_name
	elif amenity_name and frappe.db.exists("Amenity", amenity_name):
		amenity_label = (
			frappe.db.get_value("Amenity", amenity_name, "amenity_name") or amenity_name
		)

	return {
		"type": event_type,
		"event": event_type,
		"notification_type": event_type,
		"booking_id": booking_doc.name,
		"amenity": amenity_name,
		"amenity_name": amenity_label,
		"booking_date": str(getattr(booking_doc, "booking_date", "") or ""),
		"start_time": str(getattr(booking_doc, "start_time", "") or ""),
		"end_time": str(getattr(booking_doc, "end_time", "") or ""),
		"guests_count": getattr(booking_doc, "guests_count", None) or 1,
		"tenant": getattr(booking_doc, "tenant", None) or "",
		"tenant_name": getattr(booking_doc, "tenant_name", None) or "",
		"property_unit": getattr(booking_doc, "property_unit", None) or "",
		"lease": getattr(booking_doc, "lease", None) or "",
		"status": getattr(booking_doc, "status", None) or "",
		"notes": getattr(booking_doc, "notes", None) or "",
		"cancellation_reason": getattr(booking_doc, "cancellation_reason", None) or "",
		"timestamp": frappe.utils.now(),
		"route": "/amenity_bookings",
	}


def _publish_to_staff(event_names, payload, staff_users):
	"""Emit websocket events globally, to staff rooms, and to each staff user channel."""
	for ev in event_names:
		try:
			frappe.publish_realtime(event=ev, message=payload, after_commit=True)
		except Exception:
			pass

		for room in _staff_broadcast_rooms():
			try:
				frappe.publish_realtime(
					event=ev, message=payload, room=room, after_commit=True
				)
			except Exception:
				pass

		for u in staff_users:
			try:
				frappe.publish_realtime(
					event=ev, message=payload, user=u, after_commit=True
				)
			except Exception:
				pass
			for room in (f"user:{u}", f"user_{u}"):
				try:
					frappe.publish_realtime(
						event=ev, message=payload, room=room, after_commit=True
					)
				except Exception:
					pass


def _enqueue_staff_fcm(staff_users, title, body, payload):
	for staff_user in staff_users:
		try:
			frappe.enqueue(
				"propms.api.v1.amenities.notify.enqueue_amenity_booking_push",
				queue="short",
				user=staff_user,
				title=title,
				body=body,
				payload=payload,
			)
		except Exception:
			frappe.logger().error(frappe.get_traceback())


def notify_amenity_booked(booking_doc, amenity_doc=None):
	"""Notify amenity staff when a tenant creates a reservation (WebSocket + FCM)."""
	try:
		if not booking_doc:
			return

		tenant = getattr(booking_doc, "tenant", None)
		# Only notify staff for tenant-originated bookings; staff creating for self is rare.
		staff_users = _get_amenity_staff_recipients(exclude_user=tenant)
		if not staff_users:
			frappe.logger().warning(
				f"Amenity booking {booking_doc.name}: no staff recipients for notification"
			)

		payload = _booking_payload(booking_doc, amenity_doc, event_type="amenity_booked")
		events = ("amenity_booked", "amenity_booking_created", "new_amenity_booking")

		_publish_to_staff(events, payload, staff_users)

		amenity_label = payload.get("amenity_name") or "Amenity"
		tenant_label = payload.get("tenant_name") or payload.get("tenant") or "Tenant"
		unit = payload.get("property_unit") or ""
		unit_bit = f" ({unit})" if unit else ""
		title = f"Amenity booked: {amenity_label}"
		body = (
			f"{tenant_label}{unit_bit} reserved {amenity_label} on "
			f"{payload.get('booking_date')} {payload.get('start_time')}-{payload.get('end_time')}"
		)

		_enqueue_staff_fcm(staff_users, title, body, payload)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "notify_amenity_booked")


def notify_amenity_request_pending(request_doc, amenity_doc=None):
	"""Notify staff when a tenant submits an Open Amenity Booking Request (manual approval)."""
	try:
		if not request_doc:
			return

		tenant = getattr(request_doc, "tenant", None)
		staff_users = _get_amenity_staff_recipients(exclude_user=tenant)
		if not staff_users:
			frappe.logger().warning(
				f"Amenity request {request_doc.name}: no staff recipients for notification"
			)

		payload = _booking_payload(
			request_doc, amenity_doc, event_type="amenity_request_pending"
		)
		payload["request_id"] = request_doc.name
		payload["booking_id"] = getattr(request_doc, "booking", None) or ""
		payload["route"] = "/amenity_booking_requests"
		events = ("amenity_request_pending", "new_amenity_booking_request")

		_publish_to_staff(events, payload, staff_users)

		amenity_label = payload.get("amenity_name") or "Amenity"
		tenant_label = payload.get("tenant_name") or payload.get("tenant") or "Tenant"
		unit = payload.get("property_unit") or ""
		unit_bit = f" ({unit})" if unit else ""
		title = f"Amenity request: {amenity_label}"
		body = (
			f"{tenant_label}{unit_bit} requested {amenity_label} on "
			f"{payload.get('booking_date')} {payload.get('start_time')}-{payload.get('end_time')}"
		)

		_enqueue_staff_fcm(staff_users, title, body, payload)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "notify_amenity_request_pending")


def notify_amenity_booking_cancelled(booking_doc, cancelled_by=None):
	"""Notify the other party when a booking is cancelled.

	- Tenant cancels → staff
	- Staff cancels → tenant
	"""
	try:
		if not booking_doc:
			return

		cancelled_by = cancelled_by or frappe.session.user
		payload = _booking_payload(
			booking_doc, event_type="amenity_booking_cancelled"
		)
		events = ("amenity_booking_cancelled", "amenity_booking_canceled")

		amenity_label = payload.get("amenity_name") or "Amenity"
		title = f"Amenity booking cancelled: {amenity_label}"
		body = (
			f"{amenity_label} on {payload.get('booking_date')} "
			f"{payload.get('start_time')}-{payload.get('end_time')} was cancelled."
		)

		if _is_amenity_staff(cancelled_by):
			# Staff cancelled → notify tenant
			tenant = getattr(booking_doc, "tenant", None)
			if not tenant:
				return
			for ev in events:
				try:
					frappe.publish_realtime(
						event=ev, message=payload, user=tenant, after_commit=True
					)
				except Exception:
					pass
				for room in (f"user:{tenant}", f"user_{tenant}"):
					try:
						frappe.publish_realtime(
							event=ev, message=payload, room=room, after_commit=True
						)
					except Exception:
						pass
			try:
				frappe.enqueue(
					"propms.api.v1.amenities.notify.enqueue_amenity_booking_push",
					queue="short",
					user=tenant,
					title=title,
					body=body,
					payload=payload,
				)
			except Exception:
				frappe.logger().error(frappe.get_traceback())
		else:
			# Tenant cancelled → notify staff
			staff_users = _get_amenity_staff_recipients(exclude_user=cancelled_by)
			_publish_to_staff(events, payload, staff_users)
			_enqueue_staff_fcm(staff_users, title, body, payload)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "notify_amenity_booking_cancelled")


def notify_amenity_booking_approved(booking_doc, request_id=None):
	"""Notify tenant that a booking/request was approved (WebSocket + FCM)."""
	try:
		if not booking_doc:
			return
		tenant = getattr(booking_doc, "tenant", None)
		if not tenant:
			return

		payload = _booking_payload(booking_doc, event_type="amenity_booking_approved")
		if request_id:
			payload["request_id"] = request_id
		events = ("amenity_booking_approved",)

		for ev in events:
			try:
				frappe.publish_realtime(
					event=ev, message=payload, user=tenant, after_commit=True
				)
			except Exception:
				pass
			for room in (f"user:{tenant}", f"user_{tenant}"):
				try:
					frappe.publish_realtime(
						event=ev, message=payload, room=room, after_commit=True
					)
				except Exception:
					pass

		amenity_label = payload.get("amenity_name") or "Amenity"
		title = f"Amenity booking approved: {amenity_label}"
		body = (
			f"Your booking on {payload.get('booking_date')} "
			f"{payload.get('start_time')}-{payload.get('end_time')} was approved."
		)
		try:
			frappe.enqueue(
				"propms.api.v1.amenities.notify.enqueue_amenity_booking_push",
				queue="short",
				user=tenant,
				title=title,
				body=body,
				payload=payload,
			)
		except Exception:
			frappe.logger().error(frappe.get_traceback())
	except Exception:
		frappe.log_error(frappe.get_traceback(), "notify_amenity_booking_approved")


def notify_amenity_booking_rejected(booking_doc, request_id=None):
	"""Notify tenant that a booking/request was rejected (WebSocket + FCM)."""
	try:
		if not booking_doc:
			return
		tenant = getattr(booking_doc, "tenant", None)
		if not tenant:
			return

		payload = _booking_payload(booking_doc, event_type="amenity_booking_rejected")
		reason = getattr(booking_doc, "rejection_reason", None) or ""
		payload["rejection_reason"] = reason
		if request_id:
			payload["request_id"] = request_id
			# Request reject has no Confirmed Booking yet
			if getattr(booking_doc, "doctype", None) == "Amenity Booking Request":
				payload["booking_id"] = getattr(booking_doc, "booking", None) or ""
		events = ("amenity_booking_rejected",)

		for ev in events:
			try:
				frappe.publish_realtime(
					event=ev, message=payload, user=tenant, after_commit=True
				)
			except Exception:
				pass
			for room in (f"user:{tenant}", f"user_{tenant}"):
				try:
					frappe.publish_realtime(
						event=ev, message=payload, room=room, after_commit=True
					)
				except Exception:
					pass

		amenity_label = payload.get("amenity_name") or "Amenity"
		title = f"Amenity booking rejected: {amenity_label}"
		body = f"Your booking was rejected. {reason}".strip()
		try:
			frappe.enqueue(
				"propms.api.v1.amenities.notify.enqueue_amenity_booking_push",
				queue="short",
				user=tenant,
				title=title,
				body=body,
				payload=payload,
			)
		except Exception:
			frappe.logger().error(frappe.get_traceback())
	except Exception:
		frappe.log_error(frappe.get_traceback(), "notify_amenity_booking_rejected")


@frappe.whitelist()
def enqueue_amenity_booking_push(user, title=None, body=None, payload=None):
	"""Background worker: FCM + Notification Log for amenity booking events."""
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

		event_type = payload.get("type") or payload.get("event") or "amenity_booked"
		title = title or "Amenity booking update"
		body = body or ""

		data = {
			"click_action": "FLUTTER_NOTIFICATION_CLICK",
			"type": str(event_type),
			"notification_type": str(event_type),
			"update_type": str(event_type),
			"booking_id": str(payload.get("booking_id") or ""),
			"amenity": str(payload.get("amenity") or ""),
			"amenity_name": str(payload.get("amenity_name") or ""),
			"booking_date": str(payload.get("booking_date") or ""),
			"start_time": str(payload.get("start_time") or ""),
			"end_time": str(payload.get("end_time") or ""),
			"property_unit": str(payload.get("property_unit") or ""),
			"tenant": str(payload.get("tenant") or ""),
			"tenant_name": str(payload.get("tenant_name") or ""),
			"status": str(payload.get("status") or ""),
			"user": str(user),
			"route": str(payload.get("route") or "/amenity_bookings"),
		}

		if tokens:
			from propms.api.v1.utils.fcm import send_to_tokens

			send_to_tokens(tokens=tokens, data=data, title=title, body=body)
		else:
			frappe.logger().warning(f"Amenity FCM: no device tokens for {user}")

		# Desk / in-app bell
		try:
			if frappe.db.exists("DocType", "Notification Log"):
				nlog = frappe.get_doc(
					{
						"doctype": "Notification Log",
						"subject": title,
						"for_user": user,
						"email_content": body,
						"document_type": "Amenity Booking",
						"document_name": payload.get("booking_id") or "",
						"type": "Alert",
					}
				)
				nlog.insert(ignore_permissions=True)
				frappe.db.commit()
		except Exception:
			pass

		return {"status": "success", "user": user, "tokens": len(tokens or [])}
	except Exception as e:
		frappe.logger().error(f"Amenity FCM error for {user}: {e}")
		return {"status": "error", "message": str(e)}
