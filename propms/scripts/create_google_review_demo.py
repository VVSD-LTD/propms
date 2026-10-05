"""One-off: create Google Review demo Mobile VIVA tenant (demo@gmail.com)."""

import frappe
from frappe.utils import today, add_years
from frappe.utils.password import update_password


EMAIL = "demo@gmail.com"
PASSWORD = "Demo@Viva2026"
CUSTOMER_NAME = "Google Review"
FULL_NAME = "Google Review"


def execute():
	frappe.set_user("Administrator")

	customer = frappe.get_doc("Customer", CUSTOMER_NAME)
	print("customer", customer.name)

	if customer.meta.has_field("portal_users"):
		existing = [getattr(r, "user", None) for r in (customer.portal_users or [])]
		if EMAIL not in existing:
			customer.append("portal_users", {"user": EMAIL})
			customer.save(ignore_permissions=True)
			print("portal_users linked")

	if not frappe.db.exists("User", EMAIL):
		u = frappe.get_doc(
			{
				"doctype": "User",
				"email": EMAIL,
				"first_name": "Google",
				"last_name": "Review",
				"send_welcome_email": 0,
				"user_type": "Website User",
			}
		)
		u.insert(ignore_permissions=True)
		print("created user")
	else:
		u = frappe.get_doc("User", EMAIL)
		print("user exists", u.name, "enabled", u.enabled)

	u.enabled = 1
	u.save(ignore_permissions=True)
	existing_roles = set(frappe.get_roles(EMAIL))
	for role in ["Customer", "Tenant"]:
		if frappe.db.exists("Role", role) and role not in existing_roles:
			u.add_roles(role)
	update_password(EMAIL, PASSWORD)
	print("password set")

	occupied = {
		r[0]
		for r in frappe.db.sql(
			"SELECT property FROM `tabLease` WHERE lease_status='Active' AND IFNULL(property,'')!=''"
		)
	}
	prop = None
	for (pname,) in frappe.db.sql("SELECT name FROM `tabProperty` ORDER BY name"):
		if pname not in occupied:
			prop = pname
			break
	if not prop:
		raise Exception("No free property")
	print("property", prop)

	tpl_name = frappe.db.get_value("Lease", {"lease_status": "Active"}, "name")
	tpl = frappe.get_doc("Lease", tpl_name)
	print("template", tpl.name)

	pt_field = tpl.meta.get_field("payment_terms")
	print(
		"payment_terms field",
		None
		if not pt_field
		else (pt_field.fieldtype, pt_field.reqd, pt_field.options, getattr(tpl, "payment_terms", None)),
	)

	for old in frappe.get_all("Lease", filters={"lease_customer": CUSTOMER_NAME}, pluck="name"):
		print("deleting old lease", old)
		frappe.delete_doc("Lease", old, force=1, ignore_permissions=True)

	td_field = "custom_tenant_details" if tpl.meta.has_field("custom_tenant_details") else "tenant_details"
	print("td_field", td_field)

	def build_lease():
		lease = frappe.copy_doc(tpl)
		lease.name = None
		lease.property = prop
		lease.lease_customer = customer.name
		if lease.meta.has_field("customer"):
			lease.customer = customer.name
		lease.lease_status = "Active"
		lease.lease_date = today()
		lease.start_date = today()
		lease.end_date = add_years(today(), 1)
		lease.set(td_field, [])
		for row in lease.lease_item:
			if row.meta.has_field("paid_by"):
				if "Service Charge" in (row.lease_item or ""):
					row.paid_by = customer.name
				elif not row.paid_by:
					row.paid_by = customer.name
		lease.append(
			td_field,
			{
				"enabled": 1,
				"full_name": FULL_NAME,
				"user_email": EMAIL,
				"user_password": PASSWORD,
				"app_use_terms_and_conditions": 1,
				"is_first_login": 0,
				"user": EMAIL,
			},
		)
		return lease

	lease = build_lease()
	try:
		lease.insert(ignore_permissions=True)
	except frappe.MandatoryError as e:
		print("MandatoryError, retry ignore_mandatory:", e)
		lease = build_lease()
		lease.flags.ignore_mandatory = True
		lease.flags.ignore_permissions = True
		lease.insert(ignore_permissions=True)

	print("lease", lease.name)
	frappe.db.commit()

	from propms.api.v1.job_card.job_card import get_property, get_tenant_context_for_user
	from propms.custom.lease import get_customer_from_lease

	print("resolved customer", get_customer_from_lease(lease.name))
	frappe.set_user(EMAIL)
	print("ctx", get_tenant_context_for_user(EMAIL))
	try:
		print("props", get_property())
	except Exception as e:
		print("get_property err", type(e).__name__, e)
	frappe.set_user("Administrator")

	print("SI", frappe.db.count("Sales Invoice", {"customer": customer.name}))
	for dt in ["Issue", "Job Card"]:
		if frappe.db.exists("DocType", dt):
			meta = frappe.get_meta(dt)
			if meta.has_field("customer"):
				print(dt, frappe.db.count(dt, {"customer": customer.name}))
			else:
				print(dt, "no customer field")

	print("=== DEMO READY ===")
	print("email:", EMAIL)
	print("password:", PASSWORD)
	print("customer:", customer.name)
	print("lease:", lease.name)
	print("property:", prop)
