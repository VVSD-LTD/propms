# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import json
import frappe
from frappe import _
from datetime import datetime, timedelta
from frappe.utils import get_time, add_to_date, getdate, today


def execute(filters=None):
	filters = filters or {}
	columns = get_columns(filters)
	data = get_data(filters)
	report_summary = get_report_summary(data)
	return columns, data, None, None, report_summary


def get_columns(filters):
	return [
		{
			"fieldname": "person_type",
			"label": _("Person Type"),
			"fieldtype": "Data",
			"width": 120,
		},
		{
			"fieldname": "person",
			"label": _("Person ID"),
			"fieldtype": "Data",
			"width": 150,
		},
		{
			"fieldname": "person_name",
			"label": _("Name"),
			"fieldtype": "Data",
			"width": 180,
		},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 140,
		},
		{
			"fieldname": "department_or_category",
			"label": _("Department / Category"),
			"fieldtype": "Data",
			"width": 160,
		},
		{
			"fieldname": "shift",
			"label": _("Shift"),
			"fieldtype": "Link",
			"options": "Shift Type",
			"width": 140,
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
		# Checkin details
		{
			"fieldname": "actual_checkin_time",
			"label": _("Actual Time to Checkin"),
			"fieldtype": "Time",
			"width": 140,
		},
		{
			"fieldname": "checkin_time",
			"label": _("Checkin Time"),
			"fieldtype": "Time",
			"width": 120,
		},
		{
			"fieldname": "late_entry_grace_time",
			"label": _("Late Entry Grace Period"),
			"fieldtype": "Time",
			"width": 150,
		},
		{
			"fieldname": "checkin_status",
			"label": _("Checkin Status"),
			"fieldtype": "Data",
			"width": 130,
		},
		# Checkout details
		{
			"fieldname": "actual_checkout_time",
			"label": _("Actual Time to Checkout"),
			"fieldtype": "Time",
			"width": 140,
		},
		{
			"fieldname": "checkout_time",
			"label": _("Checkout Time"),
			"fieldtype": "Time",
			"width": 120,
		},
		{
			"fieldname": "early_exit_grace_time",
			"label": _("Early Exit Grace Period"),
			"fieldtype": "Time",
			"width": 150,
		},
		{
			"fieldname": "checkout_status",
			"label": _("Checkout Status"),
			"fieldtype": "Data",
			"width": 130,
		},
		# Summary & offshift
		{
			"fieldname": "total_hours_spent",
			"label": _("Total Hours Spent"),
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"fieldname": "offshift",
			"label": _("Off Shift"),
			"fieldtype": "Check",
			"width": 90,
		},
	]


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
		return [s.strip() for s in val.split(",") if s.strip()]
	return [str(val).strip()]


def build_in_condition(column_name, values_list):
	if not values_list:
		return ""
	escaped = ", ".join("'" + frappe.db.escape(v)[1:-1] + "'" for v in values_list)
	return f" AND {column_name} IN ({escaped})"


def get_data(filters):
	shift_type_list = frappe.get_all(
		"Shift Type",
		fields=["name", "start_time", "end_time", "late_entry_grace_period", "early_exit_grace_period"],
	)
	shift_types_map = {s.name: s for s in shift_type_list}

	person_type_filter = filters.get("person_type")
	fetch_employees = True
	fetch_subcontractors = True

	emp_filters_set = bool(get_filter_list(filters, "employee") or get_filter_list(filters, "department"))
	sub_filters_set = bool(get_filter_list(filters, "sub_contractor") or get_filter_list(filters, "sub_contractor_category"))

	if person_type_filter == "Employee":
		fetch_subcontractors = False
	elif person_type_filter == "Sub Contractor":
		fetch_employees = False

	if emp_filters_set and not sub_filters_set and person_type_filter != "Sub Contractor":
		fetch_subcontractors = False
	elif sub_filters_set and not emp_filters_set and person_type_filter != "Employee":
		fetch_employees = False

	records_map = {}

	if fetch_employees:
		emp_checkin_details = get_emp_checkin_details(filters)
		emp_checkout_details = get_emp_checkout_details(filters)
		process_person_records(records_map, emp_checkin_details, emp_checkout_details, shift_types_map)

	if fetch_subcontractors:
		sub_checkin_details = get_sub_checkin_details(filters)
		sub_checkout_details = get_sub_checkout_details(filters)
		process_person_records(records_map, sub_checkin_details, sub_checkout_details, shift_types_map)

	result = list(records_map.values())

	# Calculate Total Hours Spent
	for row in result:
		row["total_hours_spent"] = calc_total_hours(row.get("checkin_time"), row.get("checkout_time"))

	# Filter by shift_type list if specified
	shift_list = get_filter_list(filters, "shift_type")
	if shift_list:
		result = [r for r in result if r.get("shift") in shift_list]

	# Sort by date asc, person_name asc
	result.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("person_name") or ""), str(x.get("person_type") or "")))
	return result


