# -*- coding: utf-8 -*-
"""Call initiation & signaling service."""

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import now_datetime
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit, _parse_request_payload
from propms.api.v1.calls.token import generate_livekit_token, get_livekit_config


@frappe.whitelist(methods=["POST"])
def initiate_call(receiver=None, call_type="Voice"):
	"""Initiate a 1-on-1 audio/video call between residents/staff."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		config = get_livekit_config()
		if not config.get("enabled"):
			return {"status": "error", "message": "Voice/Video calling is currently disabled in Mobile App Settings"}

		payload = _parse_request_payload({"receiver": receiver, "call_type": call_type})
		target_receiver = (payload.get("receiver") or "").strip()
		c_type = (payload.get("call_type") or "Voice").strip()

		if not target_receiver or not frappe.db.exists("User", target_receiver):
			return {"status": "error", "message": "Valid receiver user is required"}

		current_user = frappe.session.user
		if current_user == target_receiver:
			return {"status": "error", "message": "Cannot call yourself"}

		# 1. Resolve Caller info
		caller_email = frappe.db.get_value("User", current_user, "email") or current_user
		caller_unit, _, auto_caller_name = _get_tenant_default_unit(caller_email)
		caller_name = auto_caller_name or frappe.db.get_value("User", current_user, "full_name") or current_user
		caller_roles = frappe.get_roles(current_user)
		caller_role = "Resident" if "Mobile VIVA Tenant" in caller_roles else (caller_roles[0] if caller_roles else "User")

		# 2. Resolve Receiver info
		receiver_email = frappe.db.get_value("User", target_receiver, "email") or target_receiver
		receiver_unit, _, auto_receiver_name = _get_tenant_default_unit(receiver_email)
		receiver_name = auto_receiver_name or frappe.db.get_value("User", target_receiver, "full_name") or target_receiver
		receiver_roles = frappe.get_roles(target_receiver)
		receiver_role = "Resident" if "Mobile VIVA Tenant" in receiver_roles else (receiver_roles[0] if receiver_roles else "User")

		# 3. Create Viva Call Log doc
		call_doc = frappe.get_doc({
			"doctype": "Viva Call Log",
			"call_type": c_type,
			"status": "Ringing",
			"started_at": now_datetime(),
			"caller": current_user,
			"caller_name": caller_name,
			"caller_unit": caller_unit or "",
			"caller_role": caller_role,
			"receiver": target_receiver,
			"receiver_name": receiver_name,
			"receiver_unit": receiver_unit or "",
			"receiver_role": receiver_role,
		})
		call_doc.insert(ignore_permissions=True)

		room_name = f"viva_call_{call_doc.name}"
		call_doc.room_name = room_name
		call_doc.db_set("room_name", room_name)
		frappe.db.commit()

		# 4. Generate Caller's LiveKit Token
		caller_token_info = generate_livekit_token(
			room_name=room_name,
			identity=current_user,
			name=caller_name,
			can_publish=True,
			can_subscribe=True,
		)

		call_payload = {
			"type": "incoming_call",
			"event": "incoming_call",
			"call_id": call_doc.name,
			"room_name": room_name,
			"call_type": c_type,
			"caller": current_user,
			"caller_name": caller_name,
			"caller_unit": caller_unit or "",
			"caller_role": caller_role,
			"receiver": target_receiver,
			"receiver_name": receiver_name,
			"server_url": caller_token_info.get("server_url"),
			"started_at": str(call_doc.started_at),
		}

		# 5. Emit Real-time WebSocket to receiver
		frappe.publish_realtime(
			event="incoming_call",
			message=call_payload,
			user=target_receiver,
			room=f"user:{target_receiver}",
			after_commit=True,
		)

		# 6. Enqueue High-Priority FCM Wake-up Push (CallKit compatible)
		try:
			frappe.enqueue(
				"propms.api.v1.calls.initiate.enqueue_incoming_call_push",
				queue="short",
				receiver=target_receiver,
				call_payload=call_payload,
			)
		except Exception as fcm_err:
			frappe.logger().error(f"FCM Enqueue failed for call {call_doc.name}: {fcm_err}")

		return {
			"status": "success",
			"call_id": call_doc.name,
			"room_name": room_name,
			"call_type": c_type,
			"token": caller_token_info.get("token"),
			"server_url": caller_token_info.get("server_url"),
			"caller": current_user,
			"caller_name": caller_name,
			"receiver": target_receiver,
			"receiver_name": receiver_name,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "initiate_call")
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def enqueue_incoming_call_push(receiver, call_payload):
	"""Background job to send high-priority FCM push notification for incoming calls."""
	try:
		tokens = frappe.get_all(
			"User Device",
			filters={"user": receiver},
			pluck="token",
		)
		if not tokens:
			return

		caller_name = call_payload.get("caller_name") or "Viva Resident"
		caller_unit = call_payload.get("caller_unit")
		unit_str = f" ({caller_unit})" if caller_unit else ""
		c_type = call_payload.get("call_type") or "Voice"

		title = f"📞 Incoming {c_type} Call"
		body = f"{caller_name}{unit_str} is calling you..."

		from propms.api.v1.utils.fcm import send_to_tokens

		send_to_tokens(
			tokens=tokens,
			data=call_payload,
			title=title,
			body=body,
		)
	except Exception as e:
		frappe.logger().error(f"FCM Call Wakeup Push Error for {receiver}: {e}")
