# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now


DOCTYPE = "Mobile Notifications"
PARENTTYPE = "Mobile Notifications"


def _emails_from_lease(lease_name: str) -> list[str]:
	if not lease_name:
		return []
	doc = frappe.get_doc("Lease", lease_name)
	out: list[str] = []
	for row in doc.get("custom_tenant_details") or []:
		if not cint(getattr(row, "enabled", 1)):
			continue
		em = (getattr(row, "user_email", None) or "").strip()
		if em:
			out.append(em)
	return out


def _unique_preserve(seq: list[str]) -> list[str]:
	seen = set()
	out = []
	for x in seq:
		if x not in seen:
			seen.add(x)
			out.append(x)
	return out


def _resolve_recipient_emails(doc) -> list[str]:
	"""Load tenant app users from Active leases (Tenant Details).

	Rules (AND when multiple are set):
	- Company: Lease.company matches → all tenants on those leases.
	- Property: Lease.property matches → all tenants on those leases.
	- Customer: matches Lease.lease_customer (leased customer on Lease) → all tenants on those leases.
	"""
	company = getattr(doc, "company", None) or None
	property_name = getattr(doc, "property", None) or None
	customer = getattr(doc, "customer", None) or None

	if not company and not property_name and not customer:
		return []

	filters: dict = {"lease_status": "Active"}
	if company:
		filters["company"] = company
	if property_name:
		filters["property"] = property_name
	if customer:
		filters["lease_customer"] = customer

	leases = frappe.get_all("Lease", filters=filters, pluck="name")
	emails: list[str] = []
	for ln in leases:
		emails.extend(_emails_from_lease(ln))
	return _unique_preserve(emails)


class MobileNotifications(Document):
	def before_save(self):
		self.fetch_users()

	def before_submit(self):
		if not self.recipients:
			frappe.throw(
				_(
					"No recipients. Set Company and/or Property and/or Customer to load tenants from matching "
					"Active leases (Tenant Details), or add Recipient(s) manually before submitting."
				)
			)
		self.send_notification()

	def fetch_users(self):
		emails = _resolve_recipient_emails(self)
		if not emails:
			return
		existing = {d.tenant for d in (self.recipients or []) if getattr(d, "tenant", None)}
		for em in emails:
			if em not in existing:
				self.append("recipients", {"tenant": em})
				existing.add(em)

	def send_notification(self):
		try:
			if not self.recipients:
				frappe.logger().warning(f"Mobile Notifications: no recipients for {self.name}")
				return

			unique_recipients = {d.tenant for d in self.recipients if d.tenant}
			if not unique_recipients:
				return

			title = self.subject
			body = self.message or ""

			self.send_websocket_notifications(unique_recipients)
			self.send_fcm_notifications(unique_recipients, title, body)
			self.delivery = "Sent"
		except Exception:
			frappe.logger().error(frappe.get_traceback())
			self.delivery = "Failed"

	def send_fcm_notifications(self, unique_recipients, title, body):
		if len(body) > 120:
			body = body[:117] + "..."
		for recipient_user in unique_recipients:
			try:
				frappe.enqueue(
					"propms.api.v1.notifications.notifications.enqueue_app_notification_push",
					queue="short",
					user=recipient_user,
					notification_id=self.name,
					title=title,
					body=body,
				)
			except Exception:
				frappe.logger().error(frappe.get_traceback())
				continue

	def send_websocket_notifications(self, unique_recipients):
		notification_data = {
			"type": "notification",
			"notification_id": self.name,
			"subject": self.subject,
			"message": self.message,
			"company": self.company or "",
			"property": self.property or "",
			"customer": self.customer or "",
			"lease_customer": self.customer or "",
			"timestamp": now(),
			"read_status": "Unread",
		}
		for user_email in unique_recipients:
			try:
				frappe.publish_realtime(
					event="notification_received",
					message=notification_data,
					room=f"user:{user_email}",
					after_commit=True,
				)
			except Exception:
				frappe.logger().error(frappe.get_traceback())
				continue


