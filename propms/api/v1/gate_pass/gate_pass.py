# -*- coding: utf-8 -*-
"""Visitor Gate Pass API for Mobile & Security Staff."""

from __future__ import unicode_literals

import json
import frappe
from frappe import _
from frappe.utils import (
	cint,
	cstr,
	get_datetime,
	getdate,
	now_datetime,
	nowdate,
)
from propms.custom.lease import get_customer_from_lease, get_tenant_context_for_user


def _parse_request_payload(defaults=None):
	"""Extract payload parameters safely from JSON request body or form_dict."""
	data = dict(defaults or {})
	try:
		form = getattr(frappe, "form_dict", None) or getattr(frappe.local, "form_dict", None) or {}
		if isinstance(form, dict):
			data.update({k: v for k, v in form.items() if v is not None})
	except Exception:
		pass

	try:
		req = getattr(frappe, "request", None) or getattr(frappe.local, "request", None)
		if req and callable(getattr(req, "get_json", None)):
			body = req.get_json(silent=True)
			if isinstance(body, dict):
				data.update({k: v for k, v in body.items() if v is not None})
	except Exception:
		pass
	return data


def _get_tenant_apartments_list(user_email):
	"""Resolve all active apartments and leases for a Mobile VIVA Tenant via Tenant Details child table."""
	if not user_email:
		return []

	# In PropMS, Mobile VIVA Tenants are identified via the 'Tenant Details' child table on Lease
	rows = frappe.get_all(
		"Tenant Details",
		filters={"user_email": user_email, "parenttype": "Lease"},
		fields=["name", "parent", "full_name"],
		order_by="modified desc",
	)
	if not rows:
		# Backward compatibility for legacy records
		rows = frappe.get_all(
			"Tenant Details",
			filters={"user_email": user_email, "parenttype": "Tenant"},
			fields=["name", "parent", "full_name"],
			order_by="modified desc",
		)

	apartments = []
	seen_leases = set()
	for r in rows or []:
		lease_name = r.get("parent")
		if not lease_name or lease_name in seen_leases:
			continue
		seen_leases.add(lease_name)

		prop_name = None
		if frappe.db.exists("Lease", lease_name):
			prop_name = frappe.db.get_value("Lease", lease_name, "property")

		customer_name = get_customer_from_lease(lease_name)
		display_name = r.get("full_name") or customer_name or user_email

		apartments.append({
			"lease": lease_name,
			"property": prop_name or lease_name,
			"apartment_name": prop_name or lease_name,
			"tenant_details_row": r.get("name"),
			"resident_name": display_name,
		})

	return apartments


def _get_tenant_default_unit(user_email):
	"""Resolve the primary apartment unit, lease, and resident name for the given tenant user."""
	apartments = _get_tenant_apartments_list(user_email)
	if apartments:
		primary = apartments[0]
		return primary["property"], primary["lease"], primary["resident_name"]

	# Fallback check via Tenant context
	tenant_ctx = get_tenant_context_for_user(user_email) or {}
	if tenant_ctx.get("lease"):
		lease_name = tenant_ctx["lease"]
		prop_unit = frappe.db.get_value("Lease", lease_name, "property") or lease_name
		return prop_unit, lease_name, None

	return None, None, None


