// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

let REPORT_FILTER_FIELDS = [];

const YESTERDAY_EXPR =
	"\\{\\{\\s*(?:yesterday\\(\\)|frappe\\.utils\\.add_days\\(\\s*today\\(\\)\\s*,\\s*-1\\s*\\))\\s*\\}\\}";
const TODAY_EXPR = "\\{\\{\\s*today\\(\\)\\s*\\}\\}";

function load_filter_options(frm) {
	if (!frm.doc.report) {
		REPORT_FILTER_FIELDS = [];
		refresh_filter_rows(frm);
		return;
	}

	frappe.call({
		method:
			"propms.property_management_solution.doctype.dynamic_auto_email_report.dynamic_auto_email_report.get_report_filter_fields",
		args: { report_name: frm.doc.report },
		callback(r) {
			REPORT_FILTER_FIELDS = r.message || [];
			refresh_filter_rows(frm);
		},
	});
}

function refresh_custom_format(frm) {
	if (!frm.doc.report) {
		frm.toggle_display("use_custom_format", false);
		return;
	}
	frappe.call({
		method:
			"propms.property_management_solution.doctype.dynamic_auto_email_report.dynamic_auto_email_report.get_custom_format_method",
		args: { report_name: frm.doc.report },
		callback(r) {
			const has = !!r.message;
			frm.toggle_display("use_custom_format", has);
			if (!has && frm.doc.use_custom_format) {
				frm.set_value("use_custom_format", 0);
			}
		},
	});
}

function refresh_filter_rows(frm) {
	if (!frm.fields_dict.report_filters || !frm.fields_dict.report_filters.grid) return;

	const labels = REPORT_FILTER_FIELDS.map((f) => f.label).filter(Boolean);
	const options_str = [""].concat(labels.sort((a, b) => a.localeCompare(b))).join("\n");
	frappe.meta.get_docfield("Attendance Report Filter", "fieldname").options = options_str;

	(frm.doc.report_filters || []).forEach((row) => {
		apply_fieldname_options(frm, row.name, options_str);
		const match = REPORT_FILTER_FIELDS.find(
			(f) => f.fieldname === row.fieldname || f.label === row.fieldname
		);
		if (!match || !row.fieldname) return;
		if (match.fieldname && row.raw_fieldname !== match.fieldname) {
			frappe.model.set_value(row.doctype, row.name, "raw_fieldname", match.fieldname);
		}
		if (match.label && row.fieldname !== match.label) {
			frappe.model.set_value(row.doctype, row.name, "fieldname", match.label);
		}
	});
}

function apply_fieldname_options(frm, cdn, options_str) {
	const grid_row = frm.fields_dict.report_filters.grid.get_row(cdn);
	if (!grid_row || !grid_row.doc) return;

	const df = frappe.meta.get_docfield("Attendance Report Filter", "fieldname", grid_row.doc.name);
	if (df) df.options = options_str;
	if (grid_row.columns && grid_row.columns.fieldname && grid_row.columns.fieldname.df) {
		grid_row.columns.fieldname.df.options = options_str;
	}

	const targets = [];
	if (grid_row.on_grid_fields_dict && grid_row.on_grid_fields_dict.fieldname) {
		targets.push(grid_row.on_grid_fields_dict.fieldname);
	}
	if (grid_row.grid_form && grid_row.grid_form.fields_dict && grid_row.grid_form.fields_dict.fieldname) {
		targets.push(grid_row.grid_form.fields_dict.fieldname);
	}
	targets.forEach((field) => {
		field.df.options = options_str;
		field.refresh();
	});
}

function current_filter_options() {
	const labels = REPORT_FILTER_FIELDS.map((f) => f.label).filter(Boolean);
	return [""].concat(labels.sort((a, b) => a.localeCompare(b))).join("\n");
}

