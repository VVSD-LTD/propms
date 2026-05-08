// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

/* ============================================================
   Corrupted Lease Schedules – Report JS
   File: propms/property_management_solution/report/corrupted_lease_schedules/corrupted_lease_schedules.js
   ============================================================ */

frappe.query_reports["Corrupted Lease Schedules"] = {

    filters: [],

    // ── Tree-view indentation ────────────────────────────────
    get_datatable_options(options) {
        return Object.assign(options, {
            treeView: true,
            checkboxColumn: false,
        });
    },

    // ── Column formatter: colour-code status & bold parents ──
    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (!data) return value;

        const isParent = data._is_parent == 1;

        if (column.fieldname === "lease_name" && isParent && value) {
            value = `<span style="font-weight:700;color:#1a1a2e;">${value}</span>`;
        }

        if (column.fieldname === "status") {
            if ((value || "").includes("Corrupted")) {
                value = `<span style="
                    background:#fff0f0;
                    color:#c0392b;
                    font-weight:600;
                    padding:2px 8px;
                    border-radius:4px;
                    font-size:11px;
                    border:1px solid #f5c6c6;
                ">${value}</span>`;
            } else if ((value || "").includes("Bad rows")) {
                value = `<span style="
                    background:#fff8e1;
                    color:#d68910;
                    font-weight:600;
                    padding:2px 8px;
                    border-radius:4px;
                    font-size:11px;
                    border:1px solid #fce08d;
                ">${value}</span>`;
            } else if ((value || "").includes("OK")) {
                value = `<span style="
                    background:#eafaf1;
                    color:#1e8449;
                    font-weight:600;
                    padding:2px 8px;
                    border-radius:4px;
                    font-size:11px;
                    border:1px solid #a9dfbf;
                ">${value}</span>`;
            }
        }

        if (column.fieldname === "bad_uninvoiced_count" && parseInt(value) > 0) {
            value = `<span style="color:#c0392b;font-weight:700;">${value}</span>`;
        }

        if (column.fieldname === "bad_invoiced_count" && parseInt(value) > 0) {
            value = `<span style="color:#d68910;font-weight:700;">${value}</span>`;
        }

        // Expected series: highlight the anchor day prominently
        if (column.fieldname === "expected_series" && value && value.includes("·")) {
            const parts = value.split("·");
            const anchor = (parts[0] || "").trim();   // e.g. "20th"
            const dates  = (parts[1] || "").trim();   // e.g. "2025-08-20, …"
            value = `<span style="
                display:inline-flex;
                align-items:center;
                gap:6px;
                font-size:11px;
            ">
                <span style="
                    background:#1a1a2e;
                    color:#fff;
                    font-weight:700;
                    padding:1px 7px;
                    border-radius:4px;
                    font-size:11px;
                    letter-spacing:0.5px;
                ">${anchor}</span>
                <span style="color:#555;font-family:'Courier New',monospace;">${dates}</span>
            </span>`;
        }

        // Document name: render as a clickable link to the Lease form
        if (column.fieldname === "document_name" && value && data && data._is_parent == 1) {
            const lease = data.document_name || data.lease_name;
            value = `<a
                href="/app/lease/${encodeURIComponent(lease)}"
                style="
                    color:#2563eb;
                    font-weight:600;
                    font-size:12px;
                    text-decoration:none;
                    border-bottom:1px dashed #93c5fd;
                    padding-bottom:1px;
                "
                title="Open Lease ${frappe.utils.escape_html(lease)}"
                onclick="event.stopPropagation();"
            >${frappe.utils.escape_html(lease)} ↗</a>`;
        }

        return value;
    },

    // ── Custom buttons ────────────────────────────────────────
    onload(report) {
        // ── Button 1: Fix Selected ───────────────────────────
        report.page.add_button(__("🔧 Fix Selected"), function () {
            const selected = _get_selected_parent_row(report);

            if (selected === "none") {
                frappe.msgprint({
                    title: __("No Row Selected"),
                    message: __("Please click on a <b>Lease</b> row (the top-level row) to select it, then click Fix Selected."),
                    indicator: "orange",
                });
                return;
            }

            if (selected === "child") {
                frappe.msgprint({
                    title: __("Child Row Selected"),
                    message: __("You have selected a <b>lease item</b> row (child). Please select the <b>Lease</b> row (parent) instead."),
                    indicator: "red",
                });
                return;
            }

            const lease_name = selected;
            _confirm_and_fix_single(lease_name);
        }, "Actions");

        // ── Button 2: Bulk Fix ───────────────────────────────
        report.page.add_button(__("⚡ Bulk Fix"), function () {
            _open_bulk_fix_dialog(report);
        }, "Actions");

        // Style the action buttons group
        setTimeout(() => {
            report.page.wrapper
                .find(".page-actions .btn-group")
                .last()
                .find("button")
                .css({
                    "font-size": "12px",
                    "font-weight": "600",
                    "letter-spacing": "0.3px",
                });
        }, 300);
    },
};


