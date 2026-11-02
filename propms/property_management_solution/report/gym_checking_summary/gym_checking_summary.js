// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.query_reports["Gym Checking Summary"] = {
	filters: [
		{
			fieldname: "from_datetime",
			label: __("From"),
			fieldtype: "Datetime",
			default: frappe.datetime.get_today() + " 00:00:00",
			width: "160px",
			reqd: 0,
		},
		{
			fieldname: "to_datetime",
			label: __("To"),
			fieldtype: "Datetime",
			width: "160px",
			reqd: 0,
		},
		{
			fieldname: "gym_member",
			label: __("Gym Member"),
			fieldtype: "MultiSelectList",
			get_data: function (txt) {
				return frappe.db.get_link_options("Gym Member", txt);
			},
			width: "160px",
			reqd: 0,
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Data",
			width: "140px",
			reqd: 0,
		},
		{
			fieldname: "last_name",
			label: __("Last Name / Unit"),
			fieldtype: "Data",
			width: "140px",
			reqd: 0,
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nActive\nDisabled",
			default: "",
			width: "120px",
			reqd: 0,
		},
	],
};
