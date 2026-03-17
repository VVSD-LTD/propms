# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

# import frappe
# from frappe import _
# from frappe.utils import date_diff, nowdate


# def execute(filters=None):
#     filters = filters or {}
#     columns = get_columns()
#     data = get_data(filters)
#     summary = get_summary(data)
#     chart = get_chart(data)
#     return columns, data, None, chart, summary


# # ─── COLUMNS ────────────────────────────────────────────────────────────────

# def get_columns():
#     return [
#         # {
#         #     "label": _("S/No"),
#         #     "fieldname": "idx",
#         #     "fieldtype": "Int",
#         #     "width": 55,
#         # },
#         {
#             "label": _("Jobcard ID"),
#             "fieldname": "name",
#             "fieldtype": "Link",
#             "options": "Issue",
#             "width": 155,
#         },
#         {
#             "label": _("Subject"),
#             "fieldname": "subject",
#             "fieldtype": "Data",
#             "width": 240,
#         },
#         {
#             "label": _("Property"),
#             "fieldname": "property_name",
#             "fieldtype": "Data",
#             "width": 160,
#         },
#         {
#             "label": _("Issue Type"),
#             "fieldname": "issue_type",
#             "fieldtype": "Data",
#             "width": 130,
#         },
#         {
#             "label": _("Status"),
#             "fieldname": "status",
#             "fieldtype": "Data",
#             "width": 130,
#         },
#         {
#             "label": _("Priority"),
#             "fieldname": "priority",
#             "fieldtype": "Data",
#             "width": 90,
#         },
#         {
#             "label": _("Person In Charge"),
#             "fieldname": "person_in_charge_name",
#             "fieldtype": "Data",
#             "width": 160,
#         },
#         {
#             "label": _("Sub-Contractor"),
#             "fieldname": "sub_contractor_name",
#             "fieldtype": "Data",
#             "width": 160,
#         },
#         {
#             "label": _("Raised By"),
#             "fieldname": "raised_by",
#             "fieldtype": "Data",
#             "width": 170,
#         },
#         {
#             "label": _("Opening Date"),
#             "fieldname": "opening_date",
#             "fieldtype": "Date",
#             "width": 110,
#         },
#         {
#             "label": _("Age (Days)"),
#             "fieldname": "age_days",
#             "fieldtype": "Int",
#             "width": 90,
#         },
#         {
#             "label": _("SLA Status"),
#             "fieldname": "sla_status",
#             "fieldtype": "Data",
#             "width": 160,
#         },
#         {
#             "label": _("Response By"),
#             "fieldname": "response_by",
#             "fieldtype": "Datetime",
#             "width": 140,
#         },
#         {
#             "label": _("Resolution By"),
#             "fieldname": "sla_resolution_by",
#             "fieldtype": "Datetime",
#             "width": 140,
#         },
#         {
#             "label": _("Defect Found"),
#             "fieldname": "defect_found",
#             "fieldtype": "Data",
#             "width": 200,
#         },
#         {
#             "label": _("Resolution Details"),
#             "fieldname": "resolution_details",
#             "fieldtype": "Data",
#             "width": 200,
#         },
#         {
#             "label": _("Customer Feedback"),
#             "fieldname": "customer_feedback",
#             "fieldtype": "Data",
#             "width": 160,
#         },
#         {
#             "label": _("Materials Used"),
#             "fieldname": "materials_used",
#             "fieldtype": "Data",
#             "width": 220,
#         },
#         {
#             "label": _("Materials Cost (TZS)"),
#             "fieldname": "materials_cost",
#             "fieldtype": "Currency",
#             "width": 140,
#         },
#     ]


# # ─── DATA ────────────────────────────────────────────────────────────────────

# def get_data(filters):
#     conditions = build_conditions(filters)

