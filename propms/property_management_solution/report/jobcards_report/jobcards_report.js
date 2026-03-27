// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

// // ─── Status definitions (single source of truth) ─────────────────────────────
// const JC_STATUSES = [
// 	"Open",
// 	"In Progress",
// 	"Awaiting Parts",
// 	"Under Observation",
// 	"In Discussion",
// 	"Appointment",
// 	"Hold",
// 	"Closed",
// ];

// // Subdued, business-oriented palette
// const JC_STATUS_STYLE = {
// 	"Open":              { border: "#2c5f8a", bg: "#eaf2fb", text: "#1a3f5c", dot: "#2c5f8a" },
// 	"In Progress":       { border: "#1a6b3a", bg: "#eaf5ee", text: "#144d2a", dot: "#1a6b3a" },
// 	"Awaiting Parts":    { border: "#7a4f00", bg: "#fdf3e3", text: "#5a3800", dot: "#c07a00" },
// 	"Under Observation": { border: "#5a3d8a", bg: "#f3eefa", text: "#3d2860", dot: "#5a3d8a" },
// 	"In Discussion":     { border: "#3d6e6e", bg: "#e8f5f5", text: "#2a4d4d", dot: "#3d6e6e" },
// 	"Appointment":       { border: "#1a5f6e", bg: "#e6f4f7", text: "#124350", dot: "#1a5f6e" },
// 	"Hold":              { border: "#7a3030", bg: "#faeaea", text: "#5a1f1f", dot: "#b03030" },
// 	"Closed":            { border: "#3d3d3d", bg: "#f0f0f0", text: "#222",   dot: "#666"    },
// };

// frappe.query_reports["Jobcards Report"] = {
// 	filters: [
// 		// {
// 		// 	fieldname: "from_date",
// 		// 	label: __("From Date"),
// 		// 	fieldtype: "Date",
// 		// 	default: frappe.datetime.add_days(frappe.datetime.nowdate(), -30),
// 		// 	reqd: 1,
// 		// },
// 		{
// 			fieldname: "to_date",
// 			label: __("As On Date"),
// 			fieldtype: "Date",
// 			default: frappe.datetime.nowdate(),
// 			reqd: 0,
// 		},
// 		{
// 			fieldname: "property_name",
// 			label: __("Property"),
// 			fieldtype: "Data",
// 			wildcard_filter: 1,
// 		},
// 		{
// 			fieldname: "status",
// 			label: __("Status"),
// 			fieldtype: "Select",
// 			options: "\n" + JC_STATUSES.join("\n"),
// 		},
// 		{
// 			fieldname: "issue_type",
// 			label: __("Issue Type"),
// 			fieldtype: "Data",
// 		},
// 		{
// 			fieldname: "priority",
// 			label: __("Priority"),
// 			fieldtype: "Select",
// 			options: "\nLOW\nMEDIUM\nHIGH\nCRITICAL",
// 		},
// 		{
// 			fieldname: "person_in_charge",
// 			label: __("Person In Charge"),
// 			fieldtype: "Link",
// 			options: "Employee",
// 		},
// 	],

// 	formatter: function (value, row, column, data, default_formatter) {
// 		value = default_formatter(value, row, column, data, default_formatter);

// 		if (column.fieldname === "status" && data.status) {
// 			const s = JC_STATUS_STYLE[data.status];
// 			if (s) {
// 				value = `<span style="background:${s.bg};color:${s.text};border:1px solid ${s.border};
// 				         padding:2px 9px;border-radius:3px;font-weight:600;font-size:12px;">${data.status}</span>`;
// 			}
// 		}

// 		if (column.fieldname === "priority") {
// 			const colorMap = { HIGH: "#b30000", CRITICAL: "#6b0000", MEDIUM: "#b05a00", LOW: "#1a6b3a" };
// 			const color = colorMap[data.priority] || "#333";
// 			value = `<span style="color:${color};font-weight:700;">${value}</span>`;
// 		}

// 		if (column.fieldname === "age_days") {
// 			const age = parseInt(data.age_days);
// 			let color = "#1a6b3a";
// 			if (age > 30) color = "#b30000";
// 			else if (age > 14) color = "#b05a00";
// 			else if (age > 7) color = "#c07a00";
// 			value = `<span style="color:${color};font-weight:600;">${value} days</span>`;
// 		}

// 		if (column.fieldname === "sla_status") {
// 			const colorMap = {
// 				Fulfilled: "#1a6b3a", Failed: "#b30000",
// 				"First Response Due": "#c07a00", "Resolution Due": "#b05a00",
// 			};
// 			const color = colorMap[data.sla_status] || "#333";
// 			value = `<span style="color:${color};font-weight:600;">${value}</span>`;
// 		}

// 		return value;
// 	},

// 	onload: function (report) {
// 		report.page.add_inner_button(__("Summary Dashboard"), function () {
// 			show_summary_dialog();
// 		});
// 		report.page.add_inner_button(__("Print Summary"), function () {
// 			print_summary_report();
// 		});
// 	},

// 	get_chart_data: function (columns, result) {
// 		if (!result || !result.length) return null;
// 		const typeCount = {};
// 		result.forEach((row) => {
// 			if (row.issue_type) typeCount[row.issue_type] = (typeCount[row.issue_type] || 0) + 1;
// 		});
// 		const sorted = Object.entries(typeCount).sort((a, b) => b[1] - a[1]);
// 		return {
// 			data: {
// 				labels: sorted.map((x) => x[0]),
// 				datasets: [{ name: "Jobcards", values: sorted.map((x) => x[1]) }],
// 			},
// 			type: "bar",
// 			colors: ["#2c5f8a"],
// 			barOptions: { spaceRatio: 0.3 },
// 		};
// 	},
// };


