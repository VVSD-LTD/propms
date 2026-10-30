import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"fieldname": "customer",
			"label": _("Customer"),
			"fieldtype": "Link",
			"options": "Customer",
			"width": 200,
		},
		{
			"fieldname": "tin",
			"label": _("TIN"),
			"fieldtype": "Data",
			"width": 80,
		},
		{
			"fieldname": "credit_note_no",
			"label": _("Credit Note No"),
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 150,
		},
		{
			"fieldname": "cn_amount_excl_vat",
			"label": _("Amount Excl. Vat"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "cn_exempt",
			"label": _("Exempt"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "cn_vat",
			"label": _("Vat"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "cn_total_amount",
			"label": _("Total Amount"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "corresponding_invoice_no",
			"label": _("Corresponding Invoice No"),
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 150,
		},
		{
			"fieldname": "vfd_rctnum",
			"label": _("VFD Receipt No"),
			"fieldtype": "Data",
			"width": 140,
		},
		{
			"fieldname": "invoice_date",
			"label": _("Invoice Date"),
			"fieldtype": "Date",
			"width": 100,
		},
		{
			"fieldname": "inv_amount_excl_vat",
			"label": _("Amount Excl. Vat"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "inv_exempt",
			"label": _("Exempt"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "inv_vat",
			"label": _("Vat"),
			"fieldtype": "Currency",
			"width": 120,
		},
		{
			"fieldname": "inv_total_amount",
			"label": _("Total Amount"),
			"fieldtype": "Currency",
			"width": 120,
		},
	]


def get_data(filters):
	return frappe.db.sql(
		"""
		SELECT
			si.customer,
			c.tax_id AS tin,
			si.name AS credit_note_no,
			si.base_total_taxes_and_charges / 0.18 AS cn_amount_excl_vat,
			si.base_grand_total - (
				si.base_total_taxes_and_charges + (si.base_total_taxes_and_charges / 0.18)
			) AS cn_exempt,
			si.base_total_taxes_and_charges AS cn_vat,
			si.base_grand_total AS cn_total_amount,
			si.return_against AS corresponding_invoice_no,
			si2.vfd_rctnum,
			si2.posting_date AS invoice_date,
			si2.base_total_taxes_and_charges / 0.18 AS inv_amount_excl_vat,
			si2.base_grand_total - (
				si2.base_total_taxes_and_charges + (si2.base_total_taxes_and_charges / 0.18)
			) AS inv_exempt,
			si2.base_total_taxes_and_charges AS inv_vat,
			si2.base_grand_total AS inv_total_amount
		FROM `tabSales Invoice` si
		JOIN `tabSales Invoice` si2 ON si.return_against = si2.name
		LEFT JOIN `tabCustomer` c ON si.customer = c.name
		WHERE si.status = 'Return'
			AND si.is_return = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
		ORDER BY si.posting_date, si.name
		""",
		{
			"from_date": filters.get("from_date"),
			"to_date": filters.get("to_date"),
		},
		as_dict=1,
	)
