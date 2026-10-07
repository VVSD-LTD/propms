"""Excel and PDF downloads for the VIVA reports.

Each workbook keeps the same sheet split as the requested Excel files, and every
sheet that had a chart gets that chart again, with the count and percentage summary.
"""

import math
from collections import Counter
from html import escape
from io import BytesIO

import frappe
from frappe.utils.pdf import get_pdf
from openpyxl import Workbook
from openpyxl.chart import BarChart, PieChart, Reference
from openpyxl.chart.data_source import AxDataSource, StrRef
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.series import DataPoint
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from propms.property_management_solution.report.viva_apartment_list.viva_apartment_list import (
	COLUMN_FILTERS as APARTMENT_FILTERS,
	apartment_records,
)
from propms.property_management_solution.report.viva_commercial_areas.viva_commercial_areas import (
	COLUMN_FILTERS as AREA_FILTERS,
	area_records,
)
from propms.property_management_solution.report.viva_commercial_occupancy.viva_commercial_occupancy import (
	COLUMN_FILTERS as COMMERCIAL_FILTERS,
	commercial_records,
)
from propms.property_management_solution.report.viva_report_data import (
	COMMERCIAL_GROUPS,
	COMMERCIAL_STATUSES,
	keep_row,
	number_rows,
	status_shares,
)
from propms.property_management_solution.report.viva_residential_occupancy.viva_residential_occupancy import (
	ALL_STATUSES,
	COLUMN_FILTERS as RESIDENTIAL_FILTERS,
	PORTFOLIO_STATUSES,
	residential_records,
)
from propms.property_management_solution.report.viva_tenant_contacts.viva_tenant_contacts import (
	COMMERCIAL_FILTERS,
	RESIDENTIAL_FILTERS as CONTACT_RESIDENTIAL_FILTERS,
	contact_records,
)

PIE_COLORS = ["2490EF", "29CD42", "F5A623", "E24C4C", "8E5CF6", "13C2C2", "F07C00", "6C757D", "D63384"]
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True)
THIN = Border(
	left=Side(style="thin", color="D0D7DE"),
	right=Side(style="thin", color="D0D7DE"),
	top=Side(style="thin", color="D0D7DE"),
	bottom=Side(style="thin", color="D0D7DE"),
)

COMMERCIAL_HEADERS = [
	("sn", "S/N"),
	("property_group", "Type"),
	("property", "Property"),
	("unit_owner", "Property Owner"),
	("occupancy_status", "Status"),
	("lease_customer", "Lease Customer"),
	("lease_name", "Lease Name"),
	("lease_start_date", "Lease Start Date"),
	("lease_end_date", "Lease End Date"),
]
RESIDENTIAL_HEADERS = [
	("sn", "S/N"),
	("property", "Property"),
	("unit_owner", "Property Owner"),
	("bedroom", "BHK"),
	("portfolio", "Portfolio"),
	("occupancy_status", "Status"),
	("lease_customer", "Lease Customer"),
	("lease_name", "Lease Name"),
	("lease_start_date", "Lease Start Date"),
	("lease_end_date", "Lease End Date"),
]
AREA_HEADERS = [
	("sn", "S/N"),
	("floor", "Floor"),
	("property", "Property"),
	("builtup_area", "Built Up"),
	("carpet_area", "Carpet"),
	("occupancy_status", "Status"),
	("lease_customer", "Lease Customer"),
	("lease_name", "Lease Name"),
	("lease_start_date", "Lease Start Date"),
	("lease_end_date", "Lease End Date"),
]
CONTACT_COMMERCIAL_HEADERS = [
	("sn", "S/N"),
	("property_group", "Type"),
	("property", "Property"),
	("tenant_label", "Tenant"),
	("email", "Email"),
	("phone", "Phone Number"),
	("lease_name", "Lease Name"),
	("lease_start_date", "Lease Start Date"),
	("lease_end_date", "Lease End Date"),
]
CONTACT_RESIDENTIAL_HEADERS = [
	("sn", "S/N"),
	("property", "Property"),
	("unit_owner", "Property Owner"),
	("bedroom", "BHK"),
	("tenant_label", "Tenant Name"),
	("lease_name", "Lease Name"),
	("lease_start_date", "Lease Start Date"),
	("lease_end_date", "Lease End Date"),
	("owner_email", "Email (Owner)"),
	("email", "Email"),
	("phone", "Phone Number"),
]
APARTMENT_HEADERS = [
	("property", "Property"),
	("bedroom", "BHK"),
	("unit_owner", "Property Owner"),
	("lessee", "Lessee"),
	("occupancy_status", "Status"),
	("portfolio", "Portfolio"),
	("lease_name", "Lease Name"),
	("lease_start_date", "Lease Start Date"),
	("lease_end_date", "Lease End Date"),
]


