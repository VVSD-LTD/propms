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

frappe.query_reports["VIVA Tenant Contacts"] = {
	filters: [
		{
			fieldname: "portfolio",
			label: __("List"),
			fieldtype: "Select",
			options: ["Commercial", "Virgin Plaza", "Vasta", "RedCross", "Investor"],
			default: "Commercial",
			reqd: 1,
		},
		{
			fieldname: "property_group",
			label: __("Type"),
			fieldtype: "MultiSelectList",
			depends_on: "eval:doc.portfolio=='Commercial'",
			get_data: viva_options(["Warehouse", "Ground Floor", "First Floor", "Second Floor", "Other"]),
		},
		{
			fieldname: "property",
			label: __("Property"),
			fieldtype: "Link",
			options: "Property",
		},
		{
			fieldname: "unit_owner",
			label: __("Property Owner"),
			fieldtype: "Link",
			options: "Customer",
			depends_on: "eval:doc.portfolio!='Commercial'",
		},
		{
			fieldname: "bedroom",
			label: __("BHK"),
			fieldtype: "MultiSelectList",
			depends_on: "eval:doc.portfolio!='Commercial'",
			get_data: viva_options(["2", "3", "4", "6"]),
		},
		{
			fieldname: "tenant_label",
			label: __("Tenant"),
			fieldtype: "Data",
		},
		{
			fieldname: "lease_customer",
			label: __("Lease Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "occupancy_status",
			label: __("Status"),
			fieldtype: "MultiSelectList",
			depends_on: "eval:doc.portfolio!='Commercial'",
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
			fieldname: "email",
			label: __("Email"),
			fieldtype: "Data",
		},
		{
			fieldname: "phone",
			label: __("Phone Number"),
			fieldtype: "Data",
		},
		{
			fieldname: "owner_email",
			label: __("Email (Owner)"),
			fieldtype: "Data",
			depends_on: "eval:doc.portfolio=='Investor'",
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
		report.page.add_inner_button(__("Download Excel"), () => viva_download("VIVA Tenant Contacts", "xlsx"));
		report.page.add_inner_button(__("Download PDF"), () => viva_download("VIVA Tenant Contacts", "pdf"));
	},
};
