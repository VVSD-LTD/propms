# -*- coding: utf-8 -*-
"""Auth API for Property Management Solution."""

from __future__ import unicode_literals

import json
import urllib.parse

import frappe
from propms.custom.lease import get_customer_from_lease

# Roles that represent user type (checked in order for primary user_type)
USER_TYPE_ROLES = (
	"Mobile Maintenance Manager",
	"Mobile Maintenance Officer",
	"Mobile Technician",
	"Mobile VIVA Tenant",
	"Mobile Sub Contractor",
)

MOBILE_MAINTENANCE_ROLES = (
	"Mobile Maintenance Manager",
	"Mobile Maintenance Officer",
	"Mobile Technician",
)


def _get_user_type(roles):
	"""Return the first matching user-type role, or None if none apply."""
	for role in USER_TYPE_ROLES:
		if role in roles:
			return role
	return None


def _get_app_user_record(user_email, user_type):
	"""
	Resolve the mobile-app user record (terms/first_login) for the given email and role.

	Returns dict:
		doctype, name, display_name, app_use_terms_and_conditions, is_first_login
		and for Mobile VIVA Tenant only: tenant (parent Tenant name).

	- Mobile VIVA Tenant -> Tenant Details (child of Lease doctype; lookup by user_email).
	- Mobile Maintenance roles -> Maintenance Users (by user_email)
	- Mobile Sub Contractor -> Sub Contractor User (by user_email)
	"""
	if not user_email:
		return None

	if user_type == "Mobile VIVA Tenant":
		# Support multiple Tenant Details rows per user_email (user can belong to multiple leases/apartments)
		rows = frappe.get_all(
			"Tenant Details",
			filters={"user_email": user_email, "parenttype": "Lease"},
			fields=["name", "parent", "full_name", "app_use_terms_and_conditions", "is_first_login"],
		)
		if not rows:
			# Backward compatibility: older records where Tenant Details were under Tenant.
			rows = frappe.get_all(
				"Tenant Details",
				filters={"user_email": user_email, "parenttype": "Tenant"},
				fields=["name", "parent", "full_name", "app_use_terms_and_conditions", "is_first_login"],
			)
		if not rows:
			return None

		# Aggregate states across all tenant detail rows for this user email
		any_terms_accepted = any(bool(r.get("app_use_terms_and_conditions")) for r in rows)
		all_first_login_done = all(bool(r.get("is_first_login")) for r in rows)

		# Choose the first row for display/tenant field purposes
		row = rows[0]
		# Display name from Tenant Details full_name, with parent fallback.
		tenant_name = row.parent
		customer_name = get_customer_from_lease(tenant_name) or frappe.db.get_value(
			"Tenant", tenant_name, "customer_name"
		)
		display = (
			row.full_name
			or customer_name
			or frappe.db.get_value("Tenant", tenant_name, "customer_name")
			or tenant_name
		)
		return {
			"doctype": "Tenant Details",
			"name": row.name,
			"display_name": display,
			"app_use_terms_and_conditions": any_terms_accepted,
			# is_first_login field meaning: 1 means "already done". So we mark it as done only if all rows are done.
			"is_first_login": all_first_login_done,
			"tenant": tenant_name,
		}

	if user_type in MOBILE_MAINTENANCE_ROLES:
		doc = frappe.db.get_value(
			"Maintenance Users",
			{"user_email": user_email},
			["name", "employee_name", "full_name", "app_use_terms_and_conditions", "is_first_login"],
			as_dict=True,
		)
		if not doc:
			return None
		return {
			"doctype": "Maintenance Users",
			"name": doc.name,
			"display_name": doc.full_name or doc.employee_name or doc.name,
			"app_use_terms_and_conditions": bool(doc.app_use_terms_and_conditions),
			"is_first_login": bool(doc.is_first_login),
		}

	if user_type == "Mobile Sub Contractor":
		doc = frappe.db.get_value(
			"Sub Contractor User",
			{"user_email": user_email},
			["name", "sub_contractor_name", "full_name", "app_use_terms_and_conditions", "is_first_login"],
			as_dict=True,
		)
		if not doc:
			return None
		return {
			"doctype": "Sub Contractor User",
			"name": doc.name,
			"display_name": doc.full_name or doc.sub_contractor_name or doc.name,
			"app_use_terms_and_conditions": bool(doc.app_use_terms_and_conditions),
			"is_first_login": bool(doc.is_first_login),
		}

	return None


