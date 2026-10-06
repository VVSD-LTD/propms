# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe.model.document import Document


class MobilePOSStoreService(Document):
	def validate(self):
		stype = (self.service_type or "Item").strip()
		if stype == "Amount":
			self.purchase_mode = "amount"
			self.item = None
			if not (self.amount_service or "").strip():
				frappe.throw(frappe._("Amount Service is required for Amount rows"))
			if not (self.label or "").strip() and frappe.db.exists("POS Amount Service", self.amount_service):
				self.label = frappe.db.get_value("POS Amount Service", self.amount_service, "title")
		elif stype == "Item":
			self.purchase_mode = "qty"
			self.amount_service = None
			if not (self.item or "").strip():
				frappe.throw(frappe._("Item is required for Item service rows"))
		elif stype in ("Electricity", "Special"):
			# Soft-migrate legacy rows on save
			self.service_type = "Amount"
			self.purchase_mode = "amount"
			self.item = None
			if not (self.amount_service or "").strip():
				if frappe.db.exists("POS Amount Service", "Electricity"):
					self.amount_service = "Electricity"
				else:
					frappe.throw(
						frappe._("Set Amount Service (e.g. Electricity) — Service Type Special/Electricity is retired")
					)
		else:
			frappe.throw(frappe._("Invalid Service Type: {0}").format(stype))
