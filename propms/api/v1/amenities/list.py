# -*- coding: utf-8 -*-
"""Amenities list & catalog service."""

from __future__ import unicode_literals
import frappe
from frappe import _
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload


def _load_amenity_gallery(amenity_name):
	"""Load child gallery images for a given amenity."""
	images = frappe.get_all(
		"Amenity Image",
		filters={"parent": amenity_name, "parenttype": "Viva Amenity"},
		fields=["image", "caption", "sort_order"],
		order_by="sort_order asc, idx asc",
	)
	return images or []


@frappe.whitelist(methods=["GET", "POST"])
def get_amenities(category=None):
	"""Return all active Viva Amenities with cover and gallery images."""
	try:
		payload = _parse_request_payload({"category": category})
		cat = (payload.get("category") or "").strip()

		filters = {"is_active": 1}
		if cat and cat != "all":
			filters["category"] = cat

		fields = [
			"name",
			"amenity_name",
			"category",
			"description",
			"cover_image",
			"capacity",
			"open_time",
			"close_time",
			"slot_duration_mins",
			"requires_booking",
			"max_advance_days",
			"guidelines",
			"is_active",
		]

		amenities = frappe.get_all(
			"Viva Amenity",
			filters=filters,
			fields=fields,
			order_by="category asc, amenity_name asc",
		)

		for a in amenities:
			gallery = _load_amenity_gallery(a.name)
			a["gallery_images"] = gallery

			# Fallback cover_image to first gallery image if empty
			if not a.get("cover_image") and gallery:
				a["cover_image"] = gallery[0].get("image")

		return {
			"status": "success",
			"total": len(amenities),
			"amenities": amenities,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_amenities")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_amenity_detail(amenity=None):
	"""Return full details and gallery images for a specific amenity."""
	try:
		payload = _parse_request_payload({"amenity": amenity})
		target = (payload.get("amenity") or "").strip()

		if not target or not frappe.db.exists("Viva Amenity", target):
			return {"status": "error", "message": f"Amenity {target} not found"}

		doc = frappe.get_doc("Viva Amenity", target)
		gallery = _load_amenity_gallery(doc.name)
		cover_img = doc.cover_image or (gallery[0].get("image") if gallery else None)

		amenity_data = doc.as_dict()
		amenity_data["cover_image"] = cover_img
		amenity_data["gallery_images"] = gallery

		return {
			"status": "success",
			"amenity": amenity_data,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_amenity_detail")
		return {"status": "error", "message": str(e)}
