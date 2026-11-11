# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

"""Rename the Attendance Settings single to Dynamic Auto Email Report.

Runs before model sync so migrate updates the renamed DocType instead of
creating a second one. Child rows keep the single's name in `parent`, and
Frappe's DocType rename does not change that value.
"""

import frappe
from frappe.model.rename_doc import rename_doc

OLD_NAME = "Attendance Settings"
NEW_NAME = "Dynamic Auto Email Report"
CHILD_TABLES = ("Attendance Report Schedule", "Attendance Report Filter")


def execute():
	if frappe.db.exists("DocType", OLD_NAME) and not frappe.db.exists("DocType", NEW_NAME):
		rename_doc(
			"DocType",
			OLD_NAME,
			NEW_NAME,
			force=True,
			ignore_permissions=True,
			show_alert=False,
			rebuild_search=False,
		)

	for child_doctype in CHILD_TABLES:
		move_child_parent(child_doctype)


def move_child_parent(child_doctype):
	if not frappe.db.table_exists(child_doctype):
		return

	table = frappe.qb.DocType(child_doctype)
	# Rows whose parenttype was not updated yet.
	(
		frappe.qb.update(table)
		.set(table.parent, NEW_NAME)
		.set(table.parenttype, NEW_NAME)
		.where((table.parent == OLD_NAME) & (table.parenttype == OLD_NAME))
	).run()
	# DocType rename updates parenttype and leaves parent on the old single name.
	(
		frappe.qb.update(table)
		.set(table.parent, NEW_NAME)
		.where((table.parent == OLD_NAME) & (table.parenttype == NEW_NAME))
	).run()