// // ─── Shared: build aggregated data from report result ────────────────────────
// function build_aggregate() {
// 	const result = frappe.query_report.data || [];
// 	const statusCount = {};
// 	const typeCount   = {};
// 	const typeMatrix  = {};

// 	JC_STATUSES.forEach((s) => (statusCount[s] = 0));

// 	result.forEach((row) => {
// 		const s = row.status     || "Unknown";
// 		const t = row.issue_type || "Unknown";
// 		if (statusCount[s] !== undefined) statusCount[s]++;
// 		else statusCount[s] = 1;
// 		typeCount[t] = (typeCount[t] || 0) + 1;
// 		if (!typeMatrix[t]) typeMatrix[t] = {};
// 		typeMatrix[t][s] = (typeMatrix[t][s] || 0) + 1;
// 	});

// 	return { result, statusCount, typeCount, typeMatrix, total: result.length };
// }


// // ─── Summary Dialog (extra-large) ────────────────────────────────────────────
// function show_summary_dialog() {
// 	const { statusCount, typeCount, typeMatrix, total } = build_aggregate();
// 	if (!total) { frappe.msgprint(__("No data to summarise.")); return; }

// 	const filters   = frappe.query_report.get_filter_values();
// 	const from_date = filters.from_date || "—";
// 	const to_date   = filters.to_date   || "—";

// 	// ── KPI strip ──
// 	const kpiBoxes = JC_STATUSES.map((s) => {
// 		const n  = statusCount[s] || 0;
// 		const st = JC_STATUS_STYLE[s];
// 		return `<div style="flex:1;min-width:90px;border:1px solid ${st.border};border-top:3px solid ${st.border};
// 		         border-radius:3px;padding:10px 8px;text-align:center;background:${st.bg};">
// 			<div style="font-size:22px;font-weight:800;color:${st.border};">${n}</div>
// 			<div style="font-size:10px;color:${st.text};margin-top:3px;font-weight:600;line-height:1.3;">${s}</div>
// 		</div>`;
// 	}).join("");

// 	// ── Table 1: By Status ──
// 	const statusRows = JC_STATUSES.map((s, i) => {
// 		const n   = statusCount[s] || 0;
// 		const pct = total ? ((n / total) * 100).toFixed(1) : "0.0";
// 		const st  = JC_STATUS_STYLE[s];
// 		const bar = `<div style="background:#e8e8e8;border-radius:2px;height:7px;width:100%;max-width:140px;display:inline-block;vertical-align:middle;">
// 			<div style="background:${st.border};height:7px;border-radius:2px;width:${Math.min(parseFloat(pct),100)}%;"></div>
// 		</div>`;
// 		return `<tr style="background:${i%2===0?'#fff':'#fafafa'};">
// 			<td style="padding:7px 12px;">${i+2}</td>
// 			<td style="padding:7px 12px;">
// 				<span style="display:inline-block;width:9px;height:9px;border-radius:50%;
// 				             background:${st.dot};margin-right:7px;vertical-align:middle;"></span>${s}
// 			</td>
// 			<td style="text-align:center;font-weight:700;padding:7px 12px;">${n}</td>
// 			<td style="text-align:center;padding:7px 12px;">${pct}%</td>
// 			<td style="padding:7px 16px;">${bar}</td>
// 		</tr>`;
// 	}).join("");

// 	// ── Table 2: By Issue Type ──
// 	const sortedTypes = Object.entries(typeCount).sort((a, b) => b[1] - a[1]);
// 	const typeRows = sortedTypes.map(([t, n], i) => {
// 		const pct = total ? ((n / total) * 100).toFixed(2) : "0.00";
// 		const bar = `<div style="background:#e8e8e8;border-radius:2px;height:7px;width:100%;max-width:180px;display:inline-block;vertical-align:middle;">
// 			<div style="background:#2c5f8a;height:7px;border-radius:2px;width:${Math.min(parseFloat(pct),100)}%;"></div>
// 		</div>`;
// 		return `<tr style="background:${i%2===0?'#fff':'#fafafa'};">
// 			<td style="padding:7px 12px;">${i+1}</td>
// 			<td style="padding:7px 12px;font-weight:600;">${t}</td>
// 			<td style="text-align:center;font-weight:700;padding:7px 12px;">${n}</td>
// 			<td style="text-align:center;padding:7px 12px;">${pct}%</td>
// 			<td style="padding:7px 16px;">${bar}</td>
// 		</tr>`;
// 	}).join("");

// 	// ── Section 3: Per-Issue-Type breakdown cards (all 8 statuses) ──
// 	const typeCards = sortedTypes.map(([t, totalForType], i) => {
// 		const matrix = typeMatrix[t] || {};
// 		const statCells = JC_STATUSES.map((s) => {
// 			const n  = matrix[s] || 0;
// 			const st = JC_STATUS_STYLE[s];
// 			return `<td style="text-align:center;padding:10px 6px;border-right:1px solid #ebebeb;min-width:80px;">
// 				<div style="font-size:18px;font-weight:800;color:${n > 0 ? st.border : '#bbb'};">${n > 0 ? n : "–"}</div>
// 				<div style="font-size:10px;color:#666;margin-top:3px;line-height:1.3;">${s}</div>
// 			</td>`;
// 		}).join("");
// 		return `
// 		<div style="margin-bottom:10px;border:1px solid #d4d4d4;border-radius:3px;overflow:hidden;">
// 			<div style="background:#f4f4f4;border-bottom:1px solid #d4d4d4;
// 			            padding:7px 14px;display:flex;justify-content:space-between;align-items:center;">
// 				<span style="font-size:13px;font-weight:700;color:#1a1a1a;">${t}</span>
// 				<span style="font-size:11px;color:#555;font-weight:600;background:#e0e0e0;
// 				             padding:2px 10px;border-radius:10px;">Total: ${totalForType}</span>
// 			</div>
// 			<div style="overflow-x:auto;">
// 				<table style="width:100%;border-collapse:collapse;background:#fff;">
// 					<tbody><tr>${statCells}</tr></tbody>
// 				</table>
// 			</div>
// 		</div>`;
// 	}).join("");

