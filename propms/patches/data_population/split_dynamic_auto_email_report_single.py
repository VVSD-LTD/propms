# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

"""Turn the Dynamic Auto Email Report single into one document per schedule.

Filters used to live on the single and were shared by report name. Each old
schedule row becomes its own document and receives a copy of the filters for
its report, so later edits no longer change the other copy.
"""

import frappe
from frappe.utils import cint

PARENT = "Dynamic Auto Email Report"
SCHEDULE_FIELDS = (
	"enabled",
	"dont_send_if_no_data",
	"report",
	"format",
	"frequency",
	"day_of_week",
	"day_of_month",
	"send_time",
	"recipients",
	"cc",
	"bcc",
	"subject",
	"message",
	"last_sent_at",
)


def execute():
	if not frappe.db.table_exists("Dynamic Auto Email Report"):
		return
	if not frappe.db.table_exists("Attendance Report Schedule"):
		return
	if cint(frappe.db.get_value("DocType", PARENT, "issingle")):
		return

	schedules = frappe.db.sql(
		f"""
		SELECT name, {", ".join(SCHEDULE_FIELDS)}
		FROM `tabAttendance Report Schedule`
		WHERE parent = %s AND parenttype = %s
		ORDER BY idx
		""",
		(PARENT, PARENT),
		as_dict=True,
	)
	if not schedules:
		_clear_single_shell()
		return

	filters_by_report = _filters_by_report()
	migrated = []
	for row in schedules:
		if not row.report or not row.recipients:
			frappe.log_error(
				title="Dynamic Auto Email Report migration skipped a row",
				message=f"Schedule {row.name} is missing a report or recipients.",
			)
			continue
		if frappe.db.exists(PARENT, {"legacy_schedule": row.name}):
			migrated.append(row.name)
			continue

		doc = frappe.get_doc(
			{
				"doctype": PARENT,
				"naming_series": "DAER-.YYYY.-",
				"legacy_schedule": row.name,
				**{field: row.get(field) for field in SCHEDULE_FIELDS},
				"report_filters": filters_by_report.get(row.report, []),
			}
		)
		doc.insert(ignore_permissions=True, ignore_links=True)
		migrated.append(row.name)

	if len(migrated) != len(schedules):
		return

	frappe.db.sql(
		"DELETE FROM `tabAttendance Report Schedule` WHERE parent = %s AND parenttype = %s",
		(PARENT, PARENT),
	)
	if frappe.db.table_exists("Attendance Report Filter"):
		frappe.db.sql(
			"DELETE FROM `tabAttendance Report Filter` WHERE parent = %s AND parenttype = %s",
			(PARENT, PARENT),
		)
	_clear_single_shell()


def _filters_by_report():
	if not frappe.db.table_exists("Attendance Report Filter"):
		return {}
	if not frappe.db.has_column("Attendance Report Filter", "report"):
		return {}

	grouped = {}
	for row in frappe.db.sql(
		"""
		SELECT report, fieldname, raw_fieldname, value
		FROM `tabAttendance Report Filter`
		WHERE parent = %s AND parenttype = %s
		ORDER BY idx
		""",
		(PARENT, PARENT),
		as_dict=True,
	):
		if not row.report or not row.fieldname:
			continue
		grouped.setdefault(row.report, []).append(
			{
				"fieldname": row.fieldname,
				"raw_fieldname": row.raw_fieldname,
				"value": row.value,
			}
		)
	return grouped


def _clear_single_shell():
	if frappe.db.table_exists("Singles"):
		frappe.db.delete("Singles", {"doctype": PARENT})
