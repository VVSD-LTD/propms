// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.query_reports["SF Employee & Sub Contractor Checkin Summary"] = {
	"filters": [
		{
			"fieldname": "from_datetime",
			"label": __("From"),
			"fieldtype": "Datetime",
			"width": "160px",
			"reqd": 0
		},
		{
			"fieldname": "to_datetime",
			"label": __("To"),
			"fieldtype": "Datetime",
			"width": "160px",
			"reqd": 0
		},
		{
			"fieldname": "company",
			"label": __("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"default": frappe.defaults.get_user_default("Company"),
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "person_type",
			"label": __("Person Type"),
			"fieldtype": "Select",
			"options": "\nEmployee\nSub Contractor",
			"default": "",
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "department",
			"label": __("Department"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				var company = frappe.query_report.get_filter_value("company");
				return frappe.db.get_link_options("Department", txt, company ? { company: company } : {});
			},
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "employee",
			"label": __("Employee"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				var company = frappe.query_report.get_filter_value("company");
				return frappe.db.get_link_options("Employee", txt, company ? { company: company } : {});
			},
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "sub_contractor_category",
			"label": __("Sub Contractor Category"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				return frappe.db.get_link_options("Sub Contractor Category", txt);
			},
			"width": "150px",
			"reqd": 0
		},
		{
			"fieldname": "sub_contractor",
			"label": __("Sub Contractor"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				var company = frappe.query_report.get_filter_value("company");
				return frappe.db.get_link_options("Sub Contractor", txt, company ? { company: company } : {});
			},
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "shift_type",
			"label": __("Shift Type"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				return frappe.db.get_link_options("Shift Type", txt);
			},
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "off_shift",
			"label": __("Off Shift Only"),
			"fieldtype": "Check",
			"reqd": 0
		},
	]
};