@frappe.whitelist(methods=["POST"])
def get_user_roles(user=None):
	"""
	Return roles and user_type for a user. POST with user email (defaults to logged-in user).

	Use with /api/method/frappe.auth.get_logged_user to get the current user,
	then POST here with that email to get roles.

	Returns: {"user": "<email>", "roles": ["Role1", "Role2", ...], "user_type": "Mobile VIVA Tenant"|"Mobile Maintenance Manager"|"Mobile Maintenance Officer"|"Mobile Technician"|null}
	"""
	current = frappe.session.user
	if not current or current == "Guest":
		frappe.throw(
			frappe._("Not logged in"),
			frappe.AuthenticationError,
		)
	target = (user or current).strip()
	if not target:
		target = current
	# Allow own roles, or System Manager to query any user
	if target != current and "System Manager" not in frappe.get_roles(current):
		frappe.throw(
			frappe._("Not allowed to get roles for another user"),
			frappe.PermissionError,
		)
	if not frappe.db.exists("User", target):
		frappe.throw(
			frappe._("User not found: {0}").format(target),
			frappe.DoesNotExistError,
		)
	roles = frappe.get_roles(target)
	user_type = _get_user_type(roles)
	return {"user": target, "roles": roles, "user_type": user_type}


########################################################################################################################
# Terms & Conditions
########################################################################################################################
def _resolve_user_email(user_identifier=None):
	"""Resolve email from param or form_dict (accepts legacy customer_user for backward compat)."""
	email = frappe.db.get_value("User", frappe.session.user, "email") or frappe.session.user
	param = user_identifier or (frappe.form_dict.get("user_identifier") or frappe.form_dict.get("customer_user") if getattr(frappe, "form_dict", None) else None)
	if param and "@" in str(param):
		email = param
	return email


