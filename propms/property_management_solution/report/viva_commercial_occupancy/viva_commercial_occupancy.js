function viva_options(values) {
	return function (txt) {
		return values
			.filter((value) => !txt || value.toLowerCase().includes(txt.toLowerCase()))
			.map((value) => ({ value, description: "" }));
	};
}

function viva_label_pie(chart) {
	if (!chart || chart.type !== "pie" || !chart.state || !chart.drawArea || !chart.center) {
		return;
	}
	chart.drawArea.querySelectorAll(".viva-pie-label").forEach((node) => node.remove());
	const totals = chart.state.sliceTotals || [];
	const grand = chart.state.grandTotal || 0;
	if (!grand) {
		return;
	}
	let running = 0;
	(chart.state.slicesProperties || []).forEach((property, index) => {
		const value = totals[index] || 0;
		if (!value) {
			return;
		}
		const later = totals.slice(index + 1).some((item) => item);
		const share = later ? Math.round((value * 10000) / grand) / 100 : Math.round((100 - running) * 100) / 100;
		running += share;
		if (share < 6) {
			return;
		}
		const mid = ((property.startAngle + property.angle / 2) * Math.PI) / 180;
		const distance = chart.radius * 0.62;
		const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
		text.setAttribute("class", "viva-pie-label");
		text.setAttribute("x", chart.center.x + Math.sin(mid) * distance);
		text.setAttribute("y", chart.center.y + Math.cos(mid) * distance);
		text.setAttribute("text-anchor", "middle");
		text.setAttribute("dominant-baseline", "middle");
		text.setAttribute("fill", "#ffffff");
		text.setAttribute("font-size", "12");
		text.setAttribute("font-weight", "600");
		text.textContent = share.toFixed(2) + "%";
		chart.drawArea.appendChild(text);
	});
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
		{
			fieldname: "property_group",
			label: __("Type"),
			fieldtype: "MultiSelectList",
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
		report.page.add_inner_button(__("Download Excel"), () => viva_download("VIVA Commercial Occupancy", "xlsx"));
		report.page.add_inner_button(__("Download PDF"), () => viva_download("VIVA Commercial Occupancy", "pdf"));
		const render_chart = report.render_chart.bind(report);
		report.render_chart = function (options) {
			render_chart(options);
			setTimeout(() => viva_label_pie(report.chart), 50);
		};
	},
};
