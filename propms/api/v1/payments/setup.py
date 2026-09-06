import frappe

def setup_selcom_defaults():
    """
    Automated setup executed on after_install and after_migrate:
    1. Ensures 'Selcom' Mode of Payment exists in ERPNext.
    2. Populates default mode_of_payment on Viva Selcom Settings if empty.
    """
    # 1. Provision Mode of Payment
    if not frappe.db.exists("Mode of Payment", "Selcom"):
        try:
            mop = frappe.get_doc({
                "doctype": "Mode of Payment",
                "mode_of_payment": "Selcom",
                "type": "Bank",
                "enabled": 1,
            })
            mop.insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(f"Failed to auto-provision Mode of Payment 'Selcom': {e}", "Viva Selcom Setup")

    # 2. Set defaults on Viva Selcom Settings if not configured
    try:
        current_mop = frappe.db.get_single_value("Viva Selcom Settings", "mode_of_payment")
        if not current_mop and frappe.db.exists("Mode of Payment", "Selcom"):
            frappe.db.set_single_value("Viva Selcom Settings", "mode_of_payment", "Selcom")

        webhook_url = frappe.utils.get_url("/api/method/propms.api.v1.payments.selcom_ipn_webhook")
        frappe.db.set_single_value("Viva Selcom Settings", "ipn_callback_url", webhook_url)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(f"Failed to auto-configure Viva Selcom Settings: {e}", "Viva Selcom Setup")
