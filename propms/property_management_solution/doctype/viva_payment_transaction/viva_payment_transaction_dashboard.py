# Copyright (c) 2026, VVSD and contributors
# For license information, please see license.txt


def get_data():
    return {
        "fieldname": "viva_payment_transaction",
        "internal_links": {
            "Sales Invoice": "sales_invoice",
            "Payment Entry": "payment_entry",
            "Customer": "customer",
        },
        "transactions": [
            {
                "label": "Accounting",
                "items": ["Sales Invoice", "Payment Entry"],
            },
            {
                "label": "Party",
                "items": ["Customer"],
            },
        ],
    }
