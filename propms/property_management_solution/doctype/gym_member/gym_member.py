# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document


class GymMember(Document):
	pass


@frappe.whitelist()
def receive_gym_member(person):
	"""Create or update one Gym Member from a device person record.

	Accepts the inner person object (dict or JSON string). Looks up by
	external id first, then by PIN. Passwords and photos are ignored.
	"""
	person = _as_dict(person)
	external_id = _text(person.get("id"))
	pin = _text(person.get("pin"))
	if not external_id or not pin:
		frappe.throw(_("'id' and 'pin' are required."))

	values = {
		"external_id": external_id,
		"pin": pin,
		"person_name": _text(person.get("name")) or pin,
		"last_name": _text(person.get("lastName")),
		"dept_code": _text(person.get("deptCode")),
		"dept_name": _text(person.get("deptName")),
		"gender": _text(person.get("gender")),
		"mobile_phone": _text(person.get("mobilePhone")),
		"email": _text(person.get("email")),
		"card_no": _text(person.get("cardNo")),
		"acc_level_ids": _text(person.get("accLevelIds")),
		"status": "Disabled" if _is_disabled(person.get("isDisabled")) else "Active",
	}

	name = frappe.db.get_value("Gym Member", {"external_id": external_id}, "name")
	if not name:
		name = frappe.db.get_value("Gym Member", {"pin": pin}, "name")

	if name:
		doc = frappe.get_doc("Gym Member", name)
		if _same_values(doc, values):
			return doc.name
		doc.update(values)
		doc.save()
	else:
		doc = frappe.get_doc({"doctype": "Gym Member", **values})
		doc.insert()

	return doc.name


@frappe.whitelist()
def set_gym_member_status(pin, status="Disabled"):
	"""Set Gym Member status only. Other fields stay as they are.

	Used when a door event names a person as disabled. A missing member
	returns nothing so the sync can move on; the next person pull creates them.
	"""
	pin = _text(pin)
	status = _text(status) or "Disabled"
	if not pin:
		frappe.throw(_("'pin' is required."))
	if status not in ("Active", "Disabled"):
		frappe.throw(_("Status must be Active or Disabled."))

	name = frappe.db.get_value("Gym Member", {"pin": pin}, "name")
	if not name:
		return None
	if frappe.db.get_value("Gym Member", name, "status") != status:
		frappe.db.set_value("Gym Member", name, "status", status)
	return name


def _as_dict(value):
	if isinstance(value, str):
		value = json.loads(value) if value else {}
	if not isinstance(value, dict):
		frappe.throw(_("A single record object is required."))
	return value


def _text(value):
	if value is None:
		return ""
	return str(value).strip()


def _same_values(doc, values):
	"""True when every mapped field already holds the incoming value.

	Empty device fields arrive as "" while Frappe stores them as None, so both
	sides are compared as text. An unchanged person is not saved, so modified
	time and version history stay as they are.
	"""
	for field, value in values.items():
		if _text(doc.get(field)) != _text(value):
			return False
	return True


def _is_disabled(value):
	if isinstance(value, str):
		return value.strip().lower() in {"1", "true", "yes"}
	return bool(value)