function load_report_definition(report_name) {
	return new Promise((resolve) => {
		const existing = frappe.query_reports[report_name];
		if (existing && existing.filters) {
			resolve(existing);
			return;
		}
		frappe.call({
			method: "frappe.desk.query_report.get_script",
			args: { report_name },
			callback(r) {
				if (r.message && r.message.script) {
					frappe.dom.eval(r.message.script);
				}
				resolve(frappe.query_reports[report_name] || { filters: [] });
			},
			error() {
				resolve({ filters: [] });
			},
		});
	});
}

function saved_filter_value(frm, df) {
	const row = (frm.doc.report_filters || []).find(
		(item) =>
			item.raw_fieldname === df.fieldname ||
			item.fieldname === df.label ||
			item.fieldname === df.fieldname
	);
	return row ? row.value || "" : "";
}

function classify_saved(df, value) {
	const text = (value || "").trim();
	if (!text) return { mode: "" };
	if (df.fieldtype === "Date" || df.fieldtype === "Datetime") {
		const yesterday = text.match(new RegExp("^" + YESTERDAY_EXPR + "(?:\\s+(\\d{2}:\\d{2}(?::\\d{2})?))?$"));
		const today = text.match(new RegExp("^" + TODAY_EXPR + "(?:\\s+(\\d{2}:\\d{2}(?::\\d{2})?))?$"));
		if (yesterday) return { mode: "Yesterday", time: yesterday[1] || "00:00:00" };
		if (today) return { mode: "Today", time: today[1] || "00:00:00" };
		if (text.startsWith("{{")) return { mode: "Raw", raw: text };
		return { mode: "Fixed", raw: text };
	}
	if (df.fieldtype === "MultiSelectList" || df.fieldtype === "MultiSelect") {
		try {
			const parsed = JSON.parse(text);
			if (Array.isArray(parsed)) return { mode: "List", values: parsed };
		} catch (e) {
			if (text.startsWith("{{")) return { mode: "Raw", raw: text };
		}
		return {
			mode: "List",
			values: text
				.split(",")
				.map((item) => item.trim())
				.filter(Boolean),
		};
	}
	if (text.startsWith("{{")) return { mode: "Raw", raw: text };
	return { mode: "Fixed", raw: text };
}

function normalize_time(value) {
	if (!value) return "00:00:00";
	const parts = String(value).split(":");
	while (parts.length < 3) parts.push("00");
	return parts
		.slice(0, 3)
		.map((part) => part.padStart(2, "0"))
		.join(":");
}

function options_string(df) {
	let options = df.options;
	if (typeof options === "function") options = options();
	if (Array.isArray(options)) {
		return options.map((item) => (item && item.value) || item).join("\n");
	}
	return options || "";
}

function control_options(df) {
	let options = df.options;
	if (typeof options === "function") options = options();
	if (df.fieldtype === "Autocomplete") return options || [];
	if (df.fieldtype === "Select") return options_string(df);
	return options;
}

function dialog_filter_value(card) {
	if (!card) return null;
	if (card.control && card.control.get_value) {
		const value = card.control.get_value();
		if (value != null && value !== "" && !(Array.isArray(value) && !value.length)) return value;
	}
	if (card.df.fieldtype === "Date" || card.df.fieldtype === "Datetime") {
		const written = read_card(card);
		if (written && !written.includes("{{")) return written;
	}
	return card.df.default != null && card.df.default !== "" ? card.df.default : null;
}

