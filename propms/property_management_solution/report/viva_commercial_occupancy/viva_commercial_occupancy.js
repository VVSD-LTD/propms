frappe.query_reports["VIVA Commercial Occupancy"] = {
	filters: [
		{
			fieldname: "chart_view",
			label: __("Chart"),
			fieldtype: "Select",
			options: ["Pie", "Bar"],
			default: "Pie",
			reqd: 1,
		},
	],
};
