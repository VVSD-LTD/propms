# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime


class GymCheckin(Document):
	pass


@frappe.whitelist()
def receive_gym_checkin(transaction):
	"""Create one Gym Checkin from a device transaction record.

	Accepts the inner transaction object (dict or JSON string). A repeated
	post with the same external id returns the existing document name.
	The gym member link is set when a Gym Member with the same PIN exists.
	"""
	transaction = _as_dict(transaction)
	external_id = _text(transaction.get("id"))
	event_time = transaction.get("eventTime")
	if not external_id or not event_time:
		frappe.throw(_("'id' and 'eventTime' are required."))

	existing = frappe.db.get_value("Gym Checkin", {"external_id": external_id}, "name")
	if existing:
		return existing

	if not _is_door_passage(transaction):
		return None

	pin = _text(transaction.get("pin"))
	gym_member = frappe.db.get_value("Gym Member", {"pin": pin}, "name") if pin else None

	doc = frappe.new_doc("Gym Checkin")
	doc.external_id = external_id
	doc.gym_member = gym_member
	doc.pin = pin
	doc.person_name = _text(transaction.get("name"))
	doc.last_name = _text(transaction.get("lastName"))
	doc.dept_name = _text(transaction.get("deptName"))
	doc.event_time = get_datetime(event_time)
	doc.log_type = _log_type(transaction)
	doc.reader_name = _text(transaction.get("readerName"))
	doc.reader_state = _optional_int(transaction.get("readerState"))
	doc.door_name = _text(transaction.get("doorName"))
	doc.door_number = _optional_int(transaction.get("doorNumber"))
	doc.event_point_name = _text(transaction.get("eventPointName"))
	doc.dev_name = _text(transaction.get("devName"))
	doc.dev_sn = _text(transaction.get("devSn"))
	doc.area_name = _text(transaction.get("areaName"))
	doc.event_name = _text(transaction.get("eventName"))
	doc.verify_mode_name = _text(transaction.get("verifyModeName"))
	doc.card_no = _text(transaction.get("cardNo"))
	doc.insert()
	return doc.name


def _is_door_passage(transaction):
	"""True for a named person opening a door in or out.

	Device noise such as Disabled Fingerprint, Disconnected, and passage-mode
	events is not a checkin.
	"""
	if not _text(transaction.get("pin")):
		return False
	if _log_type(transaction) not in ("IN", "OUT"):
		return False

	event_no = transaction.get("eventNo")
	if event_no is not None and str(event_no).strip() not in ("", "0"):
		return False

	event_name = _text(transaction.get("eventName")).lower()
	if event_name and event_name != "normal card swipe open":
		return False
	return True


def _log_type(transaction):
	state = transaction.get("readerState")
	if state is not None and str(state).strip() != "":
		if str(state).strip() == "0":
			return "IN"
		if str(state).strip() == "1":
			return "OUT"

	reader = _text(transaction.get("readerName")).lower()
	if reader.endswith("-in"):
		return "IN"
	if reader.endswith("-out"):
		return "OUT"
	return ""


def _optional_int(value):
	if value is None or str(value).strip() == "":
		return None
	return cint(value)


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
