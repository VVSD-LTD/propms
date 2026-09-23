# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

"""Staff APIs to create/submit/list Mobile Notifications from the mobile app."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint


DOCTYPE = "Mobile Notifications"
NOTIFICATION_STAFF_ROLES = (
	"Mobile Maintenance Manager",
	"Mobile Maintenance Officer",
)


def _require_notification_staff():
	"""Manager / Officer / System Manager only (not Technician / Tenant)."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)

	roles = frappe.get_roles(frappe.session.user)
	if "System Manager" in roles:
		return "System Manager"
	for role in NOTIFICATION_STAFF_ROLES:
		if role in roles:
			return role

	frappe.throw(_("Not permitted"), frappe.PermissionError)


def _default_company():
	"""Single-company install: use user default or first Company."""
	company = frappe.defaults.get_user_default("Company")
	if company and frappe.db.exists("Company", company):
		return company
	return frappe.db.get_value("Company", {}, "name", order_by="creation asc")


def _parse_attachments(attachments):
	"""Normalize attachments from list or JSON string -> list[dict]."""
	if attachments is None or attachments == "":
		return []
	if isinstance(attachments, str):
		try:
			attachments = json.loads(attachments)
		except Exception:
			frappe.throw(_("Invalid attachments JSON"))
	if not isinstance(attachments, (list, tuple)):
		frappe.throw(_("attachments must be a list"))
	out = []
	for row in attachments:
		if not isinstance(row, dict):
			frappe.throw(_("Each attachment must be an object with title and attachment"))
		title = (row.get("title") or "").strip()
		file_url = (row.get("attachment") or row.get("file_url") or "").strip()
		if not title or not file_url:
			frappe.throw(_("Each attachment requires title and attachment (file URL)"))
		out.append({"title": title, "attachment": file_url})
	return out


def _properties_for_customer(customer: str) -> list[str]:
	"""Property names on Active leases for this Customer (lease_customer)."""
	if not customer:
		return []
	return frappe.get_all(
		"Lease",
		filters={"lease_status": "Active", "lease_customer": customer},
		pluck="property",
		distinct=True,
	)


def _recipient_count(name: str) -> int:
	return cint(
		frappe.db.count(
			"Notified Users",
			{"parent": name, "parenttype": DOCTYPE},
		)
	)


def _serialize_preview(doc):
	recipients = [
		{"tenant": r.tenant, "read_status": getattr(r, "read_status", None) or "Unread"}
		for r in (doc.recipients or [])
		if getattr(r, "tenant", None)
	]
	attachments = [
		{"title": a.title, "attachment": a.attachment}
		for a in (doc.attachment or [])
		if getattr(a, "attachment", None)
	]
	return {
		"status": "success",
		"notification_id": doc.name,
		"docstatus": doc.docstatus,
		"subject": doc.subject,
		"message": doc.message,
		"company": doc.company,
		"property": doc.property,
		"customer": doc.customer,
		"category": getattr(doc, "category", None) or "Notice",
		"target_audience": getattr(doc, "target_audience", None) or "Everyone",
		"delivery": getattr(doc, "delivery", None) or "",
		"creation": str(doc.creation) if doc.creation else None,
		"sender": doc.sender,
		"recipient_count": len(recipients),
		"recipients": recipients,
		"attachments": attachments,
	}


