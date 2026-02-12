import frappe


def execute(filters=None):
    columns = get_columns(filters)
    data = get_data(filters)
    return columns, data


def get_columns(filters):
    """Define columns based on report type"""
    # report_type = filters.get("report_type", "Active Properties")
    
    # if report_type == "Active Properties":
    return [
        {
            "fieldname": "property",
            "label": "Property",
            "fieldtype": "Link",
            "options": "Property",
            "width": 200
        },
        {
            "fieldname": "unit_owner",
            "label": "Property Owner",
            "fieldtype": "Data",
            "width": 200
        },
        {
            "fieldname": "company",
            "label": "Company",
            "fieldtype": "Data",
            "width": 150
        },
        {
            "fieldname": "type",
            "label": "Property Type",
            "fieldtype": "Link",
            "options": "Unit Type",
            "width": 150
        },
        {
            "fieldname": "bedroom",
            "label": "Bedroom",
            "fieldtype": "Int",
            "width": 100
        },
        {
            "fieldname": "carpet_area",
            "label": "Carpet Area",
            "fieldtype": "Float",
            "width": 120
        },
        {
            "fieldname": "builtup_area",
            "label": "Built Up Area",
            "fieldtype": "Float",
            "width": 120
        },
        {
            "fieldname": "marketing_status",
            "label": "Marketing Status",
            "fieldtype": "Data",
            "width": 150
        },
        {
            "fieldname": "property_status",
            "label": "Property Status",
            "fieldtype": "Data",
            "width": 150
        },
        {
            "fieldname": "cost_center",
            "label": "Cost Center",
            "fieldtype": "Link",
            "options": "Cost Center",
            "width": 150
        },
        {
            "fieldname": "remarks",
            "label": "Remarks",
            "fieldtype": "Small Text",
            "width": 200
        }
    ]


def get_data(filters):
    """Fetch data based on filters"""
    report_type = filters.get("report_type", "Active Properties")
    property_type = filters.get("property_type", "All")
    
    # if report_type == "Active Properties":
    return get_active_properties_data(filters, property_type)
    # else:
    #     return get_empty_properties_data(filters, property_type)


def get_active_properties_data(filters, property_type):
    """Query for active properties with leases"""
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")

    conditions = []
    if property_type and property_type != "All":
        conditions.append(f"type = '{property_type}'")

    if filters.get("property_status"):
        statuses = [f'"{status}"' for status in filters.get('property_status')]
        conditions.append(f"status IN ({', '.join(statuses)})")

    where_clause = f"AND {' AND '.join(conditions)}" if conditions else ""

    query = f"""
        SELECT 
            name AS property,
            unit_owner,
            company,
            cost_center,
            type,
            bedroom,
            remarks,
            carpet_area,
            builtup_area,
            status AS property_status,
            marketing_status
        FROM `tabProperty`
        WHERE name != ''
        {where_clause}
        ORDER BY name
    """

    return frappe.db.sql(query, {"from_date": from_date, "to_date": to_date}, as_dict=1)


def get_empty_properties_data(filters, property_type):
    """Query for empty/available properties"""
    conditions = ["p.type != 'Common Area'"]
    conditions.append("(p.marketing_status IN ('Available', 'Vacating', 'Booked') OR p.marketing_status IS NULL)")
    
    if property_type and property_type != "All":
        conditions.append(f"p.type = '{property_type}'")
    
    where_clause = " AND ".join(conditions)
    
    query = f"""
        SELECT 
            p.name AS property,
            p.unit_owner,
            p.bedroom,
            p.type,
            p.builtup_area,
            p.carpet_area,
            p.marketing_status,
            p.remarks
        FROM `tabProperty` p
        WHERE {where_clause}
        AND p.status != 'Removed'
        ORDER BY p.name
    """
    
    return frappe.db.sql(query, as_dict=1)