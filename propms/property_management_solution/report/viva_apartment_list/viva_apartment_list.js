function viva_options(values) {
	return function (txt) {
		return values
			.filter((value) => !txt || value.toLowerCase().includes(txt.toLowerCase()))
			.map((value) => ({ value, description: "" }));
	};
}

function viva_download(report_name, file_format) {
	open_url_post(
		"/api/method/propms.property_management_solution.report.viva_report_export.download_viva_report",
		{
			report_name,
			file_format,
			filters: frappe.query_report.get_filter_values(),
		}
	);
}

frappe.query_reports["VIVA Apartment List"] = {
	filters: [
		{
			fieldname: "view",
			label: __("List"),
			fieldtype: "Select",
			options: ["Tower A", "Tower B", "Virgin Plaza", "Vasta", "RedCross", "Investor"],
			default: "Tower A",
			reqd: 1,
		},
		{
			fieldname: "property",
			label: __("Apt"),
			fieldtype: "Link",
			options: "Property",
		},
		{
			fieldname: "bedroom",
			label: __("BHK"),
			fieldtype: "MultiSelectList",
			get_data: viva_options(["2", "3", "4", "6"]),
		},
		{
			fieldname: "unit_owner",
			label: __("Property Owner"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "lessee",
			label: __("Lessee"),
			fieldtype: "Data",
		},
		{
			fieldname: "occupancy_status",
			label: __("Status"),
			fieldtype: "MultiSelectList",
			get_data: viva_options([
				"Viva-Leased -Customer",
				"Viva Empty",
				"Vasta-Leased- Customer",
				"Vasta Empty",
				"RedCross-Leased- Customer",
				"RedCross Empty",
				"Ownership-Self Staying",
				"Ownership-Leased",
				"Ownership Empty",
			]),
		},
		{
			fieldname: "portfolio",
			label: __("Portfolio"),
			fieldtype: "MultiSelectList",
			get_data: viva_options(["Virgin Plaza", "Vasta", "RedCross", "Investor"]),
		},
		{
			fieldname: "lease_name",
			label: __("Lease Name"),
			fieldtype: "Link",
			options: "Lease",
		},
		{
			fieldname: "lease_start_date",
			label: __("Lease Start Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "lease_end_date",
			label: __("Lease End Date"),
			fieldtype: "Date",
		},
	],
	onload: function (report) {
		report.page.add_inner_button(__("Download Excel"), () => viva_download("VIVA Apartment List", "xlsx"));
		report.page.add_inner_button(__("Download PDF"), () => viva_download("VIVA Apartment List", "pdf"));
	},
};
