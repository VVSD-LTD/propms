// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.query_reports["Customer Payment History"] = {
	filters: [
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		// {
		// 	fieldname: "company",
		// 	label: __("Company"),
		// 	fieldtype: "Link",
		// 	options: "Company",
		// 	default: frappe.defaults.get_user_default("Company"),
		// },
		{
			// When ticked, fetches POS invoices instead of standard invoices.
			// Mirrors the is_pos field on Sales Invoice (0/1).
			fieldname: "is_pos",
			label: __("Include POS Invoices"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (!data) return value;

		// ── Remarks column ───────────────────────────────────────────────────
		if (column.fieldname === "remarks") {
			const colourMap = {
				"PAID IN TIME":         "#2ea44f",   // green
				"DELAYED":              "#d73a49",   // red
				"LATE PAYMENT CHARGES": "#e36209",   // orange
				"OUTSTANDING":          "#6f42c1",   // purple
			};
			const colour = colourMap[data.remarks];
			if (colour) {
				value = `<span style="color:${colour};font-weight:bold;">${data.remarks}</span>`;
			}
		}

		// ── Days delay column ────────────────────────────────────────────────
		if (column.fieldname === "days_delay" && data.days_delay) {
			const raw = data._days_delay_raw;
			if (raw !== null && raw !== undefined) {
				const colour = raw <= 0 ? "#2ea44f" : "#d73a49";
				value = `<span style="color:${colour};">${data.days_delay}</span>`;
			}
		}

		// ── Paid Via column ──────────────────────────────────────────────────
		if (column.fieldname === "payment_source" && data.payment_source) {
			const iconMap = {
				"Payment Entry": "💳",
				"Journal Entry": "📒",
			};
			const icon = iconMap[data.payment_source] || "";
			value = `${icon} ${data.payment_source}`;
		}

		return value;
	},

	get_datatable_options(options) {
		return Object.assign(options, {
			checkboxColumn: false,
			cellHeight: 35,
		});
	},
};