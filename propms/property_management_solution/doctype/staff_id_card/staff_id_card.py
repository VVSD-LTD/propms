# -*- coding: utf-8 -*-
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

VALID_APPLICANT_TYPES = ("Resident", "Staff")
STAFF_JOB_ROLES = ("Maid", "Nanny", "Driver", "Cook", "Other")
# Backward-compat alias (staff roles only — use for Flutter role pickers)
VALID_ROLES = STAFF_JOB_ROLES
RESIDENT_JOB_ROLE = "Resident"
ALL_JOB_ROLES = (RESIDENT_JOB_ROLE,) + STAFF_JOB_ROLES
VALID_GENDERS = ("Male", "Female")
FREE_CARD_LIMIT = 3
PAID_FEE_TSH = 20000

STAFF_ROLES = (
	"System Manager",
	"Property Manager",
	"Mobile Maintenance Manager",
	"Mobile Maintenance Officer",
)


def is_staff_user(user=None):
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	return bool(set(frappe.get_roles(user)).intersection(STAFF_ROLES))


def count_approved_cards(tenant):
	"""Count submitted+Approved cards (docstatus 1) for free-quota."""
	if not tenant:
		return 0
	return frappe.db.count(
		"Staff ID Card",
		{"tenant": tenant, "status": "Approved", "docstatus": 1},
	)


def free_slots_remaining(tenant):
	return max(0, FREE_CARD_LIMIT - count_approved_cards(tenant))


def _resident_from_tenant_row(row, lease_name):
	"""Build resident dict from a Tenant Details row (or None if no User)."""
	tenant_user = (row.get("user") or "").strip() or None
	user_email = (row.get("user_email") or "").strip() or None

	if not tenant_user and user_email:
		tenant_user = frappe.db.get_value("User", {"email": user_email}, "name")
		if not tenant_user and frappe.db.exists("User", user_email):
			tenant_user = user_email

	if not tenant_user:
		return None

	full_name = (
		row.get("full_name")
		or frappe.db.get_value("User", tenant_user, "full_name")
		or tenant_user
	)
	mobile = frappe.db.get_value("User", tenant_user, "mobile_no") or ""
	email = (
		user_email
		or frappe.db.get_value("User", tenant_user, "email")
		or tenant_user
	)
	approved = count_approved_cards(tenant_user)
	remaining = max(0, FREE_CARD_LIMIT - approved)
	return {
		"lease": lease_name,
		"tenant": tenant_user,
		"tenant_name": full_name,
		"resident_mobile": mobile,
		"resident_email": email,
		"approved_count": approved,
		"free_slots_remaining": remaining,
		"free_card_limit": FREE_CARD_LIMIT,
		"quota_info": _format_quota_info(approved, remaining),
		"is_paid_issuance": 0 if remaining > 0 else 1,
		"fee_note": _format_fee_note(approved, remaining),
	}


def resolve_resident_for_property(property_unit, tenant=None):
	"""Resolve active lease + resident User from an apartment / Property.

	If multiple residents, returns ``residents`` list and picks ``tenant`` when given.
	"""
	if not property_unit:
		frappe.throw(_("Apartment / Unit is required"))

	if not frappe.db.exists("Property", property_unit):
		frappe.throw(_("Apartment / Unit {0} not found").format(property_unit))

	leases = frappe.get_all(
		"Lease",
		filters={"property": property_unit, "lease_status": "Active"},
		fields=["name"],
		order_by="modified desc",
		limit_page_length=5,
	)
	if not leases:
		frappe.throw(
			_("No active Lease found for apartment {0}").format(property_unit)
		)

	lease_name = leases[0].name
	rows = frappe.get_all(
		"Tenant Details",
		filters={"parent": lease_name, "parenttype": "Lease"},
		fields=["name", "user", "user_email", "full_name", "enabled"],
		order_by="modified desc",
	)
	enabled_rows = [r for r in rows if cint(r.get("enabled"))]
	candidates = enabled_rows or rows
	if not candidates:
		frappe.throw(
			_("No resident (Tenant Details) linked on Lease {0}").format(lease_name)
		)

	residents = []
	seen = set()
	for row in candidates:
		info = _resident_from_tenant_row(row, lease_name)
		if not info or info["tenant"] in seen:
			continue
		seen.add(info["tenant"])
		residents.append(info)

	if not residents:
		frappe.throw(
			_(
				"Apartment {0} has a lease but no App User linked. "
				"Link a User on Tenant Details first."
			).format(property_unit)
		)

	chosen = None
	if tenant:
		for r in residents:
			if r["tenant"] == tenant:
				chosen = r
				break
	if not chosen:
		chosen = residents[0]

	out = dict(chosen)
	out["residents"] = [
		{
			"tenant": r["tenant"],
			"tenant_name": r["tenant_name"],
			"resident_email": r["resident_email"],
			"free_slots_remaining": r["free_slots_remaining"],
		}
		for r in residents
	]
	out["multiple_residents"] = len(residents) > 1
	return out