// 	const html = `
// 	<style>
// 		.jc-dash { font-family: -apple-system, 'Segoe UI', Arial, sans-serif; color: #1a1a1a; }
// 		.jc-dash .sec-title {
// 			font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;
// 			color:#1a3f5c;border-bottom:2px solid #2c5f8a;padding-bottom:5px;margin:24px 0 10px;
// 		}
// 		.jc-dash table.data-table { width:100%;border-collapse:collapse;font-size:13px; }
// 		.jc-dash table.data-table thead th {
// 			background:#2c5f8a;color:#fff;padding:8px 12px;text-align:left;font-weight:600;font-size:12px;
// 		}
// 		.jc-dash table.data-table tfoot td {
// 			padding:8px 12px;font-weight:700;background:#eef2f8;border-top:2px solid #2c5f8a;font-size:13px;
// 		}
// 	</style>
// 	<div class="jc-dash">

// 		<div style="display:flex;justify-content:space-between;align-items:center;
// 		            border-bottom:2px solid #2c5f8a;padding-bottom:12px;margin-bottom:20px;">
// 			<div>
// 				<div style="font-size:16px;font-weight:700;color:#1a3f5c;">JOBCARDS SUMMARY REPORT</div>
// 				<div style="font-size:11px;color:#777;margin-top:3px;">Period: ${from_date} &nbsp;–&nbsp; ${to_date}</div>
// 			</div>
// 			<div style="font-size:13px;font-weight:700;color:#2c5f8a;">
// 				Total Jobcards: <span style="font-size:18px;">${total}</span>
// 			</div>
// 		</div>

// 		<!-- KPI Strip -->
// 		<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px;">
// 			${kpiBoxes}
// 		</div>

// 		<!-- Table 1 -->
// 		<div class="sec-title">Description by Status</div>
// 		<table class="data-table">
// 			<thead><tr>
// 				<th style="width:50px;">S/No</th>
// 				<th>Description by Status</th>
// 				<th style="text-align:center;width:80px;">Number</th>
// 				<th style="text-align:center;width:100px;">% from Total JC</th>
// 				<th style="width:160px;"></th>
// 			</tr></thead>
// 			<tbody>${statusRows}</tbody>
// 			<tfoot><tr>
// 				<td colspan="2">Cumulative Value (Total Jobcards)</td>
// 				<td style="text-align:center;">${total}</td>
// 				<td style="text-align:center;">100%</td>
// 				<td></td>
// 			</tr></tfoot>
// 		</table>

// 		<!-- Table 2 -->
// 		<div class="sec-title">Jobcard by Issue Type</div>
// 		<table class="data-table">
// 			<thead><tr>
// 				<th style="width:50px;">S/No</th>
// 				<th>Issue Type</th>
// 				<th style="text-align:center;width:100px;">Jobcards Assigned</th>
// 				<th style="text-align:center;width:120px;">% of Total JC</th>
// 				<th style="width:200px;"></th>
// 			</tr></thead>
// 			<tbody>${typeRows}</tbody>
// 			<tfoot><tr>
// 				<td colspan="2">Total</td>
// 				<td style="text-align:center;">${total}</td>
// 				<td style="text-align:center;">100.00%</td>
// 				<td></td>
// 			</tr></tfoot>
// 		</table>

// 		<!-- Section 3: Per-Type Cards -->
// 		<div class="sec-title">Issue Type Breakdown by Status</div>
// 		<p style="font-size:11px;color:#888;margin:-6px 0 12px;">
// 			Each card shows count per status across all 8 stages
// 		</p>
// 		${typeCards}

// 	</div>`;

// 	const d = new frappe.ui.Dialog({
// 		title: __("Jobcards Summary Dashboard"),
// 		size: "extra-large",
// 		fields: [{ fieldtype: "HTML", fieldname: "dashboard_html" }],
// 		primary_action_label: __("🖨 Print / Export"),
// 		primary_action() { print_summary_report(); },
// 		secondary_action_label: __("Close"),
// 		secondary_action() { d.hide(); },
// 	});

// 	// Force extra-large width override
// 	d.$wrapper.find(".modal-dialog").css({ "max-width": "92vw", "width": "92vw" });
// 	d.$wrapper.find(".modal-body").css({ "max-height": "80vh", "overflow-y": "auto", "padding": "20px 24px" });

// 	d.fields_dict.dashboard_html.$wrapper.html(html);
// 	d.show();
// }


// // ─── Print Format (A4, opens in new window) ──────────────────────────────────
// function print_summary_report() {
// 	const { statusCount, typeCount, typeMatrix, total } = build_aggregate();
// 	if (!total) { frappe.msgprint(__("No data to print.")); return; }

// 	const filters   = frappe.query_report.get_filter_values();
// 	const from_date = filters.from_date || "—";
// 	const to_date   = filters.to_date   || "—";
// 	const today     = frappe.datetime.nowdate();

// 	const statusRows = JC_STATUSES.map((s, i) => {
// 		const n   = statusCount[s] || 0;
// 		const pct = total ? ((n / total) * 100).toFixed(1) : "0.0";
// 		return `<tr style="background:${i%2===0?'#fff':'#f8f8f8'};">
// 			<td>${i + 2}</td><td>${s}</td>
// 			<td style="text-align:center;">${n}</td>
// 			<td style="text-align:center;">${pct}%</td>
// 		</tr>`;
// 	}).join("");

