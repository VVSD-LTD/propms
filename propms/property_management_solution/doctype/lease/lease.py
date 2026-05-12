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
                conflicting_leases = frappe.db.get_all(
                    "Lease",
                    filters={
                        "property": self.property,
                        "lease_status": ["!=", "Draft"],
                        "name": ["!=", self.name],
                        "docstatus": ["<", 2],
                    },
                    fields=["name", "end_date", "lease_status"],
                )
                for lease in conflicting_leases:
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
            if self.skip_end_date == None:
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
                frappe.db.set_value("Property", self.property, "status", "On Lease")
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

        item_date_ranges = {}

        for idx, item in enumerate(self.lease_item, start=1):
            if item.valid_from:
                valid_from = getdate(item.valid_from)

                if valid_from < lease_start:
                    frappe.throw(
                        _("Row {0}: Valid From date ({1}) cannot be before Lease Start Date ({2})").format(
                            idx, item.valid_from, self.start_date
                        ),
                        frappe.ValidationError
                    )

                if lease_end and valid_from > lease_end:
                    frappe.throw(
                        _("Row {0}: Valid From date ({1}) cannot be after Lease End Date ({2})").format(
                            idx, item.valid_from, self.end_date
                        ),
                        frappe.ValidationError
                    )

            if item.valid_to:
                valid_to = getdate(item.valid_to)

                if valid_to < lease_start:
                    frappe.throw(
                        _("Row {0}: Valid To date ({1}) cannot be before Lease Start Date ({2})").format(
                            idx, item.valid_to, self.start_date
                        ),
                        frappe.ValidationError
                    )

                if lease_end and valid_to > lease_end:
                    frappe.throw(
                        _("Row {0}: Valid To date ({1}) cannot be after Lease End Date ({2})").format(
                            idx, item.valid_to, self.end_date
                        ),
                        frappe.ValidationError
                    )

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

            if item.lease_item:
                item_start = getdate(item.valid_from) if item.valid_from else lease_start
                item_end = getdate(item.valid_to) if item.valid_to else lease_end

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

        for lease_item_name, ranges in item_date_ranges.items():
            ranges.sort(key=lambda x: x['start'])

            for i in range(len(ranges) - 1):
                for j in range(i + 1, len(ranges)):
                    range1 = ranges[i]
                    range2 = ranges[j]

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
        """
        if not self.lease_item:
            return

        lease_start = getdate(self.start_date)
        lease_end = getdate(self.end_date) if self.end_date and not self.skip_end_date else None

        if not lease_end:
            return

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

        for lease_item_name, ranges in lease_items_by_name.items():
            ranges.sort(key=lambda x: x['start'])

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
                )

            for i in range(len(ranges) - 1):
                current_range = ranges[i]
                next_range = ranges[i + 1]

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
                )


@frappe.whitelist()
def getAllLease():
    frappe.msgprint(_(
        "The task of making lease invoice schedule for all users has been sent for background processing."
    ))
    invoice_start_date = frappe.db.get_single_value(
        "Property Management Settings", "invoice_start_date"
    )
    lease_list = frappe.get_all(
        "Lease", filters={"end_date": (">=", invoice_start_date)}, fields=["name"]
    )
    lease_list_len = len(lease_list)
    frappe.msgprint(_("Total number of lease to be processed is {0}").format(lease_list_len))
    for lease in lease_list:
        make_lease_invoice_schedule(lease.name)

@frappe.whitelist()
def make_lease_invoice_schedule(leasedoc):
    lease = frappe.get_doc("Lease", str(leasedoc))
    try:
        # ── Step 1: Delete ALL uninvoiced schedules (past, present, future) ──
        all_schedules = frappe.get_all(
            "Lease Invoice Schedule",
            fields=["name", "invoice_number"],
            filters={"parent": lease.name},
            parent_doctype="Lease",
        )
        for s in all_schedules:
            if not s.invoice_number or s.invoice_number == "":
                frappe.delete_doc("Lease Invoice Schedule", s.name)

        # ── Step 2: Nothing to build if no items or lease already ended ──
        if not lease.lease_item or lease.end_date < getdate(today()):
            frappe.msgprint("Completed making of invoice schedule.")
            return

        invoice_start_date = frappe.db.get_single_value(
            "Property Management Settings", "invoice_start_date"
        )

        item_invoice_frequency = {
            "Monthly": 1,
            "Bi-Monthly": 2,
            "Quarterly": 3,
            "6 months": 6,
            "Annually": 12,
        }

        # ── Step 3: Assign idx to already-invoiced schedules first ──
        idx = 1
        invoiced_schedules = frappe.get_all(
            "Lease Invoice Schedule",
            fields=["name", "date_to_invoice"],
            filters={
                "parent": lease.name,
                "invoice_number": ["not in", ["", None]],
            },
            order_by="date_to_invoice asc",
        )
        for s in invoiced_schedules:
            frappe.db.set_value("Lease Invoice Schedule", s.name, "idx", idx)
            idx += 1

        # ── Step 4: Rebuild schedules per item, using per-item last invoiced period ──
        for item in lease.lease_item:
            frequency_factor = item_invoice_frequency.get(item.frequency)
            if not frequency_factor:
                frappe.log_error(
                    "Frequency incorrect",
                    f"Invalid frequency: {item.frequency} for {leasedoc}",
                )
                continue

            # Effective date range for this item
            item_start = getdate(item.valid_from) if item.get("valid_from") else getdate(lease.start_date)
            item_end = getdate(item.valid_to) if item.get("valid_to") else getdate(lease.end_date)

            # ── FIX: Per-item lookup using schedule_start_date + qty months ──
            # This avoids the off-by-one that caused duplicate creation when
            # date_to_invoice (period start) was used as the cutoff directly.
            latest_item_invoiced = frappe.get_all(
                "Lease Invoice Schedule",
                filters={
                    "parent": lease.name,
                    "lease_item": item.lease_item,
                    "invoice_number": ["not in", ["", None]],
                },
                fields=["schedule_start_date", "date_to_invoice", "qty"],
                order_by="schedule_start_date desc",
                limit=1,
            )

            if latest_item_invoiced:
                s = latest_item_invoiced[0]
                period_start = getdate(s.schedule_start_date or s.date_to_invoice)
                # qty may be fractional for the last partial period, round up to full periods
                qty_months = int(round(float(s.qty or frequency_factor)))
                # Next period starts exactly qty months after the last invoiced period start
                invoice_date = add_months(period_start, qty_months)
            else:
                invoice_date = item_start

            # Apply invoice_start_date floor
            if getdate(invoice_start_date) > invoice_date:
                invoice_date = getdate(invoice_start_date)

            # Align invoice_date to the correct period grid starting from item_start
            # so we don't start mid-period
            period_start = item_start
            while period_start < invoice_date:
                period_end = add_days(add_months(period_start, frequency_factor), -1)
                next_start = add_days(period_end, 1)
                if next_start > invoice_date:
                    break
                period_start = next_start
            invoice_date = period_start

            # Generate schedule rows forward from invoice_date
            while invoice_date <= item_end:
                invoice_period_end = add_days(add_months(invoice_date, frequency_factor), -1)
                if invoice_period_end > item_end:
                    invoice_qty = getDateMonthDiff(invoice_date, item_end, 1)
                else:
                    invoice_qty = float(frequency_factor)

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
                    item.payment_terms,
                    item.document_type,
                )
                idx += 1
                invoice_date = add_days(invoice_period_end, 1)

        # ── Step 5: Final re-index all schedules by date_to_invoice ──
        all_schedules = frappe.get_all(
            "Lease Invoice Schedule",
            fields=["name", "date_to_invoice"],
            filters={"parent": lease.name},
            order_by="date_to_invoice asc",
        )
        for index, s in enumerate(all_schedules, start=1):
            frappe.db.set_value("Lease Invoice Schedule", s.name, "idx", index)

        frappe.msgprint("Completed making of invoice schedule.")

    except Exception as e:
        frappe.msgprint("Exception error! Check app error log.")
        app_error_log(frappe.session.user, str(e))

@frappe.whitelist()
def make_lease_invoice_schedule_1(leasedoc):
    lease = frappe.get_doc("Lease", str(leasedoc))
    try:
        # ── Step 1: Delete ALL uninvoiced schedules (past, present, future) ──
        all_schedules = frappe.get_all(
            "Lease Invoice Schedule",
            fields=["name", "invoice_number"],
            filters={"parent": lease.name},
            parent_doctype="Lease",
        )
        for s in all_schedules:
            if not s.invoice_number or s.invoice_number == "":
                frappe.delete_doc("Lease Invoice Schedule", s.name)

        # ── Step 2: Nothing to build if no items or lease already ended ──
        if not lease.lease_item or lease.end_date < getdate(today()):
            frappe.msgprint("Completed making of invoice schedule.")
            return

        invoice_start_date = frappe.db.get_single_value(
            "Property Management Settings", "invoice_start_date"
        )

        item_invoice_frequency = {
            "Monthly": 1,
            "Bi-Monthly": 2,
            "Quarterly": 3,
            "6 months": 6,
            "Annually": 12,
        }

        # ── Step 3: Find the latest invoiced date so we don't duplicate invoiced periods ──
        latest_invoiced_schedule = frappe.get_all(
            "Lease Invoice Schedule",
            filters={
                "parent": lease.name,
                "invoice_number": ["not in", ["", None]],
            },
            fields=["date_to_invoice"],
            order_by="date_to_invoice desc",
            limit=1,
        )
        latest_invoiced_date = (
            getdate(latest_invoiced_schedule[0].date_to_invoice)
            if latest_invoiced_schedule
            else None
        )

        # ── Step 4: Rebuild schedules for each lease item ──
        idx = 1

        # First pass: assign idx to invoiced schedules
        invoiced_schedules = frappe.get_all(
            "Lease Invoice Schedule",
            fields=["name", "date_to_invoice"],
            filters={
                "parent": lease.name,
                "invoice_number": ["not in", ["", None]],
            },
            order_by="date_to_invoice asc",
        )
        for s in invoiced_schedules:
            frappe.db.set_value("Lease Invoice Schedule", s.name, "idx", idx)
            idx += 1

        for item in lease.lease_item:
            frequency_factor = item_invoice_frequency.get(item.frequency)
            if not frequency_factor:
                frappe.log_error(
                    "Frequency incorrect",
                    f"Invalid frequency: {item.frequency} for {leasedoc}",
                )
                continue

            # Effective date range for this item
            item_start = getdate(item.valid_from) if item.get("valid_from") else getdate(lease.start_date)
            item_end = getdate(item.valid_to) if item.get("valid_to") else getdate(lease.end_date)

            # Start generating from whichever is latest:
            # item start, invoice_start_date, or day after last invoiced period
            invoice_date = item_start
            if getdate(invoice_start_date) > invoice_date:
                invoice_date = getdate(invoice_start_date)
            if latest_invoiced_date and latest_invoiced_date >= invoice_date:
                invoice_date = add_days(latest_invoiced_date, 1)

            # Skip past periods to land on or after invoice_date
            period_start = item_start
            while period_start < invoice_date:
                period_end = add_days(add_months(period_start, frequency_factor), -1)
                if add_days(period_end, 1) > invoice_date:
                    break
                period_start = add_days(period_end, 1)
            invoice_date = period_start

            # Generate schedule rows
            while invoice_date <= item_end:
                invoice_period_end = add_days(add_months(invoice_date, frequency_factor), -1)
                if invoice_period_end > item_end:
                    invoice_qty = getDateMonthDiff(invoice_date, item_end, 1)
                else:
                    invoice_qty = float(frequency_factor)

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
                    item.payment_terms,
                    item.document_type,
                )
                idx += 1
                invoice_date = add_days(invoice_period_end, 1)

        # ── Step 5: Final re-index all schedules by date_to_invoice ──
        all_schedules = frappe.get_all(
            "Lease Invoice Schedule",
            fields=["name", "date_to_invoice"],
            filters={"parent": lease.name},
            order_by="date_to_invoice asc",
        )
        for index, s in enumerate(all_schedules, start=1):
            frappe.db.set_value("Lease Invoice Schedule", s.name, "idx", index)

        frappe.msgprint("Completed making of invoice schedule.")

    except Exception as e:
        frappe.msgprint("Exception error! Check app error log.")
        app_error_log(frappe.session.user, str(e))

@frappe.whitelist()
def make_lease_invoice_schedule_2(leasedoc):
    lease = frappe.get_doc("Lease", str(leasedoc))
    try:
        # Delete unnecessary records after lease end date (but keep invoiced records)
        lease_invoice_schedule_list = frappe.get_list(
            "Lease Invoice Schedule",
            fields=["name", "parent", "lease_item", "invoice_number", "date_to_invoice"],
            filters={"parent": lease.name, "date_to_invoice": (">", lease.end_date)},
            parent_doctype='Lease',
        )
        for lease_invoice_schedule in lease_invoice_schedule_list:
            if not lease_invoice_schedule.invoice_number or lease_invoice_schedule.invoice_number == "":
                frappe.delete_doc("Lease Invoice Schedule", lease_invoice_schedule.name)

        if len(lease.lease_item) >= 1 and lease.end_date >= getdate(today()):
            invoice_start_date = frappe.db.get_single_value(
                "Property Management Settings", "invoice_start_date"
            )

            # Clean up records before invoice start date
            lease_invoice_schedule_list = frappe.get_list(
                "Lease Invoice Schedule",
                fields=["name", "parent", "invoice_number", "date_to_invoice"],
                filters={
                    "parent": lease.name,
                    "date_to_invoice": ("<", invoice_start_date),
                },
                parent_doctype='Lease',
            )
            for lease_invoice_schedule in lease_invoice_schedule_list:
                if not lease_invoice_schedule.invoice_number or lease_invoice_schedule.invoice_number == "":
                    frappe.delete_doc("Lease Invoice Schedule", lease_invoice_schedule.name)

            # Clean up records of lease_items no longer in lease.lease_item
            lease_invoice_schedule_list = frappe.get_list(
                "Lease Invoice Schedule",
                fields=["name", "parent", "lease_item", "invoice_number", "date_to_invoice"],
                filters={"parent": lease.name},
                parent_doctype='Lease',
            )
            lease_items_list = frappe.get_list(
                "Lease Item",
                fields=["name", "parent", "lease_item"],
                filters={"parent": lease.name},
                parent_doctype='Lease',
            )
            lease_item_name_list = [lease_item["lease_item"] for lease_item in lease_items_list]
            for lease_invoice_schedule in lease_invoice_schedule_list:
                if lease_invoice_schedule.lease_item not in lease_item_name_list:
                    if not lease_invoice_schedule.invoice_number or lease_invoice_schedule.invoice_number == "":
                        frappe.delete_doc("Lease Invoice Schedule", lease_invoice_schedule.name)

            item_invoice_frequency = {
                "Monthly": 1.00,
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
                latest_schedule = all_schedules_with_invoice[0]
                latest_invoice_date = getdate(latest_schedule.date_to_invoice)

            idx = 1
            for item in lease.lease_item:
                # Determine the effective start and end dates for this lease item
                item_start_date = getdate(item.valid_from) if item.get('valid_from') else lease.start_date
                item_end_date = getdate(item.valid_to) if item.get('valid_to') else lease.end_date

                # Ensure item_start_date is not before invoice_start_date
                if item_start_date < getdate(invoice_start_date):
                    item_start_date = getdate(invoice_start_date)

                # Get ALL existing schedules for this item
                lease_invoice_schedule_list = frappe.get_all(
                    "Lease Invoice Schedule",
                    fields=[
                        "name", "parent", "lease_item", "qty",
                        "invoice_number", "date_to_invoice", "rate", "schedule_start_date",
                    ],
                    filters={
                        "parent": lease.name,
                        "lease_item": item.lease_item,
                    },
                    order_by="date_to_invoice",
                )

                # Delete schedules with wrong rates within valid date range (if uninvoiced)
                for schedule in lease_invoice_schedule_list:
                    schedule_date = getdate(schedule.date_to_invoice)
                    is_in_date_range = (schedule_date >= item_start_date and schedule_date <= item_end_date)

                    if is_in_date_range and schedule.rate != item.amount:
                        if not schedule.invoice_number or schedule.invoice_number == "":
                            frappe.delete_doc("Lease Invoice Schedule", schedule.name)

                # Re-fetch with correct rate
                lease_invoice_schedule_list = frappe.get_all(
                    "Lease Invoice Schedule",
                    fields=[
                        "name", "parent", "lease_item", "qty",
                        "invoice_number", "date_to_invoice", "rate", "schedule_start_date",
                    ],
                    filters={
                        "parent": lease.name,
                        "lease_item": item.lease_item,
                        "rate": item.amount,
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
                    if last_invoiced_schedule.qty != round(last_invoiced_schedule.qty, 0):
                        add_months_value = round(last_invoiced_schedule.qty, 0) + 1
                    else:
                        add_months_value = last_invoiced_schedule.qty

                    schedule_start_from = add_months(
                        last_invoiced_schedule.schedule_start_date or last_invoiced_schedule.date_to_invoice,
                        add_months_value
                    )
                    if schedule_start_from < item_start_date:
                        schedule_start_from = item_start_date
                else:
                    if latest_invoice_date:
                        schedule_start_from = latest_invoice_date
                        if item_start_date > schedule_start_from:
                            schedule_start_from = item_start_date
                    else:
                        schedule_start_from = item_start_date

                # Filter schedules within the valid date range
                schedules_in_range = [
                    s for s in lease_invoice_schedule_list
                    if getdate(s.date_to_invoice) >= item_start_date
                    and getdate(s.date_to_invoice) <= item_end_date
                ]

                frequency_factor = item_invoice_frequency.get(item.frequency, "Invalid frequency")
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

                # Delete uninvoiced schedules outside valid date range
                for schedule in lease_invoice_schedule_list:
                    schedule_date = getdate(schedule.date_to_invoice)
                    should_keep = (
                        (schedule.invoice_number and schedule.invoice_number != "") or
                        (schedule_date >= item_start_date and schedule_date <= item_end_date)
                    )
                    if not should_keep:
                        frappe.delete_doc("Lease Invoice Schedule", schedule.name)

                # ✅ FIX: Deduplicate using schedule_start_date instead of date_to_invoice
                uninvoiced_schedules = [s for s in schedules_in_range if not s.invoice_number or s.invoice_number == ""]
                seen_period_starts = {}
                for schedule in uninvoiced_schedules:
                    # Use schedule_start_date as the stable period key, fall back to date_to_invoice for old rows
                    period_key = str(schedule.schedule_start_date or schedule.date_to_invoice)
                    if period_key in seen_period_starts:
                        frappe.delete_doc("Lease Invoice Schedule", schedule.name)
                    else:
                        seen_period_starts[period_key] = schedule.name

                # Re-fetch final cleaned list
                lease_invoice_schedule_list = frappe.get_all(
                    "Lease Invoice Schedule",
                    fields=[
                        "name", "parent", "lease_item", "qty",
                        "invoice_number", "date_to_invoice", "rate", "schedule_start_date",
                    ],
                    filters={
                        "parent": lease.name,
                        "lease_item": item.lease_item,
                        "rate": item.amount,
                    },
                    order_by="date_to_invoice",
                )

                # ✅ Propagate payment_terms from lease item to all uninvoiced schedules
                if item.payment_terms:
                    for schedule in lease_invoice_schedule_list:
                        if not schedule.invoice_number or schedule.invoice_number == "":
                            frappe.db.set_value(
                                "Lease Invoice Schedule",
                                schedule.name,
                                "payment_terms",
                                item.payment_terms,
                            )

                # Find out the first invoice date on or after Invoice Start Date
                while end_date >= invoice_date and invoice_date < invoice_start_date:
                    invoice_period_end = add_days(add_months(invoice_date, frequency_factor), -1)
                    if invoice_period_end > end_date:
                        invoice_qty = getDateMonthDiff(invoice_date, end_date, 1)
                    invoice_date = add_days(invoice_period_end, 1)

                # ✅ FIX: Build existence map keyed on schedule_start_date (stable period key)
                existing_schedule_dates = {
                    getdate(s.schedule_start_date or s.date_to_invoice): s
                    for s in lease_invoice_schedule_list
                }

                # Process invoiced schedules to assign idx
                for schedule in lease_invoice_schedule_list:
                    if schedule.invoice_number and schedule.invoice_number != "":
                        frappe.db.set_value("Lease Invoice Schedule", schedule.name, "idx", idx)
                        idx += 1

                # Create new schedules for periods that don't exist yet
                while end_date >= invoice_date:
                    # ✅ FIX: Check by invoice_date (= period start = schedule_start_date)
                    if invoice_date in existing_schedule_dates:
                        schedule = existing_schedule_dates[invoice_date]
                        if not schedule.invoice_number or schedule.invoice_number == "":
                            # ✅ Recalculate date_to_invoice in case days_to_invoice_in_advance changed
                            recalculated_date = add_days(invoice_date, -1 * (lease.days_to_invoice_in_advance or 0))
                            frappe.db.set_value(
                                "Lease Invoice Schedule",
                                schedule.name,
                                {
                                    "idx": idx,
                                    "date_to_invoice": recalculated_date,  # ← add this
                                }
                            )
                            idx += 1
                        invoice_period_end = add_days(add_months(invoice_date, frequency_factor), -1)
                        invoice_date = add_days(invoice_period_end, 1)
                        continue

                    invoice_period_end = add_days(add_months(invoice_date, frequency_factor), -1)
                    if invoice_period_end > end_date:
                        invoice_qty = getDateMonthDiff(invoice_date, end_date, 1)

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
                        item.payment_terms,
                        item.document_type,
                    )
                    idx += 1
                    invoice_date = add_days(invoice_period_end, 1)

        # Sort all invoice schedules by date_to_invoice ascending
        all_schedules = frappe.get_all(
            "Lease Invoice Schedule",
            fields=["name", "date_to_invoice"],
            filters={"parent": lease.name},
            order_by="date_to_invoice asc",
        )

        for index, schedule in enumerate(all_schedules, start=1):
            frappe.db.set_value("Lease Invoice Schedule", schedule.name, "idx", index)

        frappe.msgprint("Completed making of invoice schedule.")

    except Exception as e:
        frappe.msgprint("Exception error! Check app error log.")
        app_error_log(frappe.session.user, str(e))

@frappe.whitelist()
def find_duplicate_invoice_schedules(dry_run=1):
    """
    Finds Lease Invoice Schedule rows that are duplicates caused by the
    global latest_invoiced_date bug — i.e. rows sharing the same
    (parent, lease_item, schedule_start_date) where more than one row
    has an invoice_number.

    dry_run=1  → only reports, does NOT delete anything
    dry_run=0  → deletes the duplicate uninvoiced rows and reports invoices
                 that were created from duplicates so you can decide what
                 to do with the actual Sales Invoices.

    Returns a dict with:
      - duplicates: list of groups with their schedule rows
      - invoices_to_review: unique invoice numbers found on duplicate rows
    """
    dry_run = int(dry_run)

    # Fetch every schedule row with enough info to detect duplicates
    all_schedules = frappe.db.sql(
        """
        SELECT
            lis.name,
            lis.parent,
            lis.lease_item,
            lis.schedule_start_date,
            lis.date_to_invoice,
            lis.qty,
            lis.rate,
            lis.invoice_number,
            lis.idx
        FROM `tabLease Invoice Schedule` lis
        ORDER BY lis.parent, lis.lease_item, lis.schedule_start_date, lis.idx
        """,
        as_dict=True,
    )

    # Group by (parent, lease_item, schedule_start_date)
    from collections import defaultdict
    groups = defaultdict(list)
    for row in all_schedules:
        key = (row.parent, row.lease_item, str(row.schedule_start_date))
        groups[key].append(row)

    duplicate_groups = []
    invoices_to_review = set()
    schedules_to_delete = []

    for key, rows in groups.items():
        if len(rows) < 2:
            continue  # not a duplicate

        lease_name, lease_item, period_start = key

        # Separate invoiced vs uninvoiced duplicates
        invoiced_rows = [r for r in rows if r.invoice_number]
        uninvoiced_rows = [r for r in rows if not r.invoice_number]

        # Collect invoice numbers from ALL rows (invoiced duplicates are the problem)
        for r in rows:
            if r.invoice_number:
                invoices_to_review.add(r.invoice_number)

        # The duplicate to remove: if there are multiple invoiced rows,
        # keep the first (lowest idx), mark the rest for deletion.
        # Uninvoiced duplicates are always safe to delete.
        rows_sorted = sorted(rows, key=lambda r: r.idx or 0)
        keeper = rows_sorted[0]

        to_delete = rows_sorted[1:]  # everything after the first

        duplicate_groups.append({
            "lease": lease_name,
            "lease_item": lease_item,
            "period_start": period_start,
            "total_rows": len(rows),
            "keeper": {
                "name": keeper.name,
                "idx": keeper.idx,
                "invoice_number": keeper.invoice_number,
                "qty": keeper.qty,
                "rate": keeper.rate,
            },
            "rows_to_delete": [
                {
                    "name": r.name,
                    "idx": r.idx,
                    "invoice_number": r.invoice_number,
                    "qty": r.qty,
                    "rate": r.rate,
                }
                for r in to_delete
            ],
        })

        for r in to_delete:
            schedules_to_delete.append(r.name)

    # ── Report ──
    report_lines = []
    report_lines.append(f"{'[DRY RUN] ' if dry_run else ''}Found {len(duplicate_groups)} duplicate group(s).\n")

    for g in duplicate_groups:
        report_lines.append(
            f"Lease: {g['lease']} | Item: {g['lease_item']} | Period Start: {g['period_start']}"
        )
        report_lines.append(
            f"  → Keeping row: {g['keeper']['name']} (idx {g['keeper']['idx']}, invoice: {g['keeper']['invoice_number'] or 'none'})"
        )
        for r in g["rows_to_delete"]:
            action = "WOULD DELETE" if dry_run else "DELETED"
            report_lines.append(
                f"  → {action}: {r['name']} (idx {r['idx']}, invoice: {r['invoice_number'] or 'none'})"
            )

    if invoices_to_review:
        report_lines.append(f"\nInvoices created from duplicate rows — review before cancelling:")
        for inv in sorted(invoices_to_review):
            report_lines.append(f"  • {inv}")
    else:
        report_lines.append("\nNo Sales Invoices found on duplicate rows.")

    # ── Delete if not dry run ──
    if not dry_run and schedules_to_delete:
        for name in schedules_to_delete:
            # Only delete uninvoiced ones automatically — invoiced ones need manual review
            row_invoice = frappe.db.get_value("Lease Invoice Schedule", name, "invoice_number")
            if not row_invoice:
                frappe.delete_doc("Lease Invoice Schedule", name, ignore_permissions=True)
                report_lines.append(f"Deleted uninvoiced duplicate: {name}")
            else:
                report_lines.append(
                    f"SKIPPED (has invoice {row_invoice}): {name} — cancel the invoice manually first, then rerun."
                )
        frappe.db.commit()

    report_text = "\n".join(report_lines)
    frappe.msgprint(f"<pre>{report_text}</pre>", title="Duplicate Schedule Audit", wide=True)

    return {
        "duplicate_groups": duplicate_groups,
        "invoices_to_review": sorted(invoices_to_review),
        "schedules_that_would_be_deleted": schedules_to_delete,
        "dry_run": bool(dry_run),
    }