function apply_report_filters(cards) {
	const existing = frappe.query_report;
	const host = existing || (frappe.query_report = {});
	const previous = {
		filters: host.filters,
		get_filter: host.get_filter,
		get_filter_value: host.get_filter_value,
		set_filter_value: host.set_filter_value,
		created: !existing,
	};
	const live_filters = () =>
		cards.map((card) => ({
			df: card.df,
			get_value() {
				return dialog_filter_value(card);
			},
		}));
	Object.defineProperty(host, "filters", {
		configurable: true,
		enumerable: true,
		get: live_filters,
	});
	host.get_filter = (fieldname) => host.filters.find((filter) => filter.df.fieldname === fieldname) || null;
	host.get_filter_value = (fieldname) => {
		const filter = host.get_filter(fieldname);
		return filter ? filter.get_value() : null;
	};
	host.set_filter_value = (fieldname, value) => {
		const card = cards.find((item) => item.df.fieldname === fieldname);
		if (card && card.control && card.control.set_value) card.control.set_value(value);
	};
	return () => {
		delete host.filters;
		if (previous.created) {
			delete frappe.query_report;
			return;
		}
		host.filters = previous.filters;
		host.get_filter = previous.get_filter;
		host.get_filter_value = previous.get_filter_value;
		host.set_filter_value = previous.set_filter_value;
	};
}

function link_doctype(options) {
	if (typeof options !== "string" || !options || options.includes("\n")) return "";
	if (options === options.toLowerCase()) return "";
	return options;
}

function report_list_data(df, cards, txt) {
	try {
		if (typeof df.get_data === "function") {
			const result = df.get_data(txt);
			if (result == null) return [];
			if (result.then) return result.then((options) => options || []);
			return result;
		}
		const doctype = link_doctype(df.options);
		if (!doctype) return [];
		const extra = {};
		const company = dialog_filter_value(cards.find((card) => card.df.fieldname === "company"));
		if (company) extra.company = company;
		return frappe.db.get_link_options(doctype, txt, extra);
	} catch (error) {
		console.error(error);
		return [];
	}
}

function report_get_query(df) {
	return df.get_query;
}

function mount_control(parent, df, value, onchange) {
	const control = frappe.ui.form.make_control({
		df: Object.assign({}, df, {
			label: "",
			reqd: df.reqd ? 1 : 0,
			onchange,
		}),
		parent,
		render_input: true,
	});
	if (value !== undefined && value !== null && value !== "") {
		control.set_value(value);
	}
	return control;
}

function read_card(card) {
	const df = card.df;
	if (card.mode === "Raw") {
		return ((card.raw_control && card.raw_control.get_value()) || "").trim();
	}
	if (df.fieldtype === "Date") {
		if (card.mode === "Today") return "{{ today() }}";
		if (card.mode === "Yesterday") return "{{ yesterday() }}";
		if (card.mode === "Fixed") return ((card.fixed && card.fixed.get_value()) || "").trim();
		return "";
	}
	if (df.fieldtype === "Datetime") {
		if (card.mode === "Today" || card.mode === "Yesterday") {
			const time = normalize_time(card.time_control && card.time_control.get_value());
			const day = card.mode === "Today" ? "{{ today() }}" : "{{ yesterday() }}";
			return day + " " + time;
		}
		if (card.mode === "Fixed") return ((card.fixed && card.fixed.get_value()) || "").trim();
		return "";
	}
	if (!card.control) return "";
	const value = card.control.get_value();
	if (df.fieldtype === "Check") return value ? "1" : "";
	if (df.fieldtype === "MultiSelectList" || df.fieldtype === "MultiSelect" || Array.isArray(value)) {
		const list = value || [];
		return list.length ? JSON.stringify(list) : "";
	}
	return value == null ? "" : String(value).trim();
}