// 	const sortedTypes = Object.entries(typeCount).sort((a, b) => b[1] - a[1]);
// 	const typeRows = sortedTypes.map(([t, n], i) => {
// 		const pct = total ? ((n / total) * 100).toFixed(2) : "0.00";
// 		return `<tr style="background:${i%2===0?'#fff':'#f8f8f8'};">
// 			<td>${i + 1}</td><td>${t}</td>
// 			<td style="text-align:center;">${n}</td>
// 			<td style="text-align:center;">${pct}%</td>
// 		</tr>`;
// 	}).join("");

// 	const breakdownRows = sortedTypes.map(([t, totalForType], i) => {
// 		const matrix = typeMatrix[t] || {};
// 		const cells  = JC_STATUSES.map((s) => {
// 			const n = matrix[s] || 0;
// 			return `<td style="text-align:center;">${n > 0 ? `<strong>${n}</strong>` : "–"}</td>`;
// 		}).join("");
// 		return `<tr style="background:${i%2===0?'#fff':'#f8f8f8'};">
// 			<td>${i + 1}</td><td><strong>${t}</strong></td>${cells}
// 			<td style="text-align:center;font-weight:700;">${totalForType}</td>
// 		</tr>`;
// 	}).join("");

// 	const statusHeaderCells = JC_STATUSES.map(
// 		(s) => `<th style="font-size:9px;padding:5px 3px;text-align:center;">${s}</th>`
// 	).join("");

// 	const kpiPrint = JC_STATUSES.map((s) => `
// 		<div class="kpi-box">
// 			<div class="kpi-val">${statusCount[s] || 0}</div>
// 			<div class="kpi-lbl">${s}</div>
// 		</div>`).join("");

// 	const html = `<!DOCTYPE html>
// <html><head>
// <meta charset="UTF-8">
// <title>Jobcards Summary Report – ${to_date}</title>
// <style>
//   * { box-sizing:border-box; margin:0; padding:0; }
//   body { font-family: Arial, sans-serif; font-size:11px; color:#1a1a1a; background:#fff; }
//   .page { max-width:1100px; margin:0 auto; padding:28px 32px; }
//   .rpt-header { text-align:center; border-bottom:2.5px solid #1a3f5c; padding-bottom:12px; margin-bottom:16px; }
//   .rpt-header h1 { font-size:17px; font-weight:700; letter-spacing:1px; color:#1a3f5c; }
//   .rpt-header .sub { font-size:11px; color:#555; margin-top:4px; }
//   .rpt-meta { display:flex; justify-content:space-between; font-size:10px; color:#555;
//               margin-bottom:16px; padding-bottom:8px; border-bottom:1px solid #ddd; }
//   .kpi-row { display:flex; gap:0; margin-bottom:20px; border:1px solid #bbb; border-radius:2px; overflow:hidden; }
//   .kpi-box { flex:1; text-align:center; padding:10px 6px; border-right:1px solid #bbb; }
//   .kpi-box:last-child { border-right:none; }
//   .kpi-val { font-size:18px; font-weight:800; color:#1a3f5c; }
//   .kpi-lbl { font-size:8px; color:#555; margin-top:2px; text-transform:uppercase; letter-spacing:.3px; }
//   .sec-title { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.5px;
//                color:#fff; background:#1a3f5c; padding:5px 10px; margin:18px 0 0; }
//   table.rpt { width:100%; border-collapse:collapse; font-size:11px; }
//   table.rpt thead th { background:#2c5f8a; color:#fff; padding:6px 9px; text-align:left; font-weight:600; }
//   table.rpt tbody td { padding:5px 9px; border-bottom:1px solid #e8e8e8; }
//   table.rpt tfoot td { padding:6px 9px; font-weight:700; background:#eef2f8; border-top:2px solid #1a3f5c; }
//   .footer { margin-top:24px; border-top:1px solid #ccc; padding-top:8px;
//             font-size:9px; color:#888; display:flex; justify-content:space-between; }
//   @media print {
//     .no-print { display:none !important; }
//     body { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
//   }
// </style>
// </head><body>
// <div class="page">
//   <div class="no-print" style="text-align:right;margin-bottom:14px;">
//     <button onclick="window.print()"
//       style="background:#1a3f5c;color:#fff;border:none;padding:7px 18px;font-size:12px;cursor:pointer;border-radius:3px;">
//       🖨 Print / Save as PDF
//     </button>
//   </div>
//   <div class="rpt-header">
//     <h1>JOBCARDS SUMMARY REPORT</h1>
//     <div class="sub">As on ${to_date}</div>
//   </div>
//   <div class="rpt-meta">
//     <span><strong>Report Period:</strong> ${from_date} – ${to_date}</span>
//     <span><strong>Generated:</strong> ${today}</span>
//     <span><strong>Total Jobcards:</strong> ${total}</span>
//   </div>
//   <div class="kpi-row">
//     <div class="kpi-box"><div class="kpi-val">${total}</div><div class="kpi-lbl">Total</div></div>
//     ${kpiPrint}
//   </div>
//   <div class="sec-title">Description by Status</div>
//   <table class="rpt">
//     <thead><tr>
//       <th style="width:45px;">S/No</th><th>Description by Status</th>
//       <th style="text-align:center;width:85px;">Number</th>
//       <th style="text-align:center;width:110px;">% from Total JC</th>
//     </tr></thead>
//     <tbody>${statusRows}</tbody>
//     <tfoot><tr>
//       <td colspan="2">Cumulative Value (Total Jobcards)</td>
//       <td style="text-align:center;">${total}</td>
//       <td style="text-align:center;">100%</td>
//     </tr></tfoot>
//   </table>
//   <div class="sec-title">Jobcard by Issue Type</div>
//   <table class="rpt">
//     <thead><tr>
//       <th style="width:45px;">S/No</th><th>Issue Type</th>
//       <th style="text-align:center;width:110px;">Jobcards Assigned</th>
//       <th style="text-align:center;width:120px;">% of Total JC</th>
//     </tr></thead>
//     <tbody>${typeRows}</tbody>
//     <tfoot><tr>
//       <td colspan="2">Total</td>
//       <td style="text-align:center;">${total}</td>
//       <td style="text-align:center;">100.00%</td>
//     </tr></tfoot>
//   </table>
//   <div class="sec-title">Issue Type Breakdown by Status</div>
//   <table class="rpt" style="font-size:10px;">
//     <thead><tr>
//       <th style="width:35px;">S/No</th>
//       <th style="min-width:110px;">Issue Type</th>
//       ${statusHeaderCells}
//       <th style="text-align:center;font-size:9px;padding:5px 4px;">TOTAL</th>
//     </tr></thead>
//     <tbody>${breakdownRows}</tbody>
//     <tfoot><tr>
//       <td colspan="2" style="padding-left:9px;">Grand Total</td>
//       ${JC_STATUSES.map((s) => `<td style="text-align:center;">${statusCount[s] || 0}</td>`).join("")}
//       <td style="text-align:center;">${total}</td>
//     </tr></tfoot>
//   </table>
//   <div class="footer">
//     <span>Jobcards Summary Report</span>
//     <span>Period: ${from_date} – ${to_date} &nbsp;|&nbsp; Printed: ${today}</span>
//   </div>
// </div>
// </body></html>`;