@frappe.whitelist()
def download_viva_report(report_name, file_format, filters=None):
	report = frappe.get_doc("Report", report_name)
	if not report.is_permitted():
		frappe.throw(frappe._("Not permitted"), frappe.PermissionError)
	if isinstance(filters, str) and filters:
		filters = frappe.parse_json(filters)
	filters = filters or {}
	file_format = (file_format or "xlsx").lower()
	if file_format == "pdf":
		content = get_pdf(_html_for(report_name, filters), {"orientation": "Landscape", "page-size": "A4"})
		extension = "pdf"
	else:
		content = _xlsx_for(report_name, filters)
		extension = "xlsx"
	frappe.local.response.filename = f"{report_name}.{extension}"
	frappe.local.response.filecontent = content
	frappe.local.response.type = "download"


def _xlsx_for(report_name, filters):
	workbook = Workbook()
	workbook.remove(workbook.active)
	for sheet in _sheets(report_name, filters):
		_write_sheet(workbook, sheet)
	if not workbook.sheetnames:
		workbook.create_sheet("Report")
	output = BytesIO()
	workbook.save(output)
	return output.getvalue()


def _html_for(report_name, filters):
	parts = [
		"<html><head><meta charset='utf-8'><style>",
		"body{font-family:Arial,sans-serif;font-size:12px;color:#1f2933}",
		"h1{font-size:18px;margin:0 0 8px}h2{font-size:14px;margin:16px 0 8px}",
		"table.data{border-collapse:collapse;width:100%;margin:0;border:1px solid #1f2933;table-layout:fixed}",
		"table.data th,table.data td{border:1px solid #1f2933;padding:3px 6px;vertical-align:top;font-size:13px}",
		"table.data th{background:#1f4e79;color:#fff;text-align:left;font-size:14px;font-weight:bold}",
		"table.data col.sn{width:14mm}",
		"table.data tr,table.data td,table.data th{page-break-inside:avoid}",
		"table.data.continued{page-break-before:always}",
		"table.data.bar-fit th,table.data.bar-fit td{font-size:16px;padding:8px 10px}",
		"table.data.bar-fit th{font-size:17px}",
		".chart{margin:4px 0 8px}",
		"table.summary{border-collapse:collapse;margin:8px 0 12px;border:1px solid #1f2933}",
		"table.summary th,table.summary td{border:1px solid #1f2933;padding:4px 8px;font-size:13px}",
		"table.summary th{background:#1f4e79;color:#fff}",
		".legend div{margin:3px 0}.swatch{display:inline-block;width:12px;height:12px;margin-right:8px}",
		".legend.pie div{font-size:16px}",
		".legend.bar div{font-size:18px}",
		"table.legend-grid{border-collapse:collapse;width:100%;margin:0}",
		"table.legend-grid td{border:none;padding:0 16px 0 0;vertical-align:top;width:50%}",
		"</style></head><body>",
	]
	sheets = _sheets(report_name, filters)
	for index, sheet in enumerate(sheets):
		page_break = "page-break-after:always" if index < len(sheets) - 1 else ""
		parts.append(f"<div class='sheet' style='{page_break}'>")
		parts.append(f"<h1>{escape(sheet['title'])}</h1>")
		chart = sheet.get("chart") or {}
		if sheet.get("summary"):
			parts.append(_html_summary(sheet["summary"]))
		if chart:
			if sheet.get("chart_title"):
				parts.append(f"<h2>{escape(sheet['chart_title'])}</h2>")
			parts.append(f"<div class='chart'>{_chart_svg(chart, large=chart.get('type') == 'pie')}</div>")
		parts.append(
			_html_table(
				sheet["headers"],
				sheet["rows"],
				bool(chart),
				bar_fit=chart.get("type") == "bar",
				chart_type=chart.get("type"),
			)
		)
		parts.append("</div>")
	parts.append("</body></html>")
	return "".join(parts)