@frappe.whitelist()
def get_user_notifications(limit=20, offset=0):
	"""Notifications for current user; payload fields match Mobile Notifications DocType."""
	try:
		user_email = frappe.session.user
		if user_email == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		try:
			limit = int(limit) if limit is not None else 20
			offset = int(offset) if offset is not None else 0
		except (ValueError, TypeError):
			limit = 20
			offset = 0

		notifications = frappe.db.sql(
			"""
			SELECT DISTINCT n.name, n.subject, n.message, n.company, n.property,
				n.customer, n.creation, n.sender,
				COALESCE(n.category, 'Notice') AS category,
				COALESCE(n.target_audience, 'Everyone') AS target_audience
			FROM `tabMobile Notifications` n
			INNER JOIN `tabNotified Users` nu
				ON nu.parent = n.name AND nu.parenttype = %s
			WHERE nu.tenant = %s AND n.docstatus = 1
			ORDER BY n.creation DESC
			LIMIT %s OFFSET %s
			""",
			(PARENTTYPE, user_email, limit, offset),
			as_dict=True,
		)

		for notification in notifications:
			recipient_data = frappe.db.sql(
				"""
				SELECT read_status, read_at
				FROM `tabNotified Users`
				WHERE parent = %s AND parenttype = %s AND tenant = %s
				""",
				(notification.name, PARENTTYPE, user_email),
				as_dict=True,
			)
			if recipient_data:
				notification["read_status"] = recipient_data[0].get("read_status", "Unread")
				notification["read_at"] = recipient_data[0].get("read_at")
			else:
				notification["read_status"] = "Unread"
				notification["read_at"] = None

			notification["attachments"] = frappe.get_all(
				"Notifications Attachment",
				filters={"parent": notification.name, "parenttype": PARENTTYPE},
				fields=["title", "attachment"],
				order_by="idx asc",
			)

		count_row = frappe.db.sql(
			"""
			SELECT COUNT(DISTINCT n.name) AS total_count
			FROM `tabMobile Notifications` n
			INNER JOIN `tabNotified Users` nu
				ON nu.parent = n.name AND nu.parenttype = %s
			WHERE nu.tenant = %s AND n.docstatus = 1
			""",
			(PARENTTYPE, user_email),
			as_dict=True,
		)
		total_count = count_row[0].total_count if count_row else 0

		return {
			"status": "success",
			"notifications": notifications,
			"total_count": total_count,
			"has_more": (offset + limit) < total_count,
		}
	except Exception as e:
		frappe.logger().error(frappe.get_traceback())
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def mark_as_read(notification_id):
	"""Mark a Mobile Notification as read for the current user (child row: Notified Users.tenant)."""
	try:
		user_email = frappe.session.user
		if user_email == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		notification = frappe.get_doc(DOCTYPE, notification_id)
		user_in_recipients = any(getattr(r, "tenant", None) == user_email for r in (notification.recipients or []))
		if not user_in_recipients:
			return {"status": "error", "message": "Access denied"}

		existing = frappe.db.sql(
			"""
			SELECT read_status, read_at
			FROM `tabNotified Users`
			WHERE parent = %s AND parenttype = %s AND tenant = %s
			""",
			(notification_id, PARENTTYPE, user_email),
			as_dict=True,
		)
		if existing and existing[0].get("read_status") == "Read":
			return {
				"status": "success",
				"message": "Notification was already marked as read",
				"read_at": existing[0].get("read_at"),
				"already_read": True,
			}

		frappe.db.sql(
			"""
			UPDATE `tabNotified Users`
			SET read_status = 'Read', read_at = %s
			WHERE parent = %s AND parenttype = %s AND tenant = %s
			""",
			(now(), notification_id, PARENTTYPE, user_email),
		)

		return {
			"status": "success",
			"message": "Notification marked as read",
			"read_at": now(),
			"already_read": False,
		}
	except Exception as e:
		frappe.logger().error(frappe.get_traceback())
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def get_notification_details(notification_id):
	"""Full detail for one notification."""
	try:
		user_email = frappe.session.user
		if user_email == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		notification = frappe.get_doc(DOCTYPE, notification_id)
		user_in_recipients = any(getattr(r, "tenant", None) == user_email for r in (notification.recipients or []))
		if not user_in_recipients:
			return {"status": "error", "message": "Access denied"}

		attachments = frappe.get_all(
			"Notifications Attachment",
			filters={"parent": notification.name, "parenttype": PARENTTYPE},
			fields=["title", "attachment"],
			order_by="idx asc",
		)

		recipient_data = frappe.db.sql(
			"""
			SELECT read_status, read_at
			FROM `tabNotified Users`
			WHERE parent = %s AND parenttype = %s AND tenant = %s
			""",
			(notification_id, PARENTTYPE, user_email),
			as_dict=True,
		)
		read_status = "Unread"
		read_at = None
		if recipient_data:
			read_status = recipient_data[0].get("read_status", "Unread")
			read_at = recipient_data[0].get("read_at")

		notification_data = {
			"name": notification.name,
			"subject": notification.subject,
			"message": notification.message,
			"company": notification.company,
			"property": notification.property,
			"customer": notification.customer,
			"creation": notification.creation,
			"sender": notification.sender,
			"attachments": attachments,
			"read_status": read_status,
			"read_at": read_at,
		}

		return {"status": "success", "notification": notification_data}
	except Exception as e:
		frappe.logger().error(frappe.get_traceback())
		return {"status": "error", "message": str(e)}
