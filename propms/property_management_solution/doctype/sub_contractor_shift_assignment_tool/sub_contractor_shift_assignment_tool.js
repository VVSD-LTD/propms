// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.provide("propms");

propms.setup_sub_contractor_filter_group = (frm) => {
	const filter_wrapper = frm.fields_dict.filter_list.$wrapper;
	filter_wrapper.empty();

	frappe.model.with_doctype("Sub Contractor", () => {
		frm.filter_list = new frappe.ui.FilterGroup({
			parent: filter_wrapper,
			doctype: "Sub Contractor",
			on_change: () => {
				frm.advanced_filters = frm.filter_list
					.get_filters()
					.reduce((filters, item) => {
						// item[3] is the value from the array [doctype, fieldname, condition, value]
						if (item[3]) {
							filters.push(item.slice(1, 4));
						}
						return filters;
					}, []);
				frm.trigger("get_sub_contractors");
			},
		});
	});
};

propms.render_sub_contractors_datatable = (
	frm,
	columns,
	sub_contractors,
	no_data_message = __("No Data"),
	get_editor = null,
	events = {},
) => {
	// section automatically collapses on applying a single filter
	frm.set_df_property("quick_filters_section", "collapsible", 0);
	frm.set_df_property("advanced_filters_section", "collapsible", 0);

	if (frm.sub_contractors_datatable) {
		frm.sub_contractors_datatable.rowmanager.checkMap = [];
		frm.sub_contractors_datatable.options.noDataMessage = no_data_message;
		frm.sub_contractors_datatable.refresh(sub_contractors, columns);
		return;
	}

	const $wrapper = frm.get_field("sub_contractors_html").$wrapper;
	const sub_contractor_wrapper = $(`<div class="sub_contractor_wrapper">`).appendTo($wrapper);
	const datatable_options = {
		columns: columns,
		data: sub_contractors,
		checkboxColumn: true,
		checkedRowStatus: false,
		serialNoColumn: false,
		dynamicRowHeight: true,
		inlineFilters: true,
		layout: "fluid",
		cellHeight: 35,
		noDataMessage: no_data_message,
		disableReorderColumn: true,
		getEditor: get_editor,
		events: events,
	};
	frm.sub_contractors_datatable = new frappe.DataTable(
		sub_contractor_wrapper.get(0),
		datatable_options,
	);
};

propms.validate_mandatory_fields = (frm, selected_rows, items = "Sub Contractors") => {
	const missing_fields = [];
	for (d in frm.fields_dict) {
		if (frm.fields_dict[d].df.reqd && !frm.doc[d] && d !== "__newname")
			missing_fields.push(frm.fields_dict[d].df.label);
	}

	if (missing_fields.length) {
		let message = __("Mandatory fields required for this action:");
		message += "<br><br><ul><li>" + missing_fields.join("</li><li>") + "</ul>";
		frappe.throw({
			message: message,
			title: __("Missing Fields"),
		});
	}

	if (!selected_rows.length)
		frappe.throw({
			message: __("Please select at least one row to perform this action."),
			title: __("No {0} Selected", [__(items)]),
		});
};

propms.handle_realtime_bulk_action_notification = (frm, event, doctype) => {
	frappe.realtime.off(event);
	frappe.realtime.on(event, (message) => {
		propms.notify_bulk_action_status(
			doctype,
			message.failure,
			message.success,
			message.for_processing,
		);

		// refresh only on complete/partial success
		if (message.success) frm.refresh();
	});
};

propms.notify_bulk_action_status = (doctype, failure, success, for_processing = false) => {
	let action = __("create/submit");
	let action_past = __("created");
	if (for_processing) {
		action = __("process");
		action_past = __("processed");
	}

	let message = "";
	let title = __("Success");
	let indicator = "green";

	if (failure.length) {
		message += __("Failed to {0} {1} for sub contractors:", [action, doctype]);
		message += " " + frappe.utils.comma_and(failure) + "<hr>";
		message += __(
			"Check <a href='/app/List/Error Log?reference_doctype={0}'>{1}</a> for more details",
			[doctype, __("Error Log")],
		);
		title = __("Failure");
		indicator = "red";

		if (success.length) {
			message += "<hr>";
			title = __("Partial Success");
			indicator = "orange";
		}
	}

	if (success.length) {
		message += __("Successfully {0} {1} for the following sub contractors:", [
			action_past,
			doctype,
		]);
		message += __(
			"<table class='table table-bordered'><tr><th>{0}</th><th>{1}</th></tr>",
			[__("Sub Contractor"), doctype],
		);
		for (const d of success) {
			message += `<tr><td>${d.sub_contractor}</td><td>${d.doc}</td></tr>`;
		}
		message += "</table>";
	}

	frappe.msgprint({
		message,
		title,
		indicator,
		is_minimizable: true,
	});
};


