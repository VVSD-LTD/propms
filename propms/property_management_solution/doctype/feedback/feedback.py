# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class Feedback(Document):
	def validate(self):
		if not self.tenant and frappe.session.user != "Guest":
			self.tenant = frappe.session.user
		if self.tenant and not self.tenant_name:
			self.tenant_name = (
				frappe.db.get_value("User", self.tenant, "full_name") or self.tenant
			)
		if not self.status:
			self.status = "Open"

		# Strip HTML-ish empty message from Text Editor
		if self.message and not frappe.utils.strip_html(self.message or "").strip():
			frappe.throw("Message is required")

		# Normalize legacy statuses → Open / Closed only
		legacy = {
			"In Progress": "Open",
			"Resolved": "Closed",
			"Cancelled": "Closed",
			"Canceled": "Closed",
		}
		if self.status in legacy:
			self.status = legacy[self.status]
		if self.status not in ("Open", "Closed"):
			self.status = "Open"

		if self.status == "Closed" and not self.resolved_on:
			self.resolved_on = now_datetime()
			if not self.resolved_by:
				self.resolved_by = frappe.session.user

	def after_insert(self):
		# Desk create (API sets skip_feedback_notify and notifies itself)
		if getattr(self.flags, "skip_feedback_notify", False):
			return
		try:
			from propms.api.v1.feedback.notify import notify_feedback_created

			notify_feedback_created(self)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Feedback.after_insert.notify")

	def on_update(self):
		# Attach File docs to this Feedback when URLs are present
		for row in self.attachments or []:
			if not row.file_url:
				continue
			exists = frappe.db.exists(
				"File",
				{
					"file_url": row.file_url,
					"attached_to_doctype": "Feedback",
					"attached_to_name": self.name,
				},
			)
			if exists:
				continue
			# Relink orphan public file if possible
			file_name = frappe.db.get_value("File", {"file_url": row.file_url}, "name")
			if file_name:
				frappe.db.set_value(
					"File",
					file_name,
					{
						"attached_to_doctype": "Feedback",
						"attached_to_name": self.name,
					},
					update_modified=False,
				)

		if getattr(self.flags, "skip_feedback_notify", False):
			return

		status_changed = self.has_value_changed("status")
		reply_changed = self.has_value_changed("staff_reply")
		if not (status_changed or reply_changed):
			return

		prev = self.get_doc_before_save()
		prev_status = prev.status if prev else None
		try:
			from propms.api.v1.feedback.notify import notify_feedback_status_or_reply

			notify_feedback_status_or_reply(self, previous_status=prev_status)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Feedback.on_update.notify")