function render_slot(card, onchange, cards) {
	const df = card.df;
	const slot = card.slot;
	slot.empty();
	card.fixed = null;
	card.time_control = null;
	card.raw_control = null;
	card.control = null;

	if (df.fieldtype === "Date" || df.fieldtype === "Datetime") {
		if (card.mode === "Raw") {
			card.raw_control = mount_control(slot, { fieldtype: "Small Text", fieldname: df.fieldname }, card.saved.raw || "", onchange);
			return;
		}
		if (card.mode === "Fixed") {
			card.fixed = mount_control(
				slot,
				{ fieldtype: df.fieldtype, fieldname: df.fieldname },
				card.saved.raw || "",
				onchange
			);
			return;
		}
		if (df.fieldtype === "Datetime" && (card.mode === "Today" || card.mode === "Yesterday")) {
			slot.append(`<div class="text-muted small" style="margin-bottom:6px">${__("Time on that day")}</div>`);
			const time_wrap = $('<div class="daer-time"></div>').appendTo(slot);
			card.time_control = mount_control(
				time_wrap,
				{ fieldtype: "Time", fieldname: df.fieldname + "_time" },
				card.saved.time || "00:00:00",
				onchange
			);
			return;
		}
		if (card.mode === "Today" || card.mode === "Yesterday") {
			slot.append(
				`<div class="text-muted small">${__("Filled in on the day the email is sent.")}</div>`
			);
		}
		return;
	}

	if (card.mode === "Raw") {
		card.raw_control = mount_control(slot, { fieldtype: "Small Text", fieldname: df.fieldname }, card.saved.raw || "", onchange);
		return;
	}

	const control_df = {
		fieldtype: df.fieldtype || "Data",
		fieldname: df.fieldname,
		options: control_options(df),
		get_query: report_get_query(df, cards || []),
		get_data:
			df.fieldtype === "MultiSelectList" || df.fieldtype === "MultiSelect"
				? (txt) => report_list_data(df, cards || [], txt)
				: df.get_data,
	};
	let initial = "";
	if (df.fieldtype === "MultiSelectList" || df.fieldtype === "MultiSelect") {
		initial = card.saved.values || [];
	} else if (df.fieldtype === "Check") {
		initial = card.saved.raw === "1" || card.saved.raw === 1 ? 1 : 0;
	} else {
		initial = card.saved.raw || "";
	}
	card.control = mount_control(slot, control_df, initial, onchange);
}

function open_filter_builder(frm) {
	if (!frm.doc.report) {
		frappe.msgprint(__("Choose a report first."));
		return;
	}

	frappe.dom.freeze(__("Loading report filters..."));
	load_report_definition(frm.doc.report)
		.then((report) => {
			frappe.dom.unfreeze();
			let filters = report.filters || [];
			if (typeof filters === "function") filters = filters() || [];
			filters = (filters || []).filter(
				(df) =>
					df &&
					df.fieldname &&
					!df.hidden &&
					df.fieldname !== "custom_format" &&
					!["Section Break", "Column Break", "Tab Break", "HTML", "Button", "Break", "Fold", "Heading"].includes(
						df.fieldtype
					)
			);
			if (!filters.length) {
				frappe.msgprint(__("This report has no filters to set."));
				return;
			}
			show_filter_builder(frm, filters);
		})
		.catch(() => {
			frappe.dom.unfreeze();
			frappe.msgprint(__("Could not load this report's filters."));
		});
}