def _sheets(report_name, filters):
	if report_name == "VIVA Commercial Occupancy":
		return _commercial_sheets(filters)
	if report_name == "VIVA Residential Occupancy":
		return _residential_sheets(filters)
	if report_name == "VIVA Commercial Areas":
		return _area_sheets(filters)
	if report_name == "VIVA Tenant Contacts":
		return _contact_sheets(filters)
	if report_name == "VIVA Apartment List":
		return _apartment_sheets(filters)
	frappe.throw(frappe._("This report has no formatted download."))


def _commercial_sheets(filters):
	rows = number_rows([row for row in commercial_records() if keep_row(row, filters, COMMERCIAL_FILTERS)])
	detail = _status_sheet(
		"COMMERCIAL",
		COMMERCIAL_HEADERS,
		rows,
		"occupancy_status",
		COMMERCIAL_STATUSES,
		"COMMERCIAL RENTED STATUS",
	)
	return [detail, _commercial_bar_sheet(rows)]


def _commercial_bar_sheet(rows):
	matrix = []
	for group in COMMERCIAL_GROUPS:
		matrix.append(
			{
				"floor": group,
				**{
					status: sum(
						1 for row in rows if row["property_group"] == group and row["occupancy_status"] == status
					)
					for status in COMMERCIAL_STATUSES
				},
			}
		)
	headers = [("floor", "Floor")] + [(status, status) for status in COMMERCIAL_STATUSES]
	return {
		"title": "COMMERCIAL SPACE",
		"headers": headers,
		"rows": matrix,
		"chart_title": "COMMERCIAL SPACE CHART",
		"chart": {
			"type": "bar",
			"title": "COMMERCIAL SPACE CHART",
			"categories": list(COMMERCIAL_GROUPS),
			"series": [
				{"name": status, "values": [row[status] for row in matrix]} for status in COMMERCIAL_STATUSES
			],
		},
		"summary": [],
	}


def _residential_sheets(filters):
	rows = [row for row in residential_records() if keep_row(row, filters, RESIDENTIAL_FILTERS)]
	names = (
		("VIRGIN", "Virgin Plaza"),
		("VASTA", "Vasta"),
		("REDCROSS", "RedCross"),
		("INVESTOR", "Investor"),
	)
	sheets = []
	for title, portfolio in names:
		subset = number_rows([row for row in rows if row["portfolio"] == portfolio])
		sheets.append(
			_status_sheet(
				title,
				RESIDENTIAL_HEADERS,
				subset,
				"occupancy_status",
				PORTFOLIO_STATUSES[portfolio],
				f"RESIDENTIAL STATUS - {title}",
			)
		)
	sheets.append(
		_status_sheet(
			"SUMMARY",
			RESIDENTIAL_HEADERS,
			number_rows(list(rows)),
			"occupancy_status",
			ALL_STATUSES,
			"RESIDENTIAL STATUS FOR ENTIRE BUILDING",
		)
	)
	return sheets


