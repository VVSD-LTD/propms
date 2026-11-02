# -*- coding: utf-8 -*-
# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, nowdate

from propms.api.v1.amenities.list import _is_amenity_staff
from propms.api.v1.amenities.overlap import find_conflicting_booking
from propms.api.v1.amenities.request import _validate_occurrence_slot
from propms.api.v1.amenities.slots import _parse_time_str
from propms.api.v1.gate_pass.gate_pass import _get_tenant_default_unit


class AmenityBookingRequest(Document):
	def before_validate(self):
		if not self.series_type:
			self.series_type = "One Time"
		if not self.status:
			self.status = "Open"
		if not self.guests_count:
			self.guests_count = 1

		self._seed_or_sync_occurrences()

	def validate(self):
		self._resolve_tenant_defaults()
		self._guard_locked_status()

		if self.status == "Rejected" and not self.rejection_reason:
			frappe.throw(_("Rejection Reason is required when status is Rejected"))

		if not self.occurrences:
			frappe.throw(_("Add at least one date under Requested Dates"))

		# Parent summary = earliest occurrence (list / filters)
		first = sorted(
			self.occurrences,
			key=lambda r: (str(r.booking_date or ""), str(r.start_time or "")),
		)[0]
		self.booking_date = first.booking_date
		self.start_time = first.start_time
		self.end_time = first.end_time

		if self.series_type == "One Time" and len(self.occurrences) > 1:
			frappe.throw(
				_("One Time requests can only have one occurrence. Switch Series Type to Recurring.")
			)

		# Slot + overlap rules for Open requests (desk + API inserts)
		if self.status == "Open":
			self._validate_open_slots_and_conflicts()

	def after_insert(self):
		"""Desk create: auto-approve amenities → Series + Booking(s) on Save.

		Mobile/API sets ``flags.skip_auto_fulfill`` and runs fulfill itself (custom notify).
		"""
		if getattr(self.flags, "skip_auto_fulfill", False):
			return
		if self.status != "Open" or not self.amenity:
			return
		if not cint(frappe.db.get_value("Amenity", self.amenity, "auto_approval")):
			return
		from propms.api.v1.amenities.request import fulfill_request_to_series_and_bookings

		self.reload()
		result = fulfill_request_to_series_and_bookings(self, notify=True, commit=False)
		if result.get("status") != "success":
			frappe.throw(result.get("message") or _("Auto-approval failed"))

	def _seed_or_sync_occurrences(self):
		"""Desk-friendly: One Time keeps a single child row from Date/Start/End."""
		if not (self.booking_date and self.start_time and self.end_time):
			return

		if self.series_type == "One Time":
			if not self.occurrences:
				self.append(
					"occurrences",
					{
						"booking_date": self.booking_date,
						"start_time": self.start_time,
						"end_time": self.end_time,
						"status": "Pending",
					},
				)
			elif len(self.occurrences) == 1:
				row = self.occurrences[0]
				if (row.status or "Pending") == "Pending" and not row.booking:
					row.booking_date = self.booking_date
					row.start_time = self.start_time
					row.end_time = self.end_time
			return

		# Recurring: if table empty but header filled, seed first day so Save works
		if not self.occurrences:
			self.append(
				"occurrences",
				{
					"booking_date": self.booking_date,
					"start_time": self.start_time,
					"end_time": self.end_time,
					"status": "Pending",
				},
			)

	def _resolve_tenant_defaults(self):
		is_staff = _is_amenity_staff()
		# Do not silently assign the logged-in staff user as the resident
		if not self.tenant:
			if is_staff:
				frappe.throw(_("Please select the Resident User for this booking request"))
			if frappe.session.user != "Guest":
				self.tenant = frappe.session.user

		if self.tenant and not self.tenant_name:
			self.tenant_name = (
				frappe.db.get_value("User", self.tenant, "full_name") or self.tenant
			)

		# Fill unit / lease when staff picks a resident and left them blank
		if self.tenant and (not self.property_unit or not self.lease):
			email = frappe.db.get_value("User", self.tenant, "email") or self.tenant
			unit, lease, resident = _get_tenant_default_unit(email)
			if not self.property_unit and unit:
				self.property_unit = unit
			if not self.lease and lease:
				self.lease = lease
			if not self.tenant_name and resident:
				self.tenant_name = resident

	def _guard_locked_status(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		if before.status == "Open":
			return
		# After Open, only notes may change via desk (status changes go through buttons)
		protected = (
			"amenity",
			"series_type",
			"booking_date",
			"series_end_date",
			"start_time",
			"end_time",
			"guests_count",
			"tenant",
			"property_unit",
			"lease",
			"status",
			"week_mon",
			"week_tue",
			"week_wed",
			"week_thu",
			"week_fri",
			"week_sat",
			"week_sun",
		)
		for field in protected:
			if self.has_value_changed(field):
				frappe.throw(
					_("Cannot change {0} after the request is {1}. Use Cancel / Approve / Reject.").format(
						frappe.bold(_(self.meta.get_label(field))), before.status
					)
				)

	def _validate_open_slots_and_conflicts(self):
		if not self.amenity or not frappe.db.exists("Amenity", self.amenity):
			frappe.throw(_("Valid Amenity is required"))

		amenity_doc = frappe.get_doc("Amenity", self.amenity)
		is_staff = _is_amenity_staff()

		if not amenity_doc.is_active:
			frappe.throw(_("{0} is currently inactive").format(amenity_doc.amenity_name))
		if not cint(amenity_doc.requires_booking):
			frappe.throw(_("This amenity does not require booking"))
		if not cint(amenity_doc.is_published) and not is_staff:
			frappe.throw(_("Amenity is not published yet"))

		today = getdate(nowdate())
		seen = set()
		for row in self.occurrences:
			if (row.status or "Pending") not in ("Pending", ""):
				continue
			if not row.booking_date or row.start_time is None or row.end_time is None:
				frappe.throw(_("Each occurrence needs Booking Date, Start Time and End Time"))

			s_time = _parse_time_str(row.start_time).strftime("%H:%M:%S")
			e_time = _parse_time_str(row.end_time).strftime("%H:%M:%S")
			key = (str(row.booking_date), s_time, e_time)
			if key in seen:
				frappe.throw(_("Duplicate occurrence on {0} {1}–{2}").format(row.booking_date, s_time, e_time))
			seen.add(key)

			# Staff may book past slots for desk corrections; tenants cannot
			slot_err = _validate_occurrence_slot(
				amenity_doc, getdate(row.booking_date), s_time, e_time, today
			)
			if slot_err:
				msg = slot_err.get("message") or _("Invalid slot")
				if is_staff and "past" in msg.lower():
					pass  # allow staff past-date edge cases only for "past date/start"
				elif is_staff and "already started" in msg.lower():
					pass
				else:
					frappe.throw(_("Row {0}: {1}").format(row.idx, msg))

			conflict = find_conflicting_booking(
				self.amenity,
				row.booking_date,
				s_time,
				e_time,
				0,
				exclude_request=self.name if not self.is_new() else None,
			)
			if conflict:
				frappe.throw(
					_("Row {0}: overlaps existing {1} ({2})").format(
						row.idx, conflict.get("source") or "booking", conflict.name
					)
				)


@frappe.whitelist()
def get_resident_booking_defaults(user=None):
	"""Compat wrapper — prefer ``propms.api.v1.amenities.desk.get_resident_booking_defaults``."""
	from propms.api.v1.amenities.desk import (
		get_resident_booking_defaults as _get_defaults,
	)

	return _get_defaults(user=user)
