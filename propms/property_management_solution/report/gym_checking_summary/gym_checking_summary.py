# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import json
from datetime import datetime

import frappe
from frappe import _
from frappe.utils import getdate


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	return columns, data, None, None, get_report_summary(data)


def get_columns():
	return [
		{
			"fieldname": "gym_member",
			"label": _("Gym Member"),
			"fieldtype": "Link",
			"options": "Gym Member",
			"width": 140,
		},
		{
			"fieldname": "pin",
			"label": _("PIN"),
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"fieldname": "person_name",
			"label": _("Name"),
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"fieldname": "last_name",
			"label": _("Last Name / Unit"),
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"fieldname": "dept_name",
			"label": _("Department"),
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"fieldname": "status",
			"label": _("Status"),
			"fieldtype": "Data",
			"width": 100,
		},
		{
			"fieldname": "date",
			"label": _("Date"),
			"fieldtype": "Date",
			"width": 110,
		},
		{
			"fieldname": "week_day",
			"label": _("Week Day"),
			"fieldtype": "Data",
			"width": 110,
		},
		{
			"fieldname": "checkin_time",
			"label": _("Checkin Time"),
			"fieldtype": "Time",
			"width": 120,
		},
		{
			"fieldname": "checkout_time",
			"label": _("Checkout Time"),
			"fieldtype": "Time",
			"width": 120,
		},
		{
			"fieldname": "total_hours_spent",
			"label": _("Total Hours Spent"),
			"fieldtype": "Data",
			"width": 140,
		},
	] + _reader_columns()


def _reader_columns():
	"""Door names for the first IN and last OUT. System Manager debugging only."""
	if "System Manager" not in frappe.get_roles():
		return []
	return [
		{
			"fieldname": "checkin_reader",
			"label": _("Checkin Reader"),
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"fieldname": "checkout_reader",
			"label": _("Checkout Reader"),
			"fieldtype": "Data",
			"width": 140,
		},
	]


def get_data(filters):
	conditions = get_conditions(filters)
	reader_select = ""
	if "System Manager" in frappe.get_roles():
		reader_select = """
			, MIN(CASE WHEN chec.log_type = 'IN' THEN CONCAT(DATE_FORMAT(chec.event_time, '%%Y%%m%%d%%H%%i%%s'), '|', IFNULL(chec.reader_name, '')) END) AS checkin_reader_key
			, MAX(CASE WHEN chec.log_type = 'OUT' THEN CONCAT(DATE_FORMAT(chec.event_time, '%%Y%%m%%d%%H%%i%%s'), '|', IFNULL(chec.reader_name, '')) END) AS checkout_reader_key
		"""
	rows = frappe.db.sql(
		f"""
		SELECT
			MAX(mem.name) AS gym_member,
			chec.pin AS pin,
			COALESCE(MAX(mem.person_name), MAX(chec.person_name)) AS person_name,
			COALESCE(MAX(mem.last_name), MAX(chec.last_name)) AS last_name,
			COALESCE(MAX(mem.dept_name), MAX(chec.dept_name)) AS dept_name,
			MAX(mem.status) AS status,
			DATE(chec.event_time) AS date,
			MIN(CASE WHEN chec.log_type = 'IN' THEN DATE_FORMAT(chec.event_time, '%%T') END) AS checkin_time,
			MAX(CASE WHEN chec.log_type = 'OUT' THEN DATE_FORMAT(chec.event_time, '%%T') END) AS checkout_time
			{reader_select}
		FROM `tabGym Checkin` chec
			LEFT JOIN `tabGym Member` mem ON mem.pin = chec.pin
		WHERE chec.log_type IN ('IN', 'OUT')
			AND IFNULL(chec.pin, '') != ''
			{conditions}
		GROUP BY chec.pin, DATE(chec.event_time)
		ORDER BY DATE(chec.event_time) ASC, person_name ASC
		""",
		filters,
		as_dict=1,
	)

	for row in rows:
		row["week_day"] = getdate(row.date).strftime("%A") if row.date else ""
		row["checkin_time"] = row.checkin_time or ""
		row["checkout_time"] = row.checkout_time or ""
		row["total_hours_spent"] = calc_total_hours(row.checkin_time, row.checkout_time)
		if "checkin_reader_key" in row:
			row["checkin_reader"] = _reader_name(row.pop("checkin_reader_key"))
			row["checkout_reader"] = _reader_name(row.pop("checkout_reader_key"))
	return rows


def _reader_name(value):
	text = "" if value is None else str(value)
	if "|" not in text:
		return ""
	return text.split("|", 1)[1]


def get_conditions(filters):
	conditions = ""
	if filters.get("from_datetime"):
		conditions += " AND chec.event_time >= %(from_datetime)s"
	if filters.get("to_datetime"):
		conditions += " AND chec.event_time <= %(to_datetime)s"

	members = get_filter_list(filters, "gym_member")
	if members:
		conditions += build_in_condition("mem.name", members)

	if filters.get("department"):
		conditions += " AND COALESCE(mem.dept_name, chec.dept_name) LIKE %(department_like)s"
		filters["department_like"] = "%" + str(filters.get("department")).strip() + "%"

	if filters.get("last_name"):
		conditions += " AND COALESCE(mem.last_name, chec.last_name) LIKE %(last_name_like)s"
		filters["last_name_like"] = "%" + str(filters.get("last_name")).strip() + "%"

	if filters.get("status"):
		conditions += " AND mem.status = %(status)s"
	return conditions


def get_filter_list(filters, fieldname):
	val = filters.get(fieldname)
	if not val:
		return []
	if isinstance(val, (list, tuple, set)):
		return [str(v).strip() for v in val if v]
	if isinstance(val, str):
		val = val.strip()
		if not val:
			return []
		if val.startswith("[") and val.endswith("]"):
			try:
				parsed = json.loads(val)
				if isinstance(parsed, list):
					return [str(v).strip() for v in parsed if v]
			except Exception:
				pass
		return [part.strip() for part in val.split(",") if part.strip()]
	return [str(val).strip()]


def build_in_condition(column_name, values_list):
	if not values_list:
		return ""
	escaped = ", ".join("'" + frappe.db.escape(v)[1:-1] + "'" for v in values_list)
	return f" AND {column_name} IN ({escaped})"


def calc_total_hours(checkin_time, checkout_time):
	if not checkin_time or not checkout_time:
		return ""
	try:
		start = datetime.strptime(str(checkin_time), "%H:%M:%S")
		end = datetime.strptime(str(checkout_time), "%H:%M:%S")
		delta = end - start
		if delta.total_seconds() < 0:
			return ""
		seconds = int(delta.total_seconds())
		hours = seconds // 3600
		minutes = (seconds % 3600) // 60
		secs = seconds % 60
		return f"{hours:02d}:{minutes:02d}:{secs:02d}"
	except Exception:
		return ""


def get_report_summary(data):
	checked_in = sum(1 for row in data if row.get("checkin_time"))
	checked_out = sum(1 for row in data if row.get("checkout_time"))
	return [
		{
			"value": checked_in,
			"indicator": "Blue",
			"label": _("IN"),
			"datatype": "Int",
		},
		{
			"value": checked_out,
			"indicator": "Orange",
			"label": _("OUT"),
			"datatype": "Int",
		},
		{
			"value": f"{checked_in} / {checked_out}",
			"indicator": "Green",
			"label": _("Total IN / OUT"),
			"datatype": "Data",
		},
	]
