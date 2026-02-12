import frappe

def execute():
    # Prepare Sample data for Property Status
    property_status_list = [
        "Available",
        "Booked",
        "Common Area (Not for lease)",
        "Managed for Customer",
        "Off Lease in 3 Months",
        "On Lease",
        "On Sale",
        "Removed",
        "Renewal",
        "Sold",
        "Vacating",
        "In Renovation"
    ]
    
    for status in property_status_list:
        if not frappe.db.exists("Property Status", status):
            frappe.get_doc({
                "doctype": "Property Status",
                "status_name": status
            }).insert()

    # Data for Property Floors
    floor_list = [
        "BASEMENT",
        "GROUND FLOOR",
        "1ST FLOOR",
        "2ND FLOOR",
        "3RD FLOOR",
        "4TH FLOOR",
        "5TH FLOOR",
        "6TH FLOOR",
        "7TH FLOOR",
        "8TH FLOOR",
        "9TH FLOOR",
        "10TH FLOOR",
        "11TH FLOOR",
        "12TH FLOOR",
        "13TH FLOOR",
        "14TH FLOOR",
        "15TH FLOOR",
        "16TH FLOOR",
        "17TH FLOOR",
        "18TH FLOOR",
        "19TH FLOOR",
        "20TH FLOOR",
        "21ST FLOOR",
        "22ND FLOOR"
    ]
    for floor in floor_list:
        if not frappe.db.exists("Property Floor", floor):
            frappe.get_doc({
                "doctype": "Property Floor",
                "floor_name": floor
            }).insert()

    # Agreement Status
    agreement_status_list = [
        "0 - Pending Delivery to Tenant",
        "1 - Delivered to Tenant",
        "2 - Received from Tenant Signed",
        "3 - Signed by Landlord",
        "4.1 - Agreement Returned to Tenant for Stamp Duty",
        "4.2 - A Copy of Agreement received with Stamp Duty Stamp",
        "5.1 - Agreement Sent for Stamp Duty Assessment",
        "5.2 - Tenant Informed to Pay Stamp Duty Amount",
        "5.3 - Stamp Duty Payment Document Received from Tenant",
        "5.4 - Agreement Sent to Authorities for Stamp Duty Stamp",
        "5.5 - A Copy of Agreement sent to Tenant with Stamp Duty",
        "6 - Agreement Copy Filed"
    ]
    for status in agreement_status_list:
        if not frappe.db.exists("Agreement Status", status):
            frappe.get_doc({
                "doctype": "Agreement Status",
                "agreement_status_name": status
            }).insert()

    # Agreement Addendum Status
    addendum_status_list = [
        "0 - Pending Delivery to Tenant",
        "1 - Delivered to Tenant",
        "2 - Received from Tenant Signed",
        "3 - Signed by Landlord",
        "4 - A Copy of Addendum sent to Tenant",
        "5 - Addendum Copy Filed"
    ]
    for status in addendum_status_list:
        if not frappe.db.exists("Agreement Addendum Status", status):
            frappe.get_doc({
                "doctype": "Agreement Addendum Status",
                "agreement_addendum_status_name": status
            }).insert()

    # Staff Types
    staff_type_list = [
        "Spik n Span",
        "Viva Staff",
        "Viva Security",
        "Consultant",
        "Others"
    ]
    for staff_type in staff_type_list:
        if not frappe.db.exists("Staff Type", staff_type):
            frappe.get_doc({
                "doctype": "Staff Type",
                "staff_type_name": staff_type
            }).insert()