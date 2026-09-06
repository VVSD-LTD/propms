# -*- coding: utf-8 -*-
"""Call answering service."""

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import now_datetime
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload
from propms.api.v1.calls.token import generate_livekit_token


@frappe.whitelist(methods=["POST"])
def answer_call(call_id=None):
	"""Accept and join an active incoming call."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"call_id": call_id})
		target_id = (payload.get("call_id") or "").strip()

		if not target_id or not frappe.db.exists("Viva Call Log", target_id):
			return {"status": "error", "message": f"Call {target_id} not found"}

		doc = frappe.get_doc("Viva Call Log", target_id)
		current_user = frappe.session.user

		if current_user not in [doc.caller, doc.receiver]:
			frappe.throw(_("Not permitted to answer this call"), frappe.PermissionError)

		doc.status = "Answered"
		doc.connected_at = now_datetime()
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		# Generate receiver token
		token_info = generate_livekit_token(
			room_name=doc.room_name,
			identity=current_user,
			name=doc.receiver_name or current_user,
			can_publish=True,
			can_subscribe=True,
		)

		answer_payload = {
			"type": "call_answered",
			"event": "call_answered",
			"call_id": doc.name,
			"room_name": doc.room_name,
			"answered_by": current_user,
			"connected_at": str(doc.connected_at),
			"status": "Answered",
		}

		# Notify caller that receiver answered
		frappe.publish_realtime(
			event="call_answered",
			message=answer_payload,
			user=doc.caller,
			room=f"user:{doc.caller}",
			after_commit=True,
		)

		return {
			"status": "success",
			"call_id": doc.name,
			"room_name": doc.room_name,
			"token": token_info.get("token"),
			"server_url": token_info.get("server_url"),
			"connected_at": str(doc.connected_at),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "answer_call")
		return {"status": "error", "message": str(e)}
