# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, add_months, add_days
from frappe import _

FREQUENCY_MONTHS = {
    "Monthly": 1,
    "Bi-Monthly": 2,
    "Quarterly": 3,
    "6 months": 6,
    "Annually": 12,
}

def _expected_starts(cycle_anchor, frequency, item_start, item_end):
    freq = FREQUENCY_MONTHS.get(frequency)
    if not freq: return set()
    current = getdate(cycle_anchor)
    item_end_d = getdate(item_end)
    dates = set()
    while current <= item_end_d:
        dates.add(current)
        period_end = add_days(add_months(current, freq), -1)
        current = add_days(period_end, 1)
    return dates

def _expected_starts_list(cycle_anchor, frequency, item_start, item_end):
    freq = FREQUENCY_MONTHS.get(frequency)
    if not freq: return []
    current = getdate(cycle_anchor)
    item_end_d = getdate(item_end)
    dates = []
    while current <= item_end_d:
        dates.append(current)
        period_end = add_days(add_months(current, freq), -1)
        current = add_days(period_end, 1)
    return dates

def _derive_cycle_anchor(schedules, lease_start):
    """
    Derives the anchor day from the lease start date, but adjusts 
    the year/month to match the earliest schedule record found.
    """
    anchor_day = getdate(lease_start).day
    earliest_date = getdate(lease_start)
    
    ssd_list = [getdate(s.schedule_start_date) for s in schedules if s.schedule_start_date]
    if ssd_list:
        earliest_date = min(ssd_list)
            
    try:
        return earliest_date.replace(day=anchor_day)
    except ValueError:
        return earliest_date.replace(day=28)

def _format_series(dates):
    if not dates: return ""
    day = dates[0].day
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(day if day < 20 else day % 10, "th")
    anchor = f"{day}{suffix}"
    shown = [str(d) for d in dates[:4]]
    tail = " …" if len(dates) > 4 else ""
    return f"{anchor} · {', '.join(shown)}{tail}"

def _audit():
    leases = frappe.get_all(
        "Lease",
        filters={"lease_status": "Active", "docstatus": ["<", 2]},
        fields=["name", "start_date", "end_date", "skip_end_date", "lease_customer"],
    )

    results = []

    for lease in leases:
        lease_start = getdate(lease.start_date)
        lease_end = getdate(lease.end_date) if lease.end_date and not lease.skip_end_date else None
        if not lease_end: continue 

        items = frappe.get_all(
            "Lease Item",
            filters={"parent": lease.name},
            fields=["lease_item", "frequency", "amount", "valid_from", "valid_to"],
            parent_doctype="Lease",
        )

        for item in items:
            item_start = getdate(item.valid_from) if item.valid_from else lease_start
            item_end = getdate(item.valid_to) if item.valid_to else lease_end

            schedules = frappe.get_all(
                "Lease Invoice Schedule",
                filters={"parent": lease.name, "lease_item": item.lease_item},
                fields=["name", "schedule_start_date", "date_to_invoice", "invoice_number"],
                order_by="schedule_start_date asc",
                parent_doctype="Lease",
            )

            invoiced_rows = [s for s in schedules if s.invoice_number and s.invoice_number.strip()]
            uninvoiced_rows = [s for s in schedules if not (s.invoice_number and s.invoice_number.strip())]

            if not invoiced_rows and not uninvoiced_rows: continue

            cycle_anchor = _derive_cycle_anchor(schedules, lease_start)
            expected = _expected_starts(cycle_anchor, item.frequency, item_start, item_end)
            expected_list = _expected_starts_list(cycle_anchor, item.frequency, item_start, item_end)

            invoiced_fixes = []
            future_is_misaligned = False

            # --- THE ANCHOR CHECK (Last Invoiced Row) ---
            if invoiced_rows:
                last_invoiced = invoiced_rows[-1]
                last_ssd = getdate(last_invoiced.schedule_start_date)
                
                if last_ssd not in expected:
                    future_is_misaligned = True
                    correct_date = None
                    for exp in sorted(expected):
                        if exp <= getdate(last_invoiced.date_to_invoice):
                            correct_date = exp
                    
                    if correct_date:
                        invoiced_fixes.append({
                            "name": last_invoiced.name,
                            "wrong": str(last_ssd),
                            "correct": str(correct_date),
                            "invoice_number": last_invoiced.invoice_number,
                            "issue": "last_invoice_drift"
                        })

            # Check other historical invoiced rows
            for s in invoiced_rows[:-1]:
                ssd = getdate(s.schedule_start_date)
                if ssd not in expected:
                    correct_ssd = None
                    for exp in sorted(expected):
                        if exp <= getdate(s.date_to_invoice):
                            correct_ssd = exp
                    if correct_ssd:
                        invoiced_fixes.append({
                            "name": s.name, "wrong": str(ssd), "correct": str(correct_ssd),
                            "invoice_number": s.invoice_number, "issue": "historical_misalignment"
                        })

            all_uninvoiced_names = [s.name for s in uninvoiced_rows]
            
            if not (future_is_misaligned or invoiced_fixes):
                continue

            results.append({
                "lease_name": lease.name,
                "document_name": lease.name,
                "lease_customer": lease.lease_customer or "",
                "lease_start": str(lease_start),
                "lease_end": str(lease_end),
                "lease_item": item.lease_item,
                "frequency": item.frequency,
                "item_start": str(item_start),
                "item_end": str(item_end),
                "expected_series": _format_series(expected_list),
                "bad_uninvoiced_count": len(all_uninvoiced_names) if future_is_misaligned else 0,
                "bad_invoiced_count": len(invoiced_fixes),
                "uninvoiced_names": all_uninvoiced_names if future_is_misaligned else [],
                "invoiced_fixes": invoiced_fixes,
            })

    return results

