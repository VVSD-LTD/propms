# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import calendar
import json
import os
import re
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.desk.query_report import build_xlsx_data, run
from frappe.model.document import Document
from frappe.utils import (
	cint,
	get_datetime,
	get_time,
	getdate,
	now,
	now_datetime,
	nowdate,
	today,
	validate_email_address,
)
from frappe.utils.pdf import get_pdf
from frappe.utils.xlsxutils import make_xlsx


class AttendanceSettings(Document):
	def validate(self):
		self.validate_schedules()

	def validate_schedules(self):
		for idx, row in enumerate(self.get("scheduled_reports") or [], start=1):
			if not row.report:
				frappe.throw(_("Row #{0}: Report is mandatory.").format(idx))

			if not row.recipients:
				frappe.throw(_("Row #{0}: To (Recipients) is mandatory.").format(idx))

			# Validate emails
			recipients = parse_email_addresses(row.recipients)
			if not recipients:
				frappe.throw(_("Row #{0}: Please provide at least one valid recipient email address.").format(idx))
			row.recipients = "\n".join(recipients)

			if row.cc:
				row.cc = "\n".join(parse_email_addresses(row.cc))

			if row.bcc:
				row.bcc = "\n".join(parse_email_addresses(row.bcc))

			if row.frequency == "Weekly" and not row.day_of_week:
				frappe.throw(_("Row #{0}: Day of Week is required for Weekly schedule.").format(idx))

			if row.frequency == "Monthly":
				dom = cint(row.day_of_month)
				if dom < 1 or dom > 28:
					frappe.throw(_("Row #{0}: Day of Month must be between 1 and 28.").format(idx))

		scheduled_reports = {row.report for row in (self.get("scheduled_reports") or []) if row.report}
		for idx, row in enumerate(self.get("report_filters") or [], start=1):
			if row.report and row.report not in scheduled_reports:
				frappe.throw(_("Row #{0} in Report Filters: Report '{1}' is not configured in Scheduled Reports.").format(idx, row.report))


def parse_email_addresses(email_str):
	if not email_str:
		return []

	# Replace commas with newlines or spaces
	clean_str = email_str.replace(",", "\n")
	raw_list = clean_str.split()
	valid_emails = []

	for em in raw_list:
		em = em.strip()
		if em:
			validate_email_address(em, True)
			valid_emails.append(em)

	return valid_emails


def render_jinja_template(template_str, context):
	if not template_str:
		return ""
	try:
		return frappe.render_template(template_str, context)
	except Exception as e:
		frappe.log_error(f"Jinja template error in Attendance Settings: {str(e)}")
		return template_str


def get_report_data(report_name, filters=None):
	filters = filters or {}
	if "from_date" not in filters:
		filters["from_date"] = today()
	if "to_date" not in filters:
		filters["to_date"] = today()

	report_result = run(report_name, filters=filters)
	return report_result


def build_report_excel(report_name, report_result):
	columns = report_result.get("columns") or []
	result = report_result.get("result") or []

	# Format columns for build_xlsx_data
	col_dicts = []
	for col in columns:
		if isinstance(col, dict):
			col_dicts.append(col)
		elif isinstance(col, str):
			col_dicts.append({"fieldname": col, "label": col, "fieldtype": "Data", "width": 120})

	report_data = frappe._dict({"columns": col_dicts, "result": result})

	sheet_title = report_name[:31] if len(report_name) > 31 else report_name
	xlsx_data, column_widths = build_xlsx_data(report_data, [], 1, ignore_visible_idx=True)
	xlsx_file = make_xlsx(xlsx_data, sheet_title, column_widths=column_widths)
	return xlsx_file.getvalue()


