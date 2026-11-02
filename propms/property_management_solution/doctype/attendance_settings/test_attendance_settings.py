# Copyright (c) 2026, VV Systems Developer LTD and Contributors
# See license.txt

from contextlib import ExitStack
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now

from propms.property_management_solution.doctype.attendance_settings.attendance_settings import (
	schedule_email_message_id,
	send_single_scheduled_report,
)


class TestAttendanceSettings(FrappeTestCase):
	def setUp(self):
		frappe.db.delete("Email Queue Recipient")
		frappe.db.delete("Email Queue")
		frappe.db.delete("Communication", {"reference_doctype": "Attendance Settings"})
		frappe.db.delete("Communication", {"reference_doctype": "Attendance Report Schedule"})

	def _schedule_row(self, **overrides):
		row = frappe._dict(
			{
				"name": "test-schedule-row",
				"enabled": 1,
				"frequency": "Daily",
				"send_time": "00:00:00",
				"report": "Employee Checkin",
				"format": "PDF",
				"recipients": "to@example.com",
				"cc": "cc@example.com",
				"bcc": "bcc1@example.com\nbcc2@example.com\nbcc3@example.com",
				"subject": "Attendance Report",
				"message": "<p>Please find the report attached.</p>",
				"dont_send_if_no_data": 0,
			}
		)
		row.update(overrides)
		return row

	def _fake_report_result(self):
		return {
			"columns": [{"fieldname": "employee", "label": "Employee"}],
			"result": [{"employee": "EMP-0001"}],
			"report_summary": [],
		}

	def _enter_report_patches(self, stack):
		mod = "propms.property_management_solution.doctype.attendance_settings.attendance_settings"
		stack.enter_context(patch(f"{mod}.get_report_data", return_value=self._fake_report_result()))
		stack.enter_context(patch(f"{mod}.build_report_pdf_html", return_value="<html></html>"))
		stack.enter_context(patch(f"{mod}.get_pdf", return_value=b"pdf-bytes"))
		return mod

	def test_scheduled_report_queues_one_email_for_to_cc_and_bcc(self):
		"""BCC must not create extra Email Queue docs; follow Frappe Communication + queue."""
		row = self._schedule_row()

		with ExitStack() as stack:
			mod = self._enter_report_patches(stack)
			stack.enter_context(patch(f"{mod}.mark_schedule_attempted"))
			send_single_scheduled_report(row, force=True)

		communications = frappe.get_all(
			"Communication",
			filters={"reference_doctype": "Attendance Settings", "subject": "Attendance Report"},
			fields=["name", "recipients", "cc", "bcc", "message_id"],
		)
		self.assertEqual(len(communications), 1, "Frappe creates one Communication per send")
		comm = communications[0]
		self.assertIn("to@example.com", comm.recipients or "")
		self.assertIn("cc@example.com", comm.cc or "")
		self.assertIn("bcc1@example.com", comm.bcc or "")
		self.assertIn("bcc2@example.com", comm.bcc or "")
		self.assertIn("bcc3@example.com", comm.bcc or "")
		self.assertEqual(comm.message_id, schedule_email_message_id(row.name))

		queues = frappe.get_all(
			"Email Queue",
			filters={"communication": comm.name},
			fields=["name", "status", "expose_recipients"],
		)
		self.assertEqual(
			len(queues),
			1,
			"BCC must stay on one Email Queue; extra queues mean BCC is being sent separately",
		)
		self.assertEqual(queues[0].status, "Not Sent")
		self.assertEqual(queues[0].expose_recipients, "header")

		queue_recipients = frappe.get_all(
			"Email Queue Recipient",
			filters={"parent": queues[0].name},
			pluck="recipient",
		)
		self.assertCountEqual(
			queue_recipients,
			["to@example.com", "cc@example.com", "bcc1@example.com", "bcc2@example.com", "bcc3@example.com"],
		)

	def test_email_failure_still_marks_last_sent_so_next_run_does_not_recreate(self):
		"""If queueing fails, last_sent_at must still be set so the next cron does not create another email."""
		row = self._schedule_row()
		mod = "propms.property_management_solution.doctype.attendance_settings.attendance_settings"

		with ExitStack() as stack:
			self._enter_report_patches(stack)
			stack.enter_context(
				patch(f"{mod}.queue_attendance_report_email", side_effect=Exception("SMTP/queue failed"))
			)
			mock_mark = stack.enter_context(patch(f"{mod}.mark_schedule_attempted"))
			result = send_single_scheduled_report(row, force=True)

		self.assertFalse(result)
		mock_mark.assert_called_with(row.name)

		row.last_sent_at = now()
		with ExitStack() as stack:
			self._enter_report_patches(stack)
			mock_queue = stack.enter_context(patch(f"{mod}.queue_attendance_report_email"))
			result = send_single_scheduled_report(row, force=False)

		self.assertFalse(result)
		mock_queue.assert_not_called()

	def test_existing_todays_email_is_not_recreated_when_last_sent_at_missing(self):
		"""A Communication already created today must not be duplicated if last_sent_at never got saved."""
		row = self._schedule_row()
		mod = "propms.property_management_solution.doctype.attendance_settings.attendance_settings"
		frappe.get_doc(
			{
				"doctype": "Communication",
				"subject": "Attendance Report",
				"content": "<p>already queued</p>",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"communication_type": "Automated Message",
				"reference_doctype": "Attendance Settings",
				"reference_name": "Attendance Settings",
				"recipients": "to@example.com",
				"message_id": schedule_email_message_id(row.name),
			}
		).insert(ignore_permissions=True)

		with ExitStack() as stack:
			self._enter_report_patches(stack)
			mock_queue = stack.enter_context(patch(f"{mod}.queue_attendance_report_email"))
			stack.enter_context(patch(f"{mod}.mark_schedule_attempted"))
			result = send_single_scheduled_report(row, force=False)

		self.assertFalse(result)
		mock_queue.assert_not_called()
		self.assertEqual(
			frappe.db.count("Communication", {"message_id": schedule_email_message_id(row.name)}),
			1,
		)