def _serialize_list_row(row):
	return {
		"notification_id": row.name,
		"subject": row.subject,
		"message": row.message,
		"customer": row.customer,
		"property": row.property,
		"category": row.category or "Notice",
		"docstatus": row.docstatus,
		"delivery": row.delivery or "",
		"sender": row.sender,
		"creation": str(row.creation) if row.creation else None,
		"recipient_count": _recipient_count(row.name),
		"status_label": (
			"Draft"
			if row.docstatus == 0
			else ("Cancelled" if row.docstatus == 2 else (row.delivery or "Submitted"))
		),
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_notification_customers(search=None, limit=50, offset=0, **kwargs):
	"""Customers that have at least one Active lease — for staff dropdown."""
	_require_notification_staff()
	kwargs.pop("cmd", None)

	limit = cint(limit) or 50
	offset = cint(offset) or 0
	search = (search or "").strip()

	params = {"limit": limit, "offset": offset}
	where = ["l.lease_status = 'Active'", "l.lease_customer IS NOT NULL", "l.lease_customer != ''"]
	if search:
		where.append("(l.lease_customer LIKE %(search)s OR c.customer_name LIKE %(search)s)")
		params["search"] = f"%{search}%"

	sql = f"""
		SELECT DISTINCT
			l.lease_customer AS name,
			COALESCE(c.customer_name, l.lease_customer) AS customer_name
		FROM `tabLease` l
		LEFT JOIN `tabCustomer` c ON c.name = l.lease_customer
		WHERE {" AND ".join(where)}
		ORDER BY customer_name ASC
		LIMIT %(limit)s OFFSET %(offset)s
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)

	count_sql = f"""
		SELECT COUNT(DISTINCT l.lease_customer) AS total
		FROM `tabLease` l
		LEFT JOIN `tabCustomer` c ON c.name = l.lease_customer
		WHERE {" AND ".join(where)}
	"""
	total = cint(frappe.db.sql(count_sql, params, as_dict=True)[0].total)

	return {
		"status": "success",
		"customers": [{"name": r.name, "customer_name": r.customer_name} for r in rows],
		"total_count": total,
		"has_more": (offset + limit) < total,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_notification_properties(customer=None, search=None, limit=50, offset=0, **kwargs):
	"""Properties for a Customer (Active leases) — for staff dropdown after Customer."""
	_require_notification_staff()
	kwargs.pop("cmd", None)

	if frappe.request and getattr(frappe.request, "is_json", False):
		payload = frappe.request.get_json(silent=True) or {}
		customer = customer if customer is not None else payload.get("customer")
		search = search if search is not None else payload.get("search")

	customer = (customer or "").strip()
	if not customer:
		return {"status": "error", "message": "customer is required"}
	if not frappe.db.exists("Customer", customer):
		return {"status": "error", "message": f"Invalid customer: {customer}"}

	limit = cint(limit) or 50
	offset = cint(offset) or 0
	search = (search or "").strip()

	params = {"customer": customer, "limit": limit, "offset": offset}
	where = ["l.lease_status = 'Active'", "l.lease_customer = %(customer)s", "l.property IS NOT NULL"]
	if search:
		where.append("(l.property LIKE %(search)s OR p.name1 LIKE %(search)s)")
		params["search"] = f"%{search}%"

	sql = f"""
		SELECT DISTINCT
			l.property AS name,
			COALESCE(p.name1, l.property) AS property_name
		FROM `tabLease` l
		LEFT JOIN `tabProperty` p ON p.name = l.property
		WHERE {" AND ".join(where)}
		ORDER BY property_name ASC
		LIMIT %(limit)s OFFSET %(offset)s
	"""
	rows = frappe.db.sql(sql, params, as_dict=True)

	count_sql = f"""
		SELECT COUNT(DISTINCT l.property) AS total
		FROM `tabLease` l
		LEFT JOIN `tabProperty` p ON p.name = l.property
		WHERE {" AND ".join(where)}
	"""
	total = cint(frappe.db.sql(count_sql, params, as_dict=True)[0].total)

	return {
		"status": "success",
		"customer": customer,
		"properties": [{"name": r.name, "property_name": r.property_name} for r in rows],
		"total_count": total,
		"has_more": (offset + limit) < total,
	}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def property_link_query(doctype, txt, searchfield, start, page_len, filters):
	"""Desk Link query: Property filtered by selected Customer (Active leases)."""
	filters = filters or {}
	customer = filters.get("customer")
	if not customer:
		return []

	return frappe.db.sql(
		"""
		SELECT DISTINCT l.property, COALESCE(p.name1, l.property)
		FROM `tabLease` l
		LEFT JOIN `tabProperty` p ON p.name = l.property
		WHERE l.lease_status = 'Active'
			AND l.lease_customer = %(customer)s
			AND l.property IS NOT NULL
			AND (l.property LIKE %(txt)s OR IFNULL(p.name1, '') LIKE %(txt)s)
		ORDER BY COALESCE(p.name1, l.property) ASC
		LIMIT %(start)s, %(page_len)s
		""",
		{
			"customer": customer,
			"txt": f"%{txt}%",
			"start": cint(start),
			"page_len": cint(page_len),
		},
	)


@frappe.whitelist(methods=["GET", "POST"])
def list_staff_notifications(status="all", limit=20, offset=0, **kwargs):
	"""List Mobile Notifications for staff (drafts + submitted).

	status: all | draft | submitted
	"""
	_require_notification_staff()
	kwargs.pop("cmd", None)

	if frappe.request and getattr(frappe.request, "is_json", False):
		payload = frappe.request.get_json(silent=True) or {}
		status = status if status is not None else payload.get("status")
		limit = limit if limit is not None else payload.get("limit")
		offset = offset if offset is not None else payload.get("offset")

	limit = cint(limit) or 20
	offset = cint(offset) or 0
	status = (status or "all").strip().lower()

	filters = {}
	if status == "draft":
		filters["docstatus"] = 0
	elif status == "submitted":
		filters["docstatus"] = 1
	elif status not in ("all", ""):
		return {"status": "error", "message": "status must be all, draft, or submitted"}

	rows = frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"subject",
			"message",
			"customer",
			"property",
			"category",
			"docstatus",
			"delivery",
			"sender",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=limit,
		limit_start=offset,
	)
	total = cint(frappe.db.count(DOCTYPE, filters))

	return {
		"status": "success",
		"notifications": [_serialize_list_row(r) for r in rows],
		"total_count": total,
		"has_more": (offset + limit) < total,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_staff_notification(notification_id=None, **kwargs):
	"""Full detail of one Mobile Notification for staff (includes recipients)."""
	_require_notification_staff()
	kwargs.pop("cmd", None)

	if frappe.request and getattr(frappe.request, "is_json", False):
		payload = frappe.request.get_json(silent=True) or {}
		notification_id = notification_id or payload.get("notification_id")

	notification_id = (notification_id or "").strip()
	if not notification_id:
		return {"status": "error", "message": "notification_id is required"}
	if not frappe.db.exists(DOCTYPE, notification_id):
		return {"status": "error", "message": "Notification not found"}

	doc = frappe.get_doc(DOCTYPE, notification_id)
	return _serialize_preview(doc)


@frappe.whitelist(methods=["POST"])
def create_notification(
	subject=None,
	message=None,
	company=None,
	property=None,
	customer=None,
	category=None,
	target_audience=None,
	target_floor=None,
	target_unit=None,
	attachments=None,
	**kwargs,
):
	"""Create a draft Mobile Notification and return resolved recipients for preview.

	Mobile UX: pick Customer (required), optional Property (must belong to Customer).
	Company is auto-filled (single-company) — clients should not send/show it.
	"""
	_require_notification_staff()
	kwargs.pop("cmd", None)

	if frappe.request and getattr(frappe.request, "is_json", False):
		payload = frappe.request.get_json(silent=True) or {}
		subject = subject if subject is not None else payload.get("subject")
		message = message if message is not None else payload.get("message")
		company = company if company is not None else payload.get("company")
		property = property if property is not None else payload.get("property")
		customer = customer if customer is not None else payload.get("customer")
		category = category if category is not None else payload.get("category")
		target_audience = (
			target_audience if target_audience is not None else payload.get("target_audience")
		)
		target_floor = target_floor if target_floor is not None else payload.get("target_floor")
		target_unit = target_unit if target_unit is not None else payload.get("target_unit")
		attachments = attachments if attachments is not None else payload.get("attachments")

	subject = (subject or "").strip()
	message = (message or "").strip()
	property = (property or "").strip() or None
	customer = (customer or "").strip() or None
	# Ignore client company; always use default (single-company)
	company = _default_company()

	if not subject:
		return {"status": "error", "message": "subject is required"}
	if not message:
		return {"status": "error", "message": "message is required"}
	if not customer:
		return {"status": "error", "message": "customer is required"}

	if not frappe.db.exists("Customer", customer):
		return {
			"status": "error",
			"message": f"Invalid customer: {customer} (must be Customer document name)",
		}

	if property:
		if not frappe.db.exists("Property", property):
			return {"status": "error", "message": f"Invalid property: {property}"}
		allowed = _properties_for_customer(customer)
		if property not in allowed:
			return {
				"status": "error",
				"message": "Property does not belong to the selected customer (Active lease)",
			}

	att_rows = _parse_attachments(attachments)

	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"subject": subject,
			"message": message,
			"company": company,
			"property": property,
			"customer": customer,
			"category": category or "Notice",
			"target_audience": target_audience or "Everyone",
			"target_floor": target_floor,
			"target_unit": target_unit,
			"sender": frappe.session.user,
		}
	)
	for row in att_rows:
		doc.append("attachment", row)

	doc.insert(ignore_permissions=True)
	doc.reload()

	if not doc.recipients:
		frappe.delete_doc(DOCTYPE, doc.name, force=1, ignore_permissions=True)
		return {
			"status": "error",
			"message": "No recipients for these filters",
		}

	return _serialize_preview(doc)


@frappe.whitelist(methods=["POST"])
def submit_notification(notification_id=None, **kwargs):
	"""Submit a draft Mobile Notification (fires WebSocket + FCM via DocType hooks)."""
	_require_notification_staff()
	kwargs.pop("cmd", None)

	if frappe.request and getattr(frappe.request, "is_json", False):
		payload = frappe.request.get_json(silent=True) or {}
		notification_id = notification_id or payload.get("notification_id")

	notification_id = (notification_id or "").strip()
	if not notification_id:
		return {"status": "error", "message": "notification_id is required"}

	if not frappe.db.exists(DOCTYPE, notification_id):
		return {"status": "error", "message": "Notification not found"}

	doc = frappe.get_doc(DOCTYPE, notification_id)
	if doc.docstatus != 0:
		return {
			"status": "error",
			"message": "Notification is already submitted",
		}

	doc.flags.ignore_permissions = True
	doc.submit()
	doc.reload()

	return {
		"status": "success",
		"notification_id": doc.name,
		"docstatus": doc.docstatus,
		"delivery": getattr(doc, "delivery", None) or "",
		"recipient_count": len(doc.recipients or []),
	}
