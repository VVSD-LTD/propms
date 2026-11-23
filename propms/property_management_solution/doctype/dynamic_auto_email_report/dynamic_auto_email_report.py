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
	add_days,
	cint,
	formatdate,
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


class DynamicAutoEmailReport(Document):
	def validate(self):
		if not self.report:
			frappe.throw(_("Report is mandatory."))

		if not self.recipients:
			frappe.throw(_("To (Recipients) is mandatory."))

		recipients = parse_email_addresses(self.recipients)
		if not recipients:
			frappe.throw(_("Please provide at least one valid recipient email address."))
		self.recipients = "\n".join(recipients)

		if self.cc:
			self.cc = "\n".join(parse_email_addresses(self.cc))

		if self.bcc:
			self.bcc = "\n".join(parse_email_addresses(self.bcc))

		if self.frequency == "Weekly" and not self.day_of_week:
			frappe.throw(_("Day of Week is required for a Weekly schedule."))

		if self.frequency == "Monthly":
			dom = cint(self.day_of_month)
			if dom < 1 or dom > 28:
				frappe.throw(_("Day of Month must be between 1 and 28."))


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


def coerce_filter_value(value):
	"""Turn a pasted JSON list into a list. Multi-select reports expect a list."""
	if not isinstance(value, str):
		return value
	text = value.strip()
	if text.startswith("[") and text.endswith("]"):
		try:
			parsed = json.loads(text)
		except Exception:
			return value
		if isinstance(parsed, list):
			return [item for item in parsed if item not in (None, "")]
	return value


def yesterday(as_of=None):
	"""Date string for the day before as_of, or before today."""
	return add_days(as_of or today(), -1)


class DateFormatter:
	"""{{ date }} is today. {{ date(yesterday(), "dd MMMM yyyy") }} formats a day."""

	def __call__(self, value=None, fmt="dd-mm-yyyy"):
		if value in (None, ""):
			return today()
		return formatdate(value, fmt)

	def __str__(self):
		return today()

	def __html__(self):
		return today()


render_date = DateFormatter()


def template_context(row_doc, **extra):
	context = {
		"today": today,
		"yesterday": yesterday,
		"nowdate": nowdate,
		"date": DateFormatter(),
		"frappe": frappe,
		"doc": row_doc,
	}
	context.update(extra)
	return context


def render_jinja_template(template_str, context):
	if not template_str:
		return ""
	try:
		return frappe.render_template(template_str, context)
	except Exception as e:
		frappe.log_error(f"Jinja template error in Dynamic Auto Email Report: {str(e)}")
		return template_str


def get_report_data(report_name, filters=None):
	filters = filters or {}
	if "from_date" not in filters:
		filters["from_date"] = today()
	if "to_date" not in filters:
		filters["to_date"] = today()

	# Prepared reports only return a previously saved file. Generate the report
	# now, and keep the filters from this schedule instead of the report's saved defaults.
	report_result = run(
		report_name,
		filters=filters,
		ignore_prepared_report=True,
		are_default_filters=False,
	)
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


def schedule_email_message_id(row_name):
	"""Stable id so a failed send can be recognized on the next cron tick."""
	if not row_name:
		return None
	return f"attendance-schedule-{row_name}-{today()}@{frappe.local.site}"


def queue_attendance_report_email(
	recipients, cc, bcc, subject, message, attachments, reference_name=None
):
	"""Queue one Communication + one Email Queue the way Frappe Notifications send mail.

	Do not send with now=True or queue_separately=True. Those make Frappe create a
	separate SMTP delivery (and, with queue_separately, a separate Email Queue) for
	every To recipient, so each BCC address is copied on every copy.
	"""
	from frappe.core.doctype.communication.email import _make as make_communication

	message_id = schedule_email_message_id(reference_name)

	communication = make_communication(
		doctype="Dynamic Auto Email Report",
		name=reference_name,
		content=message,
		subject=subject,
		recipients=recipients,
		cc=cc or None,
		bcc=bcc or None,
		send_email=False,
		communication_type="Automated Message",
	).get("name")

	if message_id:
		frappe.db.sql(
			"UPDATE `tabCommunication` SET message_id=%s WHERE name=%s",
			(message_id, communication),
		)

	frappe.sendmail(
		recipients=recipients,
		cc=cc or None,
		bcc=bcc or None,
		subject=subject,
		message=message,
		attachments=attachments,
		reference_doctype="Dynamic Auto Email Report",
		reference_name=reference_name,
		expose_recipients="header",
		communication=communication,
		message_id=message_id,
		add_unsubscribe_link=0,
		queue_separately=False,
		delayed=True,
	)
	return communication


