# -*- coding: utf-8 -*-
"""Residential / commercial audience helpers for amenities & apartments."""

from __future__ import unicode_literals

import frappe


VALID_AUDIENCES = ("Everyone", "Residential", "Commercial")


def get_property_type(property_name):
	"""Return Property.type (Unit Type name), e.g. Residential / Commercial, or None."""
	if not property_name or not frappe.db.exists("Property", property_name):
		return None
	return frappe.db.get_value("Property", property_name, "type") or None


def resolve_property_type_from_request(lease=None, property_name=None, property_type=None):
	"""Resolve unit class from explicit property_type, property, or lease."""
	explicit = (property_type or "").strip()
	if explicit:
		return explicit

	prop = (property_name or "").strip() or None
	lease_name = (lease or "").strip() or None
	if not prop and lease_name and frappe.db.exists("Lease", lease_name):
		prop = frappe.db.get_value("Lease", lease_name, "property")
	return get_property_type(prop) if prop else None


def amenity_matches_property_type(audience, property_type):
	"""True if amenity audience is visible for this Property.type."""
	aud = (audience or "Everyone").strip() or "Everyone"
	if aud == "Everyone":
		return True
	if not property_type:
		# No unit context: show only Everyone (tenants must pass lease/property)
		return False
	return aud == property_type


def assert_amenity_allowed_for_property(amenity_doc, lease=None, property_name=None, property_type=None):
	"""Raise PermissionError / return error dict-friendly message if tenant cannot use amenity.

	Staff callers should skip this. Returns None if OK, else error message string.
	"""
	audience = getattr(amenity_doc, "audience", None) or "Everyone"
	if audience == "Everyone":
		return None
	ptype = resolve_property_type_from_request(
		lease=lease, property_name=property_name, property_type=property_type
	)
	if amenity_matches_property_type(audience, ptype):
		return None
	if not ptype:
		return "lease, property, or property_type is required for this amenity"
	return (
		f"This amenity is for {audience} units only "
		f"(your unit type is {ptype})."
	)
