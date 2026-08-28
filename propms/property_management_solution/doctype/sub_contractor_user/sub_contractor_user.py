# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils.password import update_password


class SubContractorUser(Document):
	pass


def _ensure_role_exists(role_name: str) -> None:
	"""Ensure the role exists so we can assign it to created Users."""
	if not role_name:
		return
	if frappe.db.exists("Role", role_name):
		return

	try:
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 0
		if hasattr(role, "is_custom"):
			role.is_custom = 1
		role.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()


def _get_existing_subcontractor_for_email(email, exclude_name=None):
	"""Return name of a Sub Contractor User doc that already uses this email, or None."""
	if not email:
		return None

	filters = {"user_email": ("=", email)}
	if exclude_name:
		filters["name"] = ("!=", exclude_name)
	return frappe.db.get_value("Sub Contractor User", filters, "name")


def _ensure_user_for_doc(doc):
	"""Create or update the Website User for this subcontractor user doc."""
	if not doc.user_email:
		return

	# Reuse linked user if present; otherwise fall back to email
	user_name = doc.user or doc.user_email

	if frappe.db.exists("User", user_name):
		user = frappe.get_doc("User", user_name)
	elif frappe.db.exists("User", doc.user_email):
		user = frappe.get_doc("User", doc.user_email)
	else:
		user = None

	password = (
		doc.get_password("user_password")
		if getattr(doc, "user_password", None)
		else None
	)

	if not user:
		# Create new Website User
		user = frappe.new_doc("User")
		user.email = doc.user_email
		user.first_name = doc.full_name or doc.sub_contractor_name or doc.user_email
		user.user_type = "Website User"
		user.send_welcome_email = 0
		user.enabled = 1 if doc.get("enabled") else 0
		user.insert(ignore_permissions=True)
		frappe.db.commit()
	else:
		# Update existing user basic info
		user.first_name = doc.full_name or doc.sub_contractor_name or doc.user_email
		user.enabled = 1 if doc.get("enabled") else 0

	# If a password was entered, always (re)set it
	if password:
		update_password(user.name, password)
		frappe.db.commit()

	# Ensure Mobile Sub Contractor role
	role_name = "Mobile Sub Contractor"
	_ensure_role_exists(role_name)
	if frappe.db.exists("Role", role_name) and role_name not in [
		r.role for r in user.roles
	]:
		user.add_roles(role_name)

	user.save(ignore_permissions=True)
	frappe.db.commit()

	# Link back to doc
	if doc.user != user.name:
		doc.db_set("user", user.name)
		frappe.db.commit()


def before_insert(doc, method=None):
	"""Validate email uniqueness before first insert."""
	if not doc.user_email:
		return
	existing = _get_existing_subcontractor_for_email(doc.user_email)
	if existing:
		frappe.throw(
			frappe._("Another subcontractor user already uses email {0}. Use a different email.").format(
				doc.user_email
			)
		)


def validate(doc, method=None):
	"""Validate email uniqueness on every save (excluding current doc)."""
	if not doc.user_email:
		return
	existing = _get_existing_subcontractor_for_email(doc.user_email, exclude_name=doc.name)
	if existing:
		frappe.throw(
			frappe._("Another subcontractor user already uses email {0}. Use a different email.").format(
				doc.user_email
			)
		)


def after_insert(doc, method=None):
	"""Create or link User after inserting Sub Contractor User doc."""
	_ensure_user_for_doc(doc)


def on_update(doc, method=None):
	"""Sync linked User when Sub Contractor User doc is updated."""
	_ensure_user_for_doc(doc)
