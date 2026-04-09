# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt


import frappe
from frappe import _, scrub, throw
from frappe.model.naming import set_name_by_naming_series
from frappe.permissions import (
	add_user_permission,
	get_doc_permissions,
	has_permission,
	remove_user_permission,
)
from frappe.utils import cstr, getdate, today, validate_email_address
from frappe.utils.deprecations import deprecated
from frappe.utils.nestedset import NestedSet

from erpnext.utilities.transaction_base import delete_events


class SubContractor(NestedSet):
	nsm_parent_field = "reports_to"

	def autoname(self):
		set_name_by_naming_series(self)
		self.sub_contractor = self.name

	def validate(self):
		from erpnext.controllers.status_updater import validate_status

		validate_status(self.status, ["Active", "Inactive", "Suspended", "Left"])

		self.sub_contractor = self.name
		self.set_sub_contractor_name()
		self.validate_date()
		self.validate_email()
		self.validate_status()
		self.validate_reports_to()
		self.set_preferred_email()
		self.validate_preferred_email()

		if self.user_id:
			self.validate_user_details()
		else:
			existing_user_id = frappe.db.get_value("Sub Contractor", self.name, "user_id")
			if existing_user_id:
				user = frappe.get_doc("User", existing_user_id)
				validate_sub_contractor_role(user, ignore_emp_check=True)
				user.save(ignore_permissions=True)
				remove_user_permission("Sub Contractor", self.name, existing_user_id)

	def after_rename(self, old, new, merge):
		self.db_set("sub_contractor", new)

	def set_sub_contractor_name(self):
		self.sub_contractor_name = " ".join(
			filter(lambda x: x, [self.first_name, self.middle_name, self.last_name])
		)

	def validate_user_details(self):
		if self.user_id:
			data = frappe.db.get_value("User", self.user_id, ["enabled"], as_dict=1)

			if not data:
				self.user_id = None
				return

			self.validate_for_enabled_user_id(data.get("enabled", 0))
			self.validate_duplicate_user_id()

	def update_nsm_model(self):
		frappe.utils.nestedset.update_nsm(self)

	def on_update(self):
		self.update_nsm_model()
		frappe.clear_cache()
		if self.user_id:
			self.update_user()
			self.update_user_permissions()
		self.reset_sub_contractor_emails_cache()

	def update_user_permissions(self):
		if not self.has_value_changed("user_id") and not self.has_value_changed("create_user_permission"):
			return

		sub_contractor_user_permission_exists = frappe.db.exists(
			"User Permission", {"allow": "Sub Contractor", "for_value": self.name, "user": self.user_id}
		)

		if sub_contractor_user_permission_exists and not self.create_user_permission:
			remove_user_permission("Sub Contractor", self.name, self.user_id)
			remove_user_permission("Company", self.company, self.user_id)
		elif not sub_contractor_user_permission_exists and self.create_user_permission:
			add_user_permission("Sub Contractor", self.name, self.user_id)
			add_user_permission("Company", self.company, self.user_id)

	def update_user(self):
		# add sub_contractor role if missing
		user = frappe.get_doc("User", self.user_id)
		user.flags.ignore_permissions = True

		if "Sub Contractor" not in user.get("roles"):
			user.append_roles("Sub Contractor")

		# copy details like Fullname, DOB and Image to User
		if self.sub_contractor_name and not (user.first_name and user.last_name):
			sub_contractor_name = self.sub_contractor_name.split(" ")
			if len(sub_contractor_name) >= 3:
				user.last_name = " ".join(sub_contractor_name[2:])
				user.middle_name = sub_contractor_name[1]
			elif len(sub_contractor_name) == 2:
				user.last_name = sub_contractor_name[1]

			user.first_name = sub_contractor_name[0]

		if self.gender:
			user.gender = self.gender

		if self.image:
			if not user.user_image:
				user.user_image = self.image
				try:
					frappe.get_doc(
						{
							"doctype": "File",
							"file_url": self.image,
							"attached_to_doctype": "User",
							"attached_to_name": self.user_id,
						}
					).insert(ignore_if_duplicate=True)
				except frappe.DuplicateEntryError:
					# already exists
					pass

		user.save()

	def validate_date(self):
		# if self.date_of_birth and getdate(self.date_of_birth) > getdate(today()):
		# 	throw(_("Date of Birth cannot be greater than today."))

		self.validate_from_to_dates("date_of_birth", "date_of_joining")
		self.validate_from_to_dates("date_of_joining", "date_of_retirement")
		self.validate_from_to_dates("date_of_joining", "relieving_date")
		self.validate_from_to_dates("date_of_joining", "contract_end_date")

	def validate_email(self):
		if self.company_email:
			validate_email_address(self.company_email, True)
		if self.personal_email:
			validate_email_address(self.personal_email, True)

	def set_preferred_email(self):
		preferred_email_field = frappe.scrub(self.prefered_contact_email)
		self.prefered_email = self.get(preferred_email_field) if preferred_email_field else None

	def validate_status(self):
		if self.status == "Left":
			reports_to = frappe.db.get_all(
				"Sub Contractor",
				filters={"reports_to": self.name, "status": "Active"},
				fields=["name", "sub_contractor_name"],
			)
			if reports_to:
				link_to_sub_contractors = [
					frappe.utils.get_link_to_form("Sub Contractor", sub_contractor.name, label=sub_contractor.sub_contractor_name)
					for sub_contractor in reports_to
				]
				message = _("The following sub_contractors are currently still reporting to {0}:").format(
					frappe.bold(self.sub_contractor_name)
				)
				message += "<br><br><ul><li>" + "</li><li>".join(link_to_sub_contractors)
				message += "</li></ul><br>"
				message += _("Please make sure the sub_contractors above report to another Active sub_contractor.")
				throw(message, InactiveSubContractorStatusError, _("Cannot Relieve Sub Contractor"))
			if not self.relieving_date:
				throw(_("Please enter relieving date."))

	def validate_for_enabled_user_id(self, enabled):
		if not self.status == "Active":
			return

		if enabled is None:
			frappe.throw(_("User {0} does not exist").format(self.user_id))
		if enabled == 0:
			frappe.throw(_("User {0} is disabled").format(self.user_id))

	def validate_duplicate_user_id(self):
		Sub_Contractor = frappe.qb.DocType("Sub Contractor")
		sub_contractor = (
			frappe.qb.from_(Sub_Contractor)
			.select(Sub_Contractor.name)
			.where(
				(Sub_Contractor.user_id == self.user_id)
				& (Sub_Contractor.status == "Active")
				& (Sub_Contractor.name != self.name)
			)
		).run()
		if sub_contractor:
			throw(
				_("User {0} is already assigned to Sub Contractor {1}").format(self.user_id, sub_contractor[0][0]),
				frappe.DuplicateEntryError,
			)

	def validate_reports_to(self):
		if self.reports_to == self.name:
			throw(_("Sub Contractor cannot report to himself."))

	def on_trash(self):
		self.update_nsm_model()
		delete_events(self.doctype, self.name)

	def validate_preferred_email(self):
		if self.prefered_contact_email and not self.get(scrub(self.prefered_contact_email)):
			frappe.msgprint(_("Please enter {0}").format(self.prefered_contact_email))

	def reset_sub_contractor_emails_cache(self):
		prev_doc = self.get_doc_before_save() or {}
		cell_number = cstr(self.get("cell_number"))
		prev_number = cstr(prev_doc.get("cell_number"))
		if cell_number != prev_number or self.get("user_id") != prev_doc.get("user_id"):
			frappe.cache().hdel("sub_contractors_with_number", cell_number)
			frappe.cache().hdel("sub_contractors_with_number", prev_number)


