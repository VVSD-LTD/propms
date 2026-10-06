# -*- coding: utf-8 -*-
"""Selcom payment workflow registry.

Each workflow defines how a successful Selcom payment is settled in ERPNext.
Existing invoice payments keep using Payment Entry. Cart-style checkouts
(e.g. Water) can submit a Sales Order and create a paid POS Sales Invoice
instead — without touching the invoice Payment Entry path.

Add new workflows here (e.g. Electricity) without changing the Selcom client
or IPN webhook entrypoint.
"""

from __future__ import unicode_literals

import frappe
from frappe import _

# Stable workflow keys stored on Selcom Payment Transaction Log.payment_workflow
WORKFLOW_SALES_INVOICE_PAYMENT = "sales_invoice_payment"
WORKFLOW_SALES_ORDER_POS = "sales_order_pos"
WORKFLOW_ELECTRICITY_POS = "electricity_pos"
WORKFLOW_MAINTENANCE_POS = "maintenance_pos"

# Human-friendly labels for Desk
WORKFLOW_LABELS = {
    WORKFLOW_SALES_INVOICE_PAYMENT: "Sales Invoice Payment",
    WORKFLOW_SALES_ORDER_POS: "Sales Order POS Checkout",
    WORKFLOW_ELECTRICITY_POS: "Electricity POS Checkout",
    WORKFLOW_MAINTENANCE_POS: "Maintenance POS Checkout",
}


def get_success_handler(payment_workflow):
    """Return the callable that settles a successful Selcom payment."""
    from propms.api.v1.payments import handlers

    workflow = (payment_workflow or WORKFLOW_SALES_INVOICE_PAYMENT).strip()
    handler = handlers.SUCCESS_HANDLERS.get(workflow)
    if not handler:
        # Safe fallback: preserve legacy invoice Payment Entry behaviour
        handler = handlers.SUCCESS_HANDLERS[WORKFLOW_SALES_INVOICE_PAYMENT]
    return handler


def get_mobile_cart_pos_profile_name():
    """Resolve POS Profile for mobile cart checkouts from Selcom Settings.

    Accountants configure and name this profile in Desk — code never hardcodes the name.
    """
    profile = frappe.db.get_single_value("Selcom Settings", "mobile_cart_pos_profile")
    if not profile:
        frappe.throw(
            _(
                "Please set <b>Mobile Cart POS Profile</b> in "
                "<a href='/app/selcom-settings'>Selcom Settings</a>."
            ),
            title=_("POS Profile Not Configured"),
        )
    if not frappe.db.exists("POS Profile", profile):
        frappe.throw(
            _("Configured Mobile Cart POS Profile '{0}' does not exist.").format(profile),
            title=_("Invalid POS Profile"),
        )
    return profile
