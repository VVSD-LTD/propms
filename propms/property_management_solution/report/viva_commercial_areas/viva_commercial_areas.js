frappe.query_reports["VIVA Commercial Areas"] = {
	filters: [
		{
			fieldname: "floor",
			label: __("Floor"),
			fieldtype: "Select",
			options: ["All", "Warehouse", "Ground Floor", "First Floor", "Second Floor", "Other"],
			default: "All",
		},
		{
			fieldname: "only_empty",
			label: __("Empty Offices Only"),
			fieldtype: "Check",
			default: 0,
		},
	],
};
