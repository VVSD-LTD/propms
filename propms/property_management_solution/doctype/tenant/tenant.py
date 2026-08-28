 # Copyright (c) 2026, VV Systems Developer LTD and contributors
 # For license information, please see license.txt

import frappe
from frappe.model.document import Document
from propms.property_management_solution.doctype.lease.lease import (
	get_tenant_context_for_user,
)


class Tenant(Document):
	pass


def _set_lease_and_property(doc):
	"""Auto-set textual leases/properties from active/vacating leases for the customer.

	If there are multiple, join them as comma-separated text. Fields are
	informational only (Small Text), not links.
	"""
	if not getattr(doc, "customer", None):
		return

	# Always recompute text fields from current leases
	leases = frappe.get_all(
		"Lease",
		filters={
			"lease_customer": doc.customer,
			"lease_status": ["in", ["Active", "Vacating"]],
		},
		fields=["name", "property", "lease_status", "start_date"],
		order_by="start_date asc",
	)
	if not leases:
		return

	lease_names = [l.get("name") for l in leases if l.get("name")]
	property_names = [l.get("property") for l in leases if l.get("property")]
	doc.lease = ", ".join(sorted(set(lease_names)))
	doc.property = ", ".join(sorted(set(property_names)))


@frappe.whitelist()
def get_primary_lease_for_customer(customer):
	"""Return textual leases and properties for a customer."""
	if not customer:
		return {}

	leases = frappe.get_all(
		"Lease",
		filters={
			"lease_customer": customer,
			"lease_status": ["in", ["Active", "Vacating"]],
		},
		fields=["name", "property", "lease_status", "start_date"],
		order_by="start_date asc",
	)
	if not leases:
		return {}

	lease_names = [l.get("name") for l in leases if l.get("name")]
	property_names = [l.get("property") for l in leases if l.get("property")]
	return {
		"lease": ", ".join(sorted(set(lease_names))),
		"property": ", ".join(sorted(set(property_names))),
	}


def before_insert(doc, method=None):
	"""Keep legacy Tenant behavior limited to informational fields."""
	_set_lease_and_property(doc)


def validate(doc, method=None):
	"""Keep legacy Tenant behavior limited to informational fields."""
	_set_lease_and_property(doc)


def after_insert(doc, method=None):
	"""Deprecated: tenant-user provisioning now runs from Lease hooks."""
	return


def on_update(doc, method=None):
	"""Deprecated: tenant-user provisioning now runs from Lease hooks."""
	return


@frappe.whitelist()
def get_properties_for_customer(customer):
 	"""Return list of Property names linked to active leases for the given Customer.
 	Used by the Tenant form to filter the Property link field."""
 	if not customer:
 		return []
 	names = frappe.get_all(
 		"Lease",
 		filters={
 			"lease_customer": customer,
 			"lease_status": ["in", ["Active", "Vacating"]],
 		},
 		pluck="property",
 		distinct=True,
 	)
 	return names or []