// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.query_reports["Employee & Sub Contractor Checking Summary"] = {
	"filters": [
		// {
		// 	"fieldname": "from_date",
		// 	"label": __("From Date"),
		// 	"fieldtype": "Date",
		// 	"default": frappe.datetime.month_start(),
		// 	"width": "140px",
		// 	"reqd": 0
		// },
		// {
		// 	"fieldname": "to_date",
		// 	"label": __("To Date"),
		// 	"fieldtype": "Date",
		// 	"default": frappe.datetime.get_today(),
		// 	"width": "140px",
		// 	"reqd": 0
		// },
		{
			"fieldname": "from_datetime",
			"label": __("From"),
			"fieldtype": "Datetime",
			"width": "160px",
			"reqd": 0
		},
		{
			"fieldname": "to_datetime",
			"label": __("To"),
			"fieldtype": "Datetime",
			"width": "160px",
			"reqd": 0
		},
		// {
		// 	"fieldname": "from_time",
		// 	"label": __("From Time"),
		// 	"fieldtype": "Time",
		// 	"width": "120px",
		// 	"reqd": 0
		// },
		// {
		// 	"fieldname": "to_time",
		// 	"label": __("To Time"),
		// 	"fieldtype": "Time",
		// 	"width": "120px",
		// 	"reqd": 0
		// },
		{
			"fieldname": "company",
			"label": __("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"default": frappe.defaults.get_user_default("Company"),
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "person_type",
			"label": __("Person Type"),
			"fieldtype": "Select",
			"options": "\nEmployee\nSub Contractor",
			"default": "",
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "department",
			"label": __("Department"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				var company = frappe.query_report.get_filter_value("company");
				return frappe.db.get_link_options("Department", txt, company ? { company: company } : {});
			},
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "employee",
			"label": __("Employee"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				var company = frappe.query_report.get_filter_value("company");
				return frappe.db.get_link_options("Employee", txt, company ? { company: company } : {});
			},
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "sub_contractor_category",
			"label": __("Sub Contractor Category"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				return frappe.db.get_link_options("Sub Contractor Category", txt);
			},
			"width": "150px",
			"reqd": 0
		},
		{
			"fieldname": "sub_contractor",
			"label": __("Sub Contractor"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				var company = frappe.query_report.get_filter_value("company");
				return frappe.db.get_link_options("Sub Contractor", txt, company ? { company: company } : {});
			},
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "shift_type",
			"label": __("Shift Type"),
			"fieldtype": "MultiSelectList",
			"get_data": function (txt) {
				return frappe.db.get_link_options("Shift Type", txt);
			},
			"width": "140px",
			"reqd": 0
		},
		{
			"fieldname": "off_shift",
			"label": __("Off Shift Only"),
			"fieldtype": "Check",
			"reqd": 0
		},
		// {
		// 	"fieldname": "log_type",
		// 	"label": __("Log Type"),
		// 	"fieldtype": "Select",
		// 	"options": "\nIN\nOUT",
		// 	"default": "",
		// 	"width": "140px",
		// 	"reqd": 0
		// }
	],
	// "onload": function(report) {
	// 	render_summary_cards(report);
	// },
	// "refresh": function(report) {
	// 	render_summary_cards(report);
	// },
	// "after_datatable_render": function(datatable) {
	// 	render_summary_cards(frappe.query_report);
	// }
};

function render_summary_cards(report) {
	report = report || frappe.query_report;
	if (!report) return;

	let data = report.data || [];
	if (data && data.length > 0) {
		let emp_in = 0, emp_out = 0, sub_in = 0, sub_out = 0;
		data.forEach(function(r) {
			if (r.person_type === "Employee") {
				if (r.checkin_time) emp_in++;
				if (r.checkout_time) emp_out++;
			} else if (r.person_type === "Sub Contractor") {
				if (r.checkin_time) sub_in++;
				if (r.checkout_time) sub_out++;
			}
		});

		update_cards_html({
			emp_in: emp_in,
			emp_out: emp_out,
			sub_in: sub_in,
			sub_out: sub_out,
			total_in: emp_in + sub_in,
			total_out: emp_out + sub_out
		});
		return;
	}

	let filters = report.get_values() || {};
	frappe.call({
		method: "propms.property_management_solution.report.employee_&_sub_contractor_checking_summary.employee_&_sub_contractor_checking_summary.get_merged_checkin_summary",
		args: {
			filters: filters
		},
		callback: function(r) {
			if (r.message) {
				update_cards_html(r.message);
			}
		}
	});
}

function update_cards_html(d) {
	$('#merged-checkin-summary').remove();

	let summary_html = `
		<div id="merged-checkin-summary" style="display: flex; gap: 12px; justify-content: flex-start; flex-wrap: wrap; margin-bottom: 16px; padding: 12px; background: var(--bg-color); border-radius: 8px; border: 1px solid var(--border-color);">
			<div class="card text-white bg-primary" style="min-width: 140px; flex: 1;">
				<div class="card-header" style="font-weight: 600; font-size: 12px; text-transform: uppercase; color: #ffffff;">Emp IN</div>
				<div class="card-body" style="padding: 8px 16px;">
					<h4 class="card-title" style="margin: 0; color: #ffffff;">${d.emp_in}</h4>
				</div>
			</div>
			<div class="card text-white bg-info" style="min-width: 140px; flex: 1;">
				<div class="card-header" style="font-weight: 600; font-size: 12px; text-transform: uppercase; color: #ffffff;">Emp OUT</div>
				<div class="card-body" style="padding: 8px 16px;">
					<h4 class="card-title" style="margin: 0; color: #ffffff;">${d.emp_out}</h4>
				</div>
			</div>
			<div class="card text-white bg-success" style="min-width: 140px; flex: 1;">
				<div class="card-header" style="font-weight: 600; font-size: 12px; text-transform: uppercase; color: #ffffff;">SubContractor IN</div>
				<div class="card-body" style="padding: 8px 16px;">
					<h4 class="card-title" style="margin: 0; color: #ffffff;">${d.sub_in}</h4>
				</div>
			</div>
			<div class="card text-white bg-warning" style="min-width: 140px; flex: 1;">
				<div class="card-header" style="font-weight: 600; font-size: 12px; text-transform: uppercase; color: #ffffff;">SubContractor OUT</div>
				<div class="card-body" style="padding: 8px 16px;">
					<h4 class="card-title" style="margin: 0; color: #ffffff;">${d.sub_out}</h4>
				</div>
			</div>
			<div class="card text-white bg-dark" style="min-width: 140px; flex: 1;">
				<div class="card-header" style="font-weight: 600; font-size: 12px; text-transform: uppercase; color: #ffffff;">Total IN / OUT</div>
				<div class="card-body" style="padding: 8px 16px;">
					<h4 class="card-title" style="margin: 0; color: #ffffff;">${d.total_in} / ${d.total_out}</h4>
				</div>
			</div>
		</div>
	`;

	$(summary_html).insertBefore('.page-form');
}