def _area_sheets(filters):
	rows = [row for row in area_records() if keep_row(row, filters, AREA_FILTERS)]
	sheets = []
	floor_names = {
		"Warehouse": "BASEMENT",
		"Ground Floor": "GROUND FLOOR",
		"First Floor": "FIRST FLOOR",
		"Second Floor": "SECOND FLOOR",
		"Other": "OTHER",
	}
	for group in COMMERCIAL_GROUPS:
		subset = number_rows([row for row in rows if row["floor"] == group])
		sheets.append({"title": floor_names[group], "headers": AREA_HEADERS, "rows": subset})
	sheets.append(
		{
			"title": "EMPTY OFFICES",
			"headers": AREA_HEADERS,
			"rows": number_rows([row for row in rows if row["occupancy_status"] == "Empty"]),
		}
	)
	built = [
		round(sum((row["builtup_area"] or 0) for row in rows if row["floor"] == group), 2)
		for group in COMMERCIAL_GROUPS
	]
	sheets.insert(
		0,
		{
			"title": "AREA CHART",
			"headers": [("floor", "Floor"), ("builtup_area", "Built Up")],
			"rows": [
				{"floor": group, "builtup_area": value} for group, value in zip(COMMERCIAL_GROUPS, built)
			],
			"chart_title": "BUILT UP AREA BY FLOOR",
			"chart": {
				"type": "bar",
				"title": "BUILT UP AREA BY FLOOR",
				"categories": list(COMMERCIAL_GROUPS),
				"series": [{"name": "Built Up", "values": built}],
			},
			"summary": [],
		},
	)
	return sheets


def _contact_sheets(filters):
	sheets = []
	commercial = number_rows(
		[row for row in contact_records("Commercial") if keep_row(row, filters, COMMERCIAL_FILTERS)]
	)
	sheets.append({"title": "COMMERCIAL", "headers": CONTACT_COMMERCIAL_HEADERS, "rows": commercial})
	for title, portfolio in (
		("VIRGIN PLAZA", "Virgin Plaza"),
		("VASTA", "Vasta"),
		("REDCROSS", "RedCross"),
		("INVESTOR", "Investor"),
	):
		rows = number_rows(
			[
				row
				for row in contact_records(portfolio)
				if keep_row(row, filters, CONTACT_RESIDENTIAL_FILTERS)
			]
		)
		headers = CONTACT_RESIDENTIAL_HEADERS
		if portfolio != "Investor":
			headers = [item for item in headers if item[0] != "owner_email"]
		sheets.append({"title": title, "headers": headers, "rows": rows})
	return sheets


def _apartment_sheets(filters):
	rows = [row for row in apartment_records() if keep_row(row, filters, APARTMENT_FILTERS)]
	sheets = []
	for title, tower in (("TOWER A", "Tower A"), ("TOWER B", "Tower B")):
		sheets.append(
			{
				"title": title,
				"headers": APARTMENT_HEADERS,
				"rows": [row for row in rows if row["tower"] == tower],
			}
		)
	for title, portfolio in (
		("VIRGIN", "Virgin Plaza"),
		("VASTA", "Vasta"),
		("REDCROSS", "RedCross"),
		("INVESTOR", "Investor"),
	):
		sheets.append(
			{
				"title": title,
				"headers": APARTMENT_HEADERS,
				"rows": [row for row in rows if row["portfolio"] == portfolio],
			}
		)
	return sheets


def _status_sheet(title, headers, rows, status_key, status_order, chart_title):
	counts = Counter(row.get(status_key) for row in rows)
	_total, shares = status_shares(counts, status_order)
	summary = [{"status": label, "count": count, "share": share} for label, count, share in shares]
	chart_labels = [item["status"] for item in summary if item["count"]]
	chart_values = [item["count"] for item in summary if item["count"]]
	return {
		"title": title,
		"headers": headers,
		"rows": rows,
		"chart_title": chart_title,
		"summary": summary,
		"chart": {
			"type": "pie",
			"title": chart_title,
			"categories": chart_labels,
			"series": [{"name": "Properties", "values": chart_values}],
		},
	}


