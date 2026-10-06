# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint


class POSAmountService(Document):
	def validate(self):
		handler = (self.handler or "Other").strip()
		# Legacy value from older builds
		if handler == "Generic":
			handler = "Other"
			self.handler = "Other"

		rows = [r for r in (self.get("items") or []) if cint(r.enabled) and (r.item or "").strip()]
		if not rows:
			frappe.throw(frappe._("Add at least one enabled Item on POS Amount Service {0}").format(self.title))

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
				frappe.throw(frappe._("Electricity Amount Service needs at least one t1 or t2 item"))
