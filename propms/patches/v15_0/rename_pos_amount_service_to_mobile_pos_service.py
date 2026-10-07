# -*- coding: utf-8 -*-
"""Rename POS Amount Service → Mobile POS Service (and child table) before model sync."""

from __future__ import unicode_literals

import frappe


def execute():
	_rename_doctype("POS Amount Service Item", "Mobile POS Service Item")
	_rename_doctype("POS Amount Service", "Mobile POS Service")


def _rename_doctype(old, new):
	if frappe.db.exists("DocType", new):
		return
	if not frappe.db.exists("DocType", old):
		return
	frappe.rename_doc("DocType", old, new, force=True, show_alert=False)
