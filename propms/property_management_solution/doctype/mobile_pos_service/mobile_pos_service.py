# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint


class MobilePOSService(Document):
	def validate(self):
		mode = (self.purchase_mode or "amount").strip()
		if mode not in ("amount", "qty"):
			frappe.throw(frappe._("Invalid Purchase Mode: {0}").format(mode))

		handler = (self.handler or "Other").strip()
		if handler == "Generic":
			handler = "Other"
			self.handler = "Other"

		if mode == "qty":
			self.handler = None
			self.set("items", [])
			if not (self.item or "").strip():
				frappe.throw(frappe._("Item is required for qty Purchase Mode on {0}").format(self.title))
			if cint(self.requires_delivery_window):
				if not self.delivery_open_time or not self.delivery_close_time:
					frappe.throw(
						frappe._("Delivery Open/Close times are required when Requires Delivery Window is set")
					)
			else:
				self.delivery_open_time = None
				self.delivery_close_time = None
			return

		# amount mode
		self.item = None
		self.requires_delivery_window = 0
		self.delivery_open_time = None
		self.delivery_close_time = None
		self.handler = handler or "Other"

		rows = [r for r in (self.get("items") or []) if cint(r.enabled) and (r.item or "").strip()]
		if not rows:
			frappe.throw(
				frappe._("Add at least one enabled Item on Mobile POS Service {0}").format(self.title)
			)

		if handler == "Electricity":
			tariffs = set()
			for r in rows:
				tariff = (r.trackspm_tariff or "").strip()
				if tariff not in ("t1", "t2"):
					frappe.throw(
						frappe._(
							"Electricity items must set TrackSPM Tariff (t1 or t2). Item {0} is missing a tariff."
						).format(r.item)
					)
				tariffs.add(tariff)
			if "t1" not in tariffs and "t2" not in tariffs:
				frappe.throw(frappe._("Electricity service needs at least one t1 or t2 item"))