def process_person_records(records_map, checkin_details, checkout_details, shift_types_map):
	for c in checkin_details:
		key = (c.person_type, c.person, str(c.date))
		shift = c.shift_type or c.default_shift or ""

		actual_checkin, late_grace, checkin_status = "", "", ""
		if shift and shift in shift_types_map:
			actual_checkin, late_grace, checkin_status = evaluate_checkin_status(c.checkin_time, shift_types_map[shift])

		records_map[key] = {
			"person_type": c.person_type,
			"person": c.person,
			"person_name": c.person_name,
			"company": c.company,
			"department_or_category": c.department_or_category,
			"shift": shift,
			"date": c.date,
			"week_day": getdate(c.date).strftime("%A") if c.date else "",
			"actual_checkin_time": actual_checkin,
			"checkin_time": c.checkin_time or "",
			"late_entry_grace_time": late_grace,
			"checkin_status": checkin_status,
			"actual_checkout_time": "",
			"checkout_time": "",
			"early_exit_grace_time": "",
			"checkout_status": "",
			"total_hours_spent": "",
			"offshift": c.offshift or 0,
		}

	for c in checkout_details:
		key = (c.person_type, c.person, str(c.date))
		shift = c.shift_type or c.default_shift or ""

		actual_checkout, early_grace, checkout_status = "", "", ""
		if shift and shift in shift_types_map:
			actual_checkout, early_grace, checkout_status = evaluate_checkout_status(c.checkout_time, shift_types_map[shift])

		if key in records_map:
			rec = records_map[key]
			rec["actual_checkout_time"] = actual_checkout
			rec["checkout_time"] = c.checkout_time or ""
			rec["early_exit_grace_time"] = early_grace
			rec["checkout_status"] = checkout_status
			if c.offshift:
				rec["offshift"] = 1
			if not rec.get("shift"):
				rec["shift"] = shift
		else:
			records_map[key] = {
				"person_type": c.person_type,
				"person": c.person,
				"person_name": c.person_name,
				"company": c.company,
				"department_or_category": c.department_or_category,
				"shift": shift,
				"date": c.date,
				"week_day": getdate(c.date).strftime("%A") if c.date else "",
				"actual_checkin_time": "",
				"checkin_time": "",
				"late_entry_grace_time": "",
				"checkin_status": "",
				"actual_checkout_time": actual_checkout,
				"checkout_time": c.checkout_time or "",
				"early_exit_grace_time": early_grace,
				"checkout_status": checkout_status,
				"total_hours_spent": "",
				"offshift": c.offshift or 0,
			}


def evaluate_checkin_status(checkin_time_str, shift_type):
	if not checkin_time_str or not shift_type or not shift_type.start_time:
		return "", "", ""
	start_time = get_time(str(shift_type.start_time))
	checkin_time = get_time(str(checkin_time_str))
	grace_min = shift_type.late_entry_grace_period or 0
	late_grace_str = f"00:{int(grace_min):02d}:00"

	start_time_with_grace = get_time(
		add_to_date(str(shift_type.start_time), minutes=grace_min, as_string=False, as_datetime=True)
	)

	if checkin_time <= start_time:
		status = "Early Checkin"
	elif checkin_time <= start_time_with_grace:
		status = "On Time"
	else:
		status = "Late Checkin"

	return str(shift_type.start_time), late_grace_str, status


