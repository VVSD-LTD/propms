// Copyright (c) 2025, Aakvatech and contributors
// For license information, please see license.txt

frappe.query_reports["CASH & BANK MOVEMENT REPORT"] = {
	"filters": [
		{
			"fieldname": "company",
			"label": __("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"default": frappe.defaults.get_user_default("Company"),
			"reqd": 1
		},
		{
			"fieldname": "from_date",
			"label": __("From Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			"reqd": 1
		},
		{
			"fieldname": "to_date",
			"label": __("To Date"),
			"fieldtype": "Date",
			"default": frappe.datetime.get_today(),
			"reqd": 1
		},
		// {
		// 	"fieldname": "account_group",
		// 	"label": __("Account Group"),
		// 	"fieldtype": "Link",
		// 	"options": "Account",
		// 	"get_query": function() {
		// 		return {
		// 			"filters": {
		// 				"is_group": 1,
		// 				"company": frappe.query_report.get_filter_value('company')
		// 			}
		// 		};
		// 	}
		// },
		// {
		// 	"fieldname": "account",
		// 	"label": __("Account"),
		// 	"fieldtype": "Link",
		// 	"options": "Account",
		// 	"get_query": function() {
		// 		var company = frappe.query_report.get_filter_value('company');
		// 		var account_group = frappe.query_report.get_filter_value('account_group');
				
		// 		var filters = {
		// 			"company": company,
		// 			"is_group": 0
		// 		};
				
		// 		if (account_group) {
		// 			filters["parent_account"] = account_group;
		// 		}
				
		// 		return {
		// 			"filters": filters
		// 		};
		// 	}
		// }
	],
	
	"formatter": function(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		
		// Make group headers bold
		if (data && data.indent === 0 && !data.opening && !data.closing) {
			value = value.bold();
		}
		
		// Make total rows bold and with background color
		if (data && data.account && data.account.startsWith('Grand Total ')) {
			if (column.fieldname === 'account') {
				value = `<span style="font-weight: bold;">${value}</span>`;
			} else {
				value = `<span style="font-weight: bold;">${value}</span>`;
			}
		}
		
		// Color negative values in red
		if (data && parseFloat(data[column.fieldname]) < 0) {
			value = `<span style="color: red">${value}</span>`;
		}
		
		return value;
	},
	
	"tree": true,
	"name_field": "account",
	"parent_field": "parent_account",
	"initial_depth": 1
};
