# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from . import __version__ as app_version

app_name = "propms"
app_title = "Property Management Solution"
app_publisher = "VV Systems Developer LTD"
app_description = "Property Management Solution"
app_icon = "octicon octicon-home"
app_color = "grey"
app_email = "info@vvsdtz.com"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/propms/css/propms.css"
# app_include_js = "/assets/propms/js/propms.js"

# include js, css files in header of web template
# web_include_css = "/assets/propms/css/propms.css"
# web_include_js = "/assets/propms/js/propms.js"

# include js in page
# page_js = {"page" : "public/js/file.js"}
page_js = {
    "pos": "property_management_solution/point_of_sale.js",
    "point-of-sale": "property_management_solution/point_of_sale.js",
}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
doctype_js = {
    "Sales Invoice": "property_management_solution/sales_invoice.js",
    "Journal Entry Account": "property_management_solution/journal_entry_account.js",
    "Issue": [
        "property_management_solution/issue.js",
        "public/js/maintenance_jobcard.js",
        "public/js/maintenance_jobcard_sub_contractor.js",
    ],
    "Company": "property_management_solution/company.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Website user home page (by function)
# get_website_user_home_page = "propms.utils.get_home_page"

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------

# before_install = "propms.install.before_install"
after_install = [
    "propms.utils.create_custom_fields.execute",
    "propms.utils.create_property_setter.execute",
    "propms.api.v1.payments.setup.setup_selcom_defaults",
]

after_migrate = [
    "propms.utils.create_custom_fields.execute",
    "propms.utils.create_property_setter.execute",
    "propms.api.v1.payments.setup.setup_selcom_defaults",
]

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "propms.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# Fixtures removed - Custom Fields and Property Setters are now managed through
# JSON files in patches/ directory and created via utility functions in after_install/after_migrate hooks


doc_events = {
    "Lease": {
        "before_insert": "propms.custom.lease.lease_before_insert",
        "validate": "propms.custom.lease.lease_validate",
        "after_insert": "propms.custom.lease.lease_after_insert",
        "on_update": "propms.custom.lease.lease_on_update",
    },
    "Tenant": {
        "before_insert": "propms.property_management_solution.doctype.tenant.tenant.before_insert",
        "validate": "propms.property_management_solution.doctype.tenant.tenant.validate",
        "after_insert": "propms.property_management_solution.doctype.tenant.tenant.after_insert",
        "on_update": "propms.property_management_solution.doctype.tenant.tenant.on_update",
    },
    "Maintenance Users": {
        "before_insert": "propms.property_management_solution.doctype.maintenance_users.maintenance_users.before_insert",
        "validate": "propms.property_management_solution.doctype.maintenance_users.maintenance_users.validate",
        "after_insert": "propms.property_management_solution.doctype.maintenance_users.maintenance_users.after_insert",
        "on_update": "propms.property_management_solution.doctype.maintenance_users.maintenance_users.on_update",
    },
    "Sub Contractor User": {
        "before_insert": "propms.property_management_solution.doctype.sub_contractor_user.sub_contractor_user.before_insert",
        "validate": "propms.property_management_solution.doctype.sub_contractor_user.sub_contractor_user.validate",
        "after_insert": "propms.property_management_solution.doctype.sub_contractor_user.sub_contractor_user.after_insert",
        "on_update": "propms.property_management_solution.doctype.sub_contractor_user.sub_contractor_user.on_update",
    },
    "Issue": {
        "validate": [
            "propms.issue_hook.validate",
        ],
        "after_insert": [
            "propms.issue_hook.after_insert",
        ],
    },
    "Material Request": {
        "validate": "propms.auto_custom.makeSalesInvoice",
        "on_update": "propms.auto_custom.makeSalesInvoice",
        "on_change": "propms.auto_custom.makeSalesInvoice",
    },
    "Sales Order": {
        "validate": "propms.auto_custom.validateSalesInvoiceItemDuplication"
    },
    "Key Set Detail": {"on_change": "propms.auto_custom.changeStatusKeyset"},
    "Meter Reading": {"on_submit": "propms.auto_custom.make_invoice_meter_reading"},
    "Sales Invoice": {
        "before_save": "propms.custom.custom.before_save",
        "on_submit": "propms.api.v1.invoices.invoices.on_sales_invoice_submit",
    },
    "Payment Entry": {
        "on_submit": "propms.api.v1.invoices.invoices.on_payment_entry_submit",
    },
}


scheduler_events = {
    "daily": [
        "propms.auto_custom.statusChangeBeforeLeaseExpire",
        "propms.auto_custom.statusChangeAfterLeaseExpire",
        "propms.api.v1.gate_pass.gate_pass.auto_expire_overdue_passes",
    ],
    "cron": {
        # "00 12 * * *": ["propms.lease_invoice.leaseInvoiceAutoCreate"],
        "00 02 * * *": [
            "propms.custom.custom.create_maintenance_job_card",
            "propms.custom.custom.get_overdue_sales_invoices",
        ],
        "50 23 * * *": [
            "propms.custom.sales_invoice_penalty.process_daily_sales_invoice_penalties",
        ],
        "00 00 * * *": ["propms.lease_invoice_schedule.make_lease_invoice_schedule"],
        "00 12 * * *": ["propms.lease_invoice.enqueue_lease_invoice_auto_create"],
        "*/5 * * * *": ["propms.property_management_solution.doctype.attendance_settings.attendance_settings.send_scheduled_reports"],
        # "*/1 * * * *": ["propms.api.v1.payments.services.auto_reconcile_pending_payments"],
    }
}


# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"propms.tasks.all"
# 	],
# 	"daily": [
# 		"propms.tasks.daily"
# 	],
# 	"hourly": [
# 		"propms.tasks.hourly"
# 	],
# 	"weekly": [
# 		"propms.tasks.weekly"
# 	]
# 	"monthly": [
# 		"propms.tasks.monthly"
# 	]
# }

# Testing
# -------

# before_tests = "propms.install.before_tests"

# Overriding Whitelisted Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "propms.event.get_events"
# }