def _write_sheet(workbook, sheet):
	worksheet = workbook.create_sheet(_safe_title(sheet["title"]))
	headers = sheet["headers"]
	_write_headers(worksheet, [label for _key, label in headers])
	for row_index, row in enumerate(sheet["rows"], start=2):
		for col_index, (key, _label) in enumerate(headers, start=1):
			cell = worksheet.cell(row_index, col_index, _excel_value(row.get(key)))
			cell.border = THIN
			cell.alignment = Alignment(vertical="center")
			if key.endswith("_date") and row.get(key):
				cell.number_format = "DD-MM-YYYY"
			if key in ("builtup_area", "carpet_area"):
				cell.number_format = "#,##0.00"
		worksheet.row_dimensions[row_index].height = 18
	for col_index, (_key, label) in enumerate(headers, start=1):
		worksheet.column_dimensions[_column_letter(col_index)].width = max(14, min(len(label) + 6, 32))
	worksheet.auto_filter.ref = f"A1:{_column_letter(len(headers))}{max(len(sheet['rows']) + 1, 1)}"
	worksheet.freeze_panes = "A2"
	worksheet.page_setup.orientation = "landscape"
	worksheet.page_setup.fitToPage = True
	worksheet.page_setup.fitToWidth = 1
	worksheet.page_setup.fitToHeight = 0
	worksheet.sheet_properties.pageSetUpPr.fitToPage = True
	worksheet.oddHeader.left.text = sheet["title"]
	worksheet.oddFooter.center.text = "Page &P of &N"
	if sheet.get("summary"):
		_write_summary_and_pie(worksheet, len(headers) + 2, sheet)
	elif sheet.get("chart") and sheet["chart"]["type"] == "bar":
		_write_bar(worksheet, sheet["chart"], len(headers))


def _write_headers(worksheet, labels):
	for col_index, label in enumerate(labels, start=1):
		cell = worksheet.cell(1, col_index, label)
		cell.fill = HEADER_FILL
		cell.font = HEADER_FONT
		cell.alignment = Alignment(horizontal="center")
		cell.border = THIN
	worksheet.row_dimensions[1].height = 22
	worksheet.auto_filter.ref = "A1"
	worksheet.page_setup.orientation = "landscape"
	worksheet.sheet_view.showGridLines = False
	worksheet.oddHeader.center.text = ""
	worksheet.print_title_rows = "1:1"
	worksheet.page_setup.paperSize = worksheet.PAPERSIZE_A4
	worksheet.sheet_properties.tabColor = "1F4E79"


def _write_summary_and_pie(worksheet, start_col, sheet):
	summary = sheet.get("summary") or []
	headers = ("Status", "Count")
	for offset, label in enumerate(headers):
		cell = worksheet.cell(1, start_col + offset, label)
		cell.fill = HEADER_FILL
		cell.font = HEADER_FONT
		cell.border = THIN
	count_letter = _column_letter(start_col + 1)
	first_row = 2
	last_row = 1 + len(summary)
	for index, item in enumerate(summary, start=first_row):
		status_cell = worksheet.cell(index, start_col, item["status"])
		status_cell.border = THIN
		count_cell = worksheet.cell(index, start_col + 1, item["count"])
		count_cell.border = THIN
	total_row = last_row + 1
	total_label = worksheet.cell(total_row, start_col, "Total")
	total_label.font = Font(bold=True)
	total_label.border = THIN
	total_count = worksheet.cell(total_row, start_col + 1, f"=SUM({count_letter}{first_row}:{count_letter}{last_row})")
	total_count.font = Font(bold=True)
	total_count.border = THIN
	status_width = max(18, max((len(str(item["status"])) for item in summary), default=12) + 2)
	worksheet.column_dimensions[_column_letter(start_col)].width = status_width
	worksheet.column_dimensions[count_letter].width = 12
	if not summary or not any(item["count"] for item in summary):
		return
	chart = sheet.get("chart") or {}
	pie = PieChart()
	pie.title = chart.get("title")
	# The pie reads the Status and Count cells of this table, so editing a count updates the chart.
	data = Reference(worksheet, min_col=start_col + 1, min_row=1, max_row=last_row)
	labels = Reference(worksheet, min_col=start_col, min_row=first_row, max_row=last_row)
	pie.add_data(data, titles_from_data=True)
	pie.set_categories(labels)
	pie.series[0].cat = AxDataSource(strRef=StrRef(f=str(labels)))
	pie.dataLabels = DataLabelList()
	pie.dataLabels.showPercent = True
	pie.dataLabels.showCatName = False
	pie.dataLabels.showVal = False
	pie.dataLabels.showSerName = False
	pie.width = 15
	pie.height = 8
	_color_points(pie.series[0], len(summary))
	worksheet.add_chart(pie, f"{_column_letter(start_col)}{total_row + 2}")