def mark_schedule_attempted(row_name):
	"""Stamp last_sent_at even when queueing fails, so the next cron tick cannot recreate."""
	if not row_name:
		return
	frappe.db.set_value("Dynamic Auto Email Report", row_name, "last_sent_at", now())


def todays_schedule_email_exists(row_name):
	"""True if this schedule row already created a Communication or Email Queue today."""
	message_id = schedule_email_message_id(row_name)
	if not message_id:
		return False
	return bool(
		frappe.db.exists("Communication", {"message_id": message_id})
		or frappe.db.exists("Email Queue", {"message_id": message_id})
	)


def send_single_scheduled_report(row_doc, force=False):
	if not force and not row_doc.enabled:
		return False

	now_dt = now_datetime()
	current_day = calendar.day_name[now_dt.weekday()]
	current_dom = now_dt.day

	if not force:
		# Lock this document so overlapping */5 cron ticks cannot double-send.
		if row_doc.name and frappe.db.exists("Dynamic Auto Email Report", row_doc.name):
			frappe.db.sql(
				"SELECT name FROM `tabDynamic Auto Email Report` WHERE name=%s FOR UPDATE",
				row_doc.name,
			)
			row_doc.last_sent_at = frappe.db.get_value(
				"Dynamic Auto Email Report", row_doc.name, "last_sent_at"
			)

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

		# Email already created today (queue may have failed after Communication insert).
		if todays_schedule_email_exists(row_doc.name):
			mark_schedule_attempted(row_doc.name)
			return False

	# Fetch report data
	report_name = row_doc.report or "Employee & Sub Contractor Checking Summary"
	filters = {"from_date": today(), "to_date": today()}

	jinja_ctx = template_context(row_doc)

	report_fields = get_report_filter_fields(report_name)
	field_map = {}
	for f in report_fields:
		if f.get("label"):
			field_map[f["label"].strip()] = f["fieldname"]
		if f.get("fieldname"):
			field_map[f["fieldname"].strip()] = f["fieldname"]

	for f_item in row_doc.get("report_filters") or []:
		if f_item.fieldname:
			raw_val = f_item.value or ""
			rendered_val = render_jinja_template(raw_val, jinja_ctx)
			raw_fn = str(f_item.fieldname).strip()
			target_field = (getattr(f_item, "raw_fieldname", None) or field_map.get(raw_fn, raw_fn)).strip()
			filters[target_field] = coerce_filter_value(rendered_val)

	filters.pop("custom_format", None)
	report_result = get_report_data(report_name, filters)

	# Check if 'Don't Send If No Data' is enabled and report result is empty
	result_data = report_result.get("result") or []
	if row_doc.get("dont_send_if_no_data") and not result_data:
		frappe.logger().info(f"Skipping scheduled report '{report_name}': No data found and 'Don't Send If No Data' is enabled.")
		frappe.db.set_value("Dynamic Auto Email Report", row_doc.name, "last_sent_at", now())
		return False

	# Format content
	file_ext = "pdf" if row_doc.format == "PDF" else "xlsx"
	clean_report_name = report_name.replace(" ", "_").replace("&", "and")
	fname = f"{clean_report_name}_{today()}.{file_ext}"

	if row_doc.get("use_custom_format"):
		attachment_content = custom_report_attachment(report_name, file_ext, filters)
	elif row_doc.format == "PDF":
		html_content = build_report_pdf_html(report_name, report_result, filters)
		attachment_content = get_pdf(html_content)
	else:
		attachment_content = build_report_excel(report_name, report_result)

	attachments = [{"fname": fname, "fcontent": attachment_content}]

	context = template_context(
		row_doc,
		report_name=report_name,
		filters=filters,
		summary=report_result.get("report_summary") or [],
	)

	default_subject = f"{report_name} - {today()}"
	default_message = f"<p>Hello,</p><p>Please find attached the scheduled report: <strong>{report_name}</strong> for {today()}.</p>"

	subject = render_jinja_template(row_doc.subject, context) if row_doc.subject else default_subject
	message = render_jinja_template(row_doc.message, context) if row_doc.message else default_message

	recipients = parse_email_addresses(row_doc.recipients)
	cc = parse_email_addresses(row_doc.cc) if row_doc.cc else []
	bcc = parse_email_addresses(row_doc.bcc) if row_doc.bcc else []

	try:
		queue_attendance_report_email(
			recipients=recipients,
			cc=cc,
			bcc=bcc,
			subject=subject,
			message=message,
			attachments=attachments,
			reference_name=row_doc.name,
		)
		return True
	except Exception:
		frappe.log_error(
			title=f"Attendance scheduled report email failed ({row_doc.name})",
			message=frappe.get_traceback(),
		)
		return False
	finally:
		# Always stamp the attempt. If last_sent_at is left empty, the next */5
		# cron recreates Communication + Email Queue even though the first try failed.
		mark_schedule_attempted(row_doc.name)


