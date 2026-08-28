// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.query_reports["Sub Contractor Checkin Summary"] = {
	filters: [
		{
			fieldname: "sub_contractor",
			label: __("Sub Contractor"),
			fieldtype: "Link",
			options: "Sub Contractor",
			mandatory: 0,
		},
		{
			fieldname: "from_time",
			label: __("From"),
			fieldtype: "Datetime",
			mandatory: 0,
		},
		{
			fieldname: "to_time",
			label: __("To"),
			fieldtype: "Datetime",
			mandatory: 0,
		},
		{
			fieldname: "log_type",
			label: __("Log Type"),
			fieldtype: "Select",
			options: "\nIN\nOUT",
			mandatory: 0,
		},
		{
			fieldname: "shift_type",
			label: __("Shift Type"),
			fieldtype: "Link",
			options: "Shift Type",
			mandatory: 0,
		},
		{
			fieldname: "sub_contractor_category",
			label: __("Sub Contractor Category"),
			fieldtype: "Link",
			options: "Sub Contractor Category",
			mandatory: 0,
		},
		{
			fieldname: "off_shift",
			label: __("Off Shift"),
			fieldtype: "Check",
		}
	],
};