// 	const w = window.open("", "_blank");
// 	w.document.write(html);
// 	w.document.close();
// }

// ─── Status definitions (single source of truth) ─────────────────────────────
const JC_STATUSES = [
	"Open",
	"In Progress",
	"Awaiting Parts",
	"Under Observation",
	"In Discussion",
	"Appointment",
	"Hold",
	"Closed",
];

// Subdued, business-oriented palette
const JC_STATUS_STYLE = {
	"Open":              { border: "#2c5f8a", bg: "#eaf2fb", text: "#1a3f5c", dot: "#2c5f8a" },
	"In Progress":       { border: "#1a6b3a", bg: "#eaf5ee", text: "#144d2a", dot: "#1a6b3a" },
	"Awaiting Parts":    { border: "#7a4f00", bg: "#fdf3e3", text: "#5a3800", dot: "#c07a00" },
	"Under Observation": { border: "#5a3d8a", bg: "#f3eefa", text: "#3d2860", dot: "#5a3d8a" },
	"In Discussion":     { border: "#3d6e6e", bg: "#e8f5f5", text: "#2a4d4d", dot: "#3d6e6e" },
	"Appointment":       { border: "#1a5f6e", bg: "#e6f4f7", text: "#124350", dot: "#1a5f6e" },
	"Hold":              { border: "#7a3030", bg: "#faeaea", text: "#5a1f1f", dot: "#b03030" },
	"Closed":            { border: "#3d3d3d", bg: "#f0f0f0", text: "#222",   dot: "#666"    },
};

// ─── Helper: fieldname key for a status ──────────────────────────────────────
function statusKey(s) {
	return "status_" + s.toLowerCase().replace(/ /g, "_").replace(/-/g, "_");
}

frappe.query_reports["Jobcards Report"] = {
	filters: [
		{
			fieldname: "to_date",
			label: __("As On Date"),
			fieldtype: "Date",
			default: frappe.datetime.nowdate(),
			reqd: 0,
		},
		{
			fieldname: "property_name",
			label: __("Property"),
			fieldtype: "Data",
			wildcard_filter: 1,
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\n" + JC_STATUSES.join("\n"),
		},
		{
			fieldname: "issue_type",
			label: __("Issue Type"),
			fieldtype: "Data",
		},
		{
			fieldname: "priority",
			label: __("Priority"),
			fieldtype: "Select",
			options: "\nLOW\nMEDIUM\nHIGH\nCRITICAL",
		},
		{
			fieldname: "person_in_charge",
			label: __("Person In Charge"),
			fieldtype: "Link",
			options: "Employee",
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data, default_formatter);

		// Highlight the grand total row
		if (data && data.issue_type === "GRAND TOTAL") {
			value = `<span style="font-weight:700;color:#1a3f5c;">${value}</span>`;
		}

		// Colour zero values grey, non-zero bold
		if (column.fieldname && column.fieldname.startsWith("status_")) {
			const n = parseInt(data[column.fieldname]) || 0;
			if (n === 0) {
				value = `<span style="color:#bbb;">–</span>`;
			} else {
				const s = JC_STATUSES.find(s => statusKey(s) === column.fieldname);
				const color = s && JC_STATUS_STYLE[s] ? JC_STATUS_STYLE[s].border : "#333";
				value = `<span style="font-weight:700;color:${color};">${n}</span>`;
			}
		}

		if (column.fieldname === "pct") {
			const n = parseFloat(data[column.fieldname]) || 0;
			const color = n > 30 ? "#b30000" : n > 15 ? "#b05a00" : "#1a6b3a";
			value = `<span style="color:${color};font-weight:600;">${n}%</span>`;
		}

		return value;
	},

	onload: function (report) {
		report.page.add_inner_button(__("Summary Dashboard"), function () {
			show_summary_dialog();
		});
		report.page.add_inner_button(__("Print Summary"), function () {
			print_summary_report();
		});
	},

	get_chart_data: function (columns, result) {
		if (!result || !result.length) return null;
		// data[0] is grand total row, skip it; rest are per-type rows
		const rows = result.filter(r => r.issue_type !== "GRAND TOTAL");
		return {
			data: {
				labels: rows.map(r => r.issue_type),
				datasets: [{ name: "Jobcards", values: rows.map(r => r.total) }],
			},
			type: "bar",
			colors: ["#2c5f8a"],
			barOptions: { spaceRatio: 0.3 },
		};
	},
};


