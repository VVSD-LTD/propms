# -*- coding: utf-8 -*-
"""Emergency incident report intake service."""

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import now_datetime
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit, _parse_request_payload
from propms.api.v1.emergency.notify import broadcast_emergency_alert, create_emergency_notification_log


@frappe.whitelist(methods=["POST"])
def report_emergency(
	incident_type=None,
	property_unit=None,
	location_details=None,
	details=None,
):
	"""Report a building emergency incident and trigger real-time dispatch."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({
			"incident_type": incident_type,
			"property_unit": property_unit,
			"location_details": location_details,
			"details": details,
		})

		inc_type = (payload.get("incident_type") or "").strip()
		if not inc_type:
			return {"status": "error", "message": "incident_type is required"}

		current_user = frappe.session.user
		user_email = frappe.db.get_value("User", current_user, "email") or current_user
		auto_unit, auto_lease, auto_resident = _get_tenant_default_unit(user_email)

		reporter_name = auto_resident or frappe.db.get_value("User", current_user, "full_name") or current_user
		reporter_phone = frappe.db.get_value("User", current_user, "mobile_no") or ""
		prop_unit = payload.get("property_unit") or auto_unit or "Viva Towers Unit"
		loc_details = (payload.get("location_details") or "").strip() or prop_unit

		doc = frappe.get_doc({
			"doctype": "Viva Emergency Incident",
			"incident_type": inc_type,
			"reporter": current_user,
			"reporter_name": reporter_name,
			"reporter_phone": reporter_phone,
			"property_unit": prop_unit,
			"location_details": loc_details,
			"details": (payload.get("details") or "").strip(),
			"reported_at": now_datetime(),
			"status": "Open",
		})
		doc.insert(ignore_permissions=True)
		frappe.db.commit()

		# 1. Dispatch real-time WebSocket alert to staff and reporter
		broadcast_emergency_alert(doc)

		# 2. Create in-app Notification Log for staff
		create_emergency_notification_log(doc)

		return {
			"status": "success",
			"message": f"Emergency alert ({doc.incident_type}) reported successfully. Security & emergency teams have been dispatched.",
			"incident_id": doc.name,
			"incident": doc.as_dict(),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "report_emergency")
		return {"status": "error", "message": str(e)}
