# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from propms.api.v1.amenities.list import get_amenities, get_amenity_detail, set_amenity_published
from propms.api.v1.amenities.slots import get_available_slots, get_amenity_day_availability
from propms.api.v1.amenities.booking import create_booking
from propms.api.v1.amenities.request import create_booking_request
from propms.api.v1.amenities.user_bookings import get_my_bookings, cancel_booking
from propms.api.v1.amenities.staff_actions import approve_amenity_booking, reject_amenity_booking

__all__ = [
	"get_amenities",
	"get_amenity_detail",
	"set_amenity_published",
	"get_available_slots",
	"get_amenity_day_availability",
	"create_booking",
	"create_booking_request",
	"get_my_bookings",
	"cancel_booking",
	"approve_amenity_booking",
	"reject_amenity_booking",
]
