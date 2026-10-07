import frappe


def setup_selcom_defaults():
	"""
	Automated setup executed on after_install and after_migrate:
	1. Ensures 'Selcom' Mode of Payment exists in ERPNext (optional PE label).
	2. Refreshes IPN callback URL on Selcom Settings.
	"""
	# 1. Provision Mode of Payment (used as optional PE.mode_of_payment stamp only)
	if not frappe.db.exists("Mode of Payment", "Selcom"):
		try:
			mop = frappe.get_doc(
				{
					"doctype": "Mode of Payment",
					"mode_of_payment": "Selcom",
					"type": "Bank",
					"enabled": 1,
				}
			)
			mop.insert(ignore_permissions=True)
			frappe.db.commit()
		except Exception as e:
			frappe.log_error(
				f"Failed to auto-provision Mode of Payment 'Selcom': {e}",
				"Viva Selcom Setup",
			)

	# 2. Keep IPN URL in sync with current site
	try:
		if frappe.db.exists("DocType", "Selcom Settings"):
			webhook_url = frappe.utils.get_url(
				"/api/method/propms.api.v1.payments.selcom_ipn_webhook"
			)
			frappe.db.set_single_value("Selcom Settings", "ipn_callback_url", webhook_url)
			frappe.db.commit()
	except Exception as e:
		frappe.log_error(
			f"Failed to auto-configure Selcom Settings: {e}",
			"Viva Selcom Setup",
		)
