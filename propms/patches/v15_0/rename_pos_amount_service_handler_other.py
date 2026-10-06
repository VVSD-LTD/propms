# -*- coding: utf-8 -*-
"""Rename POS Amount Service handler Generic → Other (Service type)."""

from __future__ import unicode_literals

import frappe


def execute():
	if not frappe.db.exists("DocType", "POS Amount Service"):
		return
	if not frappe.db.has_column("POS Amount Service", "handler"):
		return
	frappe.db.sql(
		"""
		update `tabPOS Amount Service`
		set handler = 'Other'
		where ifnull(handler, '') in ('Generic', '')
		"""
	)
	frappe.clear_cache(doctype="POS Amount Service")
