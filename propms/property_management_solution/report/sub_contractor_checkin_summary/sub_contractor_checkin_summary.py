# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {
            "fieldname": "sub_contractor",
            "label": _("Sub Contractor"),
            "fieldtype": "Link",
            "options": "Sub Contractor",
            "width": 180,
        },
        {
            "fieldname": "sub_contractor_name",
            "label": _("Name"),
            "fieldtype": "Data",
            "width": 180,
        },
        {
            "fieldname": "sub_contractor_category",
            "label": _("Category"),
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "fieldname": "shift_type",
            "label": _("Shift Type"),
            "fieldtype": "Data",
            "width": 150,
        },
        {
            "fieldname": "time",
            "label": _("Time"),
            "fieldtype": "Datetime",
            "width": 180,
        },
        {
            "fieldname": "log_type",
            "label": _("Log Type"),
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "fieldname": "offshift",
            "label": _("Off Shift"),
            "fieldtype": "Check",
            "width": 90,
        },
    ]


def get_data(filters):
    conditions = get_conditions(filters)

    data = frappe.db.sql(
        """
        SELECT
            c.sub_contractor,
            c.sub_contractor_name,
            c.sub_contractor_category,
            COALESCE(sa.shift_type, sc.default_shift) AS shift_type,
            c.time,
            c.log_type,
            c.offshift
        FROM `tabSub Contractor Checkin` c
        LEFT JOIN `tabSub Contractor Shift Assignment` sa
            ON sa.sub_contractor = c.sub_contractor
            AND sa.status = 'Active'
        LEFT JOIN `tabSub Contractor` sc
            ON sc.name = c.sub_contractor
        WHERE 1=1
        {conditions}
        ORDER BY c.sub_contractor, c.time
        """.format(conditions=conditions),
        filters,
        as_dict=1,
    )

    return data


def get_conditions(filters):
    conditions = ""

    if not filters:
        return conditions

    if filters.get("sub_contractor"):
        conditions += " AND c.sub_contractor = %(sub_contractor)s"

    if filters.get("from_time"):
        conditions += " AND c.time >= %(from_time)s"

    if filters.get("to_time"):
        conditions += " AND c.time <= %(to_time)s"

    if filters.get("log_type"):
        conditions += " AND c.log_type = %(log_type)s"

    if filters.get("shift_type"):
        conditions += " AND COALESCE(sa.shift_type, sc.default_shift) = %(shift_type)s"

    if filters.get("sub_contractor_category"):
        conditions += " AND c.sub_contractor_category = %(sub_contractor_category)s"

    if filters.get("off_shift"):
        conditions += " AND c.offshift = 1"

    return conditions