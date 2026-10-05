# -*- coding: utf-8 -*-
"""Amenity booking creation service.

Tenant create always goes through Amenity Booking Request (auto-approve → Booking).
``create_booking`` keeps the mobile whitelist name and delegates to the request flow.
"""

from __future__ import unicode_literals

import frappe


@frappe.whitelist(methods=["POST"])
def create_booking(
	amenity=None,
	booking_date=None,
	start_time=None,
	end_time=None,
	guests_count=1,
	notes=None,
	lease=None,
	property_unit=None,
):
	"""Create booking request (and Confirmed Booking when amenity auto_approval=1)."""
	from propms.api.v1.amenities.request import create_booking_request

	return create_booking_request(
		amenity=amenity,
		booking_date=booking_date,
		start_time=start_time,
		end_time=end_time,
		guests_count=guests_count,
		notes=notes,
		lease=lease,
		property_unit=property_unit,
	)
