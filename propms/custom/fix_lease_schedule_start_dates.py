"""
fix_lease_schedule_start_dates.py
===================================
Fixes corrupted Lease Invoice Schedule entries where `schedule_start_date`
was set from an edited `date_to_invoice` instead of the true period-start
derived from the lease's start_date cycle.

Three entry points
------------------
1. identify_corrupted_leases()        – read-only audit, returns a report dict
2. fix_corrupted_schedules(dry_run=True)  – shows what would change (dry_run=True)
                                           or executes the fix (dry_run=False)

Root cause
----------
Old code keyed period existence on `date_to_invoice`, which is user-editable.
When a user moved the invoice date (e.g. from the 20th to the 10th), the
schedule row inherited that date as `schedule_start_date`, so subsequent
regeneration created *new* rows on the 20th while the old (now-orphaned) row
sat on the 10th.  The new code keys on `schedule_start_date`, so fixing the
stored `schedule_start_date` values and deleting uninvoiced junk rows is enough
to let `make_lease_invoice_schedule` rebuild cleanly.
"""

from __future__ import unicode_literals
import frappe
from frappe.utils import getdate, add_months, add_days


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

FREQUENCY_MONTHS = {
    "Monthly": 1,
    "Bi-Monthly": 2,
    "Quarterly": 3,
    "6 months": 6,
    "Annually": 12,
}


def _expected_schedule_start_dates(lease_start, item_frequency, item_start, item_end):
    """
    Generate the sequence of period-start dates (= correct schedule_start_date
    values) for a lease item, starting from item_start up to item_end.

    Returns a list of date objects.
    """
    freq = FREQUENCY_MONTHS.get(item_frequency)
    if not freq:
        return []

    # Align the series to the lease start day-of-month cycle.
    # Walk forward from item_start in freq-month steps.
    dates = []
    current = getdate(item_start)
    end = getdate(item_end)

    while current <= end:
        dates.append(current)
        period_end = add_days(add_months(current, freq), -1)
        current = add_days(period_end, 1)

    return dates


def _get_active_leases_with_uninvoiced():
    """Return names of Active leases that have at least one uninvoiced schedule row."""
    leases = frappe.get_all(
        "Lease",
        filters={"lease_status": "Active", "docstatus": ["<", 2]},
        fields=["name", "start_date", "end_date", "skip_end_date"],
    )

    result = []
    for lease in leases:
        uninvoiced = frappe.get_all(
            "Lease Invoice Schedule",
            filters={
                "parent": lease.name,
                "invoice_number": ["in", ["", None]],
            },
            fields=["name"],
            limit=1,
            parent_doctype="Lease",
        )
        if uninvoiced:
            result.append(lease)

    return result


def _get_lease_items(lease_name):
    return frappe.get_all(
        "Lease Item",
        filters={"parent": lease_name},
        fields=["lease_item", "frequency", "amount", "valid_from", "valid_to"],
        parent_doctype="Lease",
    )


def _get_schedules_for_item(lease_name, lease_item_name):
    return frappe.get_all(
        "Lease Invoice Schedule",
        filters={"parent": lease_name, "lease_item": lease_item_name},
        fields=[
            "name", "lease_item", "date_to_invoice",
            "schedule_start_date", "invoice_number", "qty", "rate",
        ],
        order_by="schedule_start_date asc, date_to_invoice asc",
        parent_doctype="Lease",
    )


def _is_invoiced(schedule):
    return bool(schedule.invoice_number and schedule.invoice_number.strip())


