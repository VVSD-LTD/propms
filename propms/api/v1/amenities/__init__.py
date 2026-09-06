# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from propms.api.v1.amenities.list import get_amenities, get_amenity_detail
from propms.api.v1.amenities.slots import get_available_slots
from propms.api.v1.amenities.booking import create_booking
from propms.api.v1.amenities.user_bookings import get_my_bookings, cancel_booking

__all__ = [
	"get_amenities",
	"get_amenity_detail",
	"get_available_slots",
	"create_booking",
	"get_my_bookings",
	"cancel_booking",
]