function show_filter_builder(frm, filters) {
	const cards = [];
	const release_filters = apply_report_filters(cards);
	const dialog = new frappe.ui.Dialog({
		title: __("Set Filters"),
		size: "extra-large",
		fields: [
			{ fieldname: "intro", fieldtype: "HTML" },
			{ fieldname: "builder", fieldtype: "HTML" },
			{ fieldname: "preview_section", fieldtype: "Section Break", label: __("Filters to save") },
			{ fieldname: "preview", fieldtype: "HTML" },
		],
		primary_action_label: __("Save Changes"),
		primary_action() {
			if (!save_builder_filters(frm, filters, cards)) return;
			dialog.hide();
		},
	});

	dialog.show();
	dialog.$wrapper.on("hidden.bs.modal", release_filters);
	dialog.$wrapper.find(".modal-dialog").css("max-width", "980px");
	dialog.$wrapper.find(".modal, .modal-content, .modal-body").css("overflow", "visible");
	dialog.fields_dict.intro.$wrapper.html(
		`<p class="text-muted" style="margin:0 0 12px">${__(
			"Use the same choices as the report. Today and Yesterday are filled in when the email is sent. Save Changes writes the rows below into Filters."
		)}</p>`
	);

	const grid = $('<div class="daer-grid"></div>');
	dialog.fields_dict.builder.$wrapper
		.html("")
		.append(
			`<style>
				.daer-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
				@media (max-width: 720px){.daer-grid{grid-template-columns:1fr}}
				.daer-grid,.daer-card,.daer-card .frappe-control,.daer-card .form-group{overflow:visible}
				.daer-card{border:1px solid var(--border-color,#d1d8dd);border-radius:10px;padding:12px 14px;background:var(--fg-color,#fff);position:relative}
				.daer-card-title{font-weight:650;margin-bottom:8px}
				.daer-card .frappe-control{margin-bottom:0}
				.daer-card .dropdown-menu{z-index:1056;max-height:260px;overflow-y:auto}
			</style>`
		)
		.append(grid);

	const refresh_preview = () => {
		const rows = cards
			.map((card) => ({
				label: card.df.label || card.df.fieldname,
				value: read_card(card),
			}))
			.filter((row) => row.value);
		if (!rows.length) {
			dialog.fields_dict.preview.$wrapper.html(
				`<div class="text-muted">${__("Nothing selected yet. Empty filters are left out.")}</div>`
			);
			return;
		}
		const body = rows
			.map(
				(row) =>
					`<tr><td>${frappe.utils.escape_html(row.label)}</td><td><code>${frappe.utils.escape_html(
						row.value
					)}</code></td></tr>`
			)
			.join("");
		dialog.fields_dict.preview.$wrapper.html(
			`<table class="table table-bordered" style="margin:0"><thead><tr><th>${__("Filter")}</th><th>${__(
				"Value"
			)}</th></tr></thead><tbody>${body}</tbody></table>`
		);
	};

	filters.forEach((df) => {
		let saved = classify_saved(df, saved_filter_value(frm, df));
		if (!saved.mode && df.default != null && df.default !== "") {
			if (df.fieldtype === "Date" || df.fieldtype === "Datetime") {
				saved = { mode: "Fixed", raw: df.default };
			} else if (df.fieldtype === "MultiSelectList" || df.fieldtype === "MultiSelect") {
				if (Array.isArray(df.default) && df.default.length) saved = { mode: "List", values: df.default };
			} else {
				saved = { mode: "Fixed", raw: df.default };
			}
		}
		const card = { df, saved, mode: saved.mode || "" };
		const mandatory = df.reqd
			? ` <span class="text-danger">*</span> <span class="text-muted" style="font-weight:400">${__("Mandatory")}</span>`
			: "";
		const box = $(
			`<div class="daer-card${df.reqd ? " daer-reqd" : ""}"><div class="daer-card-title">${frappe.utils.escape_html(
				df.label || df.fieldname
			)}${mandatory} <span class="text-muted" style="font-weight:400">${frappe.utils.escape_html(
				df.fieldtype || ""
			)}</span></div></div>`
		);
		if (df.fieldtype === "Date" || df.fieldtype === "Datetime") {
			const fixed_label = df.fieldtype === "Datetime" ? __("Choose a date and time") : __("Choose a date");
			const select = $(
				`<select class="form-control">${[
					["", __("Leave empty")],
					["Fixed", fixed_label],
					["Today", __("Today")],
					["Yesterday", __("Yesterday")],
				]
					.concat(saved.mode === "Raw" ? [["Raw", __("Keep written value")]] : [])
					.map(
						([value, label]) =>
							`<option value="${value}" ${card.mode === value ? "selected" : ""}>${label}</option>`
					)
					.join("")}</select>`
			);
			box.append(select);
			card.slot = $('<div style="margin-top:8px"></div>').appendTo(box);
			select.on("change", () => {
				card.mode = select.val();
				render_slot(card, refresh_preview, cards);
				refresh_preview();
			});
		} else if (saved.mode === "Raw") {
			card.mode = "Raw";
			card.slot = $('<div></div>').appendTo(box);
			const swap = $(`<button type="button" class="btn btn-xs btn-default" style="margin-top:8px">${__("Pick from the report instead")}</button>`);
			box.append(swap);
			swap.on("click", () => {
				card.mode = "Fixed";
				card.saved = { mode: "Fixed", raw: "" };
				swap.remove();
				render_slot(card, refresh_preview, cards);
				refresh_preview();
			});
		} else {
			card.mode = "Fixed";
			card.slot = $('<div></div>').appendTo(box);
		}
		grid.append(box);
		cards.push(card);
		try {
			render_slot(card, refresh_preview, cards);
		} catch (error) {
			console.error(df.fieldname, error);
			card.slot.append(
				`<div class="text-muted small">${__(
					"This filter could not be shown. Write its value in the Filters table."
				)}</div>`
			);
		}
	});
	refresh_preview();
}