# ──────────────────────────────────────────────────────────────────────────────
# 1.  IDENTIFY
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def identify_corrupted_leases():
    """
    Read-only audit.

    Returns a dict:
    {
        "leases_checked": int,
        "corrupted_leases": [
            {
                "lease": str,
                "items": [
                    {
                        "lease_item": str,
                        "uninvoiced_to_delete": [ {name, schedule_start_date, date_to_invoice} ],
                        "invoiced_to_fix":     [ {name, schedule_start_date, correct_start, date_to_invoice} ],
                    }
                ]
            }
        ]
    }
    """
    active_leases = _get_active_leases_with_uninvoiced()
    corrupted = []

    for lease in active_leases:
        lease_start = getdate(lease.start_date)
        lease_end   = getdate(lease.end_date) if lease.end_date and not lease.skip_end_date else None
        items       = _get_lease_items(lease.name)

        lease_issues = []

        for item in items:
            item_start = getdate(item.valid_from)  if item.valid_from  else lease_start
            item_end   = getdate(item.valid_to)    if item.valid_to    else lease_end
            if not item_end:
                continue  # open-ended lease, skip gap analysis

            expected_starts = _expected_schedule_start_dates(
                lease_start, item.frequency, item_start, item_end
            )
            if not expected_starts:
                continue

            expected_set = set(expected_starts)
            schedules    = _get_schedules_for_item(lease.name, item.lease_item)

            uninvoiced_to_delete = []
            invoiced_to_fix      = []

            for s in schedules:
                ssd = getdate(s.schedule_start_date) if s.schedule_start_date else None

                if _is_invoiced(s):
                    # Invoiced row: check whether schedule_start_date aligns with expected cycle
                    if ssd and ssd not in expected_set:
                        # Find the nearest expected start that is <= date_to_invoice
                        # (the period this invoice was probably billing for)
                        doi = getdate(s.date_to_invoice)
                        correct = None
                        for exp in sorted(expected_starts):
                            if exp <= doi:
                                correct = exp
                        if correct and correct != ssd:
                            invoiced_to_fix.append({
                                "name": s.name,
                                "schedule_start_date": str(ssd),
                                "correct_start": str(correct),
                                "date_to_invoice": str(s.date_to_invoice),
                                "invoice_number": s.invoice_number,
                            })
                else:
                    # Uninvoiced row: flag if its schedule_start_date is NOT in expected set
                    if ssd and ssd not in expected_set:
                        uninvoiced_to_delete.append({
                            "name": s.name,
                            "schedule_start_date": str(ssd),
                            "date_to_invoice": str(s.date_to_invoice),
                        })
                    elif not ssd:
                        # No schedule_start_date at all – also suspicious
                        uninvoiced_to_delete.append({
                            "name": s.name,
                            "schedule_start_date": None,
                            "date_to_invoice": str(s.date_to_invoice),
                            "note": "Missing schedule_start_date",
                        })

            if uninvoiced_to_delete or invoiced_to_fix:
                lease_issues.append({
                    "lease_item": item.lease_item,
                    "frequency": item.frequency,
                    "item_start": str(item_start),
                    "item_end":   str(item_end),
                    "expected_starts": [str(d) for d in expected_starts],
                    "uninvoiced_to_delete": uninvoiced_to_delete,
                    "invoiced_to_fix":      invoiced_to_fix,
                })

        if lease_issues:
            corrupted.append({
                "lease": lease.name,
                "lease_start": str(lease_start),
                "items": lease_issues,
            })

    report = {
        "leases_checked": len(active_leases),
        "corrupted_leases_count": len(corrupted),
        "corrupted_leases": corrupted,
    }

    # Pretty-print to console / error log for easy reading
    import json
    frappe.msgprint(
        "<pre>" + json.dumps(report, indent=2, default=str) + "</pre>",
        title="Corrupted Lease Schedule Audit",
        wide=True,
    )
    return report


