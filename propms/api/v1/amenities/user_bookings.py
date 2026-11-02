# -*- coding: utf-8 -*-
"""User amenity bookings & cancellation service."""

from __future__ import unicode_literals

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

from propms.api.v1.amenities.doctypes import (
	AMENITY_BOOKING,
	AMENITY_BOOKING_REQUEST,
	AMENITY_BOOKING_SERIES,
)
from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.amenities.lifecycle import (
	reconcile_completed_amenity_bookings,
	reconcile_stale_open_amenity_requests,
	reconcile_stale_pending_amenity_bookings,
)
from propms.api.v1.gate_pass.gate_pass import _parse_request_payload


def _apply_status_filter(filters, target_status):
	target_status = (target_status or "all").strip().lower()
	if target_status in ("confirmed", "upcoming"):
		filters["status"] = "Confirmed"
	elif target_status == "completed":
		filters["status"] = "Completed"
	elif target_status == "cancelled":
		filters["status"] = "Cancelled"
	elif target_status == "no show":
		filters["status"] = "No Show"
	elif target_status == "pending":
		filters["status"] = "Pending"
	elif target_status == "rejected":
		filters["status"] = "Rejected"
	elif target_status in ("open", "approved", "expired"):
		# Request-only statuses; exclude bookings from this filter
		filters["status"] = "__skip__"
	return filters


def _request_statuses_for_list(target_status):
	"""Which Amenity Booking Request statuses to include for a list filter."""
	target_status = (target_status or "all").strip().lower()
	if target_status in ("all", "pending", "open"):
		return ["Open"]
	if target_status == "cancelled":
		return ["Cancelled"]
	if target_status == "rejected":
		return ["Rejected"]
	if target_status == "expired":
		return ["Expired"]
	if target_status == "approved":
		return ["Approved"]
	# confirmed / upcoming / completed / no show → bookings only
	return []


def _enrich_bookings(bookings):
	for b in bookings:
		amenity_meta = (
			frappe.db.get_value(
				"Amenity",
				b.amenity,
				["amenity_name", "category", "cover_image", "floor"],
				as_dict=True,
			)
			or {}
		)
		b["amenity_name"] = amenity_meta.get("amenity_name") or b.amenity
		b["category"] = amenity_meta.get("category")
		b["cover_image"] = amenity_meta.get("cover_image")
		b["floor"] = amenity_meta.get("floor")
		# Ensure tenant display name for staff list
		if not b.get("tenant_name") and b.get("tenant"):
			b["tenant_name"] = (
				frappe.db.get_value("User", b["tenant"], "full_name") or b["tenant"]
			)
	return bookings


def _status_summary(filters_base, request_filters_base=None):
	"""Count by status for the same scope as the list (tenant or all)."""
	rows = frappe.get_all(
		AMENITY_BOOKING,
		filters=filters_base,
		fields=["status"],
		ignore_permissions=True,
	)
	open_requests = 0
	if request_filters_base is not None:
		req_filters = dict(request_filters_base)
		req_filters["status"] = "Open"
		open_requests = frappe.db.count(
			AMENITY_BOOKING_REQUEST, filters=req_filters
		)

	pending_bookings = sum(1 for x in rows if x.status == "Pending")
	return {
		"total": len(rows) + open_requests,
		"confirmed": sum(1 for x in rows if x.status == "Confirmed"),
		"completed": sum(1 for x in rows if x.status == "Completed"),
		"cancelled": sum(1 for x in rows if x.status == "Cancelled"),
		"no_show": sum(1 for x in rows if x.status == "No Show"),
		"pending": pending_bookings + open_requests,
		"open": open_requests,
	}


def _row_sort_key(row):
	return (
		str(row.get("booking_date") or ""),
		str(row.get("start_time") or ""),
		str(row.get("creation") or ""),
	)


def _series_type_for(series_name):
	if not series_name:
		return None
	return frappe.db.get_value(AMENITY_BOOKING_SERIES, series_name, "series_type") or "Recurring"


