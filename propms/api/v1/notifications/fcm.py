import frappe
from frappe.utils import now


# Phone + tablet; older rows from reinstalls are pruned on each register.
MAX_DEVICES_PER_USER = 2


def _prune_user_tokens(user: str, keep: int = MAX_DEVICES_PER_USER):
	"""Keep only the newest *keep* User Device rows for this user."""
	rows = frappe.get_all(
		"User Device",
		filters={"user": user},
		fields=["name"],
		order_by="modified desc",
	)
	for row in rows[keep:]:
		frappe.delete_doc("User Device", row.name, ignore_permissions=True, force=1)


def delete_invalid_token(token: str):
	"""Remove User Device row(s) for a token FCM rejected as stale/invalid."""
	if not token:
		return
	names = frappe.get_all("User Device", filters={"token": token}, pluck="name")
	for name in names:
		frappe.delete_doc("User Device", name, ignore_permissions=True, force=1)
	if names:
		frappe.logger().info(
			f"FCM: deleted {len(names)} stale User Device row(s) for token {token[:16]}..."
		)


def is_invalid_token_error(exc) -> bool:
	"""True when FCM says the registration token should be dropped."""
	try:
		from firebase_admin import messaging

		if isinstance(
			exc,
			(
				messaging.UnregisteredError,
				messaging.SenderIdMismatchError,
			),
		):
			return True
	except Exception:
		pass

	msg = str(exc).lower()
	return any(
		s in msg
		for s in (
			"requested entity was not found",
			"registration-token-not-registered",
			"not a valid fcm registration token",
			"invalid registration",
			"unregistered",
		)
	)


@frappe.whitelist(methods=["POST"])
def register_token(fcm_token: str):
	"""Register or refresh an FCM token for the logged-in user.

	Expects DocType "User Device" with fields:
	  - user (Link User)
	  - token (Data)

	Also prunes older tokens for the same user so reinstalls do not pile up.
	"""
	user = frappe.session.user
	if not fcm_token:
		return {"status": "error", "message": "Missing fcm_token"}

	if user in ("Guest", None):
		frappe.throw("Not permitted", frappe.PermissionError)

	existing = frappe.get_all(
		"User Device",
		filters={"user": user, "token": fcm_token},
		pluck="name",
	)
	if existing:
		# Bump modified so this token stays among the kept newest rows
		frappe.db.sql(
			"UPDATE `tabUser Device` SET modified=%s WHERE name=%s",
			(now(), existing[0]),
		)
		_prune_user_tokens(user)
		return {"status": "ok", "message": "updated"}

	# Same physical token after re-login under this account — reassign
	other = frappe.get_all("User Device", filters={"token": fcm_token}, pluck="name")
	if other:
		frappe.db.sql(
			"UPDATE `tabUser Device` SET user=%s, modified=%s WHERE name=%s",
			(user, now(), other[0]),
		)
		_prune_user_tokens(user)
		return {"status": "ok", "message": "reassigned"}

	doc = frappe.get_doc({
		"doctype": "User Device",
		"user": user,
		"token": fcm_token,
	})
	doc.insert(ignore_permissions=True)
	_prune_user_tokens(user)
	return {"status": "ok", "message": "registered"}


@frappe.whitelist(methods=["POST"])
def unregister_token(fcm_token: str):
	"""Unregister an FCM token for the logged-in user."""
	user = frappe.session.user
	if not fcm_token:
		return {"status": "error", "message": "Missing fcm_token"}

	if user in ("Guest", None):
		frappe.throw("Not permitted", frappe.PermissionError)

	names = frappe.get_all(
		"User Device",
		filters={"user": user, "token": fcm_token},
		pluck="name",
	)
	for name in names:
		frappe.delete_doc("User Device", name, ignore_permissions=True)
	return {"status": "ok", "message": "unregistered"}