#     issues = frappe.db.sql(
#         f"""
#         SELECT
#             i.name,
#             i.subject,
#             i.property_name,
#             i.issue_type,
#             i.status,
#             i.priority,
#             i.person_in_charge_name,
#             i.sub_contractor_name,
#             i.raised_by,
#             i.opening_date,
#             i.agreement_status   AS sla_status,
#             i.response_by,
#             i.sla_resolution_by,
#             i.defect_found,
#             i.resolution_details,
#             i.customer_feedback,
#             i.workflow_state
#         FROM `tabIssue` i
#         WHERE {conditions}
#         ORDER BY i.opening_date DESC, i.creation DESC
#         """,
#         filters,
#         as_dict=True,
#     )

#     # Fetch materials per issue in one query
#     mat_rows = frappe.db.sql(
#         """
#         SELECT
#             parent,
#             GROUP_CONCAT(CONCAT(quantity, ' x ', item) SEPARATOR ', ') AS materials_used,
#             SUM(amount) AS materials_cost
#         FROM `tabIssue Materials Billed`
#         WHERE parent IN ({placeholders})
#         GROUP BY parent
#         """.format(
#             placeholders=", ".join(["%s"] * len(issues)) if issues else "''"
#         ),
#         tuple(i["name"] for i in issues) if issues else (),
#         as_dict=True,
#     )
#     mat_map = {m["parent"]: m for m in mat_rows}

#     def strip_html(val):
#         """Remove HTML tags from Frappe HTML fields."""
#         import re
#         if not val:
#             return ""
#         clean = re.sub(r"<[^>]+>", "", val or "")
#         return clean.strip()

#     data = []
#     for idx, issue in enumerate(issues, start=1):
#         mat = mat_map.get(issue["name"], {})
#         data.append(
#             {
#                 # "idx": idx,
#                 "name": issue["name"],
#                 "subject": issue["subject"],
#                 "property_name": issue["property_name"],
#                 "issue_type": issue["issue_type"],
#                 "status": issue["status"],
#                 "priority": issue["priority"],
#                 "person_in_charge_name": issue["person_in_charge_name"],
#                 "sub_contractor_name": issue["sub_contractor_name"],
#                 "raised_by": issue["raised_by"],
#                 "opening_date": issue["opening_date"],
#                 "age_days": date_diff(nowdate(), issue["opening_date"])
#                 if issue["opening_date"]
#                 else 0,
#                 "sla_status": issue["sla_status"],
#                 "response_by": issue["response_by"],
#                 "sla_resolution_by": issue["sla_resolution_by"],
#                 "defect_found": strip_html(issue["defect_found"]),
#                 "resolution_details": strip_html(issue["resolution_details"]),
#                 "customer_feedback": strip_html(issue["customer_feedback"]),
#                 "materials_used": mat.get("materials_used") or "—",
#                 "materials_cost": mat.get("materials_cost") or 0,
#             }
#         )

#     return data


# def build_conditions(filters):
#     conditions = ["1=1"]

#     # if filters.get("from_date"):
#     #     conditions.append("i.opening_date >= %(from_date)s")
#     if filters.get("to_date"):
#         conditions.append("i.opening_date <= %(to_date)s")
#     if filters.get("property_name"):
#         conditions.append("i.property_name LIKE %(property_name)s")
#         filters["property_name"] = f"%{filters['property_name']}%"
#     if filters.get("status"):
#         conditions.append("i.status = %(status)s")
#     if filters.get("issue_type"):
#         conditions.append("i.issue_type LIKE %(issue_type)s")
#         filters["issue_type"] = f"%{filters['issue_type']}%"
#     if filters.get("priority"):
#         conditions.append("i.priority = %(priority)s")
#     if filters.get("person_in_charge"):
#         conditions.append("i.person_in_charge = %(person_in_charge)s")

#     return " AND ".join(conditions)

# # ─── SUMMARY CARDS (shown above the report) ──────────────────────────────────