def _tag_booking_row(row):
	row = dict(row)
	row["kind"] = "booking"
	row["booking_id"] = row.get("name")
	row["request_id"] = None
	row["series_id"] = (row.get("series") or "").strip() or None
	row["series_type"] = _series_type_for(row["series_id"])
	return row


def _tag_request_row(row):
	row = dict(row)
	row["kind"] = "request"
	row["request_id"] = row.get("name")
	# Linked booking after approve; Open requests have none
	row["booking_id"] = row.get("booking") or None
	row["series_id"] = (row.get("series") or "").strip() or None
	row["series_type"] = _series_type_for(row["series_id"])
	# Flutter compat: treat Open as pending-like
	if row.get("status") == "Open":
		row["display_status"] = "Pending"
	else:
		row["display_status"] = row.get("status")
	# Keep keys Flutter already reads; request has no cancellation_reason
	if "cancellation_reason" not in row:
		row["cancellation_reason"] = None
	return row


def _build_hub_items(combined_rows, current_user, is_staff, payload, target_status):
	"""Hub cards: one series card per series + one-time booking/request cards."""
	from propms.api.v1.amenities.series import serialize_series_card

	series_ids = set()
	one_time = []
	for row in combined_rows:
		sid = (row.get("series_id") or row.get("series") or "").strip()
		if sid:
			series_ids.add(sid)
		else:
			one_time.append(row)

	# Load series for this scope (even if all children filtered out of page)
	series_filters = {}
	if not is_staff:
		series_filters["tenant"] = current_user
	amenity_name = (payload.get("amenity") or "").strip()
	if amenity_name:
		series_filters["amenity"] = amenity_name
	st = (target_status or "all").strip().lower()
	if st == "pending":
		series_filters["status"] = "Pending"
	elif st in ("confirmed", "upcoming"):
		series_filters["status"] = "Confirmed"
	elif st == "cancelled":
		series_filters["status"] = "Cancelled"
	elif st == "rejected":
		series_filters["status"] = "Rejected"
	elif st in ("completed", "no show", "open", "approved", "expired"):
		# Series has no Completed/Open — skip series cards for these filters
		series_filters["status"] = "__skip__"

	series_cards = []
	if series_filters.get("status") != "__skip__":
		# Prefer series referenced by list rows; also include orphan series in scope
		names = list(series_ids)
		extra = frappe.get_all(
			AMENITY_BOOKING_SERIES,
			filters=series_filters,
			pluck="name",
			ignore_permissions=True,
		)
		for n in extra:
			if n not in series_ids:
				names.append(n)
		for name in names:
			if not frappe.db.exists(AMENITY_BOOKING_SERIES, name):
				continue
			# Re-check status filter on each
			if series_filters.get("status") and series_filters["status"] != "__skip__":
				if frappe.db.get_value(AMENITY_BOOKING_SERIES, name, "status") != series_filters["status"]:
					continue
			series_cards.append(serialize_series_card(name))

	def _hub_sort(item):
		if item.get("kind") == "series":
			nxt = item.get("next_occurrence") or {}
			return (
				nxt.get("booking_date")
				or item.get("series_start_date")
				or "",
				item.get("start_time") or "",
			)
		return (str(item.get("booking_date") or ""), str(item.get("start_time") or ""))

	hub = list(series_cards) + list(one_time)
	hub.sort(key=_hub_sort, reverse=True)
	return hub


def _apply_common_scope_filters(filters, payload, current_user, is_staff):
	"""Mutate filters with tenant/amenity/date scope shared by Booking + Request."""
	if not is_staff:
		filters["tenant"] = current_user

	amenity_name = (payload.get("amenity") or "").strip()
	if amenity_name:
		filters["amenity"] = amenity_name

	one_day = (payload.get("booking_date") or "").strip()
	date_from = (payload.get("booking_date_from") or "").strip()
	date_to = (payload.get("booking_date_to") or "").strip()
	if one_day:
		filters["booking_date"] = one_day
	else:
		if date_from and date_to:
			filters["booking_date"] = ["between", [date_from, date_to]]
		elif date_from:
			filters["booking_date"] = [">=", date_from]
		elif date_to:
			filters["booking_date"] = ["<=", date_to]
	return filters