def build_report_pdf_html(report_name, report_result, filters=None):
	columns = report_result.get("columns") or []
	result = report_result.get("result") or []
	report_summary = report_result.get("report_summary") or []

	# Format columns
	formatted_cols = []
	for col in columns:
		if isinstance(col, dict):
			formatted_cols.append(col)
		else:
			formatted_cols.append({"fieldname": str(col), "label": str(col)})

	date_str = today()

	summary_html = ""
	if report_summary:
		summary_html += "<div style='display: flex; gap: 15px; margin-bottom: 20px; flex-wrap: wrap;'>"
		for item in report_summary:
			val = item.get("value", "")
			lbl = item.get("label", "")
			color = "#2563eb"
			summary_html += f"""
			<div style='border: 1px solid #e2e8f0; background-color: #f8fafc; padding: 8px 14px; border-radius: 6px; min-width: 110px;'>
				<div style='font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600;'>{lbl}</div>
				<div style='font-size: 18px; font-weight: 700; color: {color}; margin-top: 2px;'>{val}</div>
			</div>
			"""
		summary_html += "</div>"

	# Build table header
	table_header = "<tr>"
	table_header += "<th style='border: 1px solid #cbd5e1; padding: 6px 8px; background-color: #f1f5f9; font-size: 11px; text-align: left;'>#</th>"
	for col in formatted_cols:
		lbl = col.get("label", col.get("fieldname", ""))
		table_header += f"<th style='border: 1px solid #cbd5e1; padding: 6px 8px; background-color: #f1f5f9; font-size: 11px; text-align: left;'>{lbl}</th>"
	table_header += "</tr>"

	# Build table rows
	table_rows = ""
	for idx, row in enumerate(result, start=1):
		bg = "#ffffff" if idx % 2 != 0 else "#f8fafc"
		table_rows += f"<tr style='background-color: {bg};'>"
		table_rows += f"<td style='border: 1px solid #e2e8f0; padding: 5px 8px; font-size: 10px;'>{idx}</td>"
		for col in formatted_cols:
			fieldname = col.get("fieldname", "")
			val = row.get(fieldname, "")
			if val is None:
				val = ""
			table_rows += f"<td style='border: 1px solid #e2e8f0; padding: 5px 8px; font-size: 10px;'>{val}</td>"
		table_rows += "</tr>"

	html = f"""
	<!DOCTYPE html>
	<html>
	<head>
		<meta charset='utf-8'>
		<style>
			body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #1e293b; padding: 10px; }}
			h2 {{ margin-top: 0; color: #0f172a; font-size: 18px; }}
			.meta-info {{ font-size: 11px; color: #64748b; margin-bottom: 15px; }}
			table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
		</style>
	</head>
	<body>
		<h2>{report_name}</h2>
		<div class='meta-info'>Generated on {date_str} | Date Filter: {filters.get('from_date', date_str)} to {filters.get('to_date', date_str)}</div>
		{summary_html}
		<table>
			<thead>
				{table_header}
			</thead>
			<tbody>
				{table_rows}
			</tbody>
		</table>
	</body>
	</html>
	"""
	return html