# def get_summary(data):
#     total = len(data)
#     closed_24h = sum(
#         1
#         for d in data
#         if d["status"] == "Closed" and d["age_days"] is not None and d["age_days"] <= 1
#     )
#     open_jc = sum(1 for d in data if d["status"] == "Open")
#     in_progress = sum(1 for d in data if d["status"] == "In Progress")
#     awaiting = sum(1 for d in data if d["status"] == "Awaiting Parts")
#     high_priority = sum(1 for d in data if d["priority"] in ("HIGH", "CRITICAL"))
#     total_mat_cost = sum(d.get("materials_cost") or 0 for d in data)

#     return [
#         {
#             "label": _("Total Jobcards"),
#             "value": total,
#             "datatype": "Int",
#             "indicator": "blue",
#         },
#         {
#             "label": _("Closed (Last 24 hrs)"),
#             "value": closed_24h,
#             "datatype": "Int",
#             "indicator": "green",
#         },
#         {
#             "label": _("Open Jobcards"),
#             "value": open_jc,
#             "datatype": "Int",
#             "indicator": "orange",
#         },
#         {
#             "label": _("In Progress"),
#             "value": in_progress,
#             "datatype": "Int",
#             "indicator": "blue",
#         },
#         {
#             "label": _("Awaiting Parts"),
#             "value": awaiting,
#             "datatype": "Int",
#             "indicator": "red",
#         },
#         {
#             "label": _("High / Critical Priority"),
#             "value": high_priority,
#             "datatype": "Int",
#             "indicator": "red",
#         },
#         {
#             "label": _("Total Materials Cost (TZS)"),
#             "value": total_mat_cost,
#             "datatype": "Currency",
#             "indicator": "blue",
#         },
#     ]


# # ─── CHART (Bar: Jobcards by Issue Type) ─────────────────────────────────────

# def get_chart(data):
#     type_count = {}
#     for row in data:
#         t = row.get("issue_type") or "Unknown"
#         type_count[t] = type_count.get(t, 0) + 1

#     sorted_types = sorted(type_count.items(), key=lambda x: -x[1])
#     labels = [x[0] for x in sorted_types]
#     values = [x[1] for x in sorted_types]

#     return {
#         "data": {
#             "labels": labels,
#             "datasets": [{"name": _("Jobcards"), "values": values}],
#         },
#         "type": "bar",
#         "height": 280,
#         "colors": ["#1a73e8"],
#         "barOptions": {"spaceRatio": 0.3},
#     }

import frappe
from frappe import _
from frappe.utils import nowdate


def execute(filters=None):
    filters = filters or {}
    raw = get_raw_data(filters)
    data = get_data(raw)
    summary = get_summary(raw)
    chart = get_chart(raw)
    return get_columns(), data, None, chart, summary


# ─── STATUSES ────────────────────────────────────────────────────────────────

STATUSES = [
    "Open",
    "In Progress",
    "Awaiting Parts",
    "Under Observation",
    "In Discussion",
    "Appointment",
    "Hold",
    "Closed",
]


# ─── COLUMNS ─────────────────────────────────────────────────────────────────

def get_columns():
    cols = [
        {
            "label": _("Issue Type"),
            "fieldname": "issue_type",
            "fieldtype": "Data",
            "width": 200,
        },
        {
            "label": _("Total"),
            "fieldname": "total",
            "fieldtype": "Int",
            "width": 80,
        },
    ]
    for s in STATUSES:
        cols.append({
            "label": _(s),
            "fieldname": "status_" + s.lower().replace(" ", "_").replace("-", "_"),
            "fieldtype": "Int",
            "width": 120,
        })
    cols.append({
        "label": _("% of Total"),
        "fieldname": "pct",
        "fieldtype": "Percent",
        "width": 100,
    })
    return cols


# ─── RAW DATA (minimal columns, no materials) ─────────────────────────────────

def get_raw_data(filters):
    conditions = build_conditions(filters)
    return frappe.db.sql(
        f"""
        SELECT
            i.issue_type,
            i.status
        FROM `tabIssue` i
        WHERE {conditions}
        """,
        filters,
        as_dict=True,
    )


