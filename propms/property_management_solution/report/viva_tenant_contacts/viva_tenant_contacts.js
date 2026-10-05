frappe.query_reports["VIVA Tenant Contacts"] = {
	filters: [
		{
			fieldname: "portfolio",
			label: __("List"),
			fieldtype: "Select",
			options: ["Commercial", "Virgin Plaza", "Vasta", "TRCS", "Investor"],
			default: "Commercial",
			reqd: 1,
		},
	],
};
