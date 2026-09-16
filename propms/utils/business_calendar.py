# -*- coding: utf-8 -*-
import calendar

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, today

from propms.property_management_solution.doctype.sales_invoice_penalty_settings.sales_invoice_penalty_settings import (
	is_date_public_holiday,
)

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

SCHEDULED_REMINDER_TYPES = ("After Overdue", "Withholding Tax")


def get_excluded_days_map(settings_doc):
	return {
		row.day: row.exclude for row in settings_doc.get("excluded_days", [])
	}


def is_excluded_today(excluded_days_map, target_date=None):
	if not target_date:
		target_date = today()

	dt = getdate(target_date)
	day_name = dt.strftime("%A")

	if excluded_days_map.get(day_name):
		return True, f"Day of week ({day_name}) is excluded"

	if excluded_days_map.get("Public Holiday") and is_date_public_holiday(dt):
		return True, f"Date ({dt}) is a Public Holiday"

	return False, None


def is_schedule_due_today(frequency, weekday=None, day_of_month=None, target_date=None):
	if not target_date:
		target_date = today()

	dt = getdate(target_date)

	if frequency == "Daily":
		return True

	if frequency == "Weekly":
		return bool(weekday) and dt.strftime("%A") == weekday

	if frequency == "Monthly":
		if not day_of_month:
			return False

		day_of_month = cint(day_of_month)
		last_day = calendar.monthrange(dt.year, dt.month)[1]

		if day_of_month > last_day:
			return dt.day == last_day

		return dt.day == day_of_month

	return False


def resolve_days_after_overdue(email_setting_doc):
	if email_setting_doc.get("use_penalty_grace_period"):
		return cint(
			frappe.db.get_single_value(
				"Sales Invoice Penalty Settings", "days_after_overdue"
			)
			or 1
		)

	return cint(email_setting_doc.get("days_after_overdue") or 1)


def get_email_setting_excluded_days_map(email_setting_doc):
	if cint(email_setting_doc.get("use_penalty_exclusion_calendar", 1)):
		penalty_settings = frappe.get_single("Sales Invoice Penalty Settings")
		return get_excluded_days_map(penalty_settings)

	return get_excluded_days_map(email_setting_doc)


def get_monthly_send_date(year, month, day_of_month):
	day_of_month = cint(day_of_month)
	last_day = calendar.monthrange(year, month)[1]
	send_day = min(day_of_month, last_day)
	return getdate(f"{year}-{month:02d}-{send_day:02d}")


def validate_weekday_not_excluded(weekday, excluded_days_map, schedule_label):
	if not weekday:
		return

	if excluded_days_map.get(weekday):
		frappe.throw(
			_(
				"{0}: {1} is marked as excluded in your calendar. "
				"Choose a different weekday or update your exclusion settings."
			).format(schedule_label, weekday)
		)


def validate_monthly_schedule_exclusions(
	day_of_month, excluded_days_map, schedule_label, year=None
):
	if not day_of_month:
		return

	year = year or getdate(today()).year
	skipped_dates = []
	valid_occurrences = 0

	for month in range(1, 13):
		send_date = get_monthly_send_date(year, month, day_of_month)
		is_excluded, reason = is_excluded_today(excluded_days_map, send_date)
		if is_excluded:
			skipped_dates.append(
				f"{send_date.strftime('%B')} {send_date.strftime('%d-%m-%Y')} ({reason})"
			)
		else:
			valid_occurrences += 1

	if valid_occurrences == 0:
		frappe.throw(
			_(
				"{0}: Day {1} of the month never sends in {2} because every occurrence "
				"falls on excluded days. Choose a different day or update your exclusion settings."
			).format(schedule_label, cint(day_of_month), year)
		)

	if skipped_dates:
		frappe.msgprint(
			_(
				"{0}: Day {1} of the month will <b>not</b> send on the following dates in {2} "
				"because they collide with your exclusion settings:<br><br>{3}"
			).format(
				schedule_label,
				cint(day_of_month),
				year,
				"<br>".join(skipped_dates),
			),
			indicator="orange",
			title=_("Exclusion Calendar Notice"),
		)


