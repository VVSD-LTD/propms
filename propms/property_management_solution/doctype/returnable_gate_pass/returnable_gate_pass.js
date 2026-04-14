// Copyright (c) 2026, VV Systems Developer LTD and contributors
// For license information, please see license.txt

frappe.ui.form.on("Returnable Gate Pass", {
	setup(frm) {
		sync_remaining_qty(frm);
	},

	items_add(frm) {
		sync_remaining_qty(frm);
	},

	validate(frm) {
		sync_remaining_qty(frm);
	},

	refresh(frm) {
		if (!frm.is_new() && frm.doc.items?.length) {
			sync_remaining_qty(frm);
		}

		if (frm.is_new() || !frm.doc.items?.length) {
			return;
		}

		const approval_state = (frm.doc.workflow_state || "").trim();
		if (approval_state !== "Approved") {
			return;
		}

		const has_pending_items = (frm.doc.items || []).some((item) => {
			const remaining_qty = flt(item.remaining_qty || 0);
			return remaining_qty > 0;
		});

		if (!has_pending_items) {
			return;
		}

		frm.add_custom_button(__("Create Return"), () => {
			open_return_approval_dialog(frm);
		});
	},
});

function open_return_approval_dialog(frm) {
	const items = (frm.doc.items || [])
		.map((item) => {
			const quantity = flt(item.quantity || 0);
			const remaining_qty = Math.max(flt(item.remaining_qty || 0), 0);

			return {
				row_name: item.name,
				item_name: item.item_name,
				uom: item.uom,
				quantity,
				remaining_qty,
				return_qty: remaining_qty,
				__checked: 1,
			};
		})
		.filter((item) => item.remaining_qty > 0);

	if (!items.length) {
		frappe.msgprint(__("All items are already returned."));
		return;
	}

	const dialog = new frappe.ui.Dialog({
		title: __("Returned Items"),
		size: "large",
		fields: [
			{
				fieldname: "items",
				fieldtype: "Table",
				label: __("Items"),
				cannot_add_rows: true,
				cannot_delete_rows: true,
				in_place_edit: true,
				reqd: 1,
				data: items,
				fields: [
					{
						fieldname: "row_name",
						fieldtype: "Data",
						hidden: 1,
					},
					{
						fieldname: "item_name",
						fieldtype: "Data",
						label: __("Item"),
						read_only: 1,
						columns: 3,
						in_list_view: 1,
					},
					{
						fieldname: "uom",
						fieldtype: "Data",
						label: __("UOM"),
						read_only: 1,
						columns: 1,
						in_list_view: 1,
					},
					{
						fieldname: "quantity",
						fieldtype: "Float",
						label: __("Issued Qty"),
						read_only: 1,
						columns: 0,
						in_list_view: 1,
					},
					{
						fieldname: "return_qty",
						fieldtype: "Float",
						label: __("Return Qty"),
						columns: 0,
						default: 0,
						in_list_view: 1,
					},
				],
			},
		],
		primary_action_label: __("Return"),
		primary_action() {
			const grid = dialog.fields_dict.items.grid;
			const grid_data = grid.get_data() || [];
			let selected_rows = grid_data.filter((row) => row.__checked);

			// Fallback for table-dialog edge cases where selection metadata is not synced.
			if (!selected_rows.length) {
				selected_rows = grid.get_selected_children() || [];
			}

			const rows = selected_rows.filter((row) => flt(row.return_qty) > 0);

			if (!selected_rows.length) {
				frappe.msgprint(__("Select at least one item to approve."));
				return;
			}

			if (!rows.length) {
				frappe.msgprint(__("Enter return quantity for at least one item."));
				return;
			}

			for (const row of rows) {
				const source_row = (frm.doc.items || []).find((item) => item.name === row.row_name);
				const max_return_qty = flt(source_row?.remaining_qty || 0);

				if (flt(row.return_qty) > max_return_qty) {
					frappe.msgprint(
						__("Return quantity cannot be greater than remaining quantity for item {0}.", [
							row.item_name,
						])
					);
					return;
				}
			}

			frappe.call({
				method: "propms.property_management_solution.doctype.returnable_gate_pass.returnable_gate_pass.approve_item_return",
				args: {
					returnable_gate_pass: frm.doc.name,
					items: rows,
				},
				freeze: true,
				freeze_message: __("Approving returned items..."),
				callback: function (r) {
					if (!r.exc) {
						dialog.hide();
						show_return_documents_dialog(frm, r.message?.gate_pass_item_return);
					}
				},
			});
		},
	});

	dialog.show();
	dialog.fields_dict.items.grid.refresh();
}

