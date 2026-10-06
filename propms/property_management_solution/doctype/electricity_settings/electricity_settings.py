# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ElectricitySettings(Document):
	def validate(self):
		frappe.throw(
			frappe._(
				"Electricity Settings has been merged into POS Amount Service "
				"(Service type Electricity). Open POS Amount Service → Electricity to "
				"configure items and TrackSPM tariffs."
			)
		)
