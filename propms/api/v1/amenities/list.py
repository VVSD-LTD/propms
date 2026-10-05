# -*- coding: utf-8 -*-
"""Amenities list & catalog service."""

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.utils import cint
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload

STAFF_AMENITY_ROLES = (
	"System Manager",
	"Property Manager",
	"Mobile Maintenance Manager",
	"Mobile Maintenance Officer",
)


def _is_amenity_staff(user=None):
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	roles = set(frappe.get_roles(user))
	return bool(roles.intersection(STAFF_AMENITY_ROLES))


def _require_auth():
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)


def _load_amenity_gallery(amenity_name):
	"""Load child gallery images for a given amenity."""
	images = frappe.get_all(
		"Amenity Image",
		filters={"parent": amenity_name, "parenttype": "Amenity"},
		fields=["image", "caption", "sort_order"],
		order_by="sort_order asc, idx asc",
		ignore_permissions=True,
	)
	return images or []


def _amenity_list_fields():
	return [
		"name",
		"amenity_name",
		"category",
		"floor",
		"description",
		"facilities",
		"cover_image",
		"capacity",
		"open_time",
		"close_time",
		"slot_duration_mins",
		"booking_time_step_mins",
		"cleanup_buffer_mins",
		"auto_approval",
		"cancel_before_hours",
		"requires_booking",
		"max_advance_days",
		"guidelines",
		"is_active",
		"is_published",
	]


@frappe.whitelist(methods=["GET", "POST"])
def get_amenities(category=None, published=None):
	"""Return Viva Amenities for mobile.

	Tenants: active + published only.
	Staff: active amenities including unpublished drafts (same UI, verify before publish).
	Optional ``published`` filter for staff: 1 / 0 / all.
	"""
	_require_auth()
	try:
		payload = _parse_request_payload({"category": category, "published": published})
		cat = (payload.get("category") or "").strip()
		pub_filter = payload.get("published")
		if pub_filter is None:
			pub_filter = published

		is_staff = _is_amenity_staff()
		filters = {"is_active": 1}

		if not is_staff:
			filters["is_published"] = 1
		else:
			# Staff may filter: published=1|0|all (default all active)
			if pub_filter is not None and str(pub_filter).strip().lower() not in ("", "all"):
				filters["is_published"] = 1 if cint(pub_filter) else 0

		if cat and cat != "all":
			filters["category"] = cat

		amenities = frappe.get_all(
			"Amenity",
			filters=filters,
			fields=_amenity_list_fields(),
			order_by="is_published desc, category asc, amenity_name asc",
			ignore_permissions=True,
		)

		for a in amenities:
			gallery = _load_amenity_gallery(a.name)
			a["gallery_images"] = gallery
			a["is_published"] = 1 if a.get("is_published") else 0
			a["is_active"] = 1 if a.get("is_active") else 0
			if not a.get("cover_image") and gallery:
				a["cover_image"] = gallery[0].get("image")

		return {
			"status": "success",
			"total": len(amenities),
			"is_staff": is_staff,
			"amenities": amenities,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_amenities")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["GET", "POST"])
def get_amenity_detail(amenity=None):
	"""Return full details and gallery images for a specific amenity.

	Tenants may only open published + active amenities.
	Staff may open unpublished drafts.
	"""
	_require_auth()
	try:
		payload = _parse_request_payload({"amenity": amenity})
		target = (payload.get("amenity") or "").strip()

		if not target or not frappe.db.exists("Amenity", target):
			return {"status": "error", "message": _("Amenity {0} not found").format(target)}

		doc = frappe.get_doc("Amenity", target)
		is_staff = _is_amenity_staff()

		if not cint(doc.is_active):
			return {"status": "error", "message": _("Amenity is not active")}

		if not cint(doc.is_published) and not is_staff:
			return {"status": "error", "message": _("Amenity is not published yet")}

		gallery = _load_amenity_gallery(doc.name)
		cover_img = doc.cover_image or (gallery[0].get("image") if gallery else None)

		amenity_data = doc.as_dict()
		amenity_data["cover_image"] = cover_img
		amenity_data["gallery_images"] = gallery
		amenity_data["is_published"] = 1 if cint(doc.is_published) else 0
		amenity_data["is_active"] = 1 if cint(doc.is_active) else 0

		return {
			"status": "success",
			"is_staff": is_staff,
			"amenity": amenity_data,
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_amenity_detail")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def set_amenity_published(amenity=None, is_published=None):
	"""Staff only: publish / unpublish an amenity for tenants."""
	_require_auth()
	if not _is_amenity_staff():
		frappe.throw(_("Only staff can publish amenities"), frappe.PermissionError)

	payload = _parse_request_payload({"amenity": amenity, "is_published": is_published})
	target = (payload.get("amenity") or "").strip()
	if not target or not frappe.db.exists("Amenity", target):
		return {"status": "error", "message": _("Amenity {0} not found").format(target)}

	if payload.get("is_published") is None:
		return {"status": "error", "message": _("is_published is required (0 or 1)")}

	flag = 1 if cint(payload.get("is_published")) else 0
	doc = frappe.get_doc("Amenity", target)
	doc.is_published = flag
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"status": "success",
		"amenity": doc.name,
		"is_published": flag,
		"message": _("Published") if flag else _("Unpublished"),
	}
