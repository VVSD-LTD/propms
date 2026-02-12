from __future__ import unicode_literals
import frappe
from frappe.model.document import Document
from frappe.utils import add_days, today, getdate, add_months, get_datetime, now
from propms.auto_custom import app_error_log, makeInvoiceSchedule, getDateMonthDiff
from frappe import _


class Lease(Document):
    def on_submit(self):
        try:
            checklist_doc = frappe.get_doc("Checklist Checkup Area", "Handover")
            if checklist_doc:
                check_list = []
                for task in checklist_doc.task:
                    check = {}
                    check["checklist_task"] = task.task_name
                    check_list.append(check)

                frappe.get_doc(
                    dict(
                        doctype="Daily Checklist",
                        area="Handover",
                        checkup_date=self.start_date,
                        daily_checklist_detail=check_list,
                        property=self.property,
                    )
                ).insert()
        except Exception as e:
            app_error_log(frappe.session.user, str(e))

    def validate(self):
        try:
            # Validate lease item dates before any other validation
            self.validate_lease_item_dates()
            
            # Validate no date gaps in lease items
            self.validate_no_date_gaps()
            
            # Lease Status Validation: Prevent multiple active leases per property
            if self.lease_status == "Active":
                # Query for other non-draft leases for the same property
                conflicting_leases = frappe.db.get_all(
                    "Lease",
                    filters={
                        "property": self.property,
                        "lease_status": ["!=", "Draft"],
                        "name": ["!=", self.name],
                        "docstatus": ["<", 2],  # Exclude cancelled
                    },
                    fields=["name", "end_date", "lease_status"],
                )
                for lease in conflicting_leases:
                    # If end_date is blank or in the future, block activation
                    if not lease["end_date"] or getdate(lease["end_date"]) > getdate(
                        self.start_date
                    ):
                        msg = _(
                            "Cannot activate lease <b>{0}</b> for property <b>{1}</b>.<br>Conflicting lease: <b>{2} (Status: {3}, End Date: {4})</b>"
                        ).format(
                            self.name,
                            self.property,
                            lease["name"],
                            lease["lease_status"],
                            lease["end_date"] or "None",
                        )
                        frappe.throw(msg, frappe.ValidationError)

            if (
                get_datetime(self.start_date)
                <= get_datetime(now())
                <= get_datetime(add_months(self.end_date, -3))
            ):
                frappe.db.set_value("Property", self.property, "status", "On Lease")
                frappe.msgprint(_(f'Property "{self.property}" has now been set <b>On Lease from Active</b> for Lease "{self.name}"'))
            if (
                self.skip_end_date == None
            ):
                if (
                    get_datetime(add_months(self.end_date, -3))
                    <= get_datetime(now())
                    <= get_datetime(add_months(self.end_date, 3))
                ):
                    frappe.db.set_value(
                        "Property", self.property, "status", "Off Lease in 3 Months"
                    )
                    frappe.msgprint(_(f'Property "{self.property}" has now been set <b>Off Lease in 3 Months</b> for Lease "{self.name}"'))
            else:
                frappe.db.set_value(
                    "Property", self.property, "status", "On Lease"
                )
                frappe.msgprint(_(f'Property "{self.property}" has now been set <b>On Lease from Active</b> for Lease "{self.name}"'))
        except Exception as e:
            app_error_log(frappe.session.user, str(e))
    
    def validate_lease_item_dates(self):
        """
        Validate lease item date ranges:
        1. valid_from should not be before start_date
        2. valid_from should not be after end_date (if skip_end_date is not checked)
        3. valid_to should not be before start_date
        4. valid_to should not be after end_date (if skip_end_date is not checked)
        5. valid_from should not be after valid_to
        6. Same lease items should not have overlapping date ranges
        """
        if not self.lease_item:
            return
        
        lease_start = getdate(self.start_date)
        lease_end = getdate(self.end_date) if self.end_date and not self.skip_end_date else None
        
        # Dictionary to store date ranges for each lease item
        item_date_ranges = {}
        
        for idx, item in enumerate(self.lease_item, start=1):
            # Validate valid_from
            if item.valid_from:
                valid_from = getdate(item.valid_from)
                
                # Check if valid_from is before lease start_date
                if valid_from < lease_start:
                    frappe.throw(
                        _("Row {0}: Valid From date ({1}) cannot be before Lease Start Date ({2})").format(
                            idx, item.valid_from, self.start_date
                        ),
                        frappe.ValidationError
                    )
                
                # Check if valid_from is after lease end_date
                if lease_end and valid_from > lease_end:
                    frappe.throw(
                        _("Row {0}: Valid From date ({1}) cannot be after Lease End Date ({2})").format(
                            idx, item.valid_from, self.end_date
                        ),
                        frappe.ValidationError
                    )
            
            # Validate valid_to
            if item.valid_to:
                valid_to = getdate(item.valid_to)
                
                # Check if valid_to is before lease start_date
                if valid_to < lease_start:
                    frappe.throw(
                        _("Row {0}: Valid To date ({1}) cannot be before Lease Start Date ({2})").format(
                            idx, item.valid_to, self.start_date
                        ),
                        frappe.ValidationError
                    )
                
                # Check if valid_to is after lease end_date
                if lease_end and valid_to > lease_end:
                    frappe.throw(
                        _("Row {0}: Valid To date ({1}) cannot be after Lease End Date ({2})").format(
                            idx, item.valid_to, self.end_date
                        ),
                        frappe.ValidationError
                    )
            
            # Validate valid_from vs valid_to
            if item.valid_from and item.valid_to:
                valid_from = getdate(item.valid_from)
                valid_to = getdate(item.valid_to)
                
                if valid_from > valid_to:
                    frappe.throw(
                        _("Row {0}: Valid From date ({1}) cannot be after Valid To date ({2})").format(
                            idx, item.valid_from, item.valid_to
                        ),
                        frappe.ValidationError
                    )
            
            # Build date ranges for overlap checking
            if item.lease_item:
                item_start = getdate(item.valid_from) if item.valid_from else lease_start
                item_end = getdate(item.valid_to) if item.valid_to else lease_end
                
                # Only check overlaps if we have valid dates
                if item_start and item_end:
                    if item.lease_item not in item_date_ranges:
                        item_date_ranges[item.lease_item] = []
                    
                    item_date_ranges[item.lease_item].append({
                        'idx': idx,
                        'start': item_start,
                        'end': item_end,
                        'valid_from': item.valid_from or self.start_date,
                        'valid_to': item.valid_to or self.end_date,
                        'name': item.name
                    })
        
        # Check for date overlaps within the same lease item
        for lease_item_name, ranges in item_date_ranges.items():
            # Sort by start date
            ranges.sort(key=lambda x: x['start'])
            
            # Check each pair for overlap
            for i in range(len(ranges) - 1):
                for j in range(i + 1, len(ranges)):
                    range1 = ranges[i]
                    range2 = ranges[j]
                    
                    # Check if they overlap: start1 <= end2 AND start2 <= end1
                    if range1['start'] <= range2['end'] and range2['start'] <= range1['end']:
                        frappe.throw(
                            _(
                                "Date overlap detected for Lease Item <b>{0}</b>:<br><br>"
                                "Row {1}: {2} to {3}<br>"
                                "Row {4}: {5} to {6}<br><br>"
                                "Please adjust the dates to avoid overlap."
                            ).format(
                                lease_item_name,
                                range1['idx'], range1['valid_from'], range1['valid_to'],
                                range2['idx'], range2['valid_from'], range2['valid_to']
                            ),
                            frappe.ValidationError
                        )

    def validate_no_date_gaps(self):
        """
        Validate that each unique lease item has continuous date coverage 
        between the lease's start_date and end_date with no gaps.
        
        This ensures that every day in the lease period is covered by at least 
        one row for each lease item.
        """
        if not self.lease_item:
            return
        
        lease_start = getdate(self.start_date)
        lease_end = getdate(self.end_date) if self.end_date and not self.skip_end_date else None
        
        # If there's no end_date (skip_end_date is checked), we can't validate gaps
        if not lease_end:
            return
        
        # Group lease items by lease_item name
        lease_items_by_name = {}
        
        for idx, item in enumerate(self.lease_item, start=1):
            if not item.lease_item:
                continue
            
            item_start = getdate(item.valid_from) if item.valid_from else lease_start
            item_end = getdate(item.valid_to) if item.valid_to else lease_end
            
            if item.lease_item not in lease_items_by_name:
                lease_items_by_name[item.lease_item] = []
            
            lease_items_by_name[item.lease_item].append({
                'idx': idx,
                'start': item_start,
                'end': item_end,
                'valid_from': item.valid_from or self.start_date,
                'valid_to': item.valid_to or self.end_date,
            })
        
        # Check each unique lease item for date gaps
        for lease_item_name, ranges in lease_items_by_name.items():
            # Sort by start date
            ranges.sort(key=lambda x: x['start'])
            
            # Check if the first range starts at or before lease start
            if ranges[0]['start'] > lease_start:
                gap_start = lease_start
                gap_end = add_days(ranges[0]['start'], -1)
                frappe.throw(
                    _(
                        "Date gap detected for Lease Item <b>{0}</b>:<br><br>"
                        "Missing coverage from <b>{1}</b> to <b>{2}</b><br>"
                        "First row (Row {3}) starts on {4}<br><br>"
                        "Please add a row or adjust dates to cover the entire lease period."
                    ).format(
                        lease_item_name,
                        gap_start,
                        gap_end,
                        ranges[0]['idx'],
                        ranges[0]['valid_from']
                    ),
                    # frappe.ValidationError
                )
            
            # Check for gaps between consecutive ranges
            for i in range(len(ranges) - 1):
                current_range = ranges[i]
                next_range = ranges[i + 1]
                
                # There's a gap if the next range starts more than 1 day after current range ends
                expected_next_start = add_days(current_range['end'], 1)
                
                if next_range['start'] > expected_next_start:
                    gap_start = expected_next_start
                    gap_end = add_days(next_range['start'], -1)
                    frappe.throw(
                        _(
                            "Date gap detected for Lease Item <b>{0}</b>:<br><br>"
                            "Missing coverage from <b>{1}</b> to <b>{2}</b><br>"
                            "Row {3} ends on {4}<br>"
                            "Row {5} starts on {6}<br><br>"
                            "Please add a row or adjust dates to cover the gap."
                        ).format(
                            lease_item_name,
                            gap_start,
                            gap_end,
                            current_range['idx'],
                            current_range['valid_to'],
                            next_range['idx'],
                            next_range['valid_from']
                        ),
                        frappe.ValidationError
                    )
            
            # Check if the last range ends at or after lease end
            if ranges[-1]['end'] < lease_end:
                gap_start = add_days(ranges[-1]['end'], 1)
                gap_end = lease_end
                frappe.throw(
                    _(
                        "Date gap detected for Lease Item <b>{0}</b>:<br><br>"
                        "Missing coverage from <b>{1}</b> to <b>{2}</b><br>"
                        "Last row (Row {3}) ends on {4}<br><br>"
                        "Please add a row or adjust dates to cover the entire lease period."
                    ).format(
                        lease_item_name,
                        gap_start,
                        gap_end,
                        ranges[-1]['idx'],
                        ranges[-1]['valid_to']
                    ),
                    # frappe.ValidationError
                )