/* ─── Helpers ────────────────────────────────────────────────────────────── */

/**
 * Find which row is currently focused/selected in the datatable.
 * Returns: "none" | "child" | "<lease_name string>"
 */
function _get_selected_parent_row(report) {
    // Frappe datatable stores the checked rows in report.datatable.rowmanager
    const dt = report.datatable;
    if (!dt) return "none";

    // Try checked rows first
    const checked = dt.rowmanager && dt.rowmanager.getCheckedRows
        ? dt.rowmanager.getCheckedRows()
        : [];

    // Fall back to highlighted row
    const highlighted = dt.rowmanager && dt.rowmanager.highlightedRowIndex != null
        ? [dt.rowmanager.highlightedRowIndex]
        : [];

    const indices = checked.length ? checked : highlighted;
    if (!indices.length) return "none";

    // Get data for first selected index
    const rowIndex = indices[0];
    const row = dt.datamanager && dt.datamanager.getRow
        ? dt.datamanager.getRow(rowIndex)
        : null;

    if (!row) return "none";

    // Each cell in the row is an object; find the lease_name cell
    // Frappe stores row data in the row array with meta
    const data = report.data && report.data[rowIndex];
    if (!data) return "none";

    if (data._is_parent == 1) {
        return data.lease_name;
    } else {
        return "child";
    }
}


/**
 * Show a dry-run preview, then confirm to execute the fix for one lease.
 */
function _confirm_and_fix_single(lease_name) {
    frappe.call({
        method: "propms.property_management_solution.report.corrupted_lease_schedules.corrupted_lease_schedules.fix_lease",
        args: { lease_name, dry_run: true },
        freeze: true,
        freeze_message: __("Analysing {0}…", [lease_name]),
        callback(r) {
            if (!r.message) return;
            const res = r.message;

            if (res.status === "clean") {
                frappe.msgprint({ title: __("All Good"), message: res.message, indicator: "green" });
                return;
            }

            const actions = res.actions || [];
            const deletes = actions.filter(a => a.type === "DELETE_UNINVOICED");
            const fixes   = actions.filter(a => a.type === "FIX_SSD");

            let preview_html = `
                <div style="font-family:'Courier New',monospace;font-size:12px;line-height:1.8;">
                <p style="font-size:14px;font-weight:600;margin-bottom:12px;">
                    Preview for <span style="color:#2c3e50;">${lease_name}</span>
                </p>`;

            if (deletes.length) {
                preview_html += `<p style="color:#c0392b;font-weight:600;">
                    🗑 ${deletes.length} uninvoiced row(s) will be deleted:</p><ul>`;
                deletes.forEach(d => {
                    preview_html += `<li>${d.lease_item} — row <code>${d.name}</code></li>`;
                });
                preview_html += `</ul>`;
            }

            if (fixes.length) {
                preview_html += `<p style="color:#d68910;font-weight:600;margin-top:8px;">
                    🔧 ${fixes.length} invoiced row(s) schedule_start_date will be corrected:</p><ul>`;
                fixes.forEach(f => {
                    const issueLabel = f.issue === "duplicate_ssd"
                        ? `<span style="background:#fff0f0;color:#c0392b;font-size:10px;padding:1px 5px;border-radius:3px;margin-left:4px;">duplicate period</span>`
                        : `<span style="background:#fff8e1;color:#d68910;font-size:10px;padding:1px 5px;border-radius:3px;margin-left:4px;">wrong cycle</span>`;
                    preview_html += `<li>${f.lease_item} — <code>${f.from}</code> → <code>${f.to}</code>
                        ${issueLabel}
                        &nbsp;<span style="color:#777;">(inv: ${f.invoice})</span></li>`;
                });
                preview_html += `</ul>`;
            }

            preview_html += `<p style="margin-top:12px;color:#555;">
                After deletion &amp; correction, the schedule will be <b>rebuilt automatically</b>.
            </p></div>`;

            frappe.confirm(
                preview_html + `<br><b>Execute the fix now?</b>`,
                function () {
                    // User confirmed → live run
                    frappe.call({
                        method: "propms.property_management_solution.report.corrupted_lease_schedules.corrupted_lease_schedules.fix_lease",
                        args: { lease_name, dry_run: false },
                        freeze: true,
                        freeze_message: __("Fixing {0}…", [lease_name]),
                        callback(r2) {
                            const res2 = r2.message || {};
                            frappe.msgprint({
                                title: __("Fix Complete"),
                                message: __(
                                    "<b>{0}</b><br>{1} uninvoiced deleted · {2} invoiced fixed · Schedule rebuilt: {3}",
                                    [
                                        lease_name,
                                        res2.uninvoiced_deleted || 0,
                                        res2.invoiced_fixed || 0,
                                        res2.rebuilt ? "✓ Yes" : "✗ No",
                                    ]
                                ),
                                indicator: res2.error ? "red" : "green",
                            });
                            frappe.query_report.refresh();
                        },
                    });
                }
            );
        },
    });
}