def validate_schedule_fields(frequency, weekday, day_of_month, schedule_label):
	if not frequency:
		frappe.throw(_("{0}: Frequency is required.").format(schedule_label))

	if frequency == "Weekly" and not weekday:
		frappe.throw(_("{0}: Weekday is required when frequency is Weekly.").format(schedule_label))

	if frequency == "Monthly":
		day = cint(day_of_month)
		if not day or day < 1 or day > 31:
			frappe.throw(
				_("{0}: Day of Month must be between 1 and 31 for Monthly frequency.").format(
					schedule_label
				)
			)


def validate_schedule_exclusions(frequency, weekday, day_of_month, excluded_days_map, schedule_label):
	if frequency == "Weekly":
		validate_weekday_not_excluded(weekday, excluded_days_map, schedule_label)
	elif frequency == "Monthly":
		validate_monthly_schedule_exclusions(day_of_month, excluded_days_map, schedule_label)


def get_wh_reference_date(invoice_doc, basis):
	basis = basis or "Posting Date"

	if basis == "Payment Due Date":
		return getdate(invoice_doc.due_date) if invoice_doc.get("due_date") else None

	if basis == "Withholding Date":
		wh_date = invoice_doc.get("outstanding_withholding_date")
		return getdate(wh_date) if wh_date else None

	return getdate(invoice_doc.posting_date) if invoice_doc.get("posting_date") else None


def get_wh_condition_context(invoice_doc, current_date=None, reference_date=None):
	current_date = getdate(current_date or today())
	if hasattr(invoice_doc, "as_dict") and callable(getattr(invoice_doc, "as_dict", None)):
		context = invoice_doc.as_dict()
	elif isinstance(invoice_doc, dict):
		context = dict(invoice_doc)
	else:
		context = {}

	wh_date = None
	if hasattr(invoice_doc, "get"):
		wh_date = invoice_doc.get("outstanding_withholding_date")
	elif isinstance(invoice_doc, dict):
		wh_date = invoice_doc.get("outstanding_withholding_date")

	posting_date = None
	due_date = None
	if hasattr(invoice_doc, "get"):
		posting_date = invoice_doc.get("posting_date")
		due_date = invoice_doc.get("due_date")
	elif isinstance(invoice_doc, dict):
		posting_date = invoice_doc.get("posting_date")
		due_date = invoice_doc.get("due_date")

	context.update({
		"doc": invoice_doc,
		"today": current_date,
		"posting_date": getdate(posting_date) if posting_date else None,
		"due_date": getdate(due_date) if due_date else None,
		"withholding_date": getdate(wh_date) if wh_date else None,
		"reference_date": getdate(reference_date) if reference_date else None,
		"add_days": frappe.utils.add_days,
		"add_months": frappe.utils.add_months,
		"get_first_day": frappe.utils.get_first_day,
		"getdate": getdate,
		"date_diff": date_diff,
		"cint": cint,
	})
	return context


def evaluate_wh_condition(condition, invoice_doc, current_date=None, reference_date=None):
	condition = (condition or "").strip()
	if not condition:
		# Default: start from the 1st of the month after Date Basis
		if not reference_date:
			return False
		ref_date = getdate(reference_date)
		current = getdate(current_date or today())
		return (current.year, current.month) > (ref_date.year, ref_date.month)

	try:
		result = frappe.safe_eval(
			condition,
			eval_globals={},
			eval_locals=get_wh_condition_context(
				invoice_doc, current_date, reference_date=reference_date
			),
		)
		return bool(result)
	except Exception as e:
		frappe.log_error(
			f"Error evaluating withholding condition '{condition}' for {invoice_doc.name}: {e}",
			"Withholding Email Condition Error",
		)
		return False


def validate_wh_condition_syntax(condition):
	condition = (condition or "").strip()
	if not condition:
		return

	try:
		frappe.safe_eval(
			condition,
			eval_globals={},
			eval_locals=get_wh_condition_context(
				frappe._dict(
					posting_date=today(),
					due_date=today(),
					outstanding_withholding_date=today(),
					name="SINV-TEST",
				),
				today(),
				reference_date=today(),
			),
		)
	except Exception as e:
		frappe.throw(_("Invalid Withholding Condition: {0}").format(str(e)))


