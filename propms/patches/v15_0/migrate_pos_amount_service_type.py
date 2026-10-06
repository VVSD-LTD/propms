# -*- coding: utf-8 -*-
"""Seed POS Amount Service 'Electricity' and retarget hub rows to Item|Amount."""

from __future__ import unicode_literals

import frappe


def execute():
	_ensure_electricity_amount_service()
	_retarget_hub_rows()


def _ensure_electricity_amount_service():
	if not frappe.db.exists("DocType", "POS Amount Service"):
		return
	if frappe.db.exists("POS Amount Service", "Electricity"):
		return
	doc = frappe.get_doc(
		{
			"doctype": "POS Amount Service",
			"title": "Electricity",
			"handler": "Electricity",
			"enabled": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.db.commit()


def _retarget_hub_rows():
	if not frappe.db.exists("DocType", "Mobile POS Store Service"):
		return
	if not frappe.db.exists("POS Amount Service", "Electricity"):
		return

	cols = {c[0] for c in frappe.db.sql("show columns from `tabMobile POS Store Service`")}
	has_amount_service = "amount_service" in cols
	has_special_key = "special_key" in cols

	# Rows that were Electricity / Special electricity → Amount + Electricity catalog
	if has_special_key:
		frappe.db.sql(
			"""
			update `tabMobile POS Store Service`
			set service_type='Amount',
			    purchase_mode='amount',
			    item=null
			    {amount_set}
			where service_type in ('Electricity', 'Special')
			   or (service_type='Amount' and ifnull(special_key,'') in ('electricity','Electricity'))
			""".format(
				amount_set=", amount_service='Electricity'" if has_amount_service else ""
			)
		)
	else:
		frappe.db.sql(
			"""
			update `tabMobile POS Store Service`
			set service_type='Amount',
			    purchase_mode='amount',
			    item=null
			    {amount_set}
			where service_type in ('Electricity', 'Special', 'Amount')
			  and (
			  	ifnull(label,'') in ('Electricity','electricity')
			  	or service_type='Electricity'
			  )
			""".format(
				amount_set=", amount_service='Electricity'" if has_amount_service else ""
			)
		)

	# Ensure current Electricity-labelled Amount rows have the link
	if has_amount_service:
		frappe.db.sql(
			"""
			update `tabMobile POS Store Service`
			set amount_service='Electricity', purchase_mode='amount'
			where service_type='Amount'
			  and ifnull(amount_service,'')=''
			  and ifnull(label,'') like '%%Electric%%'
			"""
		)

	frappe.db.commit()
