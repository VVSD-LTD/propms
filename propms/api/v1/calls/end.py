# -*- coding: utf-8 -*-
"""Call termination & duration tracking service."""

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload


@frappe.whitelist(methods=["POST"])
def end_call(call_id=None, reason="ended"):
	"""End or decline an active call and calculate total duration."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"call_id": call_id, "reason": reason})
		target_id = (payload.get("call_id") or "").strip()
		end_reason = (payload.get("reason") or "ended").strip().lower()

		if not target_id or not frappe.db.exists("Viva Call Log", target_id):
			return {"status": "error", "message": f"Call {target_id} not found"}

		doc = frappe.get_doc("Viva Call Log", target_id)
		current_user = frappe.session.user

		if current_user not in [doc.caller, doc.receiver]:
			user_roles = frappe.get_roles(current_user)
			if "System Manager" not in user_roles:
				frappe.throw(_("Not permitted to end this call"), frappe.PermissionError)

		now_ts = now_datetime()
		doc.ended_at = now_ts

		if doc.status == "Answered":
			doc.status = "Ended"
			if doc.connected_at:
				try:
					c_dt = get_datetime(doc.connected_at)
					doc.duration_seconds = max(0, int((now_ts - c_dt).total_seconds()))
				except Exception:
					pass
		elif doc.status in ["Initiated", "Ringing"]:
			if end_reason == "declined":
				doc.status = "Declined"
			elif end_reason == "missed":
				doc.status = "Missed"
			else:
				doc.status = "Declined" if current_user == doc.receiver else "Missed"

		doc.save(ignore_permissions=True)
		frappe.db.commit()

		end_payload = {
			"type": "call_ended",
			"event": "call_ended",
			"call_id": doc.name,
			"room_name": doc.room_name,
			"status": doc.status,
			"ended_by": current_user,
			"duration_seconds": doc.duration_seconds or 0,
			"reason": end_reason,
		}

		# Broadcast to both caller and receiver
		for target in [doc.caller, doc.receiver]:
			if target:
				frappe.publish_realtime(
					event="call_ended",
					message=end_payload,
					user=target,
					room=f"user:{target}",
					after_commit=True,
				)

		return {
			"status": "success",
			"message": f"Call {doc.name} {doc.status.lower()}",
			"call_id": doc.name,
			"status_value": doc.status,
			"duration_seconds": doc.duration_seconds or 0,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "end_call")
		return {"status": "error", "message": str(e)}