// ─── Shared: read aggregated data from the already-summarised report rows ─────
function build_aggregate() {
	const result = frappe.query_report.data || [];

	// data[0] is the grand total row Python put first
	const grandRow  = result.find(r => r.issue_type === "GRAND TOTAL") || {};
	const typeRows  = result.filter(r => r.issue_type !== "GRAND TOTAL");
	const total     = grandRow.total || 0;

	// Rebuild statusCount and typeCount/typeMatrix from the summary rows
	const statusCount = {};
	JC_STATUSES.forEach(s => {
		statusCount[s] = grandRow[statusKey(s)] || 0;
	});

	const typeCount  = {};
	const typeMatrix = {};
	typeRows.forEach(row => {
		const t = row.issue_type;
		typeCount[t] = row.total || 0;
		typeMatrix[t] = {};
		JC_STATUSES.forEach(s => {
			typeMatrix[t][s] = row[statusKey(s)] || 0;
		});
	});

	return { result: typeRows, statusCount, typeCount, typeMatrix, total };
}


// ─── Summary Dialog (extra-large) ────────────────────────────────────────────
function show_summary_dialog() {
	const { statusCount, typeCount, typeMatrix, total } = build_aggregate();
	if (!total) { frappe.msgprint(__("No data to summarise.")); return; }

	const filters   = frappe.query_report.get_filter_values();
	const from_date = filters.from_date || "—";
	const to_date   = filters.to_date   || "—";

	// ── KPI strip ──
	const kpiBoxes = JC_STATUSES.map((s) => {
		const n  = statusCount[s] || 0;
		const st = JC_STATUS_STYLE[s];
		return `<div style="flex:1;min-width:90px;border:1px solid ${st.border};border-top:3px solid ${st.border};
		         border-radius:3px;padding:10px 8px;text-align:center;background:${st.bg};">
			<div style="font-size:22px;font-weight:800;color:${st.border};">${n}</div>
			<div style="font-size:10px;color:${st.text};margin-top:3px;font-weight:600;line-height:1.3;">${s}</div>
		</div>`;
	}).join("");

	// ── Table 1: By Status ──
	const statusRows = JC_STATUSES.map((s, i) => {
		const n   = statusCount[s] || 0;
		const pct = total ? ((n / total) * 100).toFixed(1) : "0.0";
		const st  = JC_STATUS_STYLE[s];
		const bar = `<div style="background:#e8e8e8;border-radius:2px;height:7px;width:100%;max-width:140px;display:inline-block;vertical-align:middle;">
			<div style="background:${st.border};height:7px;border-radius:2px;width:${Math.min(parseFloat(pct),100)}%;"></div>
		</div>`;
		return `<tr style="background:${i%2===0?'#fff':'#fafafa'};">
			<td style="padding:7px 12px;">${i+1}</td>
			<td style="padding:7px 12px;">
				<span style="display:inline-block;width:9px;height:9px;border-radius:50%;
				             background:${st.dot};margin-right:7px;vertical-align:middle;"></span>${s}
			</td>
			<td style="text-align:center;font-weight:700;padding:7px 12px;">${n}</td>
			<td style="text-align:center;padding:7px 12px;">${pct}%</td>
			<td style="padding:7px 16px;">${bar}</td>
		</tr>`;
	}).join("");

	// ── Table 2: By Issue Type ──
	const sortedTypes = Object.entries(typeCount).sort((a, b) => b[1] - a[1]);
	// const typeRows = sortedTypes.map(([t, n], i) => {
	// 	const pct = total ? ((n / total) * 100).toFixed(2) : "0.00";
	// 	const bar = `<div style="background:#e8e8e8;border-radius:2px;height:7px;width:100%;max-width:180px;display:inline-block;vertical-align:middle;">
	// 		<div style="background:#2c5f8a;height:7px;border-radius:2px;width:${Math.min(parseFloat(pct),100)}%;"></div>
	// 	</div>`;
	// 	return `<tr style="background:${i%2===0?'#fff':'#fafafa'};">
	// 		<td style="padding:7px 12px;">${i+1}</td>
	// 		<td style="padding:7px 12px;font-weight:600;">${t}</td>
	// 		<td style="text-align:center;font-weight:700;padding:7px 12px;">${n}</td>
	// 		<td style="text-align:center;padding:7px 12px;">${pct}%</td>
	// 		<td style="padding:7px 16px;">${bar}</td>
	// 	</tr>`;
	// }).join("");

	// ── Section 3: Per-Issue-Type breakdown cards ──
	const typeCards = sortedTypes.map(([t, totalForType], i) => {
		const matrix = typeMatrix[t] || {};
		const statCells = JC_STATUSES.map((s) => {
			const n  = matrix[s] || 0;
			const st = JC_STATUS_STYLE[s];
			return `<td style="text-align:center;padding:10px 6px;border-right:1px solid #ebebeb;min-width:80px;">
				<div style="font-size:18px;font-weight:800;color:${n > 0 ? st.border : '#bbb'};">${n > 0 ? n : "–"}</div>
				<div style="font-size:10px;color:#666;margin-top:3px;line-height:1.3;">${s}</div>
			</td>`;
		}).join("");
		return `
		<div style="margin-bottom:10px;border:1px solid #d4d4d4;border-radius:3px;overflow:hidden;">
			<div style="background:#f4f4f4;border-bottom:1px solid #d4d4d4;
			            padding:7px 14px;display:flex;justify-content:space-between;align-items:center;">
				<span style="font-size:13px;font-weight:700;color:#1a1a1a;">${t}</span>
				<span style="font-size:11px;color:#555;font-weight:600;background:#e0e0e0;
				             padding:2px 10px;border-radius:10px;">Total: ${totalForType}</span>
			</div>
			<div style="overflow-x:auto;">
				<table style="width:100%;border-collapse:collapse;background:#fff;">
					<tbody><tr>${statCells}</tr></tbody>
				</table>
			</div>
		</div>`;
	}).join("");

	const html = `
	<style>
		.jc-dash { font-family: -apple-system, 'Segoe UI', Arial, sans-serif; color: #1a1a1a; }
		.jc-dash .sec-title {
			font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;
			color:#1a3f5c;border-bottom:2px solid #2c5f8a;padding-bottom:5px;margin:24px 0 10px;
		}
		.jc-dash table.data-table { width:100%;border-collapse:collapse;font-size:13px; }
		.jc-dash table.data-table thead th {
			background:#2c5f8a;color:#fff;padding:8px 12px;text-align:left;font-weight:600;font-size:12px;
		}
		.jc-dash table.data-table tfoot td {
			padding:8px 12px;font-weight:700;background:#eef2f8;border-top:2px solid #2c5f8a;font-size:13px;
		}
	</style>
	<div class="jc-dash">

		<div style="display:flex;justify-content:space-between;align-items:center;
		            border-bottom:2px solid #2c5f8a;padding-bottom:12px;margin-bottom:20px;">
			<div>
				<div style="font-size:16px;font-weight:700;color:#1a3f5c;">JOBCARDS SUMMARY REPORT</div>
				<div style="font-size:11px;color:#777;margin-top:3px;">Period: ${from_date} &nbsp;–&nbsp; ${to_date}</div>
			</div>
			<div style="font-size:13px;font-weight:700;color:#2c5f8a;">
				Total Jobcards: <span style="font-size:18px;">${total}</span>
			</div>
		</div>

		<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px;">
			${kpiBoxes}
		</div>

		<div class="sec-title">Description by Status</div>
		<table class="data-table">
			<thead><tr>
				<th style="width:50px;">S/No</th>
				<th>Description by Status</th>
				<th style="text-align:center;width:80px;">Number</th>
				<th style="text-align:center;width:100px;">% from Total JC</th>
				<th style="width:160px;"></th>
			</tr></thead>
			<tbody>${statusRows}</tbody>
			<tfoot><tr>
				<td colspan="2">Cumulative Value (Total Jobcards)</td>
				<td style="text-align:center;">${total}</td>
				<td style="text-align:center;">100%</td>
				<td></td>
			</tr></tfoot>
		</table>

		<--
		<div class="sec-title">Jobcard by Issue Type</div>
		<table class="data-table">
			<thead><tr>
				<th style="width:50px;">S/No</th>
				<th>Issue Type</th>
				<th style="text-align:center;width:100px;">Jobcards Assigned</th>
				<th style="text-align:center;width:120px;">% of Total JC</th>
				<th style="width:200px;"></th>
			</tr></thead>
			<tbody>${typeRows}</tbody>
			<tfoot><tr>
				<td colspan="2">Total</td>
				<td style="text-align:center;">${total}</td>
				<td style="text-align:center;">100.00%</td>
				<td></td>
			</tr></tfoot>
		</table> -->

		<div class="sec-title">Issue Type Breakdown by Status</div>
		<p style="font-size:11px;color:#888;margin:-6px 0 12px;">Each card shows count per status across all 8 stages</p>
		${typeCards}

	</div>`;

	const d = new frappe.ui.Dialog({
		title: __("Jobcards Summary Dashboard"),
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "dashboard_html" }],
		primary_action_label: __("🖨 Print / Export"),
		primary_action() { print_summary_report(); },
		secondary_action_label: __("Close"),
		secondary_action() { d.hide(); },
	});

	d.$wrapper.find(".modal-dialog").css({ "max-width": "92vw", "width": "92vw" });
	d.$wrapper.find(".modal-body").css({ "max-height": "80vh", "overflow-y": "auto", "padding": "20px 24px" });

	d.fields_dict.dashboard_html.$wrapper.html(html);
	d.show();
}


