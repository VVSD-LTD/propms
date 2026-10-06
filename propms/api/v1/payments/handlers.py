# -*- coding: utf-8 -*-
"""Payment success handlers keyed by payment_workflow."""

from __future__ import unicode_literals

from propms.api.v1.payments.workflows import (
    WORKFLOW_SALES_INVOICE_PAYMENT,
    WORKFLOW_SALES_ORDER_POS,
    WORKFLOW_ELECTRICITY_POS,
    WORKFLOW_MAINTENANCE_POS,
    WORKFLOW_AMOUNT_POS,
)
from propms.api.v1.payments.handlers_invoice import settle_sales_invoice_payment
from propms.api.v1.payments.handlers_sales_order_pos import settle_sales_order_pos
from propms.api.v1.payments.handlers_electricity_pos import settle_electricity_pos
from propms.api.v1.payments.handlers_maintenance_pos import settle_maintenance_pos
from propms.api.v1.payments.handlers_amount_pos import settle_amount_pos

SUCCESS_HANDLERS = {
    WORKFLOW_SALES_INVOICE_PAYMENT: settle_sales_invoice_payment,
    WORKFLOW_SALES_ORDER_POS: settle_sales_order_pos,
    WORKFLOW_ELECTRICITY_POS: settle_electricity_pos,
    WORKFLOW_MAINTENANCE_POS: settle_maintenance_pos,
    WORKFLOW_AMOUNT_POS: settle_amount_pos,
}