def is_wh_invoice_eligible(invoice_doc, setting_doc, current_date=None):
	current_date = getdate(current_date or today())
	basis = setting_doc.get("wh_date_basis") or "Posting Date"
	ref_date = get_wh_reference_date(invoice_doc, basis)

	if not ref_date:
		return False

	return evaluate_wh_condition(
		setting_doc.get("wh_condition"),
		invoice_doc,
		current_date,
		reference_date=ref_date,
	)


def get_overdue_condition_context(invoice_doc, current_date=None, days_after_overdue=None):
	current_date = getdate(current_date or today())
	if hasattr(invoice_doc, "as_dict") and callable(getattr(invoice_doc, "as_dict", None)):
		context = invoice_doc.as_dict()
	elif isinstance(invoice_doc, dict):
		context = dict(invoice_doc)
	else:
		context = {}

	def _get(field):
		if hasattr(invoice_doc, "get"):
			return invoice_doc.get(field)
		if isinstance(invoice_doc, dict):
			return invoice_doc.get(field)
		return None

	posting_date = _get("posting_date")
	due_date = _get("due_date")
	due_date_val = getdate(due_date) if due_date else None
	days_overdue = date_diff(current_date, due_date_val) if due_date_val else None

	context.update({
		"doc": invoice_doc,
		"today": current_date,
		"posting_date": getdate(posting_date) if posting_date else None,
		"due_date": due_date_val,
		"days_overdue": days_overdue,
		"days_after_overdue": cint(days_after_overdue) if days_after_overdue is not None else None,
		"outstanding_amount": flt(_get("outstanding_amount")),
		"outstanding_penalty_amount": flt(_get("outstanding_penalty_amount")),
		"penalty_paid": cint(_get("penalty_paid")),
		"add_days": frappe.utils.add_days,
		"add_months": frappe.utils.add_months,
		"get_first_day": frappe.utils.get_first_day,
		"getdate": getdate,
		"date_diff": date_diff,
		"cint": cint,
		"flt": flt,
	})
	return context


def evaluate_overdue_condition(condition, invoice_doc, current_date=None, days_after_overdue=None):
	condition = (condition or "").strip()
	context = get_overdue_condition_context(
		invoice_doc, current_date, days_after_overdue=days_after_overdue
	)
	if not condition:
		return context["outstanding_amount"] > 0 or context["outstanding_penalty_amount"] > 0

	try:
		result = frappe.safe_eval(
			condition,
			eval_globals={},
			eval_locals=context,
		)
		return bool(result)
	except Exception as e:
		frappe.log_error(
			f"Error evaluating after-overdue condition '{condition}' for "
			f"{getattr(invoice_doc, 'name', invoice_doc.get('name') if isinstance(invoice_doc, dict) else '')}: {e}",
			"After Overdue Email Condition Error",
		)
		return False


def validate_overdue_condition_syntax(condition):
	condition = (condition or "").strip()
	if not condition:
		return

	try:
		frappe.safe_eval(
			condition,
			eval_globals={},
			eval_locals=get_overdue_condition_context(
				frappe._dict(
					posting_date=today(),
					due_date=today(),
					outstanding_amount=100,
					outstanding_penalty_amount=50,
					penalty_paid=0,
					name="SINV-TEST",
				),
				today(),
				days_after_overdue=1,
			),
		)
	except Exception as e:
		frappe.throw(_("Invalid After Overdue Condition: {0}").format(str(e)))


def is_after_overdue_invoice_eligible(invoice_doc, setting_doc, current_date=None):
	current_date = getdate(current_date or today())
	if cint(invoice_doc.get("outstanding_withholding")):
		return False

	due_date = getdate(invoice_doc.due_date) if invoice_doc.get("due_date") else None
	if not due_date or due_date >= current_date:
		return False

	days_after = resolve_days_after_overdue(setting_doc)
	if date_diff(current_date, due_date) < days_after:
		return False

	if cint(invoice_doc.get("penalty_paid")):
		return False

	return evaluate_overdue_condition(
		setting_doc.get("overdue_condition"),
		invoice_doc,
		current_date,
		days_after_overdue=days_after,
	)
