# Copyright (c) 2026, Aakvatech and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PropertyInstalledEquipment(Document):
	pass

@frappe.whitelist()
def enable_equipment(equipment_detail):
    frappe.db.set_value("Property Installed Equipment Detail", equipment_detail, {
        "enabled": 1,
        "parentfield": "table_5"
    })
    return {"status": "success", "message": "Enabled"}

@frappe.whitelist()
def disable_equipment(equipment_detail):
    frappe.db.set_value("Property Installed Equipment Detail", equipment_detail, {
        "enabled": 0,
        "parentfield": "disabled_equipment"
    })
    return {"status": "success", "message": "Disabled"}