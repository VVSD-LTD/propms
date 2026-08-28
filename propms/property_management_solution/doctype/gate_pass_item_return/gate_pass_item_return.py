# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import flt, now_datetime


class GatePassItemReturn(Document):
	def before_save(self):
		self._apply_workflow_user_stamps()

	def on_update_after_submit(self):
		self._apply_workflow_user_stamps()
		self.db_set("authorized_by", self.authorized_by, update_modified=False)
		self.db_set("authorized_on", self.authorized_on, update_modified=False)
		self.db_set("approved_by", self.approved_by, update_modified=False)
		self.db_set("approved_on", self.approved_on, update_modified=False)

	def before_submit(self):
		source_doc = _get_source_returnable_gate_pass(self)

		if not self.authorized_by:
			self.authorized_by = source_doc.authorized_by
		if not self.authorized_on:
			self.authorized_on = source_doc.authorized_on

	def on_submit(self):
		source_doc = _get_source_returnable_gate_pass(self)

		for returned_row in self.items:
			apply_qty = flt(returned_row.quantity)
			if apply_qty <= 0:
				continue

			candidate_rows = [
				row
				for row in source_doc.items
				if row.item_name == returned_row.item_name
				and row.uom == returned_row.uom
				and (not returned_row.serial_no or row.serial_no == returned_row.serial_no)
			]

			if not candidate_rows:
				frappe.throw(f"Unable to find source item row for {returned_row.item_name}.")

			for source_row in candidate_rows:
				if apply_qty <= 0:
					break

				total_qty = flt(source_row.quantity)
				current_returned = flt(source_row.returned_qty)
				has_remaining = (
					source_row.remaining_qty is not None
					and source_row.remaining_qty != ""
				)
				current_remaining = (
					max(flt(source_row.remaining_qty), 0)
					if has_remaining
					else max(total_qty - current_returned, 0)
				)

				if current_remaining <= 0:
					continue

				adjust_qty = min(current_remaining, apply_qty)
				new_remaining_qty = max(current_remaining - adjust_qty, 0)
				new_returned_qty = max(total_qty - new_remaining_qty, 0)

				frappe.db.set_value(
					"Gate Pass Item", source_row.name, "returned_qty", new_returned_qty, update_modified=False
				)
				frappe.db.set_value(
					"Gate Pass Item", source_row.name, "remaining_qty", new_remaining_qty, update_modified=False
				)

				apply_qty -= adjust_qty

			if apply_qty > 0:
				frappe.throw(
					f"Unable to fully process quantity for {returned_row.item_name}. "
					"Remaining quantity in source document is lower than requested quantity."
				)

	def on_cancel(self):
		source_doc = _get_source_returnable_gate_pass(self, action="cancel")

		for returned_row in self.items:
			reverse_qty = flt(returned_row.quantity)
			if reverse_qty <= 0:
				continue

			candidate_rows = [
				row
				for row in source_doc.items
				if row.item_name == returned_row.item_name
				and row.uom == returned_row.uom
				and (not returned_row.serial_no or row.serial_no == returned_row.serial_no)
			]

			if not candidate_rows:
				frappe.throw(f"Unable to find source item row for {returned_row.item_name}.")

			for source_row in candidate_rows:
				if reverse_qty <= 0:
					break

				total_qty = flt(source_row.quantity)
				current_returned = flt(source_row.returned_qty)
				has_remaining = (
					source_row.remaining_qty is not None
					and source_row.remaining_qty != ""
				)
				current_remaining = (
					flt(source_row.remaining_qty)
					if has_remaining
					else max(total_qty - current_returned, 0)
				)

				if current_returned <= 0:
					continue

				adjust_qty = min(current_returned, reverse_qty)
				new_returned_qty = max(current_returned - adjust_qty, 0)
				new_remaining_qty = min(current_remaining + adjust_qty, total_qty)

				frappe.db.set_value(
					"Gate Pass Item", source_row.name, "returned_qty", new_returned_qty, update_modified=False
				)
				frappe.db.set_value(
					"Gate Pass Item", source_row.name, "remaining_qty", new_remaining_qty, update_modified=False
				)

				reverse_qty -= adjust_qty

			if reverse_qty > 0:
				frappe.throw(
					f"Unable to fully reverse quantity for {returned_row.item_name}. "
					"Returned quantity in source document is lower than cancellation quantity."
				)

	def _apply_workflow_user_stamps(self):
		state = (self.workflow_state or "").strip().lower()

		if state in {"mm approved", "om approved"}:
			self.authorized_by = frappe.session.user
			self.authorized_on = now_datetime()

		# Final approver/rejector
		if state in {"approved", "rejected"}:
			self.approved_by = frappe.session.user
			self.approved_on = now_datetime()


def _extract_source_from_remark(remark: str | None) -> str | None:
	if not remark:
		return None

	prefix = "Return against "
	if not remark.startswith(prefix):
		return None

	candidate = remark[len(prefix) :].strip()
	if not candidate:
		return None

	if frappe.db.exists("Returnable Gate Pass", candidate):
		return candidate

	return None


def _get_source_returnable_gate_pass(doc: "GatePassItemReturn", action: str = "submit"):
	source_name = doc.get("reference_no") or doc.amended_from or _extract_source_from_remark(doc.remark)
	if not source_name:
		frappe.throw(
			f"Cannot {action} return because source Returnable Gate Pass is missing. "
			"Set Reference No (or ensure remark has source doc name) and retry."
		)
	return frappe.get_doc("Returnable Gate Pass", source_name)
