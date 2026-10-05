import frappe


def execute():
	if not frappe.db.exists("DocType", "Amenity"):
		return
	if not frappe.db.has_column("Amenity", "auto_approval"):
		frappe.reload_doc("Property Management Solution", "doctype", "amenity")
	if frappe.db.has_column("Amenity", "requires_approval"):
		frappe.db.sql(
			"""
			UPDATE `tabAmenity`
			SET auto_approval = CASE
				WHEN IFNULL(requires_approval, 0) = 0 THEN 1
				ELSE 0
			END
			"""
		)
	if frappe.db.has_column("Amenity", "requires_approval"):
		# sql_ddl commits first so ALTER does not hit implicit-commit guards
		frappe.db.sql_ddl("ALTER TABLE `tabAmenity` DROP COLUMN `requires_approval`")