@frappe.whitelist(methods=["GET", "POST"])
def get_tenant_apartments():
	"""Return all apartments and active leases linked to the authenticated Mobile VIVA Tenant."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		current_user = frappe.session.user
		tenant_email = frappe.db.get_value("User", current_user, "email") or current_user
		apartments = _get_tenant_apartments_list(tenant_email)

		return {
			"status": "success",
			"total": len(apartments),
			"apartments": apartments,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_tenant_apartments")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def create_visitor_pass(
	visitor_name=None,
	phone_number=None,
	visitor_type="Guest",
	expected_arrival_date=None,
	expected_arrival_time=None,
	vehicle_plate=None,
	validity_type="One-Time Entry",
	lease=None,
	property_unit=None,
	notes=None,
):
	"""Create a new Visitor Gate Pass for the authenticated tenant resident."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({
			"visitor_name": visitor_name,
			"phone_number": phone_number,
			"visitor_type": visitor_type or "Guest",
			"expected_arrival_date": expected_arrival_date or nowdate(),
			"expected_arrival_time": expected_arrival_time,
			"vehicle_plate": vehicle_plate,
			"validity_type": validity_type or "One-Time Entry",
			"lease": lease,
			"property_unit": property_unit,
			"notes": notes,
		})

		v_name = (payload.get("visitor_name") or "").strip()
		if not v_name:
			return {"status": "error", "message": "Visitor name is required"}

		current_user = frappe.session.user
		tenant_email = frappe.db.get_value("User", current_user, "email") or current_user

		prop_unit = payload.get("property_unit")
		lease_name = payload.get("lease")
		auto_unit, auto_lease, auto_resident = _get_tenant_default_unit(tenant_email)

		prop_unit = prop_unit or auto_unit or "Viva Towers Unit"
		lease_name = lease_name or auto_lease

		# Identify tenant display name from Tenant Details child table on Lease
		tenant_name = auto_resident or frappe.db.get_value("User", current_user, "full_name") or current_user

		doc = frappe.get_doc({
			"doctype": "Visitor Gate Pass",
			"tenant": current_user,
			"tenant_name": tenant_name,
			"property_unit": prop_unit,
			"lease": lease_name,
			"visitor_name": v_name,
			"phone_number": (payload.get("phone_number") or "").strip(),
			"visitor_type": payload.get("visitor_type") or "Guest",
			"vehicle_plate": (payload.get("vehicle_plate") or "").strip().upper(),
			"expected_arrival_date": payload.get("expected_arrival_date") or nowdate(),
			"expected_arrival_time": payload.get("expected_arrival_time") or "",
			"validity_type": payload.get("validity_type") or "One-Time Entry",
			"notes": (payload.get("notes") or "").strip(),
			"status": "Active",
		})
		doc.insert(ignore_permissions=True)
		frappe.db.commit()

		# Publish real-time creation event for tenant UI sync
		frappe.publish_realtime(
			event="gate_pass_created",
			message={
				"pass_id": doc.name,
				"visitor_name": doc.visitor_name,
				"status": doc.status,
				"property_unit": doc.property_unit,
			},
			room=f"user:{current_user}",
			after_commit=True,
		)

		doc.reload()

		return {
			"status": "success",
			"message": "Visitor pass created successfully",
			"pass_id": doc.name,
			"qr_payload": doc.qr_payload,
			"doc": doc.as_dict(),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "create_visitor_pass")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_tenant_visitor_passes(status="all", lease=None, page=1, page_length=20):
	"""Return list of visitor passes created by the logged-in tenant."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({
			"status": status,
			"lease": lease,
			"page": page,
			"page_length": page_length,
		})

		current_user = frappe.session.user
		target_status = (payload.get("status") or "all").strip().lower()
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 20)))
		offset = (page_num - 1) * page_len

		# Auto-expire overdue passes on query
		auto_expire_overdue_passes()

		filters = {"tenant": current_user}
		if target_status == "active":
			filters["status"] = "Active"
		elif target_status == "checked_in" or target_status == "checked in":
			filters["status"] = "Checked In"
		elif target_status == "expired":
			filters["status"] = "Expired"
		elif target_status == "cancelled":
			filters["status"] = "Cancelled"

		if payload.get("lease"):
			filters["lease"] = payload.get("lease")

		fields = [
			"name",
			"visitor_name",
			"phone_number",
			"visitor_type",
			"vehicle_plate",
			"expected_arrival_date",
			"expected_arrival_time",
			"validity_type",
			"valid_until",
			"status",
			"property_unit",
			"lease",
			"check_in_time",
			"checked_in_by",
			"check_out_time",
			"qr_payload",
			"notes",
			"creation",
		]

		passes = frappe.get_all(
			"Visitor Gate Pass",
			filters=filters,
			fields=fields,
			order_by="creation desc",
			limit_start=offset,
			limit_page_length=page_len,
		)

		# Compute status counts for the user
		all_user_passes = frappe.get_all(
			"Visitor Gate Pass",
			filters={"tenant": current_user},
			fields=["status"],
		)
		counts = {
			"total": len(all_user_passes),
			"active": sum(1 for p in all_user_passes if p.status == "Active"),
			"checked_in": sum(1 for p in all_user_passes if p.status == "Checked In"),
			"expired": sum(1 for p in all_user_passes if p.status == "Expired"),
			"cancelled": sum(1 for p in all_user_passes if p.status == "Cancelled"),
		}

		return {
			"status": "success",
			"summary": counts,
			"page": page_num,
			"page_length": page_len,
			"passes": passes,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_tenant_visitor_passes")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_visitor_pass_details(pass_id=None):
	"""Get complete details and verification payload for a specific visitor pass."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"pass_id": pass_id})
		target_id = (payload.get("pass_id") or "").strip()

		if not target_id:
			return {"status": "error", "message": "pass_id is required"}

		if not frappe.db.exists("Visitor Gate Pass", target_id):
			return {"status": "error", "message": f"Visitor pass {target_id} not found"}

		doc = frappe.get_doc("Visitor Gate Pass", target_id)

		# Access check: Tenant owner, or Staff / System Manager
		current_user = frappe.session.user
		user_roles = frappe.get_roles(current_user)
		is_staff = any(r in user_roles for r in ["System Manager", "Mobile Maintenance Manager", "Mobile Maintenance Officer", "Property Manager"])

		if not is_staff and doc.tenant != current_user:
			frappe.throw(_("Not permitted to view this pass"), frappe.PermissionError)

		return {
			"status": "success",
			"pass": doc.as_dict(),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_visitor_pass_details")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def cancel_visitor_pass(pass_id=None):
	"""Cancel an active visitor pass prior to check-in."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"pass_id": pass_id})
		target_id = (payload.get("pass_id") or "").strip()

		if not target_id:
			return {"status": "error", "message": "pass_id is required"}

		if not frappe.db.exists("Visitor Gate Pass", target_id):
			return {"status": "error", "message": f"Visitor pass {target_id} not found"}

		doc = frappe.get_doc("Visitor Gate Pass", target_id)

		current_user = frappe.session.user
		user_roles = frappe.get_roles(current_user)
		is_staff = any(r in user_roles for r in ["System Manager", "Mobile Maintenance Manager", "Mobile Maintenance Officer", "Property Manager"])

		if not is_staff and doc.tenant != current_user:
			frappe.throw(_("Not permitted to cancel this pass"), frappe.PermissionError)

		if doc.status == "Checked In":
			return {"status": "error", "message": "Cannot cancel a pass that is already Checked In."}
		if doc.status == "Cancelled":
			return {"status": "error", "message": "Pass is already cancelled."}

		doc.status = "Cancelled"
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		# Publish real-time cancellation
		frappe.publish_realtime(
			event="gate_pass_cancelled",
			message={"pass_id": doc.name, "status": "Cancelled"},
			room=f"user:{doc.tenant}",
			after_commit=True,
		)
		frappe.publish_realtime(
			event="gate_pass_cancelled",
			message={"pass_id": doc.name, "status": "Cancelled"},
			room=f"gate_pass:{doc.name}",
			after_commit=True,
		)

		return {
			"status": "success",
			"message": "Visitor pass cancelled successfully",
			"pass_id": doc.name,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "cancel_visitor_pass")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def validate_and_checkin_visitor(pass_id=None, notes=None):
	"""Security / Staff endpoint: Validate QR scan & check in visitor with WebSocket & FCM push."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"pass_id": pass_id, "notes": notes})
		target_id = (payload.get("pass_id") or "").strip()

		# Support QR JSON string input (extract pass_id if raw JSON payload was scanned)
		if target_id.startswith("{") and "pass_id" in target_id:
			try:
				parsed = json.loads(target_id)
				target_id = parsed.get("pass_id", target_id)
			except Exception:
				pass

		if not target_id or not frappe.db.exists("Visitor Gate Pass", target_id):
			return {
				"status": "error",
				"message": f"Invalid or unrecognized Visitor QR Pass ({target_id}).",
			}

		doc = frappe.get_doc("Visitor Gate Pass", target_id)

		# 1. Validation checks
		if doc.status == "Cancelled":
			return {
				"status": "error",
				"message": "This visitor pass has been cancelled by the resident.",
				"pass_status": doc.status,
			}

		if doc.status == "Expired":
			return {
				"status": "error",
				"message": "This visitor pass has expired.",
				"pass_status": doc.status,
			}

		# Check date/valid_until expiration
		if doc.valid_until and get_datetime(doc.valid_until) < now_datetime():
			doc.status = "Expired"
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			return {
				"status": "error",
				"message": "This visitor pass has expired.",
				"pass_status": "Expired",
			}

		if doc.status == "Checked In":
			if doc.validity_type == "One-Time Entry":
				return {
					"status": "error",
					"message": f"One-Time pass was already checked in on {doc.check_in_time}.",
					"pass_status": doc.status,
					"check_in_time": str(doc.check_in_time),
				}

		# 2. Perform Check-In
		current_user = frappe.session.user
		guard_name = frappe.db.get_value("User", current_user, "full_name") or current_user

		doc.status = "Checked In"
		doc.check_in_time = now_datetime()
		doc.checked_in_by = current_user
		if payload.get("notes"):
			doc.notes = f"{doc.notes or ''}\n[Check-in note]: {payload.get('notes')}".strip()

		doc.save(ignore_permissions=True)
		frappe.db.commit()

		check_in_time_str = str(doc.check_in_time)

		# 3. Real-Time WebSocket Event (dispatches to user socket and room)
		tenant_user = doc.tenant
		websocket_payload = {
			"type": "visitor_arrived",
			"event": "visitor_arrived",
			"pass_id": doc.name,
			"visitor_name": doc.visitor_name,
			"visitor_type": doc.visitor_type,
			"property_unit": doc.property_unit,
			"vehicle_plate": doc.vehicle_plate or "",
			"check_in_time": check_in_time_str,
			"checked_in_by": guard_name,
			"status": "Checked In",
		}

		# Emit to user personal room and gate_pass room
		for evt_name in ["visitor_arrived", "visitor_checked_in"]:
			frappe.publish_realtime(
				event=evt_name,
				message=websocket_payload,
				user=tenant_user,
				room=f"user:{tenant_user}",
				after_commit=True,
			)
			frappe.publish_realtime(
				event=evt_name,
				message=websocket_payload,
				room=f"gate_pass:{doc.name}",
				after_commit=True,
			)

		# 4. In-App Notification Log (Frappe Standard Inbox/Bell)
		try:
			if tenant_user and tenant_user != "Guest":
				notif = frappe.new_doc("Notification Log")
				notif.for_user = tenant_user
				notif.type = "Alert"
				notif.document_type = "Visitor Gate Pass"
				notif.document_name = doc.name
				notif.subject = f"🔔 Visitor {doc.visitor_name} checked in at Gate"
				notif.email_content = f"{doc.visitor_name} ({doc.visitor_type}) has arrived and checked in at Viva Reception for Unit {doc.property_unit}."
				notif.insert(ignore_permissions=True)
		except Exception as notif_err:
			frappe.logger().warning(f"Notification Log insert failed: {notif_err}")

		# 5. Enqueue FCM Push Notification to Tenant's mobile devices
		push_title = f"🔔 Visitor Arrival: {doc.visitor_name}"
		vehicle_info = f" (Vehicle: {doc.vehicle_plate})" if doc.vehicle_plate else ""
		push_body = f"Your visitor {doc.visitor_name} ({doc.visitor_type}){vehicle_info} has arrived and checked in at Viva Reception."

		try:
			frappe.enqueue(
				"propms.api.v1.gate_pass.gate_pass.enqueue_gate_pass_push",
				queue="short",
				user=tenant_user,
				pass_id=doc.name,
				title=push_title,
				body=push_body,
				visitor_name=doc.visitor_name,
				property_unit=doc.property_unit,
				vehicle_plate=doc.vehicle_plate or "",
				event_type="visitor_arrived",
			)
		except Exception as fcm_err:
			frappe.logger().error(f"FCM Enqueue failed for gate pass {doc.name}: {fcm_err}")

		return {
			"status": "success",
			"message": f"Visitor {doc.visitor_name} successfully checked in!",
			"pass_id": doc.name,
			"visitor_name": doc.visitor_name,
			"visitor_type": doc.visitor_type,
			"property_unit": doc.property_unit,
			"check_in_time": check_in_time_str,
			"checked_in_by": guard_name,
			"doc": doc.as_dict(),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "validate_and_checkin_visitor")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def checkout_visitor(pass_id=None):
	"""Security / Staff endpoint: Check out visitor leaving premises."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({"pass_id": pass_id})
		target_id = (payload.get("pass_id") or "").strip()

		if not target_id or not frappe.db.exists("Visitor Gate Pass", target_id):
			return {"status": "error", "message": f"Visitor pass {target_id} not found"}

		doc = frappe.get_doc("Visitor Gate Pass", target_id)

		doc.status = "Checked Out"
		doc.check_out_time = now_datetime()
		doc.save(ignore_permissions=True)
		frappe.db.commit()

		tenant_user = doc.tenant
		check_out_payload = {
			"type": "visitor_departed",
			"event": "visitor_departed",
			"pass_id": doc.name,
			"visitor_name": doc.visitor_name,
			"property_unit": doc.property_unit,
			"check_out_time": str(doc.check_out_time),
			"status": "Checked Out",
		}

		# Publish WebSocket events
		for evt_name in ["visitor_departed", "visitor_checked_out"]:
			frappe.publish_realtime(
				event=evt_name,
				message=check_out_payload,
				user=tenant_user,
				room=f"user:{tenant_user}",
				after_commit=True,
			)
			frappe.publish_realtime(
				event=evt_name,
				message=check_out_payload,
				room=f"gate_pass:{doc.name}",
				after_commit=True,
			)

		return {
			"status": "success",
			"message": f"Visitor {doc.visitor_name} checked out successfully",
			"pass_id": doc.name,
			"check_out_time": str(doc.check_out_time),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "checkout_visitor")
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def enqueue_gate_pass_push(
	user,
	pass_id,
	title,
	body,
	visitor_name=None,
	property_unit=None,
	vehicle_plate=None,
	event_type="visitor_arrived",
):
	"""Send FCM push notification for Gate Pass events with rich data payload."""
	try:
		if not user:
			return {"status": "no_user"}

		tokens = frappe.get_all(
			"User Device",
			filters={"user": user},
			pluck="token",
		)
		if not tokens:
			frappe.logger().warning(f"📲 FCM Gate Pass: No device tokens for user {user}")
			return {"status": "no_tokens", "user": user}

		data = {
			"type": event_type or "visitor_arrived",
			"notification_type": event_type or "visitor_arrived",
			"pass_id": str(pass_id),
			"visitor_name": str(visitor_name or ""),
			"property_unit": str(property_unit or ""),
			"vehicle_plate": str(vehicle_plate or ""),
			"user": str(user),
		}

		from propms.api.v1.utils.fcm import send_to_tokens

		result = send_to_tokens(
			tokens=tokens,
			data=data,
			title=title,
			body=body,
		)
		return {"status": "success", "result": result}
	except Exception as e:
		frappe.logger().error(f"❌ FCM Gate Pass Error for {user}: {e}")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_security_gate_passes(date=None, status=None, search=None, page=1, page_length=50):
	"""Security / Reception endpoint: View and search gate passes."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload({
			"date": date,
			"status": status,
			"search": search,
			"page": page,
			"page_length": page_length,
		})

		query_date = payload.get("date") or nowdate()
		target_status = (payload.get("status") or "all").strip().lower()
		search_term = (payload.get("search") or "").strip()
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 50)))
		offset = (page_num - 1) * page_len

		# Auto-expire overdue passes
		auto_expire_overdue_passes()

		filters = {}
		if query_date and query_date != "all":
			filters["expected_arrival_date"] = query_date

		if target_status and target_status != "all":
			if target_status == "active":
				filters["status"] = "Active"
			elif target_status in ["checked_in", "checked in"]:
				filters["status"] = "Checked In"
			elif target_status == "expired":
				filters["status"] = "Expired"
			elif target_status == "cancelled":
				filters["status"] = "Cancelled"

		or_filters = None
		if search_term:
			or_filters = [
				["Visitor Gate Pass", "visitor_name", "like", f"%{search_term}%"],
				["Visitor Gate Pass", "phone_number", "like", f"%{search_term}%"],
				["Visitor Gate Pass", "vehicle_plate", "like", f"%{search_term}%"],
				["Visitor Gate Pass", "property_unit", "like", f"%{search_term}%"],
				["Visitor Gate Pass", "name", "like", f"%{search_term}%"],
			]

		fields = [
			"name",
			"visitor_name",
			"phone_number",
			"visitor_type",
			"vehicle_plate",
			"expected_arrival_date",
			"expected_arrival_time",
			"validity_type",
			"valid_until",
			"status",
			"property_unit",
			"lease",
			"tenant",
			"tenant_name",
			"check_in_time",
			"checked_in_by",
			"check_out_time",
			"notes",
			"creation",
		]

		passes = frappe.get_all(
			"Visitor Gate Pass",
			filters=filters,
			or_filters=or_filters,
			fields=fields,
			order_by="creation desc",
			limit_start=offset,
			limit_page_length=page_len,
		)

		return {
			"status": "success",
			"date": query_date,
			"page": page_num,
			"page_length": page_len,
			"passes": passes,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_security_gate_passes")
		return {"status": "error", "message": str(e)}


def auto_expire_overdue_passes():
	"""Background / On-demand task: Expire active visitor passes whose valid_until is in the past."""
	try:
		now_ts = now_datetime()
		overdue_passes = frappe.get_all(
			"Visitor Gate Pass",
			filters={"status": "Active", "valid_until": ["<", now_ts]},
			pluck="name",
		)
		for p_name in overdue_passes or []:
			frappe.db.set_value("Visitor Gate Pass", p_name, "status", "Expired", update_modified=True)
		if overdue_passes:
			frappe.db.commit()
	except Exception as e:
		frappe.logger().error(f"Error in auto_expire_overdue_passes: {e}")
