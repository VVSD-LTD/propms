# -*- coding: utf-8 -*-
"""Emergency real-time alert and notification dispatcher."""

from __future__ import unicode_literals
import frappe
from frappe import _


def broadcast_emergency_alert(incident_doc):
	"""Broadcast high-priority real-time WebSocket alert and enqueue FCM push."""
	try:
		reported_at_str = str(incident_doc.reported_at)
		payload = {
			"type": "emergency_alert",
			"event": "emergency_alert",
			"incident_id": incident_doc.name,
			"incident_type": incident_doc.incident_type,
			"property_unit": incident_doc.property_unit or "",
			"location_details": incident_doc.location_details or "",
			"reporter_name": incident_doc.reporter_name or "",
			"reporter_phone": incident_doc.reporter_phone or "",
			"details": incident_doc.details or "",
			"reported_at": reported_at_str,
			"status": incident_doc.status or "Open",
		}

		# Broadcast to staff roles and reporter user room
		target_rooms = [
			f"user:{incident_doc.reporter}",
			f"incident:{incident_doc.name}",
			"emergency_broadcast",
			"role:Mobile Maintenance Manager",
			"role:Mobile Maintenance Officer",
			"role:System Manager",
		]

		for room in target_rooms:
			try:
				frappe.publish_realtime(
					event="emergency_alert",
					message=payload,
					room=room,
					after_commit=True,
				)
			except Exception as emit_err:
				frappe.logger().error(f"WebSocket emit failed for room {room}: {emit_err}")

		# Enqueue high-priority FCM Push Notification to staff
		try:
			frappe.enqueue(
				"propms.api.v1.emergency.notify.enqueue_emergency_fcm_push",
				queue="short",
				incident_id=incident_doc.name,
				incident_type=incident_doc.incident_type,
				location=incident_doc.location_details or incident_doc.property_unit or "Viva Towers",
				reporter=incident_doc.reporter_name or "Resident",
			)
		except Exception as fcm_err:
			frappe.logger().error(f"Failed to enqueue emergency FCM: {fcm_err}")
	except Exception as e:
		frappe.logger().error(f"Error in broadcast_emergency_alert: {e}")


def create_emergency_notification_log(incident_doc):
	"""Insert in-app Notification Log for staff members."""
	try:
		staff_users = frappe.db.sql(
			"""
			SELECT DISTINCT u.name
			FROM `tabUser` u
			INNER JOIN `tabHas Role` hr ON hr.parent = u.name
			WHERE hr.role IN ('Mobile Maintenance Manager', 'Mobile Maintenance Officer', 'System Manager')
			  AND u.enabled = 1
			""",
			pluck="name",
		)
		for staff in staff_users or []:
			try:
				notif = frappe.new_doc("Notification Log")
				notif.for_user = staff
				notif.type = "Alert"
				notif.document_type = "Viva Emergency Incident"
				notif.document_name = incident_doc.name
				notif.subject = f"🚨 EMERGENCY: {incident_doc.incident_type} reported"
				notif.email_content = (
					f"Emergency alert for {incident_doc.property_unit or 'Viva Towers'} ({incident_doc.location_details}). "
					f"Reporter: {incident_doc.reporter_name} ({incident_doc.reporter_phone})."
				)
				notif.insert(ignore_permissions=True)
			except Exception:
				continue
		frappe.db.commit()
	except Exception as e:
		frappe.logger().error(f"Error in create_emergency_notification_log: {e}")


@frappe.whitelist()
def enqueue_emergency_fcm_push(incident_id, incident_type, location, reporter):
	"""Background job to send high-priority push notification to security & staff devices."""
	try:
		staff_users = frappe.db.sql(
			"""
			SELECT DISTINCT u.name
			FROM `tabUser` u
			INNER JOIN `tabHas Role` hr ON hr.parent = u.name
			WHERE hr.role IN ('Mobile Maintenance Manager', 'Mobile Maintenance Officer', 'System Manager')
			  AND u.enabled = 1
			""",
			pluck="name",
		)
		if not staff_users:
			return

		tokens = frappe.get_all(
			"User Device",
			filters={"user": ["in", staff_users]},
			pluck="token",
		)
		if not tokens:
			return

		data = {
			"type": "emergency_alert",
			"notification_type": "emergency_alert",
			"incident_id": str(incident_id),
			"incident_type": str(incident_type),
			"location": str(location),
			"reporter": str(reporter),
		}

		title = f"🚨 EMERGENCY: {incident_type}"
		body = f"Incident reported at {location} by {reporter}. Immediate response required."

		from propms.api.v1.utils.fcm import send_to_tokens

		send_to_tokens(tokens=tokens, data=data, title=title, body=body)
	except Exception as e:
		frappe.logger().error(f"Emergency FCM push error: {e}")