def _check_cancel_deadline(doc, is_staff):
	"""Return error dict if tenant cancel window has closed, else None."""
	if is_staff:
		return None
	amenity = frappe.get_doc("Amenity", doc.amenity)
	hours = cint(getattr(amenity, "cancel_before_hours", None) or 2)
	start_dt = get_datetime(f"{doc.booking_date} {doc.start_time}")
	deadline = start_dt - timedelta(hours=hours)
	if now_datetime() >= deadline:
		return {
			"status": "error",
			"message": f"Cancellation is only allowed until {hours} hour(s) before start.",
		}
	return None


def _mark_request_item_cancelled(booking_name, request_name=None):
	"""Keep Request Item row in sync when a day booking is cancelled."""
	if not booking_name:
		return
	filters = {"booking": booking_name}
	if request_name:
		filters["parent"] = request_name
	if not frappe.db.exists("DocType", "Amenity Booking Request Item"):
		return
	for row in frappe.get_all(
		"Amenity Booking Request Item",
		filters=filters,
		fields=["name", "parent"],
		ignore_permissions=True,
	):
		frappe.db.set_value(
			"Amenity Booking Request Item",
			row.name,
			"status",
			"Cancelled",
			update_modified=False,
		)


def _retarget_request_primary_booking(request_name):
	"""If Request.booking points at a cancelled day, point at next Confirmed day."""
	if not request_name or not frappe.db.exists(AMENITY_BOOKING_REQUEST, request_name):
		return
	current = frappe.db.get_value(AMENITY_BOOKING_REQUEST, request_name, "booking")
	if current and frappe.db.get_value(AMENITY_BOOKING, current, "status") == "Confirmed":
		return
	nxt = frappe.get_all(
		AMENITY_BOOKING,
		filters={"request": request_name, "status": "Confirmed"},
		fields=["name"],
		order_by="booking_date asc, start_time asc",
		limit=1,
		ignore_permissions=True,
	)
	frappe.db.set_value(
		AMENITY_BOOKING_REQUEST,
		request_name,
		"booking",
		nxt[0].name if nxt else None,
		update_modified=False,
	)


def _cancel_booking_doc(doc, reason, current_user, is_staff, notify=True, commit=True):
	"""Apply existing Confirmed/Pending booking cancel rules. Returns result dict."""
	if doc.status == "Cancelled":
		return {"status": "error", "message": "Booking is already cancelled"}
	if doc.status == "Completed":
		return {"status": "error", "message": "Cannot cancel a completed booking"}

	deadline_err = _check_cancel_deadline(doc, is_staff)
	if deadline_err:
		return deadline_err

	doc.status = "Cancelled"
	if reason:
		doc.cancellation_reason = reason
	doc.save(ignore_permissions=True)

	req_name = getattr(doc, "request", None)
	_mark_request_item_cancelled(doc.name, req_name)
	_retarget_request_primary_booking(req_name)

	if commit:
		frappe.db.commit()

	if notify:
		try:
			from propms.api.v1.amenities.notify import notify_amenity_booking_cancelled

			notify_amenity_booking_cancelled(doc, cancelled_by=current_user)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "cancel_booking.notify")

	return {
		"status": "success",
		"message": "Booking cancelled successfully",
		"booking_id": doc.name,
		"request_id": req_name or None,
		"series_id": getattr(doc, "series", None) or None,
		"kind": "booking",
		"booking_date": str(doc.booking_date or ""),
	}