def evaluate_checkout_status(checkout_time_str, shift_type):
	if not checkout_time_str or not shift_type or not shift_type.end_time:
		return "", "", ""
	end_time = get_time(str(shift_type.end_time))
	checkout_time = get_time(str(checkout_time_str))
	grace_min = shift_type.early_exit_grace_period or 0
	early_grace_str = f"00:{int(grace_min):02d}:00"

	end_time_minus_grace = get_time(
		add_to_date(str(shift_type.end_time), minutes=(-grace_min), as_string=False, as_datetime=True)
	)

	if checkout_time <= end_time_minus_grace:
		status = "Early Checkout"
	elif checkout_time <= end_time:
		status = "On Time"
	else:
		status = "Late Checkout"

	return str(shift_type.end_time), early_grace_str, status


def calc_total_hours(checkin_time, checkout_time):
	if not checkin_time or not checkout_time:
		return ""
	try:
		t1 = datetime.strptime(str(checkin_time), "%H:%M:%S")
		t2 = datetime.strptime(str(checkout_time), "%H:%M:%S")
		delta = t2 - t1
		if delta.total_seconds() < 0:
			delta += timedelta(days=1)
		seconds = int(delta.total_seconds())
		hours = seconds // 3600
		minutes = (seconds % 3600) // 60
		secs = seconds % 60
		return f"{hours:02d}:{minutes:02d}:{secs:02d}"
	except Exception:
		return ""


def get_emp_checkin_details(filters):
	conditions = get_emp_conditions(filters)
	return frappe.db.sql(
		f"""
		SELECT 
			chec.employee AS person,
			chec.employee_name AS person_name,
			'Employee' AS person_type,
			emp.company AS company,
			emp.department AS department_or_category,
			emp.default_shift AS default_shift,
			sha.shift_type AS shift_type,
			DATE_FORMAT(chec.time, '%%Y-%%m-%%d') AS date,
			MIN(DATE_FORMAT(chec.time, '%%T')) AS checkin_time,
			MAX(chec.offshift) AS offshift
		FROM `tabEmployee Checkin` chec
			INNER JOIN `tabEmployee` emp ON emp.name = chec.employee
			LEFT JOIN `tabShift Assignment` sha ON chec.employee = sha.employee 
			AND sha.docstatus = 1
			AND DATE(chec.time) >= sha.start_date
			AND (sha.end_date IS NULL OR DATE(chec.time) <= sha.end_date)
		WHERE chec.log_type = 'IN' {conditions}
		GROUP BY chec.employee, DATE(chec.time)
		ORDER BY DATE(chec.time) ASC, chec.employee ASC
		""",
		filters,
		as_dict=1,
	)


def get_emp_checkout_details(filters):
	conditions = get_emp_conditions(filters)
	return frappe.db.sql(
		f"""
		SELECT 
			chec.employee AS person,
			chec.employee_name AS person_name,
			'Employee' AS person_type,
			emp.company AS company,
			emp.department AS department_or_category,
			emp.default_shift AS default_shift,
			sha.shift_type AS shift_type,
			DATE_FORMAT(chec.time, '%%Y-%%m-%%d') AS date,
			MAX(DATE_FORMAT(chec.time, '%%T')) AS checkout_time,
			MAX(chec.offshift) AS offshift
		FROM `tabEmployee Checkin` chec
			INNER JOIN `tabEmployee` emp ON emp.name = chec.employee
			LEFT JOIN `tabShift Assignment` sha ON chec.employee = sha.employee 
			AND sha.docstatus = 1
			AND DATE(chec.time) >= sha.start_date
			AND (sha.end_date IS NULL OR DATE(chec.time) <= sha.end_date)
		WHERE chec.log_type = 'OUT' {conditions}
		GROUP BY chec.employee, DATE(chec.time)
		ORDER BY DATE(chec.time) ASC, chec.employee ASC
		""",
		filters,
		as_dict=1,
	)


