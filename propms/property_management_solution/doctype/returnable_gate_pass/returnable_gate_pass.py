# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.model.document import Document
from frappe.utils import flt


class ReturnableGatePass(Document):
	pass


@frappe.whitelist()
def approve_item_return(returnable_gate_pass: str, items):
	if not returnable_gate_pass:
		frappe.throw("Returnable Gate Pass is required.")

	if isinstance(items, str):
		items = json.loads(items)

	if not items:
		frappe.throw("At least one returned item is required.")

	doc = frappe.get_doc("Returnable Gate Pass", returnable_gate_pass)

	approval_state = (doc.get("workflow_state") or doc.get("status") or "").strip()
	if approval_state != "Approved":
		frappe.throw("Return can only be created for Approved Returnable Gate Pass.")

	existing_return = frappe.db.get_value(
		"Gate Pass Item Return",
		{"reference_no": doc.name, "docstatus": ["!=", 2]},
		["name", "docstatus"],
		order_by="creation desc",
		as_dict=True,
	)

	item_row_map = {row.name: row for row in doc.items}
	processed_items = []

	for row in items:
		row_name = row.get("row_name")
		return_qty = flt(row.get("return_qty"))

		if not row_name or return_qty <= 0:
			continue

		if row_name not in item_row_map:
			frappe.throw(f"Invalid item row: {row_name}")

		source_row = item_row_map[row_name]
		total_qty = flt(source_row.quantity)
		remaining_qty = max(flt(source_row.remaining_qty), 0)

		if return_qty > remaining_qty:
			frappe.throw(f"Return quantity exceeds remaining quantity for item {source_row.item_name}.")

		new_remaining_qty = max(remaining_qty - return_qty, 0)

		processed_items.append(
			{
				"item_name": source_row.item_name,
				"uom": source_row.uom,
				"quantity": return_qty,
				"serial_no": source_row.serial_no,
				"description": source_row.description,
				"remark": source_row.remark,
				# For visibility on Gate Pass Item Return rows
				"returned_qty": return_qty,
				"remaining_qty": new_remaining_qty,
			}
		)

	if not processed_items:
		frappe.throw("No valid returned quantity found.")

	if existing_return and existing_return.docstatus == 1:
		return {
			"gate_pass_item_return": existing_return.name,
			"already_exists": 1,
		}

	if existing_return and existing_return.docstatus == 0:
		gate_pass_item_return = frappe.get_doc("Gate Pass Item Return", existing_return.name)
	else:
		gate_pass_item_return = frappe.new_doc("Gate Pass Item Return")
		gate_pass_item_return.reference_no = doc.name

	# Copy required header fields from Returnable Gate Pass (only these).
	if not gate_pass_item_return.residenttenantcompany_name:
		gate_pass_item_return.residenttenantcompany_name = doc.residenttenantcompany_name
	if not gate_pass_item_return.department_if_applicable:
		gate_pass_item_return.department_if_applicable = doc.department_if_applicable
	if not gate_pass_item_return.officeshopapartment_no:
		gate_pass_item_return.officeshopapartment_no = doc.officeshopapartment_no

	existing_signatures = {
		(
			row.item_name or "",
			row.uom or "",
			row.serial_no or "",
			flt(row.quantity),
		)
		for row in (gate_pass_item_return.items or [])
	}

	for returned_item in processed_items:
		signature = (
			returned_item.get("item_name") or "",
			returned_item.get("uom") or "",
			returned_item.get("serial_no") or "",
			flt(returned_item.get("quantity")),
		)
		if signature in existing_signatures:
			continue
		gate_pass_item_return.append("items", returned_item)
		existing_signatures.add(signature)

	if gate_pass_item_return.is_new():
		gate_pass_item_return.insert()
	else:
		gate_pass_item_return.save()

	return {
		"gate_pass_item_return": gate_pass_item_return.name,
	}
