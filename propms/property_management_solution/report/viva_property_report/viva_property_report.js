// Copyright (c) 2026, Aakvatech and contributors
// For license information, please see license.txt

frappe.query_reports["VIVA Property Report"] = {
	"filters": [
        {
            "fieldname": "property_type",
            "label": __("Property Type"),
            "fieldtype": "Select",
            "options": [
                "All",
                "Residential",
                "Commercial"
            ],
            "default": "All",
            "reqd": 1
        },
        {
            "fieldname": "property_status",
            "label": __("Property Status"),
            "fieldtype": "MultiSelectList",
            "get_data": function(txt) {
                return frappe.db.get_list('Property Status', {
                    fields: ['name'],
                    filters: txt ? {
                        name: ['like', '%' + txt + '%']
                    } : {},
                    order_by: 'name asc',
                    limit_page_length: 50
                }).then(r => {
                    return r.map(d => ({
                        value: d.name,
                        description: d.name
                    }));
                });
            },
            "reqd": 0,
        },
    ],
    
    "formatter": function(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        
        // Color code marketing status
        if (column.fieldname == "marketing_status") {
            if (value == "Available") {
                value = "<span style='color:green'>" + value + "</span>";
            } else if (value == "Vacating") {
                value = "<span style='color:orange'>" + value + "</span>";
            } else if (value == "Booked") {
                value = "<span style='color:blue'>" + value + "</span>";
            }
        }
        
        // Color code lease status
        if (column.fieldname == "lease_status") {
            if (value == "Active") {
                value = "<span style='color:green'>" + value + "</span>";
            } else if (value == "Expired") {
                value = "<span style='color:red'>" + value + "</span>";
            }
        }
        
        return value;
    }
};