@frappe.whitelist()
def check_terms_acceptance(user_identifier: str = None):
	"""Check if the user has accepted the mobile app terms & conditions.

	Mobile VIVA Tenant: Tenant Details (Lease doctype child table), with Tenant fallback.
	Maintenance: Maintenance Users. Mobile Sub Contractor: Sub Contractor User.
	`user_identifier`: optional email (defaults to logged-in user). Legacy param `customer_user` still accepted.
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw("You must be logged in to check terms acceptance.")

		roles = frappe.get_roles(frappe.session.user)
		app_user_type = _get_user_type(roles)
		user_email = _resolve_user_email(user_identifier)

		if not user_email:
			return {"status": "error", "message": "User email not found", "is_accepted": False}
		if not app_user_type:
			return {"status": "error", "message": "User type not recognized (not a mobile app user)", "is_accepted": False}

		rec = _get_app_user_record(user_email, app_user_type)
		if not rec:
			return {"status": "error", "message": "App user record not found for this login", "is_accepted": False}

		out = {
			"status": "success",
			"is_accepted": rec["app_use_terms_and_conditions"],
			"user_type": app_user_type,
			"user_name": rec["display_name"],
			"doc_type": rec["doctype"],
			"doc_name": rec["name"],
		}
		if rec.get("tenant"):
			out["tenant"] = rec["tenant"]
		return out
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Terms Acceptance Check Error")
		return {"status": "error", "message": str(e), "is_accepted": False}


@frappe.whitelist()
def get_terms_and_conditions(user_identifier: str = None):
	"""Return latest mobile Terms & Conditions and acceptance state. Mobile VIVA Tenant uses Tenant Details from Lease (Tenant fallback)."""
	if frappe.session.user == "Guest":
		frappe.throw("You must be logged in to access terms.")

	roles = frappe.get_roles(frappe.session.user)
	app_user_type = _get_user_type(roles)
	user_email = _resolve_user_email(user_identifier)

	terms_doc = frappe.get_all(
		"Terms and Conditions Mobile",
		fields=["name", "terms_and_conditions"],
		order_by="modified desc",
		limit=1,
	)

	accepted = False
	doc_type = None
	doc_name = None
	rec = None
	if user_email and app_user_type:
		rec = _get_app_user_record(user_email, app_user_type)
		if rec:
			accepted = rec["app_use_terms_and_conditions"]
			doc_type = rec["doctype"]
			doc_name = rec["name"]

	out = {
		"terms": terms_doc[0]["terms_and_conditions"] if terms_doc else "",
		"title": terms_doc[0]["name"] if terms_doc else "Terms and Conditions Mobile",
		"accepted": accepted,
		"doc_type": doc_type,
		"doc_name": doc_name,
		"user_type": app_user_type,
	}
	if rec and rec.get("tenant"):
		out["tenant"] = rec["tenant"]
	return out


@frappe.whitelist()
def accept_terms_and_conditions(user_identifier: str = None):
	"""Mark mobile app terms accepted. Mobile VIVA Tenant: Tenant Details from Lease (Tenant fallback)."""
	try:
		if frappe.session.user == "Guest":
			frappe.throw("You must be logged in to accept terms.")

		roles = frappe.get_roles(frappe.session.user)
		app_user_type = _get_user_type(roles)
		user_email = _resolve_user_email(user_identifier)

		if not user_email or not app_user_type:
			frappe.throw("App user not found. Please verify you are logged in as a mobile app user.")

		rec = _get_app_user_record(user_email, app_user_type)
		if not rec:
			frappe.throw("App user record not found for this login.")

		if app_user_type == "Mobile VIVA Tenant":
			# Update all tenant detail rows for this user email so onboarding state is consistent
			rows = frappe.get_all(
				"Tenant Details",
				filters={"user_email": user_email, "parenttype": "Lease"},
				fields=["name"],
			)
			if not rows:
				rows = frappe.get_all(
					"Tenant Details",
					filters={"user_email": user_email, "parenttype": "Tenant"},
					fields=["name"],
				)
			for r in rows or []:
				frappe.db.set_value(
					"Tenant Details",
					r["name"],
					"app_use_terms_and_conditions",
					1,
					update_modified=True,
				)
			frappe.db.commit()
			updated_value = 1
		else:
			frappe.db.set_value(
				rec["doctype"],
				rec["name"],
				"app_use_terms_and_conditions",
				1,
				update_modified=True,
			)
			frappe.db.commit()

			updated_value = frappe.db.get_value(
				rec["doctype"],
				rec["name"],
				"app_use_terms_and_conditions",
			)
			if int(updated_value or 0) != 1:
				frappe.throw("Failed to update terms acceptance status")

		out = {
			"status": "success",
			"message": "Terms accepted",
			"doc_type": rec["doctype"],
			"doc_name": rec["name"],
			"value_set": updated_value,
			"user_type": app_user_type,
		}
		if rec.get("tenant"):
			out["tenant"] = rec["tenant"]
		return out
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Terms Acceptance Error")
		return {"status": "error", "message": str(e)}


########################################################################################################################
# Set Initial Password
########################################################################################################################
@frappe.whitelist()
def check_first_login():
	"""Check if the logged-in app user must change password on first login.

	Uses is_first_login on Tenant Details / Maintenance Users / Sub Contractor User:
	- 1/True => first login (must change password)
	- 0/False => initial password already set
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(frappe._("Authentication required"), frappe.AuthenticationError)

		user_email = frappe.db.get_value("User", frappe.session.user, "email") or frappe.session.user
		roles = frappe.get_roles(frappe.session.user)
		app_user_type = _get_user_type(roles)

		if not app_user_type:
			return {"status": "error", "message": "User type not recognized (not a mobile app user)"}

		rec = _get_app_user_record(user_email, app_user_type)
		if not rec:
			return {"status": "error", "message": "App user record not found for this login"}

		# Doc is_first_login 0/None => first login (must change password); 1 => already set
		is_first_login = not rec["is_first_login"]

		out = {
			"status": "success",
			"is_first_login": is_first_login,
			"doc_type": rec["doctype"],
			"doc_name": rec["name"],
			"user_name": rec["display_name"],
		}
		if rec.get("tenant"):
			out["tenant"] = rec["tenant"]
		return out
	except frappe.AuthenticationError:
		return {"status": "error", "message": "Authentication failed"}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "check_first_login")
		return {"status": "error", "message": str(e)}