def _cancel_open_request(req, reason, current_user, is_staff, notify=True, commit=True):
	"""Open Request → Cancelled (no cancel-before-hours gate).

	Uses for_update + re-check Open (same race guard as `_claim_open_request`).
	"""
	if not is_staff and req.tenant != current_user:
		frappe.throw(_("Not permitted to cancel this booking"), frappe.PermissionError)

	# Lock + re-check so concurrent approve cannot race with cancel
	req = frappe.get_doc(AMENITY_BOOKING_REQUEST, req.name, for_update=True)
	if req.status != "Open":
		return {
			"status": "error",
			"message": f"Only Open requests can be cancelled this way (current: {req.status})",
		}

	req.status = "Cancelled"
	for child in getattr(req, "occurrences", None) or []:
		if (child.status or "Pending") == "Pending":
			child.status = "Cancelled"
	# Request DocType has no cancellation_reason; keep reason in notes if provided
	if reason:
		existing = (req.notes or "").strip()
		note = f"Cancelled: {reason}"
		req.notes = f"{existing}\n{note}".strip() if existing else note
	req.save(ignore_permissions=True)

	try:
		from propms.api.v1.amenities.staff_actions import _sync_parent_series_after_request

		_sync_parent_series_after_request(getattr(req, "series", None))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "cancel_booking.sync_series")

	if commit:
		frappe.db.commit()

	if notify:
		try:
			from propms.api.v1.amenities.notify import notify_amenity_booking_cancelled

			notify_amenity_booking_cancelled(req, cancelled_by=current_user)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "cancel_booking.notify_request")

	return {
		"status": "success",
		"message": "Booking request cancelled successfully",
		"booking_id": None,
		"request_id": req.name,
		"series_id": getattr(req, "series", None) or None,
		"kind": "request",
	}


def _maybe_mark_series_cancelled(series_id):
	if not series_id or not frappe.db.exists(AMENITY_BOOKING_SERIES, series_id):
		return
	still = frappe.db.count(
		AMENITY_BOOKING, {"series": series_id, "status": "Confirmed"}
	)
	if still == 0:
		frappe.db.set_value(
			AMENITY_BOOKING_SERIES,
			series_id,
			"status",
			"Cancelled",
			update_modified=True,
		)


def _cancel_many_request_bookings(req, rows, reason, current_user, is_staff):
	"""Cancel many Confirmed day bookings for one request (cancel-all path)."""
	cancelled = []
	failed = []
	for row in rows:
		doc = frappe.get_doc(AMENITY_BOOKING, row.name)
		if not is_staff and doc.tenant != current_user:
			failed.append({"booking_id": doc.name, "message": "Not permitted"})
			continue
		result = _cancel_booking_doc(
			doc, reason, current_user, is_staff, notify=False, commit=False
		)
		if result.get("status") == "success":
			cancelled.append(
				{
					"booking_id": doc.name,
					"booking_date": str(doc.booking_date or ""),
				}
			)
		else:
			failed.append(
				{
					"booking_id": doc.name,
					"booking_date": str(row.booking_date or ""),
					"message": result.get("message") or "cancel failed",
				}
			)

	if not cancelled and failed:
		frappe.db.rollback()
		return {
			"status": "error",
			"message": failed[0].get("message") or _("Could not cancel bookings"),
			"request_id": req.name,
			"series_id": getattr(req, "series", None) or None,
			"failed": failed,
		}

	series_id = getattr(req, "series", None)
	_maybe_mark_series_cancelled(series_id)
	frappe.db.commit()

	try:
		from propms.api.v1.amenities.notify import notify_amenity_booking_cancelled

		if cancelled:
			notify_amenity_booking_cancelled(
				frappe.get_doc(AMENITY_BOOKING, cancelled[-1]["booking_id"]),
				cancelled_by=current_user,
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "cancel_approved_request.notify")

	msg = _("Cancelled {0} remaining day(s)").format(len(cancelled))
	if failed:
		msg = _("{0}; {1} day(s) could not be cancelled").format(msg, len(failed))

	return {
		"status": "success",
		"message": msg,
		"request_id": req.name,
		"series_id": series_id,
		"kind": "request",
		"cancelled_bookings": cancelled,
		"failed": failed,
		"remaining_count": frappe.db.count(
			AMENITY_BOOKING, {"request": req.name, "status": "Confirmed"}
		),
		"booking_id": cancelled[0]["booking_id"] if cancelled else None,
	}