def get_emp_conditions(filters):
	conditions = ""
	if filters.get("from_datetime"):
		conditions += " AND chec.time >= %(from_datetime)s"
	elif filters.get("from_date"):
		conditions += " AND DATE(chec.time) >= %(from_date)s"

	if filters.get("to_datetime"):
		conditions += " AND chec.time <= %(to_datetime)s"
	elif filters.get("to_date"):
		conditions += " AND DATE(chec.time) <= %(to_date)s"

	if filters.get("from_time"):
		conditions += " AND TIME(chec.time) >= %(from_time)s"
	if filters.get("to_time"):
		conditions += " AND TIME(chec.time) <= %(to_time)s"

	if filters.get("company"):
		conditions += " AND emp.company = %(company)s"

	dept_list = get_filter_list(filters, "department")
	if dept_list:
		from frappe.utils.nestedset import get_descendants_of
		all_depts = set()
		for d in dept_list:
			all_depts.add(d)
			descendants = get_descendants_of("Department", d) or []
			all_depts.update(descendants)
		conditions += build_in_condition("emp.department", list(all_depts))

	emp_list = get_filter_list(filters, "employee")
	if emp_list:
		conditions += build_in_condition("chec.employee", emp_list)

	if filters.get("off_shift"):
		conditions += " AND chec.offshift = 1"
	return conditions


def get_sub_checkin_details(filters):
	conditions = get_sub_conditions(filters)
	return frappe.db.sql(
		f"""
		SELECT 
			chec.sub_contractor AS person,
			chec.sub_contractor_name AS person_name,
			'Sub Contractor' AS person_type,
			sc.company AS company,
			COALESCE(chec.sub_contractor_category, sc.sub_contractor_category) AS department_or_category,
			sc.default_shift AS default_shift,
			sha.shift_type AS shift_type,
			DATE_FORMAT(chec.time, '%%Y-%%m-%%d') AS date,
			MIN(DATE_FORMAT(chec.time, '%%T')) AS checkin_time,
			MAX(chec.offshift) AS offshift
		FROM `tabSub Contractor Checkin` chec
			INNER JOIN `tabSub Contractor` sc ON sc.name = chec.sub_contractor
			LEFT JOIN `tabSub Contractor Shift Assignment` sha ON chec.sub_contractor = sha.sub_contractor 
			AND sha.status = 'Active'
			AND DATE(chec.time) >= sha.start_date
			AND (sha.end_date IS NULL OR DATE(chec.time) <= sha.end_date)
		WHERE chec.log_type = 'IN' {conditions}
		GROUP BY chec.sub_contractor, DATE(chec.time)
		ORDER BY DATE(chec.time) ASC, chec.sub_contractor ASC
		""",
		filters,
		as_dict=1,
	)


def get_sub_checkout_details(filters):
	conditions = get_sub_conditions(filters)
	return frappe.db.sql(
		f"""
		SELECT 
			chec.sub_contractor AS person,
			chec.sub_contractor_name AS person_name,
			'Sub Contractor' AS person_type,
			sc.company AS company,
			COALESCE(chec.sub_contractor_category, sc.sub_contractor_category) AS department_or_category,
			sc.default_shift AS default_shift,
			sha.shift_type AS shift_type,
			DATE_FORMAT(chec.time, '%%Y-%%m-%%d') AS date,
			MAX(DATE_FORMAT(chec.time, '%%T')) AS checkout_time,
			MAX(chec.offshift) AS offshift
		FROM `tabSub Contractor Checkin` chec
			INNER JOIN `tabSub Contractor` sc ON sc.name = chec.sub_contractor
			LEFT JOIN `tabSub Contractor Shift Assignment` sha ON chec.sub_contractor = sha.sub_contractor 
			AND sha.status = 'Active'
			AND DATE(chec.time) >= sha.start_date
			AND (sha.end_date IS NULL OR DATE(chec.time) <= sha.end_date)
		WHERE chec.log_type = 'OUT' {conditions}
		GROUP BY chec.sub_contractor, DATE(chec.time)
		ORDER BY DATE(chec.time) ASC, chec.sub_contractor ASC
		""",
		filters,
		as_dict=1,
	)