def execute(filters=None):
    return get_columns(), get_data(filters)

def get_columns():
    return [
        {"fieldname": "lease_name", "label": _("Lease / Item"), "fieldtype": "Data", "width": 220},
        {"fieldname": "document_name", "label": _("Document"), "fieldtype": "Link", "options": "Lease", "width": 180},
        {"fieldname": "lease_item", "label": _("Lease Item"), "fieldtype": "Data", "width": 190},
        {"fieldname": "frequency", "label": _("Frequency"), "fieldtype": "Data", "width": 100},
        {"fieldname": "expected_series", "label": _("Expected Date Series"), "fieldtype": "Data", "width": 290},
        {"fieldname": "bad_uninvoiced_count", "label": _("Uninvoiced to Delete"), "fieldtype": "Int", "width": 140},
        {"fieldname": "bad_invoiced_count", "label": _("Invoiced to Patch"), "fieldtype": "Int", "width": 140},
        {"fieldname": "summary", "label": _("Summary"), "fieldtype": "Data", "width": 350},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
    ]

def get_data(filters=None):
    audit_results = _audit()
    from collections import defaultdict
    grouped = defaultdict(list)
    for row in audit_results:
        grouped[row["lease_name"]].append(row)

    rows = []
    for lease_name, items in grouped.items():
        total_uninvoiced = sum(i["bad_uninvoiced_count"] for i in items)
        total_invoiced = sum(i["bad_invoiced_count"] for i in items)

        rows.append({
            "lease_name": lease_name,
            "document_name": lease_name,
            "bad_uninvoiced_count": total_uninvoiced,
            "bad_invoiced_count": total_invoiced,
            "summary": _("{0} items affected · {1} rows impacted").format(len(items), total_uninvoiced + total_invoiced),
            "status": "⚠ Corrupted",
            "indent": 0,
            "_items": frappe.as_json(items),
            "_is_parent": 1,
        })

        for item in items:
            rows.append({
                "lease_name": "",
                "lease_item": item["lease_item"],
                "frequency": item["frequency"],
                "expected_series": item.get("expected_series", ""),
                "bad_uninvoiced_count": item["bad_uninvoiced_count"],
                "bad_invoiced_count": item["bad_invoiced_count"],
                "summary": _("Repairing via Last Invoice Anchor"),
                "status": "✗ Bad Row",
                "indent": 1,
                "_uninvoiced_names": frappe.as_json(item["uninvoiced_names"]),
                "_invoiced_fixes": frappe.as_json(item["invoiced_fixes"]),
            })
    return rows

@frappe.whitelist()
def fix_lease(lease_name, dry_run=True):
    if isinstance(dry_run, str): dry_run = dry_run.lower() in ("1", "true", "yes")
    audit = _audit()
    lease_items = [r for r in audit if r["lease_name"] == lease_name]
    if not lease_items: return {"status": "clean"}
    return _apply_fixes(lease_name, lease_items, dry_run)

@frappe.whitelist()
def fix_leases_bulk(lease_names, dry_run=True):
    if isinstance(dry_run, str): dry_run = dry_run.lower() in ("1", "true", "yes")
    if isinstance(lease_names, str):
        import json
        lease_names = json.loads(lease_names)

    audit = _audit()
    summary = {"dry_run": dry_run, "leases": [], "total_fixed": 0}

    for lease_name in lease_names:
        lease_items = [r for r in audit if r["lease_name"] == lease_name]
        if not lease_items: continue
        result = _apply_fixes(lease_name, lease_items, dry_run)
        summary["leases"].append(result)
        summary["total_fixed"] += 1
    return summary

def _apply_fixes(lease_name, lease_items, dry_run):
    result = {"lease": lease_name, "uninvoiced_deleted": 0, "invoiced_fixed": 0, "actions": []}
    try:
        for item in lease_items:
            for fix in item["invoiced_fixes"]:
                if not dry_run:
                    frappe.db.set_value("Lease Invoice Schedule", fix["name"], "schedule_start_date", fix["correct"])
                result["invoiced_fixed"] += 1

            for name in item["uninvoiced_names"]:
                if not dry_run:
                    frappe.delete_doc("Lease Invoice Schedule", name, force=True)
                result["uninvoiced_deleted"] += 1

        if not dry_run:
            from propms.property_management_solution.doctype.lease.lease import make_lease_invoice_schedule
            make_lease_invoice_schedule(lease_name)
            frappe.db.commit()
            
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), f"Fix failed for {lease_name}")
        result["error"] = str(e)
    return result