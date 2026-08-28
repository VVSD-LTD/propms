import frappe

from propms.api.v1.notifications import fcm as v1_fcm


@frappe.whitelist(methods=["POST"])
def register_token(fcm_token: str):
    return v1_fcm.register_token(fcm_token=fcm_token)


@frappe.whitelist(methods=["POST"])
def unregister_token(fcm_token: str):
    return v1_fcm.unregister_token(fcm_token=fcm_token)
