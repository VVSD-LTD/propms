# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AfritrackSettings(Document):
	pass


@frappe.whitelist()
def sync_meters_from_trackspm():
	"""Desk action: Afritrack Meter Sync — full /units/list JSON + Meter ID update."""
	frappe.only_for("System Manager")
	from propms.property_management_solution.doctype.afritrack_meter_sync.afritrack_meter_sync import (
		sync_now,
	)

	return sync_now()