/**
 * Open the bulk-fix dialog with a multiselect list of corrupted leases.
 */
function _open_bulk_fix_dialog(report) {
    // Collect all parent rows from current report data
    const parent_rows = (report.data || []).filter(d => d._is_parent == 1);

    if (!parent_rows.length) {
        frappe.msgprint({
            title: __("Nothing to Fix"),
            message: __("The report shows no corrupted leases. Try refreshing."),
            indicator: "green",
        });
        return;
    }

    // Build checkbox HTML for each lease
    const items_html = parent_rows.map((row, i) => {
        const uninv = row.bad_uninvoiced_count || 0;
        const inv   = row.bad_invoiced_count   || 0;
        return `
        <label class="bulk-fix-item" style="
            display:flex;
            align-items:flex-start;
            gap:10px;
            padding:10px 14px;
            margin-bottom:6px;
            border-radius:6px;
            border:1px solid #e0e0e0;
            background:#fafafa;
            cursor:pointer;
            transition:background 0.15s;
        " onmouseover="this.style.background='#f0f4ff'" onmouseout="this.style.background='#fafafa'">
            <input type="checkbox" class="bulk-lease-cb" value="${frappe.utils.escape_html(row.lease_name)}"
                style="margin-top:3px;accent-color:#2563eb;width:15px;height:15px;cursor:pointer;">
            <div>
                <div style="font-weight:700;font-size:13px;color:#1a1a2e;">${frappe.utils.escape_html(row.lease_name)}</div>
                <div style="font-size:11px;color:#666;margin-top:2px;">
                    ${uninv ? `<span style="color:#c0392b;">🗑 ${uninv} uninvoiced to delete</span>` : ""}
                    ${uninv && inv ? " &nbsp;·&nbsp; " : ""}
                    ${inv  ? `<span style="color:#d68910;">🔧 ${inv} invoiced to fix</span>` : ""}
                </div>
            </div>
        </label>`;
    }).join("");

    const dialog = new frappe.ui.Dialog({
        title: __("⚡ Bulk Fix – Select Leases"),
        size: "large",
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "bulk_html",
                options: `
                <div style="margin-bottom:10px;display:flex;gap:8px;">
                    <button class="btn btn-xs btn-default" id="bulk-select-all"
                        style="font-size:11px;">Select All</button>
                    <button class="btn btn-xs btn-default" id="bulk-deselect-all"
                        style="font-size:11px;">Deselect All</button>
                    <span style="margin-left:auto;font-size:11px;color:#888;align-self:center;">
                        ${parent_rows.length} corrupted lease(s) found
                    </span>
                </div>
                <div style="
                    max-height:380px;
                    overflow-y:auto;
                    padding-right:4px;
                ">
                    ${items_html}
                </div>
                <div style="
                    margin-top:14px;
                    padding:10px 14px;
                    border-radius:6px;
                    background:#fff8e1;
                    border:1px solid #fce08d;
                    font-size:12px;
                    color:#856404;
                ">
                    ⚠ A <b>dry-run preview</b> will be shown before any changes are made.
                </div>`,
            },
        ],
        primary_action_label: __("Preview & Fix"),
        primary_action() {
            const selected = [];
            dialog.wrapper
                .find(".bulk-lease-cb:checked")
                .each(function () {
                    selected.push($(this).val());
                });

            if (!selected.length) {
                frappe.msgprint(__("Please select at least one lease."));
                return;
            }

            dialog.hide();
            _bulk_dry_run_then_confirm(selected);
        },
    });

    dialog.show();

    // Wire up select/deselect all
    dialog.wrapper.find("#bulk-select-all").on("click", function () {
        dialog.wrapper.find(".bulk-lease-cb").prop("checked", true);
    });
    dialog.wrapper.find("#bulk-deselect-all").on("click", function () {
        dialog.wrapper.find(".bulk-lease-cb").prop("checked", false);
    });
}