@frappe.whitelist()
def set_initial_password(new_password=None):
	"""Set password on first login (admin provided initial credentials).

	Requires `new_password`; updates User and sets is_first_login=1 on Tenant Details / Maintenance Users / Sub Contractor User.
	"""
	try:
		if frappe.session.user == "Guest":
			frappe.throw(frappe._("Authentication required"), frappe.AuthenticationError)

		# Prefer form_dict (works for JSON and form-encoded in most Frappe setups)
		if frappe.form_dict and frappe.form_dict.get("new_password"):
			new_password = frappe.form_dict.get("new_password") or new_password

		# Fallback: raw request body (JSON or x-www-form-urlencoded)
		if not new_password and hasattr(frappe, "request") and frappe.request and hasattr(frappe.request, "get_data"):
			data = (frappe.request.get_data(as_text=True, cache=False) or "").strip()
			if data.startswith("{"):
				try:
					new_password = (json.loads(data) or {}).get("new_password") or new_password
				except Exception:
					pass
			elif "=" in data:
				for part in data.split("&"):
					if "=" not in part:
						continue
					key, value = part.split("=", 1)
					if urllib.parse.unquote(key) == "new_password":
						new_password = urllib.parse.unquote(value)
						break

		if not new_password:
			return {"status": "error", "message": "new_password is required"}

		user_email = frappe.db.get_value("User", frappe.session.user, "email") or frappe.session.user
		roles = frappe.get_roles(frappe.session.user)
		app_user_type = _get_user_type(roles)

		if not app_user_type:
			return {"status": "error", "message": "User type not recognized (not a mobile app user)"}

		rec = _get_app_user_record(user_email, app_user_type)
		if not rec:
			return {"status": "error", "message": "App user record not found for this login"}

		# If already set (doc is_first_login is 1 across all matched records), block
		if rec["is_first_login"]:
			return {
				"status": "error",
				"message": "Initial password has already been set. Please use change password flow.",
			}

		if len(new_password) < 8:
			return {"status": "error", "message": "Password must be at least 8 characters long."}
		if not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):
			return {"status": "error", "message": "Password must contain both letters and numbers."}

		user = frappe.get_doc("User", frappe.session.user)
		user.new_password = new_password
		user.save(ignore_permissions=True)

		if app_user_type == "Mobile VIVA Tenant":
			rows = frappe.get_all(
				"Tenant Details",
				filters={"user_email": user_email, "parenttype": "Lease"},
				fields=["name"],
			)
			if not rows:
				rows = frappe.get_all(
					"Tenant Details",
					filters={"user_email": user_email, "parenttype": "Tenant"},
					fields=["name"],
				)
			for r in rows or []:
				frappe.db.set_value(
					"Tenant Details",
					r["name"],
					"is_first_login",
					1,
					update_modified=True,
				)
			frappe.db.commit()
		else:
			frappe.db.set_value(
				rec["doctype"],
				rec["name"],
				"is_first_login",
				1,
				update_modified=True,
			)
			frappe.db.commit()

		return {"status": "success", "message": "Initial password set successfully", "is_first_login_updated": True}
	except frappe.AuthenticationError:
		return {"status": "error", "message": "Authentication failed"}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "set_initial_password")
		return {"status": "error", "message": str(e)}