def send_single_scheduled_report(row_doc, force=False):
	if not force and not row_doc.enabled:
		return False

	now_dt = now_datetime()
	current_day = calendar.day_name[now_dt.weekday()]
	current_dom = now_dt.day

	if not force:
		# Check frequency
		if row_doc.frequency == "Weekly" and row_doc.day_of_week != current_day:
			return False
		elif row_doc.frequency == "Monthly" and row_doc.day_of_month != current_dom:
			return False

		# Check time
		send_time_obj = get_time(str(row_doc.send_time))
		current_time_obj = now_dt.time()

		# Check if last sent is today
		if row_doc.last_sent_at:
			last_sent_date = getdate(row_doc.last_sent_at)
			if last_sent_date == getdate(now_dt):
				# Already sent today
				return False

		# Compare send_time vs current_time
		# Only send if current_time has reached or passed send_time
		curr_sec = current_time_obj.hour * 3600 + current_time_obj.minute * 60 + current_time_obj.second
		send_sec = send_time_obj.hour * 3600 + send_time_obj.minute * 60 + send_time_obj.second

		if curr_sec < send_sec:
			return False

	# Fetch report data
	report_name = row_doc.report or "Employee & Sub Contractor Checking Summary"
	filters = {"from_date": today(), "to_date": today()}

	# Build Jinja context for filter value rendering
	jinja_ctx = {
		"today": today,
		"nowdate": nowdate,
		"date": today(),
		"frappe": frappe,
		"doc": row_doc,
	}

	# Read per-report filters from the separate Report Filters child table on parent doc
	parent_doc = frappe.get_single("Attendance Settings")
	report_fields = get_report_filter_fields(report_name)
	field_map = {}
	for f in report_fields:
		if f.get("label"):
			field_map[f["label"].strip()] = f["fieldname"]
		if f.get("fieldname"):
			field_map[f["fieldname"].strip()] = f["fieldname"]

	for f_item in parent_doc.get("report_filters") or []:
		if f_item.report == report_name and f_item.fieldname:
			raw_val = f_item.value or ""
			rendered_val = render_jinja_template(raw_val, jinja_ctx)
			raw_fn = str(f_item.fieldname).strip()
			target_field = (getattr(f_item, "raw_fieldname", None) or field_map.get(raw_fn, raw_fn)).strip()
			filters[target_field] = rendered_val

	report_result = get_report_data(report_name, filters)

	# Check if 'Don't Send If No Data' is enabled and report result is empty
	result_data = report_result.get("result") or []
	if row_doc.get("dont_send_if_no_data") and not result_data:
		frappe.logger().info(f"Skipping scheduled report '{report_name}': No data found and 'Don't Send If No Data' is enabled.")
		frappe.db.set_value("Attendance Report Schedule", row_doc.name, "last_sent_at", now())
		return False

	# Format content
	file_ext = "pdf" if row_doc.format == "PDF" else "xlsx"
	clean_report_name = report_name.replace(" ", "_").replace("&", "and")
	fname = f"{clean_report_name}_{today()}.{file_ext}"

	if row_doc.format == "PDF":
		html_content = build_report_pdf_html(report_name, report_result, filters)
		attachment_content = get_pdf(html_content)
	else:
		attachment_content = build_report_excel(report_name, report_result)

	attachments = [{"fname": fname, "fcontent": attachment_content}]

	# Prepare Jinja Context
	context = {
		"today": today,
		"nowdate": nowdate,
		"date": today(),
		"report_name": report_name,
		"filters": filters,
		"summary": report_result.get("report_summary") or [],
		"frappe": frappe,
		"doc": row_doc,
	}

	default_subject = f"{report_name} - {today()}"
	default_message = f"<p>Hello,</p><p>Please find attached the scheduled report: <strong>{report_name}</strong> for {today()}.</p>"

	subject = render_jinja_template(row_doc.subject, context) if row_doc.subject else default_subject
	message = render_jinja_template(row_doc.message, context) if row_doc.message else default_message

	recipients = parse_email_addresses(row_doc.recipients)
	cc = parse_email_addresses(row_doc.cc) if row_doc.cc else None
	bcc = parse_email_addresses(row_doc.bcc) if row_doc.bcc else None

	frappe.sendmail(
		recipients=recipients,
		cc=cc,
		bcc=bcc,
		subject=subject,
		message=message,
		attachments=attachments,
		now=True,
	)

	# Update last_sent_at
	frappe.db.set_value("Attendance Report Schedule", row_doc.name, "last_sent_at", now())
	return True


def send_scheduled_reports():
	"""Called by scheduler to send scheduled attendance reports."""
	if not frappe.db.exists("DocType", "Attendance Settings"):
		return

	doc = frappe.get_single("Attendance Settings")
	for row in doc.get("scheduled_reports") or []:
		try:
			send_single_scheduled_report(row, force=False)
		except Exception as e:
			frappe.log_error(f"Error sending scheduled report {row.name}: {str(e)}")


@frappe.whitelist()
def trigger_send_now():
	"""Whitelisted method for desk button to send enabled reports immediately."""
	frappe.only_for("System Manager")
	doc = frappe.get_single("Attendance Settings")
	sent_count = 0
	for row in doc.get("scheduled_reports") or []:
		if row.enabled:
			res = send_single_scheduled_report(row, force=True)
			if res:
				sent_count += 1
	return {"sent_count": sent_count}


