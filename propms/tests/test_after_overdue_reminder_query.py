# Copyright (c) 2026, VV Systems Developer LTD and contributors

import inspect


class TestAfterOverdueReminderQuery:
	def test_query_does_not_hardcode_the_overdue_condition(self):
		from propms.custom.custom import _get_after_overdue_invoices

		src = inspect.getsource(_get_after_overdue_invoices)
		where = src.split("WHERE", 1)[-1]
		assert "outstanding_amount >" not in where
		assert "penalty_paid" not in where
		assert "is_return" not in where
		assert "outstanding_penalty_amount" in src.split("WHERE", 1)[0]

	def test_eligibility_uses_the_query_row(self):
		from propms.custom.custom import _process_after_overdue_reminder

		src = inspect.getsource(_process_after_overdue_reminder)
		assert 'get_doc("Sales Invoice"' not in src
		assert "is_after_overdue_invoice_eligible(invoice" in src


def execute():
	runner = TestAfterOverdueReminderQuery()
	for name in (
		"test_query_does_not_hardcode_the_overdue_condition",
		"test_eligibility_uses_the_query_row",
	):
		getattr(runner, name)()
		print(f"PASS: {name}")
	print("ALL UNIT CHECKS PASSED")