function show_return_documents_dialog(frm, created_return) {
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Gate Pass Item Return",
			filters: { reference_no: frm.doc.name },
			fields: ["name", "workflow_state", "creation"],
			order_by: "creation desc",
			limit_page_length: 50,
		},
		callback: function (res) {
			const docs = res.message || [];
			if (!docs.length) {
				frm.reload_doc();
				return;
			}
			if (docs.length === 1) {
				frappe.set_route("Form", "Gate Pass Item Return", docs[0].name);
				return;
			}
			const rows_html = docs
				.map((doc) => {
					const state = doc.workflow_state || __("Pending");
					const is_created = created_return && doc.name === created_return;
					const new_tag = is_created ? ` <span class="indicator-pill green">${__("New")}</span>` : "";
					return `<tr>
						<td><a href="/app/gate-pass-item-return/${doc.name}" target="_blank">${doc.name}</a>${new_tag}</td>
						<td>${frappe.utils.escape_html(state)}</td>
					</tr>`;
				})
				.join("");

			const d = new frappe.ui.Dialog({
				title: __("Return Documents"),
				fields: [{ fieldtype: "HTML", fieldname: "return_docs_html" }],
				primary_action_label: __("Close"),
				primary_action() {
					d.hide();
					frm.reload_doc();
				},
			});

			d.fields_dict.return_docs_html.$wrapper.html(
				`<div style="max-height: 320px; overflow: auto;">
					<table class="table table-bordered">
						<thead>
							<tr>
								<th>${__("Gate Pass Item Return")}</th>
								<th>${__("Workflow State")}</th>
							</tr>
						</thead>
						<tbody>${rows_html}</tbody>
					</table>
				</div>`
			);
			d.show();
		},
	});
}

function sync_remaining_qty(frm) {
	(frm.doc.items || []).forEach((row) => {
		const quantity = flt(row.quantity || 0);
		const has_remaining_qty = row.remaining_qty !== undefined && row.remaining_qty !== null && row.remaining_qty !== "";
		// _previous_quantity is client-only and not loaded from the server. Treat undefined/null as
		// "not yet tracked" so the first sync after load does not apply a false full-quantity delta
		// (which was resetting remaining/returned to match issued qty and hiding DB updates).
		const prevRaw = row._previous_quantity;
		const previous_tracked = prevRaw !== undefined && prevRaw !== null;
		const previous_quantity = previous_tracked ? flt(prevRaw) : null;
		const quantity_delta = previous_quantity === null ? 0 : quantity - previous_quantity;

		// Auto-initialize remaining qty from quantity for new/empty rows.
		if (row.__islocal || !has_remaining_qty) {
			row.remaining_qty = quantity;
		} else if (quantity_delta !== 0) {
			// Keep already-returned qty intact by shifting remaining with quantity delta.
			row.remaining_qty = flt(row.remaining_qty || 0) + quantity_delta;
		}

		// Keep returned qty derived from quantity - remaining qty.
		row.returned_qty = Math.max(quantity - flt(row.remaining_qty || 0), 0);
		// Avoid invalid negative values.
		if (row.remaining_qty < 0) {
			row.remaining_qty = 0;
		}
		if (row.remaining_qty > quantity) {
			row.remaining_qty = quantity;
		}

		row._previous_quantity = quantity;
	});

	frm.refresh_field("items");
}

frappe.ui.form.on("Gate Pass Item", {
	quantity(frm) {
		sync_remaining_qty(frm);
	},
});