// ─── Print Format (A4, opens in new window) ───────────────────────────────────
function print_summary_report() {
	const { statusCount, typeCount, typeMatrix, total } = build_aggregate();
	if (!total) { frappe.msgprint(__("No data to print.")); return; }

	const filters   = frappe.query_report.get_filter_values();
	const from_date = filters.from_date || "—";
	const to_date   = filters.to_date   || "—";
	const today     = frappe.datetime.nowdate();

	const statusRows = JC_STATUSES.map((s, i) => {
		const n   = statusCount[s] || 0;
		const pct = total ? ((n / total) * 100).toFixed(1) : "0.0";
		return `<tr style="background:${i%2===0?'#fff':'#f8f8f8'};">
			<td>${i+1}</td><td>${s}</td>
			<td style="text-align:center;">${n}</td>
			<td style="text-align:center;">${pct}%</td>
		</tr>`;
	}).join("");

	const sortedTypes = Object.entries(typeCount).sort((a, b) => b[1] - a[1]);
	const typeRows = sortedTypes.map(([t, n], i) => {
		const pct = total ? ((n / total) * 100).toFixed(2) : "0.00";
		return `<tr style="background:${i%2===0?'#fff':'#f8f8f8'};">
			<td>${i+1}</td><td>${t}</td>
			<td style="text-align:center;">${n}</td>
			<td style="text-align:center;">${pct}%</td>
		</tr>`;
	}).join("");

	const breakdownRows = sortedTypes.map(([t, totalForType], i) => {
		const matrix = typeMatrix[t] || {};
		const cells  = JC_STATUSES.map((s) => {
			const n = matrix[s] || 0;
			return `<td style="text-align:center;">${n > 0 ? `<strong>${n}</strong>` : "–"}</td>`;
		}).join("");
		return `<tr style="background:${i%2===0?'#fff':'#f8f8f8'};">
			<td>${i+1}</td><td><strong>${t}</strong></td>${cells}
			<td style="text-align:center;font-weight:700;">${totalForType}</td>
		</tr>`;
	}).join("");

	const statusHeaderCells = JC_STATUSES.map(
		(s) => `<th style="font-size:9px;padding:5px 3px;text-align:center;">${s}</th>`
	).join("");

	const kpiPrint = JC_STATUSES.map((s) => `
		<div class="kpi-box">
			<div class="kpi-val">${statusCount[s] || 0}</div>
			<div class="kpi-lbl">${s}</div>
		</div>`).join("");

	const html = `<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<title>Jobcards Summary Report – ${to_date}</title>
<style>
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:Arial,sans-serif; font-size:11px; color:#1a1a1a; background:#fff; }
  .page { max-width:1100px; margin:0 auto; padding:28px 32px; }
  .rpt-header { text-align:center; border-bottom:2.5px solid #1a3f5c; padding-bottom:12px; margin-bottom:16px; }
  .rpt-header h1 { font-size:17px; font-weight:700; letter-spacing:1px; color:#1a3f5c; }
  .rpt-header .sub { font-size:11px; color:#555; margin-top:4px; }
  .rpt-meta { display:flex; justify-content:space-between; font-size:10px; color:#555;
              margin-bottom:16px; padding-bottom:8px; border-bottom:1px solid #ddd; }
  .kpi-row { display:flex; gap:0; margin-bottom:20px; border:1px solid #bbb; border-radius:2px; overflow:hidden; }
  .kpi-box { flex:1; text-align:center; padding:10px 6px; border-right:1px solid #bbb; }
  .kpi-box:last-child { border-right:none; }
  .kpi-val { font-size:18px; font-weight:800; color:#1a3f5c; }
  .kpi-lbl { font-size:8px; color:#555; margin-top:2px; text-transform:uppercase; letter-spacing:.3px; }
  .sec-title { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.5px;
               color:#fff; background:#1a3f5c; padding:5px 10px; margin:18px 0 0; }
  table.rpt { width:100%; border-collapse:collapse; font-size:11px; }
  table.rpt thead th { background:#2c5f8a; color:#fff; padding:6px 9px; text-align:left; font-weight:600; }
  table.rpt tbody td { padding:5px 9px; border-bottom:1px solid #e8e8e8; }
  table.rpt tfoot td { padding:6px 9px; font-weight:700; background:#eef2f8; border-top:2px solid #1a3f5c; }
  .footer { margin-top:24px; border-top:1px solid #ccc; padding-top:8px;
            font-size:9px; color:#888; display:flex; justify-content:space-between; }
  @media print {
    body { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  }
</style>
</head><body>
<div class="page">
  <div class="rpt-header">
    <h1>JOBCARDS SUMMARY REPORT</h1>
    <div class="sub">As on ${to_date}</div>
  </div>
  <div class="rpt-meta">
    <span><strong>Report Period:</strong> ${from_date} – ${to_date}</span>
    <span><strong>Generated:</strong> ${today}</span>
    <span><strong>Total Jobcards:</strong> ${total}</span>
  </div>
  <div class="kpi-row">
    <div class="kpi-box"><div class="kpi-val">${total}</div><div class="kpi-lbl">Total</div></div>
    ${kpiPrint}
  </div>
  <div class="sec-title">Description by Status</div>
  <table class="rpt">
    <thead><tr>
      <th style="width:45px;">S/No</th><th>Description by Status</th>
      <th style="text-align:center;width:85px;">Number</th>
      <th style="text-align:center;width:110px;">% from Total JC</th>
    </tr></thead>
    <tbody>${statusRows}</tbody>
    <tfoot><tr>
      <td colspan="2">Cumulative Value (Total Jobcards)</td>
      <td style="text-align:center;">${total}</td>
      <td style="text-align:center;">100%</td>
    </tr></tfoot>
  </table>
  <!-- <div class="sec-title">Jobcard by Issue Type</div>
  <table class="rpt">
    <thead><tr>
      <th style="width:45px;">S/No</th><th>Issue Type</th>
      <th style="text-align:center;width:110px;">Jobcards Assigned</th>
      <th style="text-align:center;width:120px;">% of Total JC</th>
    </tr></thead>
    <tbody>${typeRows}</tbody>
    <tfoot><tr>
      <td colspan="2">Total</td>
      <td style="text-align:center;">${total}</td>
      <td style="text-align:center;">100.00%</td>
    </tr></tfoot>
  </table>
  -->
  <div class="sec-title">Issue Type Breakdown by Status</div>
  <table class="rpt" style="font-size:10px;">
    <thead><tr>
      <th style="width:35px;">S/No</th>
      <th style="min-width:110px;">Issue Type</th>
      ${statusHeaderCells}
      <th style="text-align:center;font-size:9px;padding:5px 4px;">TOTAL</th>
    </tr></thead>
    <tbody>${breakdownRows}</tbody>
    <tfoot><tr>
      <td colspan="2" style="padding-left:9px;">Grand Total</td>
      ${JC_STATUSES.map((s) => `<td style="text-align:center;">${statusCount[s] || 0}</td>`).join("")}
      <td style="text-align:center;">${total}</td>
    </tr></tfoot>
  </table>
  <div class="footer">
    <span>Jobcards Summary Report</span>
    <span>Period: ${from_date} – ${to_date} &nbsp;|&nbsp; Printed: ${today}</span>
  </div>
</div>
</body></html>`;

	const w = window.open("", "_blank");
	w.document.write(html);
	w.document.close();
}