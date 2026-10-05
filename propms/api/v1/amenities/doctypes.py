# -*- coding: utf-8 -*-
"""Canonical amenity DocType names after v15 rename patches.

Patches rename Viva Amenity* → Amenity*. JSON fixtures may still recreate empty
Viva* tables; mobile APIs must target the renamed doctypes that hold live data.
"""

from __future__ import unicode_literals

AMENITY = "Amenity"
AMENITY_BOOKING = "Amenity Booking"
AMENITY_BOOKING_SERIES = "Amenity Booking Series"
AMENITY_BOOKING_REQUEST = "Amenity Booking Request"
