// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

let REPORT_FILTER_CACHE = {};

function get_current_report_names(frm) {
	const set = new Set();
	(frm.doc.scheduled_reports || []).forEach(r => {
		if (r.report) set.add(r.report);
	});
	(frm.doc.report_filters || []).forEach(rf => {
		if (rf.report) set.add(rf.report);
	});
	return Array.from(set);
}

function set_report_filter_queries(frm) {
	frm.set_query("report", "report_filters", function () {
		const scheduled_reports = (frm.doc.scheduled_reports || [])
			.map(r => r.report)
			.filter(Boolean);

		return {
			filters: [
				["Report", "name", "in", scheduled_reports.length ? scheduled_reports : [""]]
			]
		};
	});
}

function load_all_filter_options(frm, callback_fn) {
	const current_reports = get_current_report_names(frm);
	if (!current_reports.length) {
		REPORT_FILTER_CACHE = {};
		refresh_grid_rows(frm);
		if (callback_fn) callback_fn();
		return;
	}

	frappe.call({
		method: "propms.property_management_solution.doctype.attendance_settings.attendance_settings.get_all_scheduled_report_filter_fields",
		args: { reports: current_reports },
		callback: function (r) {
			if (r.message) {
				REPORT_FILTER_CACHE = r.message || {};
				refresh_grid_rows(frm);
			}
			if (callback_fn) callback_fn();
		}
	});
}

function refresh_grid_rows(frm) {
	if (!frm.fields_dict.report_filters || !frm.fields_dict.report_filters.grid) return;

	// Master list of ONLY clean human-readable labels across all cached reports, sorted ASC
	let all_labels_set = new Set([""]);
	Object.keys(REPORT_FILTER_CACHE).forEach(rep => {
		(REPORT_FILTER_CACHE[rep] || []).forEach(f => {
			if (f.label) all_labels_set.add(f.label);
		});
	});

	const sorted_labels = Array.from(all_labels_set).slice(1).sort((a, b) => a.localeCompare(b));
	const master_options = [""].concat(sorted_labels).join("\n");
	frappe.meta.get_docfield("Attendance Report Filter", "fieldname").options = master_options;

	(frm.doc.report_filters || []).forEach(row => {
		const fields = REPORT_FILTER_CACHE[row.report] || [];
		if (row.fieldname && fields.length) {
			const match = fields.find(f => f.fieldname === row.fieldname || f.label === row.fieldname);
			if (match) {
				if (match.fieldname && (!row.raw_fieldname || row.raw_fieldname !== match.fieldname)) {
					frappe.model.set_value(row.doctype, row.name, "raw_fieldname", match.fieldname);
				}
				if (match.label && row.fieldname !== match.label) {
					frappe.model.set_value(row.doctype, row.name, "fieldname", match.label);
				}
			}
		}
		set_options_for_specific_row(frm, row.doctype, row.name);
	});
}

function set_options_for_specific_row(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	if (!row) return;

	const grid = frm.fields_dict.report_filters.grid;
	const grid_row = grid.get_row(cdn);

	if (!row.report) {
		if (grid_row && grid_row.fields_dict && grid_row.fields_dict.fieldname) {
			grid_row.fields_dict.fieldname.df.options = "";
			grid_row.fields_dict.fieldname.refresh();
		}
		return;
	}

	const fields = REPORT_FILTER_CACHE[row.report] || [];
	const labels = fields.map(f => f.label).filter(Boolean);
	const sorted_row_labels = labels.sort((a, b) => a.localeCompare(b));
	const options_str = [""].concat(sorted_row_labels).join("\n");

	if (grid_row) {
		if (grid_row.fields_dict && grid_row.fields_dict.fieldname) {
			grid_row.fields_dict.fieldname.df.options = options_str;
			grid_row.fields_dict.fieldname.refresh();
		}
		frappe.meta.get_docfield("Attendance Report Filter", "fieldname", cdn).options = options_str;
	}
}

function handle_row_report_change(frm, cdt, cdn, reset_val) {
	const row = frappe.get_doc(cdt, cdn);
	if (!row) return;

	if (reset_val) {
		frappe.model.set_value(cdt, cdn, "fieldname", "");
		frappe.model.set_value(cdt, cdn, "raw_fieldname", "");
	}

	if (!row.report) {
		set_options_for_specific_row(frm, cdt, cdn);
		return;
	}

	if (REPORT_FILTER_CACHE[row.report]) {
		set_options_for_specific_row(frm, cdt, cdn);
		return;
	}

	frappe.call({
		method: "propms.property_management_solution.doctype.attendance_settings.attendance_settings.get_report_filter_fields",
		args: { report_name: row.report },
		callback: function (r) {
			const fields = r.message || [];
			REPORT_FILTER_CACHE[row.report] = fields;
			set_options_for_specific_row(frm, cdt, cdn);
			refresh_grid_rows(frm);
		}
	});
}

function handle_fieldname_change(frm, cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	if (!row || !row.report || !row.fieldname) return;

	const fields = REPORT_FILTER_CACHE[row.report] || [];
	const match = fields.find(f => f.label === row.fieldname || f.fieldname === row.fieldname);
	if (match) {
		if (match.fieldname) {
			frappe.model.set_value(cdt, cdn, "raw_fieldname", match.fieldname);
		}
		if (match.label && row.fieldname !== match.label) {
			frappe.model.set_value(cdt, cdn, "fieldname", match.label);
		}
	}
}

frappe.ui.form.on("Attendance Settings", {
	onload: function (frm) {
		set_report_filter_queries(frm);
		load_all_filter_options(frm);
	},
	refresh: function (frm) {
		set_report_filter_queries(frm);
		load_all_filter_options(frm);

		frm.add_custom_button(__("Send Enabled Reports Now"), function () {
			frappe.confirm(
				__("Are you sure you want to trigger sending all enabled scheduled reports immediately?"),
				function () {
					frappe.call({
						method: "propms.property_management_solution.doctype.attendance_settings.attendance_settings.trigger_send_now",
						freeze: true,
						freeze_message: __("Sending scheduled reports..."),
						callback: function (r) {
							if (!r.exc) {
								frappe.msgprint(__("Scheduled reports processed and queued for sending."));
								frm.reload_doc();
							}
						},
					});
				}
			);
		});
	},
});

frappe.ui.form.on("Attendance Report Schedule", {
	report: function (frm) {
		set_report_filter_queries(frm);
		load_all_filter_options(frm);
	},
	scheduled_reports_add: function (frm) {
		set_report_filter_queries(frm);
		load_all_filter_options(frm);
	},
	scheduled_reports_remove: function (frm) {
		set_report_filter_queries(frm);
		load_all_filter_options(frm);
	},
});

frappe.ui.form.on("Attendance Report Filter", {
	report: function (frm, cdt, cdn) {
		handle_row_report_change(frm, cdt, cdn, true);
	},

	fieldname: function (frm, cdt, cdn) {
		handle_fieldname_change(frm, cdt, cdn);
	},

	form_render: function (frm, cdt, cdn) {
		set_options_for_specific_row(frm, cdt, cdn);
	},
});