def validate_sub_contractor_role(doc, method=None, ignore_emp_check=False):
	# called via User hook
	if not ignore_emp_check:
		if frappe.db.get_value("Sub Contractor", {"user_id": doc.name}):
			return

	user_roles = [d.role for d in doc.get("roles")]
	if "Sub Contractor" in user_roles:
		frappe.msgprint(_("User {0}: Removed Sub Contractor role as there is no mapped sub contractor.").format(doc.name))
		doc.get("roles").remove(doc.get("roles", {"role": "Sub Contractor"})[0])


@deprecated
def update_user_permissions(doc, method):
	# formerly called via User hook
	if "Sub Contractor" in [d.role for d in doc.get("roles")]:
		if not has_permission("User Permission", ptype="write", raise_exception=False):
			return
		sub_contractor = frappe.get_doc("Sub Contractor", {"user_id": doc.name})
		sub_contractor.update_user_permissions()


def get_sub_contractor_email(sub_contractor_doc):
	return (
		sub_contractor_doc.get("user_id") or sub_contractor_doc.get("personal_email") or sub_contractor_doc.get("company_email")
	)


def get_holiday_list_for_sub_contractor(sub_contractor, raise_exception=True):
	if sub_contractor:
		holiday_list, company = frappe.get_cached_value("Sub Contractor", sub_contractor, ["holiday_list", "company"])
	else:
		holiday_list = ""
		company = frappe.db.get_single_value("Global Defaults", "default_company")

	if not holiday_list:
		holiday_list = frappe.get_cached_value("Company", company, "default_holiday_list")

	if not holiday_list and raise_exception:
		frappe.throw(
			_("Please set a default Holiday List for Sub Contractor {0} or Company {1}").format(sub_contractor, company)
		)

	return holiday_list


