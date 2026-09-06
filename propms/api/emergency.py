# -*- coding: utf-8 -*-
"""Viva Emergency Incident Reporting Top-Level Router."""

from __future__ import unicode_literals
import frappe
from propms.api.v1.emergency import (
	report_emergency as _report_emergency,
	update_incident_status as _update_incident_status,
	get_emergency_incidents as _get_emergency_incidents,
)


@frappe.whitelist(methods=["POST"])
def report_emergency(
	incident_type=None,
	property_unit=None,
	location_details=None,
	details=None,
):
	return _report_emergency(
		incident_type=incident_type,
		property_unit=property_unit,
		location_details=location_details,
		details=details,
	)


@frappe.whitelist(methods=["POST"])
def update_incident_status(incident_id=None, status=None, resolution_notes=None):
	return _update_incident_status(
		incident_id=incident_id,
		status=status,
		resolution_notes=resolution_notes,
	)


@frappe.whitelist(methods=["GET", "POST"])
def get_emergency_incidents(status="all", page=1, page_length=20):
	return _get_emergency_incidents(status=status, page=page, page_length=page_length)