def get_sub_conditions(filters):
	conditions = ""
	if filters.get("from_datetime"):
		conditions += " AND chec.time >= %(from_datetime)s"
	elif filters.get("from_date"):
		conditions += " AND DATE(chec.time) >= %(from_date)s"

	if filters.get("to_datetime"):
		conditions += " AND chec.time <= %(to_datetime)s"
	elif filters.get("to_date"):
		conditions += " AND DATE(chec.time) <= %(to_date)s"

	if filters.get("from_time"):
		conditions += " AND TIME(chec.time) >= %(from_time)s"
	if filters.get("to_time"):
		conditions += " AND TIME(chec.time) <= %(to_time)s"

	if filters.get("company"):
		conditions += " AND sc.company = %(company)s"

	cat_list = get_filter_list(filters, "sub_contractor_category")
	if cat_list:
		conditions += build_in_condition("COALESCE(chec.sub_contractor_category, sc.sub_contractor_category)", cat_list)

	sub_list = get_filter_list(filters, "sub_contractor")
	if sub_list:
		conditions += build_in_condition("chec.sub_contractor", sub_list)

	if filters.get("off_shift"):
		conditions += " AND chec.offshift = 1"
	return conditions


def get_report_summary(data):
	emp_in = sum(1 for r in data if r.get("person_type") == "Employee" and r.get("checkin_time"))
	emp_out = sum(1 for r in data if r.get("person_type") == "Employee" and r.get("checkout_time"))
	sub_in = sum(1 for r in data if r.get("person_type") == "Sub Contractor" and r.get("checkin_time"))
	sub_out = sum(1 for r in data if r.get("person_type") == "Sub Contractor" and r.get("checkout_time"))
	total_in = emp_in + sub_in
	total_out = emp_out + sub_out

	return [
		{
			"value": emp_in,
			"indicator": "Blue",
			"label": _("Emp IN"),
			"datatype": "Int",
		},
		{
			"value": emp_out,
			"indicator": "Cyan",
			"label": _("Emp OUT"),
			"datatype": "Int",
		},
		{
			"value": sub_in,
			"indicator": "Green",
			"label": _("SubContractor IN"),
			"datatype": "Int",
		},
		{
			"value": sub_out,
			"indicator": "Orange",
			"label": _("SubContractor OUT"),
			"datatype": "Int",
		},
		{
			"value": f"{total_in} / {total_out}",
			"indicator": "Dark",
			"label": _("Total IN / OUT"),
			"datatype": "Data",
		},
	]


@frappe.whitelist()
def get_merged_checkin_summary(filters=None):
	if isinstance(filters, str):
		try:
			filters = json.loads(filters)
		except Exception:
			filters = {}
	filters = filters or {}

	data = get_data(filters)

	emp_in = sum(1 for r in data if r.get("person_type") == "Employee" and r.get("checkin_time"))
	emp_out = sum(1 for r in data if r.get("person_type") == "Employee" and r.get("checkout_time"))

	sub_in = sum(1 for r in data if r.get("person_type") == "Sub Contractor" and r.get("checkin_time"))
	sub_out = sum(1 for r in data if r.get("person_type") == "Sub Contractor" and r.get("checkout_time"))

	return {
		"emp_in": emp_in,
		"emp_out": emp_out,
		"sub_in": sub_in,
		"sub_out": sub_out,
		"total_in": emp_in + sub_in,
		"total_out": emp_out + sub_out,
	}