def _format_quota_info(approved, remaining):
	return _("{0} of {1} free used · {2} left").format(
		approved, FREE_CARD_LIMIT, remaining
	)


def _format_fee_note(approved, remaining):
	if remaining > 0:
		return _(
			"Free issuance available ({0} of {1} free cards used)."
		).format(approved, FREE_CARD_LIMIT)
	return _(
		"Free quota used ({0}/{1}). Further cards cost Tshs {2}/= — "
		"contact management before creating."
	).format(approved, FREE_CARD_LIMIT, f"{PAID_FEE_TSH:,}")


@frappe.whitelist()
def get_resident_for_unit(property_unit=None, tenant=None):
	"""Desk: resolve resident/lease/quota when Apartment / Unit is selected."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	return resolve_resident_for_property(property_unit, tenant=tenant)


class StaffIDCard(Document):
	def before_insert(self):
		if not self.submitted_by and frappe.session.user != "Guest":
			self.submitted_by = frappe.session.user
		# Desk / mobile create → Draft until paid (then submit).
		if not self.status:
			self.status = "Draft"
		elif self.docstatus == 0 and self.status == "Submitted" and not getattr(
			self.flags, "will_submit", False
		):
			pass
		if not self.payment_status:
			self.payment_status = "Unpaid"

	def before_validate(self):
		# Fill unit → resident before Frappe mandatory / link checks
		self._apply_unit_and_resident()
		self._apply_on_behalf_and_declaration()
		self._apply_applicant_type()
		# Quota UI/enforcement paused — client has not approved yet.
		# self._apply_quota_fields()
		if not self.payment_status:
			self.payment_status = "Unpaid"

	def _apply_applicant_type(self):
		"""Normalize applicant_type and job_role for Resident vs Staff."""
		atype = (self.applicant_type or "").strip() or "Staff"
		if atype not in VALID_APPLICANT_TYPES:
			frappe.throw(
				_("Application for must be Resident or Staff"),
				title=_("Invalid applicant type"),
			)
		self.applicant_type = atype
		if atype == "Resident":
			self.job_role = RESIDENT_JOB_ROLE
			self.job_role_other = None

	def validate(self):
		atype = (self.applicant_type or "Staff").strip()
		if atype == "Resident":
			if self.job_role != RESIDENT_JOB_ROLE:
				self.job_role = RESIDENT_JOB_ROLE
		elif self.job_role and self.job_role not in STAFF_JOB_ROLES:
			frappe.throw(_("Invalid job / role"))
		if self.job_role == "Other" and not (self.job_role_other or "").strip():
			frappe.throw(_("Please specify the Other role"))
		if self.gender and self.gender not in VALID_GENDERS:
			frappe.throw(_("Gender must be Male or Female"))

		is_draft = cint(self.docstatus) == 0 and (self.status or "Draft") == "Draft"
		if not is_draft:
			if not cint(self.declaration_accepted):
				frappe.throw(_("You must accept the declaration and ID card rules"))
			if not self.passport_photo:
				frappe.throw(_("Passport-size photo is required"))
			if not self.id_copy:
				frappe.throw(_("ID copy is required"))

		if not self.property_unit:
			frappe.throw(
				_("Please select Apartment / Unit at the top of the form")
			)
		if not self.tenant:
			frappe.throw(
				_(
					"Could not resolve Resident User for this apartment. "
					"Check the unit has an Active Lease with a linked App User."
				)
			)

		# Free quota against the resident (tenant), not the desk user.
		# Paused — client has not approved quota validation yet.
		# if self.is_new() or self.has_value_changed("tenant"):
		# 	remaining = free_slots_remaining(self.tenant)
		# 	if remaining <= 0:
		# 		frappe.throw(
		# 			_(
		# 				"Resident already has {0} free Staff ID cards. "
		# 				"Further cards cost Tshs {1}/= — contact Viva management."
		# 			).format(FREE_CARD_LIMIT, f"{PAID_FEE_TSH:,}")
		# 		)

		if self.status == "Approved" and not self.id_card_no:
			frappe.throw(_("ID Card No. is required when Approved"))
		if self.status == "Approved" and not self.scanned_id_image:
			frappe.throw(_("Scanned ID image is required when Approved"))
		if self.status == "Approved" and (self.payment_status or "") != "Paid":
			frappe.throw(_("ID Card must be Paid before Approval"))

	def before_submit(self):
		if not cint(self.declaration_accepted):
			frappe.throw(_("You must accept the declaration and ID card rules"))
		if not self.passport_photo:
			frappe.throw(_("Passport-size photo is required"))
		if not self.id_copy:
			frappe.throw(_("ID copy is required"))

		# Mobile path: Selcom must mark Paid first.
		# Desk on-behalf by staff may submit without Selcom (offline/manual).
		paid = (self.payment_status or "") == "Paid"
		staff = is_staff_user()
		if not paid and not (staff and cint(self.on_behalf)):
			frappe.throw(
				_("Payment is required before submitting an ID Card request")
			)
		if paid:
			self.is_paid_issuance = 1

		if self.status in (None, "", "Draft"):
			self.status = "Submitted"
		if not self.submitted_by and frappe.session.user != "Guest":
			self.submitted_by = frappe.session.user

	def on_submit(self):
		if self.status == "Draft":
			self.db_set("status", "Submitted", update_modified=False)
		# Desk Submit path (mobile create already notifies after submit)
		if not getattr(self.flags, "skip_submit_notify", False):
			try:
				from propms.api.v1.staff_id_card.notify import notify_staff_id_submitted

				notify_staff_id_submitted(self)
			except Exception:
				frappe.log_error(frappe.get_traceback(), "StaffIDCard.on_submit.notify")

	def before_cancel(self):
		self.status = "Cancelled"

	def on_cancel(self):
		self.db_set("status", "Cancelled", update_modified=False)

	def apply_approval_update(self):
		"""Save approval/rejection fields on a submitted document."""
		self.flags.ignore_validate_update_after_submit = True
		self.save(ignore_permissions=True)

	def _apply_unit_and_resident(self):
		"""Fill lease/tenant from unit when missing or unit changed on new docs."""
		user = frappe.session.user
		staff = is_staff_user(user)

		# Mobile tenant self-submit: default tenant to session user if not set
		if not self.tenant and not staff and user != "Guest":
			self.tenant = user

		if self.property_unit and (
			self.is_new()
			or self.has_value_changed("property_unit")
			or not self.tenant
			or not self.lease
		):
			try:
				info = resolve_resident_for_property(
					self.property_unit, tenant=self.tenant
				)
			except frappe.ValidationError:
				# Mobile may send unit without active lease resolve — only hard-fail for staff/desk
				if staff or self.on_behalf or not self.tenant:
					raise
				return

			# Staff / on-behalf: always take resolved resident from unit
			if staff or cint(self.on_behalf) or not self.tenant:
				self.lease = info["lease"]
				self.tenant = info["tenant"]
				self.tenant_name = info["tenant_name"]
				self.resident_mobile = info["resident_mobile"]
				self.resident_email = info["resident_email"]
			else:
				# Tenant path: fill blanks only; keep their tenant
				if not self.lease:
					self.lease = info["lease"]
				if not self.tenant_name:
					self.tenant_name = info["tenant_name"]
				if not self.resident_mobile:
					self.resident_mobile = info["resident_mobile"]
				if not self.resident_email:
					self.resident_email = info["resident_email"]

		if self.tenant and not self.tenant_name:
			self.tenant_name = (
				frappe.db.get_value("User", self.tenant, "full_name") or self.tenant
			)

	def _apply_on_behalf_and_declaration(self):
		user = frappe.session.user
		staff = is_staff_user(user)

		if staff and self.tenant and self.tenant != user:
			self.on_behalf = 1
		elif staff and cint(self.on_behalf):
			pass
		elif not staff:
			self.on_behalf = 0

		if cint(self.on_behalf) or (staff and self.tenant and self.tenant != user):
			self.declaration_accepted = 1
			self.on_behalf = 1

		if not self.submitted_by and user != "Guest":
			self.submitted_by = user

	def _apply_quota_fields(self):
		# Kept for when client re-enables free-quota validation.
		if not self.tenant:
			return
		approved = count_approved_cards(self.tenant)
		remaining = max(0, FREE_CARD_LIMIT - approved)
		self.quota_info = _format_quota_info(approved, remaining)
		self.is_paid_issuance = 0 if remaining > 0 else 1
		self.fee_note = _format_fee_note(approved, remaining)
