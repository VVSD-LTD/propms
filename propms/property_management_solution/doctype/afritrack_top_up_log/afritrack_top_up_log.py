# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AfritrackTopupLog(Document):
	pass


@frappe.whitelist()
def retry_afritrack_topup(topup_log_name=None, sales_invoice=None):
	"""System Manager: retry TrackSPM top-up for Failed/Partial/Blocked logs."""
	frappe.only_for("System Manager")

	from propms.api.v1.electricity.vendor import retry_missing_tariffs

	log_name = topup_log_name
	if not log_name and sales_invoice:
		log_name = frappe.db.get_value("Afritrack Top-up Log", {"sales_invoice": sales_invoice}, "name")

	if not log_name or not frappe.db.exists("Afritrack Top-up Log", log_name):
		frappe.throw(frappe._("Afritrack Top-up Log not found"))

	log = frappe.get_doc("Afritrack Top-up Log", log_name)
	if log.status not in ("Failed", "Partial", "Blocked", "Pending"):
		frappe.throw(frappe._("Only Failed, Partial, Blocked, or Pending top-ups can be retried"))

	return retry_missing_tariffs(log.sales_invoice)