@frappe.whitelist()
def getAllLease():
    # Below is temporarily created to manually run through all lease and refresh lease invoice schedule. Hardcoded to start from 1st Jan 2020.
    frappe.msgprint(_(
        "The task of making lease invoice schedule for all users has been sent for background processing."
    ))
    invoice_start_date = frappe.db.get_single_value(
        "Property Management Settings", "invoice_start_date"
    )
    lease_list = frappe.get_all(
        "Lease", filters={"end_date": (">=", invoice_start_date)}, fields=["name"]
    )
    # frappe.msgprint("Working on lease_list" + str(lease_list))
    lease_list_len = len(lease_list)
    frappe.msgprint(_("Total number of lease to be processed is {0}").format(lease_list_len))
    for lease in lease_list:
        make_lease_invoice_schedule(lease.name)


# def on_update(self):
@frappe.whitelist()
def make_lease_invoice_schedule(leasedoc):
    # frappe.msgprint("This is the parameter passed: " + str(leasedoc))
    lease = frappe.get_doc("Lease", str(leasedoc))
    try:
        # Delete unnecessary records after lease end date (but keep invoiced records)
        lease_invoice_schedule_list = frappe.get_list(
            "Lease Invoice Schedule",
            fields=[
                "name",
                "parent",
                "lease_item",
                "invoice_number",
                "date_to_invoice",
            ],
            filters={"parent": lease.name, "date_to_invoice": (">", lease.end_date)}, 
            parent_doctype='Lease',
        )
        for lease_invoice_schedule in lease_invoice_schedule_list:
            # Only delete if the record doesn't have an invoice number
            if not lease_invoice_schedule.invoice_number or lease_invoice_schedule.invoice_number == "":
                frappe.delete_doc("Lease Invoice Schedule", lease_invoice_schedule.name)
        
        # Only process lease that items and is current
        if len(lease.lease_item) >= 1 and lease.end_date >= getdate(today()):
            # Clean up records that are no longer required, i.e. of unnecessary lease items and unnecessary dates
            # Records before Invoice Start Date
            invoice_start_date = frappe.db.get_single_value(
                "Property Management Settings", "invoice_start_date"
            )
            lease_invoice_schedule_list = frappe.get_list(
                "Lease Invoice Schedule",
                fields=["name", "parent", "invoice_number", "date_to_invoice"],
                filters={
                    "parent": lease.name,
                    "date_to_invoice": ("<", invoice_start_date),
                }, 
                parent_doctype='Lease',
            )
            # frappe.msgprint("Records before Invoice Start Date " + str(lease_invoice_schedule_list))
            for lease_invoice_schedule in lease_invoice_schedule_list:
                # Only delete if the record doesn't have an invoice number
                if not lease_invoice_schedule.invoice_number or lease_invoice_schedule.invoice_number == "":
                    # frappe.msgprint("Deleting Record before Invoice Start Date " + str(invoice_start_date) + str(lease_invoice_schedule.name))
                    frappe.delete_doc("Lease Invoice Schedule", lease_invoice_schedule.name)
            
            # Records of lease_items that no longer existing in lease.lease_item
            lease_invoice_schedule_list = frappe.get_list(
                "Lease Invoice Schedule",
                fields=[
                    "name",
                    "parent",
                    "lease_item",
                    "invoice_number",
                    "date_to_invoice",
                ],
                filters={"parent": lease.name}, 
                parent_doctype='Lease',
            )
            lease_items_list = frappe.get_list(
                "Lease Item",
                fields=["name", "parent", "lease_item"],
                filters={"parent": lease.name}, 
                parent_doctype='Lease',
            )
            # Create list of lease items that are part of lease.lease_item
            lease_item_name_list = [
                lease_item["lease_item"] for lease_item in lease_items_list
            ]
            # frappe.msgprint(str(lease_item_list))
            for lease_invoice_schedule in lease_invoice_schedule_list:
                if lease_invoice_schedule.lease_item not in lease_item_name_list:
                    # Only delete if the record doesn't have an invoice number
                    if not lease_invoice_schedule.invoice_number or lease_invoice_schedule.invoice_number == "":
                        # frappe.msgprint("This lease item will be removed from invoice schedule " + str(lease_invoice_schedule.lease_item))
                        frappe.delete_doc(
                            "Lease Invoice Schedule", lease_invoice_schedule.name
                        )
            
            item_invoice_frequency = {
                "Monthly": 1.00,  # .00 to make it float type
                "Bi-Monthly": 2.00,
                "Quarterly": 3.00,
                "6 months": 6.00,
                "Annually": 12.00,
            }
            
            # Find the latest invoice date across ALL lease items in this lease
            latest_invoice_date = None
            all_schedules_with_invoice = frappe.get_all(
                "Lease Invoice Schedule",
                filters={
                    "parent": lease.name,
                    "invoice_number": ["not in", ["", None]],
                },
                fields=["date_to_invoice", "schedule_start_date"],
                order_by="date_to_invoice desc",
                limit=1
            )
            
            if all_schedules_with_invoice:
                # Get the latest invoiced date across all items
                latest_schedule = all_schedules_with_invoice[0]
                latest_invoice_date = getdate(latest_schedule.date_to_invoice)
                # frappe.msgprint(f"Latest invoice date found: {latest_invoice_date}")
            
            idx = 1
            for item in lease.lease_item:
                # frappe.msgprint("Lease item being processed: " + str(item.lease_item))
                
                # Determine the effective start and end dates for this lease item
                item_start_date = getdate(item.valid_from) if item.get('valid_from') else lease.start_date
                item_end_date = getdate(item.valid_to) if item.get('valid_to') else lease.end_date
                
                # Ensure item_start_date is not before invoice_start_date
                if item_start_date < getdate(invoice_start_date):
                    item_start_date = getdate(invoice_start_date)
                
                # ✅ FIX: Get ALL existing schedules for this item (don't filter by rate)
                lease_invoice_schedule_list = frappe.get_all(
                    "Lease Invoice Schedule",
                    fields=[
                        "name",
                        "parent",
                        "lease_item",
                        "qty",
                        "invoice_number",
                        "date_to_invoice",
                        "rate",
                        "schedule_start_date",
                    ],
                    filters={
                        "parent": lease.name, 
                        "lease_item": item.lease_item,
                        # ✅ REMOVED: rate filter - we need to see ALL schedules for this item
                    },
                    order_by="date_to_invoice",
                )
                
                # ✅ FIX: Delete schedules with wrong rates that fall within valid date range
                for schedule in lease_invoice_schedule_list:
                    schedule_date = getdate(schedule.date_to_invoice)
                    
                    # Check if this schedule falls within the current item's valid date range
                    is_in_date_range = (schedule_date >= item_start_date and schedule_date <= item_end_date)
                    
                    # If it's in the date range but has wrong rate, delete it (unless invoiced)
                    if is_in_date_range and schedule.rate != item.amount:
                        if not schedule.invoice_number or schedule.invoice_number == "":
                            frappe.delete_doc("Lease Invoice Schedule", schedule.name)
                
                # ✅ Re-fetch the cleaned up list (now only schedules with correct rate)
                lease_invoice_schedule_list = frappe.get_all(
                    "Lease Invoice Schedule",
                    fields=[
                        "name",
                        "parent",
                        "lease_item",
                        "qty",
                        "invoice_number",
                        "date_to_invoice",
                        "rate",
                        "schedule_start_date",
                    ],
                    filters={
                        "parent": lease.name, 
                        "lease_item": item.lease_item,
                        "rate": item.amount,  # Now filter by rate to work with correct schedules
                    },
                    order_by="date_to_invoice",
                )
                
                # Find the last invoiced schedule for this item/rate combination
                last_invoiced_schedule = None
                for schedule in lease_invoice_schedule_list:
                    if schedule.invoice_number and schedule.invoice_number != "":
                        last_invoiced_schedule = schedule
                
                # Determine where to start creating new schedules
                if last_invoiced_schedule:
                    # This item has invoices - start from the day after the last invoiced schedule's period
                    if last_invoiced_schedule.qty != round(last_invoiced_schedule.qty, 0):
                        add_months_value = round(last_invoiced_schedule.qty, 0) + 1
                    else:
                        add_months_value = last_invoiced_schedule.qty
                    
                    schedule_start_from = add_months(
                        last_invoiced_schedule.schedule_start_date or last_invoiced_schedule.date_to_invoice,
                        add_months_value
                    )
                    # Make sure we don't start before the item's valid_from date
                    if schedule_start_from < item_start_date:
                        schedule_start_from = item_start_date
                else:
                    # This item has NO invoices
                    # Check if there are ANY invoices in the lease
                    if latest_invoice_date:
                        # Use the latest invoice date from ANY item as the starting point
                        # This prevents creating past schedules for newly added items
                        schedule_start_from = latest_invoice_date
                        
                        # But respect the item's valid_from if it's later
                        if item_start_date > schedule_start_from:
                            schedule_start_from = item_start_date
                        
                        # frappe.msgprint(f"Item {item.lease_item}: Starting from latest invoice date {schedule_start_from}")
                    else:
                        # No invoices exist anywhere - start from item_start_date
                        schedule_start_from = item_start_date
                
                # Filter schedules to only those within the valid date range for processing
                schedules_in_range = [
                    s for s in lease_invoice_schedule_list
                    if getdate(s.date_to_invoice) >= item_start_date 
                    and getdate(s.date_to_invoice) <= item_end_date
                ]
                # frappe.msgprint(str(lease_invoice_schedule_list))
                # Get the latest item frequency incase lease was changed.
                frequency_factor = item_invoice_frequency.get(
                    item.frequency, "Invalid frequency"
                )
                # frappe.msgprint("Next Invoice date calculated: " + str(invoice_date))
                if frequency_factor == "Invalid frequency":
                    message = (
                        "Invalid frequency: "
                        + str(item.frequency)
                        + " for "
                        + str(leasedoc)
                        + " not found. Contact the developers!"
                    )
                    frappe.log_error("Frequency incorrect", message)
                    break
                invoice_qty = float(frequency_factor)
                end_date = item_end_date
                invoice_date = schedule_start_from
                
                # Delete uninvoiced schedules that fall outside the valid date range
                for schedule in lease_invoice_schedule_list:
                    schedule_date = getdate(schedule.date_to_invoice)
                    # Keep schedules that have invoices OR are within the valid range
                    should_keep = (
                        (schedule.invoice_number and schedule.invoice_number != "") or
                        (schedule_date >= item_start_date and schedule_date <= item_end_date)
                    )
                    
                    if not should_keep:
                        frappe.delete_doc("Lease Invoice Schedule", schedule.name)
                
                # Delete uninvoiced duplicate schedules (same date, same rate)
                uninvoiced_schedules = [s for s in schedules_in_range if not s.invoice_number or s.invoice_number == ""]
                seen_dates = {}
                for schedule in uninvoiced_schedules:
                    date_key = str(schedule.date_to_invoice)
                    if date_key in seen_dates:
                        # Duplicate found, delete it
                        frappe.delete_doc("Lease Invoice Schedule", schedule.name)
                    else:
                        seen_dates[date_key] = schedule.name
                
                # Re-fetch the cleaned up list
                lease_invoice_schedule_list = frappe.get_all(
                    "Lease Invoice Schedule",
                    fields=[
                        "name",
                        "parent",
                        "lease_item",
                        "qty",
                        "invoice_number",
                        "date_to_invoice",
                        "rate",
                        "schedule_start_date",
                    ],
                    filters={
                        "parent": lease.name, 
                        "lease_item": item.lease_item,
                        "rate": item.amount,
                    },
                    order_by="date_to_invoice",
                )
                # Find out the first invoice date on or after Invoice Start Date process.
                while end_date >= invoice_date and invoice_date < invoice_start_date:
                    invoice_period_end = add_days(
                        add_months(invoice_date, frequency_factor), -1
                    )
                    # Set invoice_Qty as appropriate fraction of frequency_factor
                    if invoice_period_end > end_date:
                        invoice_qty = getDateMonthDiff(invoice_date, end_date, 1)
                        # frappe.msgprint("Invoice quantity corrected as " + str(invoice_qty))
                    invoice_date = add_days(invoice_period_end, 1)
                
                # Get existing schedule dates to avoid duplicates
                existing_schedule_dates = {
                    getdate(s.date_to_invoice): s 
                    for s in lease_invoice_schedule_list
                }
                
                # Process invoiced schedules first to assign them idx
                for schedule in lease_invoice_schedule_list:
                    if schedule.invoice_number and schedule.invoice_number != "":
                        # Keep invoiced schedules, just update their idx for sorting
                        frappe.db.set_value(
                            "Lease Invoice Schedule",
                            schedule.name,
                            "idx",
                            idx,
                        )
                        idx += 1
                
                # Now create new schedules for dates that don't exist yet
                while end_date >= invoice_date:
                    # Check if a schedule already exists for this date
                    if invoice_date in existing_schedule_dates:
                        schedule = existing_schedule_dates[invoice_date]
                        # If it's uninvoiced, update its idx
                        if not schedule.invoice_number or schedule.invoice_number == "":
                            frappe.db.set_value(
                                "Lease Invoice Schedule",
                                schedule.name,
                                "idx",
                                idx,
                            )
                            idx += 1
                        # Move to next period
                        invoice_period_end = add_days(
                            add_months(invoice_date, frequency_factor), -1
                        )
                        invoice_date = add_days(invoice_period_end, 1)
                        continue
                    
                    invoice_period_end = add_days(
                        add_months(invoice_date, frequency_factor), -1
                    )
                    # frappe.msgprint("Invoice period end: " + str(invoice_period_end) + "--- Invoice Date: " + str(invoice_date))
                    # frappe.msgprint("End Date: " + str(end_date))
                    # set invoice_Qty as appropriate fraction of frequency_factor
                    if invoice_period_end > end_date:
                        invoice_qty = getDateMonthDiff(invoice_date, end_date, 1)
                        # frappe.msgprint("Invoice quantity corrected as " + str(invoice_qty))
                    # frappe.msgprint("Making Fresh Invoice Schedule for " + str(invoice_date)
                    # 	+ ", Quantity calculated: " + str(invoice_qty))
                    makeInvoiceSchedule(
                        invoice_date,
                        item.lease_item,
                        item.paid_by,
                        item.lease_item,
                        lease.name,
                        invoice_qty,
                        item.amount,
                        idx,
                        item.currency_code,
                        item.witholding_tax,
                        lease.days_to_invoice_in_advance,
                        item.invoice_item_group,
                        item.document_type,
                    )
                    idx += 1
                    invoice_date = add_days(invoice_period_end, 1)

        # Sort all invoice schedules by date_to_invoice in ascending order
        all_schedules = frappe.get_all(
            "Lease Invoice Schedule",
            fields=["name", "date_to_invoice"],
            filters={"parent": lease.name},
            order_by="date_to_invoice asc",
        )
        
        # Update idx for proper sorting
        for index, schedule in enumerate(all_schedules, start=1):
            frappe.db.set_value(
                "Lease Invoice Schedule",
                schedule.name,
                "idx",
                index,
            )
        
        frappe.msgprint("Completed making of invoice schedule.")

    except Exception as e:
        frappe.msgprint("Exception error! Check app error log.")
        app_error_log(frappe.session.user, str(e))