def _write_bar(worksheet, chart, header_count):
	if not chart.get("series") or not any(sum(series["values"]) for series in chart["series"]):
		return
	bar = BarChart()
	bar.type = "col"
	bar.grouping = "clustered"
	bar.title = chart.get("title")
	bar.y_axis.title = None
	bar.x_axis.title = None
	data = Reference(worksheet, min_col=2, max_col=1 + len(chart["series"]), min_row=1, max_row=1 + len(chart["categories"]))
	cats = Reference(worksheet, min_col=1, min_row=2, max_row=1 + len(chart["categories"]))
	bar.add_data(data, titles_from_data=True)
	bar.set_categories(cats)
	# Floor names are text. A numeric category axis leaves the x-axis blank.
	for series in bar.series:
		series.cat = AxDataSource(strRef=StrRef(f=str(cats)))
	bar.shape = 4
	bar.width = 22
	bar.height = 12
	bar.x_axis.delete = False
	bar.y_axis.delete = False
	bar.x_axis.tickLblPos = "low"
	bar.y_axis.tickLblPos = "nextTo"
	peak = max(max(series["values"] or [0]) for series in chart["series"])
	bar.y_axis.scaling.min = 0
	bar.y_axis.scaling.max = int(peak) + max(4, int(peak * 0.25))
	bar.dataLabels = DataLabelList()
	bar.dataLabels.showVal = True
	bar.dataLabels.showCatName = False
	bar.dataLabels.showSerName = False
	bar.dataLabels.dLblPos = "outEnd"
	for index, series in enumerate(bar.series):
		color = PIE_COLORS[index % len(PIE_COLORS)]
		series.graphicalProperties.solidFill = color
	if getattr(bar, "title", None) is not None:
		bar.title.overlay = False
	worksheet.add_chart(bar, f"{_column_letter(header_count + 2)}2")


def _color_points(series, count):
	points = []
	for index in range(count):
		point = DataPoint(idx=index)
		point.graphicalProperties = GraphicalProperties(solidFill=PIE_COLORS[index % len(PIE_COLORS)])
		points.append(point)
	series.data_points = points


_COL_CHARS = {
	"sn": 6,
	"bedroom": 6,
	"lease_start_date": 12,
	"lease_end_date": 12,
	"portfolio": 14,
	"property_group": 14,
	"floor": 14,
	"occupancy_status": 18,
	"property": 20,
	"unit_owner": 22,
	"lease_customer": 20,
	"lease_name": 20,
	"tenant_label": 18,
	"email": 22,
	"phone": 14,
	"owner_email": 22,
	"lessee": 18,
}


