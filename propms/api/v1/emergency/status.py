# -*- coding: utf-8 -*-
"""Emergency incident status management & query service."""

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import cint, now_datetime
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload


@frappe.whitelist(methods=["POST"])
def update_incident_status(incident_id=None, status=None, resolution_notes=None):
	"""Staff endpoint: Update emergency incident status (Dispatched, Responding, Resolved)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({
			"incident_id": incident_id,
			"status": status,
			"resolution_notes": resolution_notes,
		})

		inc_id = (payload.get("incident_id") or "").strip()
		new_status = (payload.get("status") or "").strip()

		if not inc_id or not frappe.db.exists("Viva Emergency Incident", inc_id):
			return {"status": "error", "message": f"Incident {inc_id} not found"}

		if new_status not in ["Open", "Dispatched", "Responding", "Resolved"]:
			return {"status": "error", "message": "Invalid status value"}

		doc = frappe.get_doc("Viva Emergency Incident", inc_id)
		doc.status = new_status

		current_user = frappe.session.user
		if new_status == "Resolved":
			doc.resolved_by = current_user
			doc.resolved_at = now_datetime()
			if payload.get("resolution_notes"):
				doc.resolution_notes = payload.get("resolution_notes").strip()

		doc.save(ignore_permissions=True)
		frappe.db.commit()

		# Publish WebSocket status change
		update_payload = {
			"type": "emergency_status_updated",
			"incident_id": doc.name,
			"status": doc.status,
			"resolved_by": doc.resolved_by or "",
			"resolved_at": str(doc.resolved_at or ""),
		}
		for room in [f"user:{doc.reporter}", f"incident:{doc.name}", "emergency_broadcast"]:
			frappe.publish_realtime(
				event="emergency_status_updated",
				message=update_payload,
				room=room,
				after_commit=True,
			)

		return {
			"status": "success",
			"message": f"Incident status updated to {new_status}",
			"incident_id": doc.name,
			"status_value": doc.status,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "update_incident_status")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_emergency_incidents(status="all", page=1, page_length=20):
	"""Return emergency incidents list."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"status": status, "page": page, "page_length": page_length})
		current_user = frappe.session.user
		user_roles = frappe.get_roles(current_user)
		is_staff = any(r in user_roles for r in ["System Manager", "Mobile Maintenance Manager", "Mobile Maintenance Officer"])

		target_status = (payload.get("status") or "all").strip().lower()
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 20)))
		offset = (page_num - 1) * page_len

		filters = {}
		if not is_staff:
			# Non-staff only see their own reported incidents
			filters["reporter"] = current_user

		if target_status and target_status != "all":
			filters["status"] = target_status.capitalize()

		fields = [
			"name",
			"incident_type",
			"reporter",
			"reporter_name",
			"reporter_phone",
			"property_unit",
			"location_details",
			"details",
			"reported_at",
			"status",
			"resolved_by",
			"resolved_at",
			"resolution_notes",
		]

		incidents = frappe.get_all(
			"Viva Emergency Incident",
			filters=filters,
			fields=fields,
			order_by="reported_at desc",
			limit_start=offset,
			limit_page_length=page_len,
		)

		return {
			"status": "success",
			"page": page_num,
			"page_length": page_len,
			"incidents": incidents,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_emergency_incidents")
		return {"status": "error", "message": str(e)}