/**
 * Dry-run the selected leases, show a summary, then confirm execution.
 */
function _bulk_dry_run_then_confirm(lease_names) {
    frappe.call({
        method: "propms.property_management_solution.report.corrupted_lease_schedules.corrupted_lease_schedules.fix_leases_bulk",
        args: { lease_names: JSON.stringify(lease_names), dry_run: true },
        freeze: true,
        freeze_message: __("Simulating fixes for {0} lease(s)…", [lease_names.length]),
        callback(r) {
            const summary = r.message || {};
            const leases  = summary.leases || [];

            let html = `
            <div style="font-family:'Courier New',monospace;font-size:12px;line-height:1.8;max-height:420px;overflow-y:auto;">
            <p style="font-size:14px;font-weight:700;font-family:sans-serif;margin-bottom:14px;">
                Dry-Run Summary — ${leases.length} lease(s)
            </p>`;

            leases.forEach(res => {
                const deletes = (res.actions || []).filter(a => a.type === "DELETE_UNINVOICED");
                const fixes   = (res.actions || []).filter(a => a.type === "FIX_SSD");

                html += `
                <div style="
                    border:1px solid #ddd;
                    border-radius:6px;
                    padding:10px 14px;
                    margin-bottom:10px;
                    background:#fafafa;
                ">
                <div style="font-weight:700;font-size:13px;font-family:sans-serif;color:#1a1a2e;margin-bottom:6px;">
                    ${frappe.utils.escape_html(res.lease)}
                </div>`;

                if (deletes.length) {
                    html += `<div style="color:#c0392b;">🗑 Delete ${deletes.length} uninvoiced row(s)</div>`;
                    deletes.forEach(d => {
                        html += `<div style="padding-left:16px;color:#555;">
                            ${frappe.utils.escape_html(d.lease_item)} — <code>${d.name}</code></div>`;
                    });
                }

                if (fixes.length) {
                    html += `<div style="color:#d68910;margin-top:4px;">🔧 Fix ${fixes.length} invoiced row(s)</div>`;
                    fixes.forEach(f => {
                        const issueLabel = f.issue === "duplicate_ssd"
                            ? `<span style="background:#fff0f0;color:#c0392b;font-size:10px;padding:1px 4px;border-radius:3px;"">duplicate period</span>`
                            : `<span style="background:#fff8e1;color:#d68910;font-size:10px;padding:1px 4px;border-radius:3px;">wrong cycle</span>`;
                        html += `<div style="padding-left:16px;color:#555;">
                            ${frappe.utils.escape_html(f.lease_item)} —
                            <code>${f.from}</code> → <code>${f.to}</code>
                            ${issueLabel}</div>`;
                    });
                }

                html += `</div>`;
            });

            html += `
            <div style="
                margin-top:10px;
                padding:10px 14px;
                border-radius:6px;
                background:#e8f4fd;
                border:1px solid #bee3f8;
                font-family:sans-serif;
                font-size:12px;
                color:#1a5276;
            ">
                Total: <b>${summary.total_uninvoiced_deleted} uninvoiced deleted</b> ·
                <b>${summary.total_invoiced_fixed} invoiced fixed</b>
            </div></div>`;

            frappe.confirm(
                html + `<br><b>Execute all fixes now?</b>`,
                function () {
                    frappe.call({
                        method: "propms.property_management_solution.report.corrupted_lease_schedules.corrupted_lease_schedules.fix_leases_bulk",
                        args: { lease_names: JSON.stringify(lease_names), dry_run: false },
                        freeze: true,
                        freeze_message: __("Executing fixes…"),
                        callback(r2) {
                            const s = r2.message || {};
                            const errMsg = (s.errors || []).length
                                ? `<br><span style="color:red;">Errors: ${s.errors.join(", ")}</span>`
                                : "";
                            frappe.msgprint({
                                title: __("Bulk Fix Complete"),
                                message: __(
                                    "<b>{0} lease(s) processed</b><br>" +
                                    "{1} uninvoiced deleted · {2} invoiced fixed · {3} schedule(s) rebuilt{4}",
                                    [
                                        (s.leases || []).length,
                                        s.total_uninvoiced_deleted || 0,
                                        s.total_invoiced_fixed || 0,
                                        s.total_rebuilt || 0,
                                        errMsg,
                                    ]
                                ),
                                indicator: (s.errors || []).length ? "orange" : "green",
                            });
                            frappe.query_report.refresh();
                        },
                    });
                }
            );
        },
    });
}