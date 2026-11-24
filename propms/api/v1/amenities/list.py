# -*- coding: utf-8 -*-
"""Amenities list & catalog service."""

from __future__ import unicode_literals

import json
import re

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


def serialize_important_information(raw):
	"""Normalize Desk Text Editor / JSON / plain text → list of strings for mobile.

	Accepts:
	- JSON array of strings
	- JSON array of {\"text\": \"...\"}
	- HTML (Text Editor): ``<li>``, ``<p>``, ``<br>``
	- Plain text with newlines
	"""
	if raw is None:
		return []
	if isinstance(raw, (list, tuple)):
		out = []
		for item in raw:
			if isinstance(item, dict):
				text = (item.get("text") or item.get("line") or "").strip()
			else:
				text = str(item or "").strip()
			if text:
				out.append(text)
		return out

	text = str(raw).strip()
	if not text:
		return []

	# JSON payload stored as text
	if text[0] in ("[", "{"):
		try:
			parsed = json.loads(text)
			return serialize_important_information(parsed)
		except Exception:
			pass

	# HTML list items first
	lis = re.findall(r"<li[^>]*>(.*?)</li>", text, flags=re.I | re.S)
	if lis:
		out = []
		for chunk in lis:
			line = re.sub(r"<[^>]+>", " ", chunk)
			line = re.sub(r"\s+", " ", line).strip()
			if line:
				out.append(line)
		if out:
			return out

	# Break on <br> / </p> / newlines, strip tags
	normalized = text
	normalized = re.sub(r"(?i)<br\s*/?>", "\n", normalized)
	normalized = re.sub(r"(?i)</p\s*>", "\n", normalized)
	normalized = re.sub(r"(?i)</div\s*>", "\n", normalized)
	normalized = re.sub(r"<[^>]+>", " ", normalized)
	normalized = (
		normalized.replace("&nbsp;", " ")
		.replace("&amp;", "&")
		.replace("&lt;", "<")
		.replace("&gt;", ">")
		.replace("&quot;", '"')
	)

	out = []
	for line in normalized.splitlines():
		line = re.sub(r"\s+", " ", line).strip(" \t\r\n•\-–—*")
		if line:
			out.append(line)
	return out


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
		"holiday_list",
		"holiday_hours",
		"holiday_open_time",
		"holiday_close_time",
		"slot_duration_mins",
		"booking_time_step_mins",
		"cleanup_buffer_mins",
		"auto_approval",
		"require_payment",
		"payment_hold_minutes",
		"cancel_before_hours",
		"requires_booking",
		"max_advance_days",
		"allow_recurring",
		"max_series_days",
		"guidelines",
		"important_information",
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

		from propms.api.v1.amenities.hours import (
			WEEKDAY_NAMES,
			_time_hms,
			summary_open_close,
		)

		meta_fields = {f.fieldname for f in frappe.get_meta("Amenity").fields}
		list_fields = [f for f in _amenity_list_fields() if f in meta_fields or f in (
			"name",
		)]
		# Always allow core name
		if "name" not in list_fields:
			list_fields.insert(0, "name")

		amenities = frappe.get_all(
			"Amenity",
			filters=filters,
			fields=list_fields,
			order_by="is_published desc, category asc, amenity_name asc",
			ignore_permissions=True,
		)
		day_hours_by_parent = {}
		if amenities and frappe.db.exists("DocType", "Amenity Day Hours"):
			for row in frappe.get_all(
				"Amenity Day Hours",
				filters={
					"parent": ["in", [a.name for a in amenities]],
					"parenttype": "Amenity",
				},
				fields=["parent", "day_of_week", "is_open", "open_time", "close_time", "idx"],
				order_by="idx asc",
				ignore_permissions=True,
			):
				day_hours_by_parent.setdefault(row.parent, []).append(
					{
						"day_of_week": row.day_of_week,
						"is_open": 1 if cint(row.is_open) else 0,
						"open_time": _time_hms(row.open_time) if row.open_time else None,
						"close_time": _time_hms(row.close_time) if row.close_time else None,
					}
				)
			order = {name: i for i, name in enumerate(WEEKDAY_NAMES)}
			for parent, rows in day_hours_by_parent.items():
				rows.sort(key=lambda r: order.get(r.get("day_of_week") or "", 99))

		has_require_payment = "require_payment" in meta_fields
		for a in amenities:
			gallery = _load_amenity_gallery(a.name)
			a["gallery_images"] = gallery
			a["is_published"] = 1 if a.get("is_published") else 0
			a["is_active"] = 1 if a.get("is_active") else 0
			a["require_payment"] = (
				1 if has_require_payment and cint(a.get("require_payment")) else 0
			)
			a["payment_hold_minutes"] = (
				cint(a.get("payment_hold_minutes"))
				if a["require_payment"] and "payment_hold_minutes" in meta_fields
				else 0
			)
			if "important_information" in meta_fields:
				a["important_information"] = serialize_important_information(
					a.get("important_information")
				)
			else:
				a["important_information"] = []
			# Lightweight list: rates only when payment required (detail has full rows)
			if a["require_payment"] and frappe.db.exists("DocType", "Amenity Rate"):
				from propms.api.v1.amenities.payment import serialize_payment_rates

				a["rates"] = serialize_payment_rates(
					frappe.get_cached_doc("Amenity", a.name)
				)
			else:
				a["rates"] = []
			a["payment_rates"] = a["rates"]
			if not a.get("cover_image") and gallery:
				a["cover_image"] = gallery[0].get("image")
			a["day_hours"] = day_hours_by_parent.get(a.name) or []
			# Derived summary for older mobile clients (not stored on Amenity anymore)
			open_s, close_s = summary_open_close(a["day_hours"])
			a["open_time"] = open_s
			a["close_time"] = close_s
			if "holiday_hours" not in meta_fields:
				a["holiday_list"] = None
				a["holiday_hours"] = None
				a["holiday_open_time"] = None
				a["holiday_close_time"] = None
			else:
				if not a.get("holiday_list"):
					a["holiday_hours"] = None
					a["holiday_open_time"] = None
					a["holiday_close_time"] = None
				else:
					a["holiday_hours"] = a.get("holiday_hours") or None
					if a.get("holiday_hours") == "Custom":
						if a.get("holiday_open_time"):
							a["holiday_open_time"] = _time_hms(a.get("holiday_open_time"))
						if a.get("holiday_close_time"):
							a["holiday_close_time"] = _time_hms(a.get("holiday_close_time"))
					else:
						a["holiday_open_time"] = None
						a["holiday_close_time"] = None

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

		from propms.api.v1.amenities.hours import (
			serialize_day_hours,
			serialize_holiday_hours,
			summary_open_close,
		)

		gallery = _load_amenity_gallery(doc.name)
		cover_img = doc.cover_image or (gallery[0].get("image") if gallery else None)

		amenity_data = doc.as_dict()
		amenity_data.pop("open_time", None)
		amenity_data.pop("close_time", None)
		amenity_data["cover_image"] = cover_img
		amenity_data["gallery_images"] = gallery
		amenity_data["is_published"] = 1 if cint(doc.is_published) else 0
		amenity_data["is_active"] = 1 if cint(doc.is_active) else 0
		amenity_data["day_hours"] = serialize_day_hours(doc)
		open_s, close_s = summary_open_close(amenity_data["day_hours"])
		amenity_data["open_time"] = open_s
		amenity_data["close_time"] = close_s
		amenity_data.update(serialize_holiday_hours(doc))
		amenity_data["require_payment"] = 1 if cint(getattr(doc, "require_payment", 0)) else 0
		amenity_data["payment_hold_minutes"] = (
			cint(getattr(doc, "payment_hold_minutes", 0))
			if amenity_data["require_payment"]
			else 0
		)
		from propms.api.v1.amenities.payment import serialize_payment_rates

		rates = serialize_payment_rates(doc) if amenity_data["require_payment"] else []
		amenity_data["rates"] = rates
		amenity_data["payment_rates"] = rates  # alias
		amenity_data["important_information"] = serialize_important_information(
			getattr(doc, "important_information", None)
		)

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