@frappe.whitelist()
def get_report_filter_fields(report_name):
	"""
	Return list of dicts [{'fieldname': fn, 'label': lbl, 'display': lbl}]
	for actual report filters, sorted alphabetically by label ASC.
	Prioritizes JS script filter definitions and report JSON filters.
	"""
	if not report_name:
		return []

	result = []
	seen = set()

	def add_field(fn, label=None):
		if not fn or fn in seen:
			return
		seen.add(fn)
		lbl = (label or fn).strip()
		result.append({
			"fieldname": fn,
			"label": lbl,
			"display": lbl,
		})

	try:
		report_doc = frappe.get_doc("Report", report_name)
	except Exception:
		return []

	# 1. Try JS script file first (for standard and script reports)
	script_content = ""
	try:
		script_data = frappe.desk.query_report.get_script(report_name)
		script_content = (script_data or {}).get("script") or ""
	except Exception:
		pass

	if not script_content and report_doc.module:
		try:
			module_path = frappe.get_module_path(report_doc.module)
			scrubbed1 = frappe.scrub(report_name)
			scrubbed2 = report_name.lower().replace(" ", "_")
			scrubbed3 = report_name.lower().replace(" ", "_").replace("&", "and")

			for s in (scrubbed1, scrubbed2, scrubbed3):
				js_path = os.path.join(module_path, "report", s, f"{s}.js")
				if os.path.exists(js_path):
					with open(js_path, "r", encoding="utf-8") as f:
						script_content = f.read()
					if script_content:
						break
		except Exception:
			pass

	if script_content:
		try:
			filters_match = re.search(r'["\']?filters["\']?\s*:\s*\[', script_content)
			if filters_match:
				start_idx = filters_match.start()
				target_script = script_content[start_idx:]
			else:
				target_script = script_content

			fn_matches = re.findall(r'["\']?fieldname["\']?\s*:\s*["\']([^"\']+)["\']', target_script)
			for fn in fn_matches:
				lbl_match = re.search(
					r'["\']?fieldname["\']?\s*:\s*["\']' + re.escape(fn) + r'["\'][^{}]*?["\']?label["\']?\s*:\s*(?:__\()?["\']([^"\']+)["\']',
					target_script,
					re.DOTALL
				)
				if not lbl_match:
					lbl_match = re.search(
						r'["\']?label["\']?\s*:\s*(?:__\()?["\']([^"\']+)["\'][^{}]*?["\']?fieldname["\']?\s*:\s*["\']' + re.escape(fn) + r'["\']',
						target_script,
						re.DOTALL
					)
				lbl = lbl_match.group(1) if lbl_match else fn.replace("_", " ").title()
				add_field(fn, lbl)
		except Exception:
			pass

	# 2. If no JS filters were found, try report.json or get_columns_or_filter_meta
	if not result:
		if report_doc.json:
			try:
				json_data = json.loads(report_doc.json)
				for f in (json_data.get("filters") or []):
					if isinstance(f, dict) and f.get("fieldname"):
						add_field(f["fieldname"], f.get("label"))
			except Exception:
				pass

		try:
			meta_filters = report_doc.get_columns_or_filter_meta() or []
			for f in meta_filters:
				if isinstance(f, dict) and f.get("fieldname"):
					add_field(f["fieldname"], f.get("label"))
		except Exception:
			pass

	# 3. If still no filters found, check SQL query parameters
	if not result and report_doc.query:
		try:
			sql_params = re.findall(r'%\(([^)]+)\)s', report_doc.query)
			for fn in sql_params:
				add_field(fn, fn.replace("_", " ").title())
		except Exception:
			pass

	# 4. Only for Report Builder reports (when no JS/json/sql filters exist), fall back to ref_doctype fields
	if not result and report_doc.report_type == "Report Builder" and report_doc.ref_doctype:
		try:
			meta = frappe.get_meta(report_doc.ref_doctype)
			for df in meta.fields:
				if df.fieldname and df.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML", "Table"):
					add_field(df.fieldname, df.label)
		except Exception:
			pass

	# Sort result alphabetically by label ASC
	result.sort(key=lambda x: (x.get("label") or "").lower())
	return result


@frappe.whitelist()
def get_all_scheduled_report_filter_fields(reports=None):
	"""
	Return dictionary mapping report_name -> list of filter dicts.
	Accepts an optional list of report names from client JS (for unsaved form state).
	"""
	if isinstance(reports, str):
		try:
			reports = json.loads(reports)
		except Exception:
			reports = [reports]

	reports_set = set(reports or [])

	if frappe.db.exists("DocType", "Attendance Settings"):
		doc = frappe.get_single("Attendance Settings")
		for row in (doc.get("scheduled_reports") or []):
			if row.report:
				reports_set.add(row.report)
		for r_row in (doc.get("report_filters") or []):
			if r_row.report:
				reports_set.add(r_row.report)

	data = {}
	for r in reports_set:
		if r:
			data[r] = get_report_filter_fields(r)
	return data


@frappe.whitelist()
def get_scheduled_report_names():
	"""
	Return list of report names currently configured in the
	Scheduled Reports child table, so the Report Filters table
	can restrict its report dropdown.
	"""
	doc = frappe.get_single("Attendance Settings")
	return list({row.report for row in (doc.get("scheduled_reports") or []) if row.report})
