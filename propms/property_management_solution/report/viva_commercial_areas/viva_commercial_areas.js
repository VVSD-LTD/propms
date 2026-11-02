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
			fieldname: "property",
			label: __("Property"),
			fieldtype: "Link",
			options: "Property",
		},
		{
			fieldname: "occupancy_status",
			label: __("Status"),
			fieldtype: "MultiSelectList",
			get_data: viva_options(["Leased-Customer", "Leased-Internal", "Empty"]),
		},
		{
			fieldname: "lease_customer",
			label: __("Lease Customer"),
			fieldtype: "Link",
			options: "Customer",
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
		report.page.add_inner_button(__("Download Excel"), () => viva_download("VIVA Commercial Areas", "xlsx"));
		report.page.add_inner_button(__("Download PDF"), () => viva_download("VIVA Commercial Areas", "pdf"));
	},
};