# ──────────────────────────────────────────────────────────────────────────────
# 2.  FIX  (dry_run=True → simulate only, dry_run=False → execute)
# ──────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def fix_corrupted_schedules(dry_run=True):
    """
    Fix corrupted Lease Invoice Schedule rows for all Active leases.

    Steps per lease item
    --------------------
    A) Delete uninvoiced rows whose schedule_start_date does not align with
       the expected period-start cycle.
    B) Correct schedule_start_date on invoiced rows that were stamped with
       the wrong date (usually the edited date_to_invoice day).
    C) After both steps, call make_lease_invoice_schedule() to rebuild the
       schedule cleanly.  (Only in live mode.)

    Parameters
    ----------
    dry_run : bool  (pass "1"/"0" or True/False from whitelist)
        True  → log what would happen, touch nothing.
        False → execute deletions, updates, and schedule rebuild.

    Returns a summary dict.
    """
    # Frappe whitelist passes strings
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() in ("1", "true", "yes")

    audit = identify_corrupted_leases()

    summary = {
        "dry_run": dry_run,
        "leases_processed": 0,
        "uninvoiced_deleted": 0,
        "invoiced_fixed": 0,
        "schedules_rebuilt": 0,
        "errors": [],
        "detail": [],
    }

    for lease_info in audit["corrupted_leases"]:
        lease_name   = lease_info["lease"]
        lease_detail = {
            "lease": lease_name,
            "actions": [],
        }

        try:
            for item_info in lease_info["items"]:
                lease_item = item_info["lease_item"]

                # ── A) Delete bad uninvoiced rows ──────────────────────────
                for row in item_info["uninvoiced_to_delete"]:
                    msg = (
                        f"[DELETE uninvoiced] {lease_name} / {lease_item} | "
                        f"name={row['name']} | "
                        f"schedule_start_date={row['schedule_start_date']} | "
                        f"date_to_invoice={row['date_to_invoice']}"
                    )
                    lease_detail["actions"].append({"action": "DELETE_UNINVOICED", **row, "lease_item": lease_item})

                    if not dry_run:
                        if frappe.db.exists("Lease Invoice Schedule", row["name"]):
                            frappe.delete_doc(
                                "Lease Invoice Schedule",
                                row["name"],
                                force=True,
                                ignore_permissions=True,
                            )
                            summary["uninvoiced_deleted"] += 1
                            frappe.log_error(msg, "fix_schedules: deleted uninvoiced")
                    else:
                        summary["uninvoiced_deleted"] += 1  # simulated count

                # ── B) Correct schedule_start_date on invoiced rows ────────
                for row in item_info["invoiced_to_fix"]:
                    msg = (
                        f"[FIX invoiced] {lease_name} / {lease_item} | "
                        f"name={row['name']} | "
                        f"schedule_start_date {row['schedule_start_date']} → {row['correct_start']} | "
                        f"invoice={row['invoice_number']}"
                    )
                    lease_detail["actions"].append({"action": "FIX_INVOICED_SSD", **row, "lease_item": lease_item})

                    if not dry_run:
                        if frappe.db.exists("Lease Invoice Schedule", row["name"]):
                            frappe.db.set_value(
                                "Lease Invoice Schedule",
                                row["name"],
                                "schedule_start_date",
                                row["correct_start"],
                            )
                            summary["invoiced_fixed"] += 1
                            frappe.log_error(msg, "fix_schedules: fixed invoiced ssd")
                    else:
                        summary["invoiced_fixed"] += 1  # simulated count

            # ── C) Rebuild schedule for this lease ────────────────────────
            if not dry_run:
                try:
                    from propms.lease.doctype.lease.lease import make_lease_invoice_schedule
                    make_lease_invoice_schedule(lease_name)
                    summary["schedules_rebuilt"] += 1
                    lease_detail["schedule_rebuilt"] = True
                except Exception as rebuild_err:
                    err_msg = f"Rebuild failed for {lease_name}: {rebuild_err}"
                    summary["errors"].append(err_msg)
                    frappe.log_error(err_msg, "fix_schedules: rebuild error")
                    lease_detail["schedule_rebuilt"] = False
                    lease_detail["rebuild_error"] = str(rebuild_err)
            else:
                lease_detail["schedule_rebuilt"] = "skipped (dry run)"

            summary["leases_processed"] += 1
            summary["detail"].append(lease_detail)

        except Exception as e:
            err_msg = f"Error processing lease {lease_name}: {e}"
            summary["errors"].append(err_msg)
            frappe.log_error(err_msg, "fix_schedules: lease error")

    import json
    mode = "DRY RUN (no changes made)" if dry_run else "LIVE EXECUTION"
    frappe.msgprint(
        f"<b>Mode: {mode}</b><br><pre>" + json.dumps(summary, indent=2, default=str) + "</pre>",
        title="Fix Lease Schedule Start Dates – Summary",
        wide=True,
    )
    return summary