def send_scheduled_reports():
	"""Called by scheduler to send each enabled Dynamic Auto Email Report."""
	if not frappe.db.exists("DocType", "Dynamic Auto Email Report"):
		return
	if cint(frappe.db.get_value("DocType", "Dynamic Auto Email Report", "issingle")):
		return

	for name in frappe.get_all("Dynamic Auto Email Report", filters={"enabled": 1}, pluck="name"):
		try:
			send_single_scheduled_report(frappe.get_doc("Dynamic Auto Email Report", name), force=False)
		except Exception as e:
			frappe.log_error(f"Error sending scheduled report {name}: {str(e)}")


@frappe.whitelist()
def trigger_send_now(name):
	"""Send this document immediately, ignoring the schedule clock."""
	frappe.only_for("System Manager")
	doc = frappe.get_doc("Dynamic Auto Email Report", name)
	if not doc.enabled:
		frappe.throw(_("Enable this report before sending."))
	return {"sent": bool(send_single_scheduled_report(doc, force=True))}


def _load_report_script(report_name):
	script_content = ""
	try:
		script_data = frappe.desk.query_report.get_script(report_name)
		script_content = (script_data or {}).get("script") or ""
	except Exception:
		script_content = ""

	if script_content:
		return script_content

	try:
		report_doc = frappe.get_doc("Report", report_name)
	except Exception:
		return ""

	if not report_doc.module:
		return ""

	try:
		module_path = frappe.get_module_path(report_doc.module)
		scrubbed = (
			frappe.scrub(report_name),
			report_name.lower().replace(" ", "_"),
			report_name.lower().replace(" ", "_").replace("&", "and"),
		)
		for name in scrubbed:
			js_path = os.path.join(module_path, "report", name, f"{name}.js")
			if os.path.exists(js_path):
				with open(js_path, "r", encoding="utf-8") as handle:
					script_content = handle.read()
				if script_content:
					return script_content
	except Exception:
		return ""
	return ""


def _filter_object_texts(script):
	"""Top-level objects inside a report script's filters array."""
	if not script:
		return []
	match = re.search(r"filters\s*:\s*\[", script)
	if not match:
		return []

	start = script.find("[", match.start())
	array_depth = 0
	object_depth = 0
	objects = []
	buffer = []
	collecting = False
	for char in script[start:]:
		if char == "[":
			array_depth += 1
		elif char == "]":
			array_depth -= 1
			if array_depth == 0:
				break

		if char == "{":
			if object_depth == 0 and array_depth == 1:
				collecting = True
				buffer = ["{"]
				object_depth = 1
				continue
			object_depth += 1
		elif char == "}":
			object_depth -= 1
			if collecting:
				buffer.append(char)
			if object_depth == 0 and collecting:
				objects.append("".join(buffer))
				collecting = False
			continue

		if collecting:
			buffer.append(char)
	return objects


def _parse_js_filter(text):
	fieldname = re.search(r"fieldname\s*:\s*[\"']([^\"']+)[\"']", text)
	if not fieldname:
		return None
	label = re.search(r"label\s*:\s*(?:__\()?[\"']([^\"']+)[\"']", text)
	fieldtype = re.search(r"fieldtype\s*:\s*[\"']([^\"']+)[\"']", text)
	default = re.search(r"default\s*:\s*[\"']([^\"']+)[\"']", text)
	name = fieldname.group(1)
	return {
		"fieldname": name,
		"label": label.group(1) if label else name.replace("_", " ").title(),
		"fieldtype": fieldtype.group(1) if fieldtype else "Data",
		"hidden": bool(re.search(r"\bhidden\s*:\s*(?:1|true)\b", text)),
		"default": default.group(1) if default else "",
	}