def is_holiday(sub_contractor, date=None, raise_exception=True, only_non_weekly=False, with_description=False):
	"""
	Returns True if given Sub Contractor has an holiday on the given date
	        :param sub_contractor: Sub Contractor `name`
	        :param date: Date to check. Will check for today if None
	        :param raise_exception: Raise an exception if no holiday list found, default is True
	        :param only_non_weekly: Check only non-weekly holidays, default is False
	"""

	holiday_list = get_holiday_list_for_sub_contractor(sub_contractor, raise_exception)
	if not date:
		date = today()

	if not holiday_list:
		return False

	filters = {"parent": holiday_list, "holiday_date": date}
	if only_non_weekly:
		filters["weekly_off"] = False

	holidays = frappe.get_all("Holiday", fields=["description"], filters=filters, pluck="description")

	if with_description:
		return len(holidays) > 0, holidays

	return len(holidays) > 0


@frappe.whitelist()
def create_user(sub_contractor, user=None, email=None):
	subcontractor = frappe.get_doc("Sub Contractor", sub_contractor)

	sub_contractor_name = subcontractor.sub_contractor_name.split(" ")
	middle_name = last_name = ""

	if len(sub_contractor_name) >= 3:
		last_name = " ".join(sub_contractor_name[2:])
		middle_name = sub_contractor_name[1]
	elif len(sub_contractor_name) == 2:
		last_name = sub_contractor_name[1]

	first_name = sub_contractor_name[0]

	if email:
		subcontractor.prefered_email = email

	user = frappe.new_doc("User")
	user.update(
		{
			"name": subcontractor.sub_contractor_name,
			"email": subcontractor.prefered_email,
			"enabled": 1,
			"first_name": first_name,
			"middle_name": middle_name,
			"last_name": last_name,
			"gender": subcontractor.gender,
			"birth_date": subcontractor.date_of_birth,
			"phone": subcontractor.cell_number,
			"bio": subcontractor.bio,
		}
	)
	user.insert()
	subcontractor.user_id = user.name
	subcontractor.save()
	return user.name


def get_all_sub_contractor_emails(company):
	"""Returns list of sub contractor emails either based on user_id or company_email"""
	sub_contractor_list = frappe.get_all(
		"Sub Contractor", fields=["name", "sub_contractor_name"], filters={"status": "Active", "company": company}
	)
	sub_contractor_emails = []
	for sub_contractor in sub_contractor_list:
		if not sub_contractor:
			continue
		user, company_email, personal_email = frappe.db.get_value(
			"Sub Contractor", sub_contractor, ["user_id", "company_email", "personal_email"]
		)
		email = user or company_email or personal_email
		if email:
			sub_contractor_emails.append(email)
	return sub_contractor_emails


def get_sub_contractor_emails(sub_contractor_list):
	"""Returns list of sub contractor emails either based on user_id or company_email"""
	sub_contractor_emails = []
	for sub_contractor in sub_contractor_list:
		if not sub_contractor:
			continue
		user, company_email, personal_email = frappe.db.get_value(
			"Sub Contractor", sub_contractor, ["user_id", "company_email", "personal_email"]
		)
		email = user or company_email or personal_email
		if email:
			sub_contractor_emails.append(email)
	return sub_contractor_emails


@frappe.whitelist()
def get_children(doctype, parent=None, company=None, is_root=False, is_tree=False):
	filters = [["status", "=", "Active"]]
	if company and company != "All Companies":
		filters.append(["company", "=", company])

	fields = ["name as value", "sub_contractor_name as title"]

	if is_root:
		parent = ""
	if parent and company and parent != company:
		filters.append(["reports_to", "=", parent])
	else:
		filters.append(["reports_to", "=", ""])

	sub_contractors = frappe.get_list(doctype, fields=fields, filters=filters, order_by="name")

	for sub_contractor in sub_contractors:
		is_expandable = frappe.get_all(doctype, filters=[["reports_to", "=", sub_contractor.get("value")]])
		sub_contractor.expandable = 1 if is_expandable else 0

	return sub_contractors


def on_doctype_update():
	frappe.db.add_index("Sub Contractor", ["lft", "rgt"])


def has_user_permission_for_sub_contractor(user_name, sub_contractor_name):
	return frappe.db.exists(
		{
			"doctype": "User Permission",
			"user": user_name,
			"allow": "Sub Contractor",
			"for_value": sub_contractor_name,
		}
	)


def has_upload_permission(doc, ptype="read", user=None):
	if not user:
		user = frappe.session.user
	if get_doc_permissions(doc, user=user, ptype=ptype).get(ptype):
		return True
	return doc.user_id == user
