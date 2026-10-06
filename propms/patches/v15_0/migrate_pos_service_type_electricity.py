# -*- coding: utf-8 -*-
"""Migrate POS hub Special/electricity rows → Service Type Electricity."""

from __future__ import unicode_literals

import frappe


def execute():
	if not frappe.db.exists("DocType", "Mobile POS Store Service"):
		return

	# Prefer SQL so we catch legacy special_key even if column is dropped later
	cols = {c[0] for c in frappe.db.sql("show columns from `tabMobile POS Store Service`")}
	if "special_key" in cols:
		frappe.db.sql(
			"""
			update `tabMobile POS Store Service`
			set service_type='Electricity', purchase_mode='amount', item=null
			where (
				service_type='Special'
				and ifnull(special_key,'') in ('electricity', 'Electricity')
			) or service_type='Electricity'
			"""
		)
	else:
		frappe.db.sql(
			"""
			update `tabMobile POS Store Service`
			set service_type='Electricity', purchase_mode='amount', item=null
			where service_type in ('Special', 'Electricity')
			"""
		)

	# Drop obsolete special_key column values are unused; column may remain until next sync
	frappe.db.commit()
