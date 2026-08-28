# -*- coding: utf-8 -*-
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today

DEFAULT_DAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
    "Public Holiday",
]


class SalesInvoicePenaltySettings(Document):
    def onload(self):
        self.populate_excluded_days()

    def validate(self):
        self.populate_excluded_days()
        self.validate_public_holiday_exclusion()

    def populate_excluded_days(self):
        existing_days = {row.day for row in self.get("excluded_days", [])}
        for day in DEFAULT_DAYS:
            if day not in existing_days:
                self.append("excluded_days", {"day": day, "exclude": 0})

    def validate_public_holiday_exclusion(self):
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


def get_holiday_lists_for_year(year=None):
    if not year:
        year = getdate(today()).year

    start_of_year = f"{year}-01-01"
    end_of_year = f"{year}-12-31"

    holiday_lists = frappe.db.sql(
        """
        SELECT name FROM `tabHoliday List`
        WHERE (from_date <= %s AND to_date >= %s)
           OR (YEAR(from_date) = %s OR YEAR(to_date) = %s)
        """,
        (end_of_year, start_of_year, year, year),
        as_dict=True,
    )
    return [hl.name for hl in holiday_lists]


def is_date_public_holiday(target_date=None):
    if not target_date:
        target_date = today()

    target_dt = getdate(target_date)
    year = target_dt.year
    holiday_lists = get_holiday_lists_for_year(year)
    if not holiday_lists:
        return False

    holidays = frappe.db.sql(
        """
        SELECT name FROM `tabHoliday`
        WHERE parent IN %s AND holiday_date = %s
        """,
        (tuple(holiday_lists), target_dt),
        as_dict=True,
    )
    return len(holidays) > 0