def _row_weight(row, headers):
	# Ten columns are narrower, so the same text wraps a line sooner.
	factor = 0.85 if len(headers) >= 10 else 1
	weight = 1
	for key, _label in headers:
		text = _text(row.get(key))
		width = max(8, int(_COL_CHARS.get(key, 16) * factor))
		if text:
			weight = max(weight, (len(text) + width - 1) // width)
	return weight


def _table_chunks(rows, headers):
	# Budget is the wrapped-line total that fills a landscape page and still
	# keeps the last row on that page. Narrower sheets wrap sooner.
	if len(headers) >= 11:
		budget = 31
	elif len(headers) >= 10:
		budget = 34
	elif headers and headers[0][0] != "sn":
		budget = 32
	else:
		budget = 36
	chunks = []
	weights = []
	current = []
	used = 0
	for row in rows:
		weight = _row_weight(row, headers)
		if current and used + weight > budget:
			chunks.append(current)
			weights.append(used)
			current = []
			used = 0
		current.append(row)
		used += weight
	if current:
		chunks.append(current)
		weights.append(used)
	# A one-row tail still fits on the previous page when that page is not already full.
	if (
		len(chunks) >= 2
		and len(chunks[-1]) == 1
		and weights[-2] <= budget - 1
		and weights[-2] + weights[-1] <= budget + 1
	):
		chunks[-2].extend(chunks[-1])
		chunks.pop()
	return chunks


def _html_table(headers, rows, has_chart=False, bar_fit=False, chart_type=None):
	head = "".join(f"<th>{escape(label)}</th>" for _key, label in headers)
	cols = "".join(
		"<col class='sn' />" if key == "sn" else "<col />" for key, _label in headers
	)
	if not rows:
		return (
			f"<table class='data'><colgroup>{cols}</colgroup><thead><tr>{head}</tr></thead>"
			f"<tbody><tr><td colspan='{len(headers)}'>No rows</td></tr></tbody></table>"
		)
	# wkhtmltopdf does not repeat a thead, and it slices a table that runs past
	# the page. Each chunk is its own table. Wide sheets wrap, so the chunk
	# size follows the text instead of a fixed row count.
	# A pie leaves room for only a few rows, so a longer list starts on the next page.
	fits_with_chart = chart_type == "bar"
	chunks = _table_chunks(rows, headers)
	parts = []
	for index, chunk in enumerate(chunks):
		body = []
		for row in chunk:
			cells = "".join(f"<td>{escape(_text(row.get(key)))}</td>" for key, _label in headers)
			body.append(f"<tr>{cells}</tr>")
		continued = index > 0 or (has_chart and not fits_with_chart)
		css = "data"
		if bar_fit:
			css += " bar-fit"
		if continued:
			css += " continued"
		parts.append(
			f"<table class='{css}'><colgroup>{cols}</colgroup><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
		)
	return "".join(parts)


def _html_summary(summary):
	if not summary:
		return ""
	rows = []
	total = 0
	for item in summary:
		total += item["count"]
		rows.append(f"<tr><td>{escape(str(item['status']))}</td><td>{item['count']}</td></tr>")
	rows.append(f"<tr><td><b>Total</b></td><td><b>{total}</b></td></tr>")
	return (
		"<table class='summary'><thead><tr><th>Status</th><th>Count</th></tr></thead>"
		f"<tbody>{''.join(rows)}</tbody></table>"
	)


def _chart_svg(chart, large=False):
	if chart["type"] == "pie":
		return _pie_svg(chart["categories"], chart["series"][0]["values"], chart.get("title"), large=large)
	return _bar_svg(chart["categories"], chart["series"], chart.get("title"))


def _pie_svg(labels, values, title, large=False):
	total = sum(values) or 1
	visible_count = sum(1 for value in values if value > 0)
	# A pie with many slices plus its legend has to stay on the chart page.
	many = large and visible_count > 4
	size = 260 if many else (400 if large else 180)
	cx = cy = size / 2
	radius = size * 0.38
	start = -math.pi / 2
	paths = []
	legend_class = "legend pie" if large else "legend"
	legend = []
	for index, (label, value) in enumerate(zip(labels, values)):
		if value <= 0:
			continue
		color = "#" + PIE_COLORS[index % len(PIE_COLORS)]
		sweep = 2 * math.pi * value / total
		end = start + sweep
		large_arc = 1 if sweep > math.pi else 0
		x1 = cx + radius * math.cos(start)
		y1 = cy + radius * math.sin(start)
		x2 = cx + radius * math.cos(end)
		y2 = cy + radius * math.sin(end)
		if abs(sweep - 2 * math.pi) < 0.001:
			paths.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="{color}" />')
		else:
			paths.append(
				f'<path d="M {cx} {cy} L {x1:.2f} {y1:.2f} A {radius} {radius} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z" fill="{color}" />'
			)
		start = end
		share = value * 100 / total
		if share >= 8:
			mid = end - sweep / 2
			lx = cx + radius * 0.62 * math.cos(mid)
			ly = cy + radius * 0.62 * math.sin(mid)
			label_size = 16 if many else (24 if large else 10)
			paths.append(
				f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="{label_size}" fill="#fff">{share:.2f}%</text>'
			)
		legend.append(
			f'<div><span class="swatch" style="background:{color}"></span>{escape(str(label))} — {value} ({share:.2f}%)</div>'
		)
	svg = f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">{"".join(paths)}</svg>'
	heading = f"<div><b>{escape(title or '')}</b></div>"
	if many and len(legend) > 4:
		mid = (len(legend) + 1) // 2
		body = (
			"<table class='legend-grid'><tr>"
			f"<td>{''.join(legend[:mid])}</td><td>{''.join(legend[mid:])}</td>"
			"</tr></table>"
		)
	else:
		body = "".join(legend)
	return svg + f"<div class='{legend_class}'>{heading}{body}</div>"


def _bar_svg(categories, series, title):
	width = 980
	height = 340
	left = 50
	bottom = 290
	plot_width = width - left - 24
	group = plot_width / max(len(categories), 1)
	bar_width = max(16, group / (len(series) + 1))
	max_value = max([value for item in series for value in item["values"]] or [1]) or 1
	rects = []
	legend = [f"<div><b>{escape(title or '')}</b></div>"]
	for series_index, item in enumerate(series):
		color = "#" + PIE_COLORS[series_index % len(PIE_COLORS)]
		legend.append(f'<div><span class="swatch" style="background:{color}"></span>{escape(item["name"])}</div>')
		for cat_index, value in enumerate(item["values"]):
			bar_height = 0 if not max_value else (value / max_value) * 240
			x = left + cat_index * group + series_index * bar_width + 8
			y = bottom - bar_height
			rects.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width - 6:.1f}" height="{bar_height:.1f}" fill="{color}" />')
			label = f"{value:.2f}" if isinstance(value, float) and not float(value).is_integer() else str(int(value or 0))
			rects.append(
				f'<text x="{x + (bar_width - 6) / 2:.1f}" y="{max(y - 6, 16):.1f}" font-size="13" text-anchor="middle">{escape(label)}</text>'
			)
	labels = []
	for cat_index, category in enumerate(categories):
		labels.append(
			f'<text x="{left + cat_index * group + 8:.1f}" y="318" font-size="16">{escape(str(category))}</text>'
		)
	svg = (
		f'<svg width="100%" height="340" viewBox="0 0 {width} {height}">'
		f'<line x1="{left}" y1="{bottom}" x2="{width - 16}" y2="{bottom}" stroke="#94a3b8"/>'
		+ "".join(rects)
		+ "".join(labels)
		+ "</svg>"
	)
	return svg + "<div class='legend bar'>" + "".join(legend) + "</div>"


def _excel_value(value):
	if value in (None, ""):
		return None
	return value


def _text(value):
	if value in (None, ""):
		return ""
	if hasattr(value, "strftime"):
		return value.strftime("%d-%m-%Y")
	return str(value)


def _safe_title(title):
	cleaned = "".join(ch for ch in title if ch not in "[]:*?/\\")
	return (cleaned or "Sheet")[:31]


def _column_letter(index):
	letters = ""
	while index:
		index, remainder = divmod(index - 1, 26)
		letters = chr(65 + remainder) + letters
	return letters
