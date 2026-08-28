import frappe


@frappe.whitelist(methods=["POST"])
def register_token(fcm_token: str):
    """Register or refresh an FCM token for the logged-in user.

    Expects DocType "User Device" with fields:
      - user (Link User)
      - token (Data)
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
        return {"status": "ok", "message": "updated"}

    doc = frappe.get_doc({
        "doctype": "User Device",
        "user": user,
        "token": fcm_token,
    })
    doc.insert(ignore_permissions=True)
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


