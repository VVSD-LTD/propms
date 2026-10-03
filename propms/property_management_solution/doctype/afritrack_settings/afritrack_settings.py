# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AfritrackSettings(Document):
	pass


@frappe.whitelist()
def sync_meters_from_trackspm():
	"""Desk action: sync TrackSPM units/list → Meter.trackspm_meter_id."""
	frappe.only_for("System Manager")
	from propms.api.v1.electricity.trackspm import sync_meters_from_trackspm as _sync

	return _sync()