def _cancel_approved_request(
	req, reason, current_user, is_staff, booking_id=None, cancel_all=False
):
	"""Approved Request cancel — one day or all remaining days.

	- ``booking_id``: cancel that specific day (one-by-one).
	- ``cancel_all``: cancel every remaining Confirmed day.
	- ``request_id`` only: cancel the **next upcoming** Confirmed day (one-by-one
	  without requiring booking_id from older app builds).
	"""
	if not is_staff and req.tenant != current_user:
		frappe.throw(_("Not permitted to cancel this booking"), frappe.PermissionError)

	confirmed = frappe.get_all(
		AMENITY_BOOKING,
		filters={"request": req.name, "status": "Confirmed"},
		fields=["name", "booking_date", "start_time", "end_time"],
		order_by="booking_date asc, start_time asc",
		ignore_permissions=True,
	)

	# 1) Explicit day
	target_booking = (booking_id or "").strip() or None
	if target_booking:
		if not frappe.db.exists(AMENITY_BOOKING, target_booking):
			return {"status": "error", "message": f"Booking {target_booking} not found"}
		booking = frappe.get_doc(AMENITY_BOOKING, target_booking)
		if booking.request and booking.request != req.name:
			return {
				"status": "error",
				"message": _("Booking does not belong to this request"),
			}
		if not is_staff and booking.tenant != current_user:
			frappe.throw(_("Not permitted to cancel this booking"), frappe.PermissionError)
		result = _cancel_booking_doc(booking, reason, current_user, is_staff)
		if result.get("status") == "success":
			_maybe_mark_series_cancelled(getattr(req, "series", None))
			frappe.db.commit()
			remaining = frappe.db.count(
				AMENITY_BOOKING, {"request": req.name, "status": "Confirmed"}
			)
			result["request_id"] = req.name
			result["kind"] = "booking"
			result["remaining_count"] = remaining
			result["message"] = _(
				"Cancelled {0}. {1} day(s) still booked."
			).format(result.get("booking_date") or booking.booking_date, remaining)
		return result

	if not confirmed:
		return {
			"status": "error",
			"message": _("No remaining confirmed days to cancel"),
			"request_id": req.name,
			"series_id": getattr(req, "series", None) or None,
			"remaining_count": 0,
		}

	# 2) Cancel all remaining days
	if cancel_all:
		return _cancel_many_request_bookings(
			req, confirmed, reason, current_user, is_staff
		)

	# 3) One-by-one: next upcoming Confirmed day only
	booking = frappe.get_doc(AMENITY_BOOKING, confirmed[0].name)
	if not is_staff and booking.tenant != current_user:
		frappe.throw(_("Not permitted to cancel this booking"), frappe.PermissionError)

	result = _cancel_booking_doc(booking, reason, current_user, is_staff)
	if result.get("status") == "success":
		_maybe_mark_series_cancelled(getattr(req, "series", None))
		frappe.db.commit()
		remaining = frappe.db.count(
			AMENITY_BOOKING, {"request": req.name, "status": "Confirmed"}
		)
		result["request_id"] = req.name
		result["series_id"] = getattr(req, "series", None) or None
		result["kind"] = "booking"
		result["remaining_count"] = remaining
		result["remaining_bookings"] = [
			{
				"booking_id": r.name,
				"booking_date": str(r.booking_date),
				"start_time": str(r.start_time or ""),
			}
			for r in frappe.get_all(
				AMENITY_BOOKING,
				filters={"request": req.name, "status": "Confirmed"},
				fields=["name", "booking_date", "start_time"],
				order_by="booking_date asc",
				ignore_permissions=True,
			)
		]
		result["message"] = _(
			"Cancelled {0}. {1} day(s) still booked — cancel again for the next day, "
			"or pass cancel_all=1 to remove all."
		).format(result.get("booking_date") or booking.booking_date, remaining)
	return result


