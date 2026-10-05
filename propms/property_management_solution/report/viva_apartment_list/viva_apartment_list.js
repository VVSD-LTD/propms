frappe.query_reports["VIVA Apartment List"] = {
	filters: [
		{
			fieldname: "view",
			label: __("List"),
			fieldtype: "Select",
			options: ["Tower A", "Tower B", "Virgin Plaza", "Vasta", "TRCS", "Investor"],
			default: "Tower A",
			reqd: 1,
		},
	],
};
