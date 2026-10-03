# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import re

import frappe
from frappe import _
from frappe.model.document import Document

ALLOWED_DEPARTMENTS = (
	"Management Office",
	"Reception",
	"Security",
	"Maintenance",
	"Emergency",
	"Leasing",
)

# Digits and optional leading + only (spaces stripped for storage)
_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


def _normalize_phone(value):
	if not value:
		return ""
	cleaned = re.sub(r"[\s\-()]", "", str(value).strip())
	return cleaned


class BuildingContact(Document):
	def validate(self):
		if self.department and self.department not in ALLOWED_DEPARTMENTS:
			frappe.throw(
				_("Department must be one of: {0}").format(", ".join(ALLOWED_DEPARTMENTS))
			)

		self.phone_number = _normalize_phone(self.phone_number)
		if not self.phone_number or not _PHONE_RE.match(self.phone_number):
			frappe.throw(
				_("Phone Number must be dialable (digits / + only), e.g. +255700999111")
			)

		if self.whatsapp_number:
			self.whatsapp_number = _normalize_phone(self.whatsapp_number)
			if self.whatsapp_number and not _PHONE_RE.match(self.whatsapp_number):
				frappe.throw(
					_("WhatsApp Number must be dialable (digits / + only), e.g. +255700999111")
				)

		if self.department == "Emergency":
			self.is_emergency = 1

		if self.priority_order is None:
			self.priority_order = 10
