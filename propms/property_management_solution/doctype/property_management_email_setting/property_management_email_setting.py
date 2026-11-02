# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate, today

from propms.utils.business_calendar import (
	DEFAULT_DAYS,
	SCHEDULED_REMINDER_TYPES,
	get_email_setting_excluded_days_map,
	validate_schedule_exclusions,
	validate_schedule_fields,
	validate_overdue_condition_syntax,
	validate_wh_condition_syntax,
)
from propms.property_management_solution.doctype.sales_invoice_penalty_settings.sales_invoice_penalty_settings import (
	get_holiday_lists_for_year,
)


class PropertyManagementEmailSetting(Document):
	def onload(self):
		if self.reminder_type in SCHEDULED_REMINDER_TYPES:
			self.populate_excluded_days()

	def validate(self):
		if self.reminder_type in SCHEDULED_REMINDER_TYPES:
			self.clear_unused_schedule_fields()
			self.populate_excluded_days()
			self.validate_public_holiday_exclusion()
			self.validate_reminder_schedule()
		else:
			self.clear_schedule_fields_for_pre_due()

	def clear_unused_schedule_fields(self):
		"""Keep only fields that match the selected frequency."""
		if self.frequency == "Daily":
			self.weekday = None
			self.day_of_month = None
		elif self.frequency == "Weekly":
			self.day_of_month = None
		elif self.frequency == "Monthly":
			self.weekday = None

	def clear_schedule_fields_for_pre_due(self):
		self.frequency = None
		self.weekday = None
		self.day_of_month = None

	def populate_excluded_days(self):
		if self.use_penalty_exclusion_calendar:
			return

		existing_days = {row.day for row in self.get("excluded_days", [])}
		for day in DEFAULT_DAYS:
			if day not in existing_days:
				self.append("excluded_days", {"day": day, "exclude": 0})

	def validate_public_holiday_exclusion(self):
		if self.use_penalty_exclusion_calendar:
			return

		public_holiday_row = next(
			(row for row in self.get("excluded_days", []) if row.day == "Public Holiday"),
			None,
		)
		if public_holiday_row and public_holiday_row.exclude:
			current_year = getdate(today()).year
			holiday_lists = get_holiday_lists_for_year(current_year)
			if not holiday_lists:
				frappe.throw(
					_(
						"Cannot enable Public Holiday exclusion because no Holiday List is found for the current year ({0}). Please configure a Holiday List first."
					).format(current_year)
				)

	def validate_reminder_schedule(self):
		schedule_label = self.get_schedule_label()
		validate_schedule_fields(self.frequency, self.weekday, self.day_of_month, schedule_label)

		if self.reminder_type == "After Overdue":
			validate_overdue_condition_syntax(self.overdue_condition)

		if self.reminder_type == "Withholding Tax":
			if not self.wh_date_basis:
				frappe.throw(_("Date Basis is required for Withholding Tax reminders"))
			validate_wh_condition_syntax(self.wh_condition)

		excluded_map = get_email_setting_excluded_days_map(self)
		validate_schedule_exclusions(
			self.frequency,
			self.weekday,
			self.day_of_month,
			excluded_map,
			schedule_label,
		)

	def get_schedule_label(self):
		if self.reminder_type == "Withholding Tax":
			return _("Withholding Tax schedule")
		return _("After Overdue schedule")


@frappe.whitelist()
def get_default_excluded_days():
	return [{"day": day, "exclude": 0} for day in DEFAULT_DAYS]
