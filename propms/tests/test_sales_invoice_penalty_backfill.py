# Copyright (c) 2026, VV Systems Developer LTD and contributors

from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate


class _DummySettings:
	def __init__(self, days_after_overdue=1, excluded=None):
		self.days_after_overdue = days_after_overdue
		self.excluded_days = [
			type("Row", (), {"day": day, "exclude": 1 if exclude else 0})()
			for day, exclude in (excluded or {}).items()
		]

	def get(self, key, default=None):
		return getattr(self, key, default)


class TestPenaltyBackfillDates(FrappeTestCase):
	def test_backfill_function_exists(self):
		from propms.custom.sales_invoice_penalty import (
			backfill_previous_sales_invoice_penalties,
		)

		self.assertTrue(callable(backfill_previous_sales_invoice_penalties))

	def test_starts_on_due_date_plus_grace_and_skips_existing_and_excluded(self):
		from propms.custom.sales_invoice_penalty import iter_penalty_dates

		settings = _DummySettings(
			days_after_overdue=1,
			excluded={"Sunday": 1, "Saturday": 0, "Public Holiday": 0},
		)
		dates = iter_penalty_dates(
			due_date="2026-09-14",
			settings=settings,
			current_date="2026-09-21",
			existing_dates={getdate("2026-09-16")},
		)
		# due 14th, T+1 => first 15th. Skip Sun 20th. Skip existing 16th.
		self.assertEqual(
			dates,
			[
				getdate("2026-09-15"),
				getdate("2026-09-17"),
				getdate("2026-09-18"),
				getdate("2026-09-19"),
				getdate("2026-09-21"),
			],
		)

	def test_no_dates_before_grace_period(self):
		from propms.custom.sales_invoice_penalty import iter_penalty_dates

		settings = _DummySettings(days_after_overdue=5, excluded={})
		dates = iter_penalty_dates(
			due_date="2026-09-18",
			settings=settings,
			current_date="2026-09-21",
			existing_dates=set(),
		)
		self.assertEqual(dates, [])


def execute():
	cases = [
		"test_backfill_function_exists",
		"test_starts_on_due_date_plus_grace_and_skips_existing_and_excluded",
		"test_no_dates_before_grace_period",
	]
	for name in cases:
		getattr(TestPenaltyBackfillDates(name), name)()
		print(f"PASS: {name}")
	print("ALL UNIT CHECKS PASSED")
