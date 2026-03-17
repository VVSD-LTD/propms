# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SubContractorCheckin(Document):
	pass

@frappe.whitelist()
def add_log_based_on_subcontractor_field(
    subcontractor_field_value,
    timestamp,
    device_id=None,
    log_type=None,
    skip_auto_attendance=0,
    subcontractor_fieldname="attendance_device_id",
    latitude=None,
    longitude=None,
):
    if not subcontractor_field_value or not timestamp:
        frappe.throw(_("'subcontractor_field_value' and 'timestamp' are required."))

    subcontractor = frappe.db.get_values(
        "Sub Contractor",
        {subcontractor_fieldname: subcontractor_field_value},
        ["name", "sub_contractor_name", subcontractor_fieldname],
        as_dict=True,
    )
    if subcontractor:
        subcontractor = subcontractor[0]
    else:
        frappe.throw(
            _("No SubContractor found for the given subcontractor field value. '{}': {}").format(
                subcontractor_fieldname, subcontractor_field_value
            )
        )

    # ✅ Fix: use Sub Contractor Checkin, not Employee Checkin
    doc = frappe.new_doc("Sub Contractor Checkin")
    doc.sub_contractor = subcontractor.name
    doc.sub_contractor_name = subcontractor.sub_contractor_name
    doc.time = timestamp
    doc.device_id = device_id
    doc.log_type = log_type
    if latitude:
        doc.latitude = latitude
    if longitude:
        doc.longitude = longitude
    if cint(skip_auto_attendance) == 1:
        doc.skip_auto_attendance = "1"
    doc.insert()

    return doc