def build_conditions(filters):
    conditions = ["1=1"]
    if filters.get("to_date"):
        conditions.append("i.opening_date <= %(to_date)s")
    if filters.get("property_name"):
        conditions.append("i.property_name LIKE %(property_name)s")
        filters["property_name"] = f"%{filters['property_name']}%"
    if filters.get("status"):
        conditions.append("i.status = %(status)s")
    if filters.get("issue_type"):
        conditions.append("i.issue_type LIKE %(issue_type)s")
        filters["issue_type"] = f"%{filters['issue_type']}%"
    if filters.get("priority"):
        conditions.append("i.priority = %(priority)s")
    if filters.get("person_in_charge"):
        conditions.append("i.person_in_charge = %(person_in_charge)s")
    return " AND ".join(conditions)


# ─── AGGREGATE INTO SUMMARY ROWS ─────────────────────────────────────────────

def get_data(raw):
    total_all = len(raw)

    # Build type → status → count matrix
    matrix = {}
    for row in raw:
        t = row.get("issue_type") or "Unknown"
        s = row.get("status") or "Unknown"
        if t not in matrix:
            matrix[t] = {}
        matrix[t][s] = matrix[t].get(s, 0) + 1

    # Sort issue types by total descending
    sorted_types = sorted(matrix.items(), key=lambda x: -sum(x[1].values()))

    data = []

    # ── Grand total row first ──
    grand_row = {
        "issue_type": "GRAND TOTAL",
        "total": total_all,
        "pct": 100.0,
    }
    status_totals = {}
    for t, counts in matrix.items():
        for s, n in counts.items():
            status_totals[s] = status_totals.get(s, 0) + n
    for s in STATUSES:
        key = "status_" + s.lower().replace(" ", "_").replace("-", "_")
        grand_row[key] = status_totals.get(s, 0)
    data.append(grand_row)

    # ── One row per issue type ──
    for t, counts in sorted_types:
        type_total = sum(counts.values())
        pct = round(type_total / total_all * 100, 1) if total_all else 0.0
        row = {
            "issue_type": t,
            "total": type_total,
            "pct": pct,
        }
        for s in STATUSES:
            key = "status_" + s.lower().replace(" ", "_").replace("-", "_")
            row[key] = counts.get(s, 0)
        data.append(row)

    return data


# ─── SUMMARY CARDS ───────────────────────────────────────────────────────────

def get_summary(raw):
    total = len(raw)
    status_counts = {}
    for row in raw:
        s = row.get("status") or "Unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    return [
        {"label": _("Total Jobcards"),    "value": total,                              "datatype": "Int", "indicator": "blue"},
        {"label": _("Open"),              "value": status_counts.get("Open", 0),       "datatype": "Int", "indicator": "orange"},
        {"label": _("In Progress"),       "value": status_counts.get("In Progress", 0),"datatype": "Int", "indicator": "blue"},
        {"label": _("Awaiting Parts"),    "value": status_counts.get("Awaiting Parts", 0), "datatype": "Int", "indicator": "red"},
        {"label": _("Under Observation"), "value": status_counts.get("Under Observation", 0), "datatype": "Int", "indicator": "blue"},
        {"label": _("Hold"),              "value": status_counts.get("Hold", 0),       "datatype": "Int", "indicator": "red"},
        {"label": _("Closed"),            "value": status_counts.get("Closed", 0),     "datatype": "Int", "indicator": "green"},
    ]


# ─── CHART ───────────────────────────────────────────────────────────────────

def get_chart(raw):
    type_count = {}
    for row in raw:
        t = row.get("issue_type") or "Unknown"
        type_count[t] = type_count.get(t, 0) + 1

    sorted_types = sorted(type_count.items(), key=lambda x: -x[1])

    return {
        "data": {
            "labels": [x[0] for x in sorted_types],
            "datasets": [{"name": _("Jobcards"), "values": [x[1] for x in sorted_types]}],
        },
        "type": "bar",
        "height": 280,
        "colors": ["#1a73e8"],
        "barOptions": {"spaceRatio": 0.3},
    }