frappe.ui.form.on("Sub Contractor Shift Assignment Tool", {
	setup(frm) {
		propms.setup_sub_contractor_filter_group(frm);
	},

	refresh(frm) {
		frm.page.clear_indicator();
		frm.disable_save();
		frm.trigger("set_primary_action");
		frm.trigger("get_sub_contractors");

		propms.handle_realtime_bulk_action_notification(
			frm,
			"completed_bulk_shift_assignment",
			"Shift Assignment",
		);
		propms.handle_realtime_bulk_action_notification(
			frm,
			"completed_bulk_shift_schedule_assignment",
			"Shift Schedule Assignment",
		);
		propms.handle_realtime_bulk_action_notification(
			frm,
			"completed_bulk_shift_request_processing",
			"Shift Request",
		);
	},

	action(frm) {
		frm.trigger("set_primary_action");
		frm.trigger("get_sub_contractors");
	},

	company(frm) {
		frm.trigger("get_sub_contractors");
	},

	shift_type(frm) {
		frm.trigger("get_sub_contractors");
	},

	status(frm) {
		frm.trigger("get_sub_contractors");
	},

	start_date(frm) {
		if (frm.doc.start_date > frm.doc.end_date) frm.set_value("end_date", null);
		frm.trigger("get_sub_contractors");
	},

	end_date(frm) {
		if (frm.doc.end_date < frm.doc.start_date) frm.set_value("start_date", null);
		frm.trigger("get_sub_contractors");
	},

	shift_type_filter(frm) {
		frm.trigger("get_sub_contractors");
	},

	shift_schedule(frm) {
		frm.trigger("get_sub_contractors");
	},

	approver(frm) {
		frm.trigger("get_sub_contractors");
	},

	from_date(frm) {
		if (frm.doc.from_date > frm.doc.to_date) frm.set_value("to_date", null);
		frm.trigger("get_sub_contractors");
	},

	to_date(frm) {
		if (frm.doc.to_date < frm.doc.from_date) frm.set_value("from_date", null);
		frm.trigger("get_sub_contractors");
	},

	branch(frm) {
		frm.trigger("get_sub_contractors");
	},

	department(frm) {
		frm.trigger("get_sub_contractors");
	},

	designation(frm) {
		frm.trigger("get_sub_contractors");
	},

	grade(frm) {
		frm.trigger("get_sub_contractors");
	},

	employment_type(frm) {
		frm.trigger("get_sub_contractors");
	},

	set_primary_action(frm) {
		const select_rows_section_head = document
			.querySelector('[data-fieldname="select_rows_section"]')
			.querySelector(".section-head");
		select_rows_section_head.textContent = __("Select Sub Contractors");
		frm.clear_custom_buttons();
		frm.page.clear_primary_action();

		if (frm.doc.action === "Assign Shift")
			frm.page.set_primary_action(__("Assign Shift"), () => {
				frm.trigger("bulk_assign");
			});
		else if (frm.doc.action === "Assign Shift Schedule")
			frm.page.set_primary_action(__("Assign Shift Schedule"), () => {
				frm.trigger("bulk_assign");
			});
		else {
			frm.page.add_inner_button(
				__("Approve"),
				() => {
					frm.events.process_shift_requests(frm, "Approved");
				},
				__("Process Requests"),
			);
			frm.page.add_inner_button(
				__("Reject"),
				() => {
					frm.events.process_shift_requests(frm, "Rejected");
				},
				__("Process Requests"),
			);
			frm.page.set_inner_btn_group_as_primary(__("Process Requests"));
			frm.page.clear_menu();
			select_rows_section_head.textContent = __("Select Shift Requests");
		}
	},

	get_sub_contractors(frm) {
		if (
			(frm.doc.action === "Assign Shift" && !(frm.doc.shift_type && frm.doc.start_date)) ||
			(frm.doc.action === "Assign Shift Schedule" &&
				!(frm.doc.shift_schedule && frm.doc.start_date))
		)
			return frm.events.render_sub_contractors_datatable(frm, []);

		frm.call({
			method: "get_sub_contractors",
			args: {
				advanced_filters: frm.advanced_filters || [],
			},
			doc: frm.doc,
		}).then((r) => frm.events.render_sub_contractors_datatable(frm, r.message));
	},

	render_sub_contractors_datatable(frm, sub_contractors) {
		let columns = undefined;
		let no_data_message = undefined;
		if (frm.doc.action === "Assign Shift") {
			columns = frm.events.get_assign_shift_datatable_columns();
			no_data_message = __(
				frm.doc.shift_type && frm.doc.start_date
					? "There are no Sub Contractors without Shift Assignments for these dates based on the given filters."
					: "Please select Shift Type and assignment date(s).",
			);
		} else if (frm.doc.action === "Assign Shift Schedule") {
			columns = frm.events.get_assign_shift_datatable_columns();
			no_data_message = __(
				frm.doc.shift_schedule && frm.doc.start_date
					? "There are no Sub Contractors without active overlapping Shift Schedule Assignments based on the given filters."
					: "Please select Shift Schedule and assignment date(s).",
			);
		} else {
			columns = frm.events.get_process_shift_requests_datatable_columns();
			no_data_message = "There are no open Shift Requests based on the given filters.";
		}
		propms.render_sub_contractors_datatable(frm, columns, sub_contractors, no_data_message);
	},

	get_assign_shift_datatable_columns() {
		return [
			{
				name: "sub_contractor",
				id: "sub_contractor",
				content: __("Sub Contractor"),
			},
			{
				name: "sub_contractor_name",
				id: "sub_contractor_name",
				content: __("Sub Contractor Name"),
			},
			{
				name: "sub_contractor_category",
				id: "sub_contractor_category",
				content: __("Sub Contractor Category"),
			},
			{
				name: "default_shift",
				id: "default_shift",
				content: __("Default Shift"),
			},
		].map((x) => ({
			...x,
			editable: false,
			focusable: false,
			dropdown: false,
			align: "left",
		}));
	},

	get_process_shift_requests_datatable_columns() {
		return [
			{
				name: "shift_request",
				id: "shift_request",
				content: __("Shift Request"),
			},
			{
				name: "sub_contractor",
				id: "sub_contractor_name",
				content: __("Sub Contractor"),
			},
			{
				name: "shift_type",
				id: "shift_type",
				content: __("Shift Type"),
			},
			{
				name: "from_date",
				id: "from_date",
				content: __("From Date"),
			},
			{
				name: "to_date",
				id: "to_date",
				content: __("To Date"),
			},
		].map((x) => ({
			...x,
			editable: false,
			focusable: false,
			dropdown: false,
			align: "left",
		}));
	},

	bulk_assign(frm) {
		const rows = frm.sub_contractors_datatable.datamanager.data;
		const selected_sub_contractors = [];
		const checked_row_indexes = frm.sub_contractors_datatable.rowmanager.getCheckedRows();
		checked_row_indexes.forEach((idx) => {
			selected_sub_contractors.push(rows[idx].sub_contractor);
		});

		propms.validate_mandatory_fields(frm, selected_sub_contractors, "Sub Contractors");
		frappe.confirm(
			__("{0} to {1} sub contractor(s)?", [__(frm.doc.action), selected_sub_contractors.length]),
			() => {
				frm.call({
					method: "bulk_assign",
					doc: frm.doc,
					args: {
						sub_contractors: selected_sub_contractors,
					},
					freeze: true,
					freeze_message: __("Assigning..."),
				});
			},
		);
	},

	process_shift_requests(frm, status) {
		const rows = frm.sub_contractors_datatable.datamanager.data;
		const selected_requests = [];
		const checked_row_indexes = frm.sub_contractors_datatable.rowmanager.getCheckedRows();
		checked_row_indexes.forEach((idx) => {
			selected_requests.push({
				shift_request: rows[idx].name,
				sub_contractor: rows[idx].sub_contractor,
			});
		});

		propms.validate_mandatory_fields(frm, selected_requests, "Shift Requests");
		frappe.confirm(
			__("Process {0} Shift Request(s) as <b>{1}</b>?", [selected_requests.length, status]),
			() => {
				frm.events.bulk_process_shift_requests(frm, selected_requests, status);
			},
		);
	},

	bulk_process_shift_requests(frm, shift_requests, status) {
		frm.call({
			method: "bulk_process_shift_requests",
			doc: frm.doc,
			args: {
				shift_requests: shift_requests,
				status: status,
			},
			freeze: true,
			freeze_message: __("Processing Requests"),
		});
	},
});