@frappe.whitelist(methods=["GET", "POST"])
def get_my_bookings(
	status="all",
	page=1,
	page_length=20,
	amenity=None,
	booking_date=None,
	booking_date_from=None,
	booking_date_to=None,
):
	"""List amenity bookings and Open requests.

	- **Tenant:** own reservations / requests only.
	- **Staff** (Maintenance Manager/Officer, Property Manager, System Manager):
	  **all tenant reservations** across the building (not the staff user's own
	  bookings). Optional filters: amenity, booking_date / from-to, status.

	Each row includes ``kind``: ``"request"`` | ``"booking"``.
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		# Past Confirmed → Completed; stale Pending → Cancelled; stale Open → Expired
		reconcile_completed_amenity_bookings()
		reconcile_stale_pending_amenity_bookings()
		reconcile_stale_open_amenity_requests()

		payload = _parse_request_payload(
			{
				"status": status,
				"page": page,
				"page_length": page_length,
				"amenity": amenity,
				"booking_date": booking_date,
				"booking_date_from": booking_date_from,
				"booking_date_to": booking_date_to,
			}
		)
		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)

		target_status = (payload.get("status") or "all").strip().lower()
		page_num = max(1, cint(payload.get("page") or 1))
		page_len = min(100, max(1, cint(payload.get("page_length") or 20)))
		offset = (page_num - 1) * page_len

		filters = {}
		_apply_common_scope_filters(filters, payload, current_user, is_staff)
		scope_for_summary = dict(filters)
		request_scope = dict(filters)

		_apply_status_filter(filters, target_status)

		booking_fields = [
			"name",
			"amenity",
			"booking_date",
			"start_time",
			"end_time",
			"guests_count",
			"status",
			"tenant",
			"tenant_name",
			"property_unit",
			"lease",
			"notes",
			"cancellation_reason",
			"rejection_reason",
			"approved_by",
			"approved_on",
			"series",
			"creation",
		]
		request_fields = [
			"name",
			"amenity",
			"booking_date",
			"start_time",
			"end_time",
			"guests_count",
			"status",
			"tenant",
			"tenant_name",
			"property_unit",
			"lease",
			"notes",
			"rejection_reason",
			"approved_by",
			"approved_on",
			"booking",
			"series",
			"creation",
		]

		combined = []

		# Bookings (skip when filter is request-only "open")
		if filters.get("status") != "__skip__":
			bookings = frappe.get_all(
				AMENITY_BOOKING,
				filters=filters,
				fields=booking_fields,
				order_by="booking_date desc, start_time desc",
				ignore_permissions=True,
			)
			_enrich_bookings(bookings)
			combined.extend(_tag_booking_row(b) for b in bookings)

		req_statuses = _request_statuses_for_list(target_status)
		if req_statuses:
			req_filters = dict(request_scope)
			if len(req_statuses) == 1:
				req_filters["status"] = req_statuses[0]
			else:
				req_filters["status"] = ["in", req_statuses]
			requests = frappe.get_all(
				AMENITY_BOOKING_REQUEST,
				filters=req_filters,
				fields=request_fields,
				order_by="booking_date desc, start_time desc",
				ignore_permissions=True,
			)
			_enrich_bookings(requests)
			combined.extend(_tag_request_row(r) for r in requests)

		combined.sort(key=_row_sort_key, reverse=True)
		page_rows = combined[offset : offset + page_len]

		hub_all = _build_hub_items(
			combined, current_user, is_staff, payload, target_status
		)
		hub_page = hub_all[offset : offset + page_len]

		counts = _status_summary(scope_for_summary, request_scope)
		series_filters = dict(scope_for_summary)
		# Series uses amenity/tenant only (no booking_date on Series)
		series_count_filters = {}
		if "tenant" in series_filters:
			series_count_filters["tenant"] = series_filters["tenant"]
		if "amenity" in series_filters:
			series_count_filters["amenity"] = series_filters["amenity"]
		counts["series"] = frappe.db.count(
			AMENITY_BOOKING_SERIES, filters=series_count_filters
		)

		return {
			"status": "success",
			"is_staff": is_staff,
			"scope": "all_tenants" if is_staff else "own",
			"summary": counts,
			"page": page_num,
			"page_length": page_len,
			# Flat occurrence list (backward compatible); includes series_id when part of a series
			"bookings": page_rows,
			# Preferred hub: series cards + one-time cards
			"hub": hub_page,
			"hub_total": len(hub_all),
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "get_my_bookings")
		return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def cancel_booking(
	booking_id=None, cancellation_reason=None, request_id=None, cancel_all=None
):
	"""Cancel Open Request, one Confirmed day, or all remaining days.

	Recurring flexibility:
	- ``booking_id`` → cancel that one day
	- ``request_id`` only → cancel next upcoming day (one-by-one)
	- ``request_id`` + ``cancel_all=1`` → cancel all remaining days
	- or ``cancel_remaining_amenity_booking_series`` for series-level cancel-all
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(_("Authentication required"), frappe.AuthenticationError)

		payload = _parse_request_payload(
			{
				"booking_id": booking_id,
				"request_id": request_id,
				"cancellation_reason": cancellation_reason,
				"cancel_all": cancel_all,
			}
		)
		bid = (payload.get("booking_id") or "").strip()
		rid = (payload.get("request_id") or "").strip()
		reason = (payload.get("cancellation_reason") or "").strip() or None
		do_all = cint(payload.get("cancel_all") or 0) == 1

		if not bid and not rid:
			return {"status": "error", "message": "booking_id or request_id is required"}

		current_user = frappe.session.user
		is_staff = _is_amenity_staff(current_user)

		# 1) Explicit Amenity Booking id = cancel that day
		if bid and frappe.db.exists(AMENITY_BOOKING, bid):
			doc = frappe.get_doc(AMENITY_BOOKING, bid)
			if not is_staff and doc.tenant != current_user:
				frappe.throw(
					_("Not permitted to cancel this booking"), frappe.PermissionError
				)
			# cancel_all with a booking_id → cancel all days on that booking's request
			if do_all and getattr(doc, "request", None):
				req = frappe.get_doc(AMENITY_BOOKING_REQUEST, doc.request)
				return _cancel_approved_request(
					req, reason, current_user, is_staff, cancel_all=True
				)
			result = _cancel_booking_doc(doc, reason, current_user, is_staff)
			if result.get("status") == "success" and getattr(doc, "request", None):
				_maybe_mark_series_cancelled(getattr(doc, "series", None))
				frappe.db.commit()
				remaining = frappe.db.count(
					AMENITY_BOOKING,
					{"request": doc.request, "status": "Confirmed"},
				)
				result["remaining_count"] = remaining
				result["request_id"] = doc.request
			return result

		# 2) Request id
		req_name = rid if (rid and frappe.db.exists(AMENITY_BOOKING_REQUEST, rid)) else None
		if not req_name and bid and frappe.db.exists(AMENITY_BOOKING_REQUEST, bid):
			req_name = bid

		if req_name:
			req = frappe.get_doc(AMENITY_BOOKING_REQUEST, req_name)
			if req.status == "Open":
				return _cancel_open_request(req, reason, current_user, is_staff)
			if req.status == "Approved":
				return _cancel_approved_request(
					req,
					reason,
					current_user,
					is_staff,
					booking_id=None,
					cancel_all=do_all,
				)
			if req.status == "Cancelled":
				return {"status": "error", "message": "Request is already cancelled"}
			return {
				"status": "error",
				"message": f"Cannot cancel request in status {req.status}",
			}

		return {
			"status": "error",
			"message": f"Booking {bid or rid} not found",
		}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "cancel_booking")
		return {"status": "error", "message": str(e)}