function save_builder_filters(frm, filters, cards) {
	const missing = cards.filter((card) => card.df.reqd && !read_card(card));
	if (missing.length) {
		frappe.msgprint(
			__("Fill the mandatory filters: {0}", [
				missing.map((card) => card.df.label || card.df.fieldname).join(", "),
			])
		);
		return false;
	}
	const rows = [];
	cards.forEach((card) => {
		const value = read_card(card);
		if (!value) return;
		rows.push({
			fieldname: card.df.label || card.df.fieldname,
			raw_fieldname: card.df.fieldname,
			value,
		});
	});
	(frm.doc.report_filters || []).forEach((existing) => {
		const known = filters.some(
			(df) =>
				df.fieldname === existing.raw_fieldname ||
				df.label === existing.fieldname ||
				df.fieldname === existing.fieldname
		);
		if (!known) {
			rows.push({
				fieldname: existing.fieldname,
				raw_fieldname: existing.raw_fieldname,
				value: existing.value,
			});
		}
	});
	frm.clear_table("report_filters");
	rows.forEach((row) => frm.add_child("report_filters", row));
	frm.refresh_field("report_filters");
	refresh_filter_rows(frm);
	frm.dirty();
	return true;
}

frappe.ui.form.on("Dynamic Auto Email Report", {
	onload(frm) {
		load_filter_options(frm);
		refresh_custom_format(frm);
	},
	refresh(frm) {
		load_filter_options(frm);
		refresh_custom_format(frm);
		frm.add_custom_button(__("Set Filters"), () => open_filter_builder(frm));
		if (frm.is_new()) return;

		frm.add_custom_button(__("Send Now"), function () {
			frappe.confirm(__("Send this report now?"), function () {
				frappe.call({
					method:
						"propms.property_management_solution.doctype.dynamic_auto_email_report.dynamic_auto_email_report.trigger_send_now",
					args: { name: frm.doc.name },
					freeze: true,
					freeze_message: __("Sending report..."),
					callback(r) {
						if (!r.exc) {
							frappe.show_alert({
								message: __("Report queued for sending."),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			});
		});
	},
	report(frm) {
		load_filter_options(frm);
		refresh_custom_format(frm);
	},
});

frappe.ui.form.on("Attendance Report Filter", {
	fieldname(frm, cdt, cdn) {
		const row = frappe.get_doc(cdt, cdn);
		if (!row || !row.fieldname) return;
		const match = REPORT_FILTER_FIELDS.find(
			(f) => f.label === row.fieldname || f.fieldname === row.fieldname
		);
		if (!match) return;
		if (match.fieldname) {
			frappe.model.set_value(cdt, cdn, "raw_fieldname", match.fieldname);
		}
		if (match.label && row.fieldname !== match.label) {
			frappe.model.set_value(cdt, cdn, "fieldname", match.label);
		}
	},
	form_render(frm, cdt, cdn) {
		apply_fieldname_options(frm, cdn, current_filter_options());
	},
	report_filters_add(frm, cdt, cdn) {
		apply_fieldname_options(frm, cdn, current_filter_options());
	},
});
