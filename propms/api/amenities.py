# -*- coding: utf-8 -*-
"""Viva Amenities & Bookings Top-Level Router."""

from __future__ import unicode_literals
import frappe
from propms.api.v1.amenities import (
	get_amenities as _get_amenities,
	get_amenity_detail as _get_amenity_detail,
	get_available_slots as _get_available_slots,
	create_booking as _create_booking,
	get_my_bookings as _get_my_bookings,
	cancel_booking as _cancel_booking,
)


@frappe.whitelist(methods=["GET", "POST"])
def get_amenities(category=None):
	return _get_amenities(category=category)


@frappe.whitelist(methods=["GET", "POST"])
def get_amenity_detail(amenity=None):
	return _get_amenity_detail(amenity=amenity)


@frappe.whitelist(methods=["GET", "POST"])
def get_available_slots(amenity=None, booking_date=None):
	return _get_available_slots(amenity=amenity, booking_date=booking_date)


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
	return _create_booking(
		amenity=amenity,
		booking_date=booking_date,
		start_time=start_time,
		end_time=end_time,
		guests_count=guests_count,
		notes=notes,
		lease=lease,
		property_unit=property_unit,
	)


@frappe.whitelist(methods=["GET", "POST"])
def get_my_bookings(status="all", page=1, page_length=20):
	return _get_my_bookings(status=status, page=page, page_length=page_length)


@frappe.whitelist(methods=["POST"])
def cancel_booking(booking_id=None, cancellation_reason=None):
	return _cancel_booking(booking_id=booking_id, cancellation_reason=cancellation_reason)
