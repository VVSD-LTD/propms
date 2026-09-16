# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

"""Mark legacy Property Management Email Setting rows as Pre-Due.

Before reminder_type existed, all live settings were Pre-Due reminders
(PMES-2026-000001 .. 000006). This patch only updates rows that already
exist on the site — it does not create missing records or overwrite
payment_term / days_due / subject / body / print_format / letter_head.
"""

import frappe

# Known live Pre-Due settings from before the reminder_type redesign
LEGACY_PRE_DUE_SETTINGS = (
	"PMES-2026-000001",
	"PMES-2026-000002",
	"PMES-2026-000003",
	"PMES-2026-000004",
	"PMES-2026-000005",
	"PMES-2026-000006",
)

# Clear fields that only apply to After Overdue / Withholding Tax.
# Keep Int defaults non-null for DB columns that disallow NULL.
SCHEDULE_AND_TYPED_FIELDS = {
	"reminder_type": "Pre-Due",
	"days_after_overdue": 1,
	"use_penalty_grace_period": 0,
	"overdue_condition": "",
	"wh_date_basis": "",
	"wh_condition": "",
	"frequency": "",
	"weekday": "",
	"day_of_month": 0,
	"use_penalty_exclusion_calendar": 0,
}


def execute():
	names = set(LEGACY_PRE_DUE_SETTINGS)

	# Also catch any other pre-redesign rows that still have blank reminder_type
	blank = frappe.db.sql(
		"""
		SELECT name
		FROM `tabProperty Management Email Setting`
		WHERE IFNULL(reminder_type, '') = ''
		"""
	)
	names.update(row[0] for row in blank)

	for name in sorted(names):
		_set_pre_due_if_exists(name)


def _set_pre_due_if_exists(name):
	if not frappe.db.exists("Property Management Email Setting", name):
		return

	current = frappe.db.get_value(
		"Property Management Email Setting",
		name,
		["reminder_type", "days_due"],
		as_dict=True,
	)
	if not current:
		return

	# Never reclassify After Overdue / Withholding — even if name matches a
	# historical Pre-Due ID (other sites may have reused naming series).
	if current.reminder_type in ("After Overdue", "Withholding Tax"):
		return

	values = dict(SCHEDULE_AND_TYPED_FIELDS)

	# Pre-Due requires days_due; keep existing value, default 0 only if missing
	if current.days_due is None:
		values["days_due"] = 0

	frappe.db.set_value(
		"Property Management Email Setting",
		name,
		values,
		update_modified=False,
	)

	# Pre-Due does not use exclusion calendar child rows
	frappe.db.delete(
		"Sales Invoice Penalty Excluded Day",
		{"parent": name, "parenttype": "Property Management Email Setting"},
	)
