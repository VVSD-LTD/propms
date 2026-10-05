frappe.query_reports["VIVA Residential Occupancy"] = {
	filters: [
		{
			fieldname: "portfolio",
			label: __("Portfolio"),
			fieldtype: "Select",
			options: ["Entire Building", "Virgin Plaza", "Vasta", "TRCS", "Investor"],
			default: "Entire Building",
			reqd: 1,
		},
	],
};