def report_custom_format_method(report_name):
	"""Python path from a hidden report filter named custom_format."""
	if not report_name:
		return None
	for text in _filter_object_texts(_load_report_script(report_name)):
		parsed = _parse_js_filter(text)
		if not parsed:
			continue
		if parsed["fieldname"] == "custom_format" and parsed["hidden"] and parsed["default"]:
			return parsed["default"]

	try:
		report_doc = frappe.get_doc("Report", report_name)
		if report_doc.json:
			for filt in json.loads(report_doc.json).get("filters") or []:
				if (
					isinstance(filt, dict)
					and filt.get("fieldname") == "custom_format"
					and filt.get("hidden")
					and filt.get("default")
				):
					return filt.get("default")
	except Exception:
		return None
	return None


def _allowed_report_method(method):
	if not method or not re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", method):
		return False
	root = method.split(".", 1)[0]
	return root in set(frappe.get_installed_apps())


def custom_report_attachment(report_name, file_format, filters):
	"""Call the report's own download builder. file_format is pdf or xlsx."""
	method = report_custom_format_method(report_name)
	if not _allowed_report_method(method):
		frappe.throw(_("This report has no custom format. Add a hidden custom_format filter."))
	payload = dict(filters or {})
	payload.pop("custom_format", None)
	content = frappe.get_attr(method)(report_name, file_format, payload)
	if not isinstance(content, (bytes, bytearray)):
		frappe.throw(_("Custom format did not return a file."))
	return bytes(content)


@frappe.whitelist()
def get_custom_format_method(report_name):
	return report_custom_format_method(report_name) or ""


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

	def add_field(fn, label=None, fieldtype=None, hidden=False):
		if not fn or fn in seen or hidden or fn == "custom_format":
			return
		seen.add(fn)
		lbl = (label or fn).strip()
		result.append({
			"fieldname": fn,
			"label": lbl,
			"display": lbl,
			"fieldtype": fieldtype or "Data",
		})

	try:
		report_doc = frappe.get_doc("Report", report_name)
	except Exception:
		return []

	script_content = _load_report_script(report_name)
	if script_content:
		try:
			for text in _filter_object_texts(script_content):
				parsed = _parse_js_filter(text)
				if parsed:
					add_field(parsed["fieldname"], parsed["label"], parsed["fieldtype"], parsed["hidden"])
		except Exception:
			pass

		if not result:
			try:
				filters_match = re.search(r'["\']?filters["\']?\s*:\s*\[', script_content)
				target_script = script_content[filters_match.start():] if filters_match else script_content
				fn_matches = re.findall(r'["\']?fieldname["\']?\s*:\s*["\']([^"\']+)["\']', target_script)
				for fn in fn_matches:
					lbl_match = re.search(
						r'["\']?fieldname["\']?\s*:\s*["\']' + re.escape(fn) + r'["\'][^{}]*?["\']?label["\']?\s*:\s*(?:__\()?["\']([^"\']+)["\']',
						target_script,
						re.DOTALL,
					)
					if not lbl_match:
						lbl_match = re.search(
							r'["\']?label["\']?\s*:\s*(?:__\()?["\']([^"\']+)["\'][^{}]*?["\']?fieldname["\']?\s*:\s*["\']' + re.escape(fn) + r'["\']',
							target_script,
							re.DOTALL,
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
						add_field(f["fieldname"], f.get("label"), f.get("fieldtype"), bool(f.get("hidden")))
			except Exception:
				pass

		try:
			meta_filters = report_doc.get_columns_or_filter_meta() or []
			for f in meta_filters:
				if isinstance(f, dict) and f.get("fieldname"):
					add_field(f["fieldname"], f.get("label"), f.get("fieldtype"), bool(f.get("hidden")))
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
	if cint(frappe.db.get_value("DocType", "Dynamic Auto Email Report", "issingle")):
		return []
	return [name for name in frappe.get_all("Dynamic Auto Email Report", pluck="report") if name]
