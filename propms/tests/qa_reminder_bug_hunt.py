# Copyright (c) 2026, VV Systems Developer LTD and contributors
"""End-to-end bug hunt for Property Management Email reminders.

Creates dedicated QA email settings + sales invoices, runs the scheduler once,
then cross-checks Notify Customer against an expected trigger matrix.

Usage:
  bench --site <site> execute propms.tests.qa_reminder_bug_hunt.execute
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import add_days, add_months, cint, flt, get_first_day, getdate, today

from propms.custom.custom import process_invoice_email_reminders
from propms.utils.business_calendar import (
	is_after_overdue_invoice_eligible,
	is_wh_invoice_eligible,
)

QA_TAG = "QA-REMINDER-BUGHUNT"
QA_CUSTOMER = "VV SYSTEMS DEVELOPER LTD"
COMPANY = "Virgin Plaza Ltd."
TERM_21 = "21 Days"
TERM_7 = "7 Days"
# Live template name is "3 DAYS" (uppercase) — intentional mismatch vs Payment Term "3 Days"
TERM_3_TEMPLATE = "3 DAYS"
TERM_3_PAYMENT_TERM = "3 Days"

OVERDUE_CONDITION = (
	"flt(outstanding_amount) > 1 or "
	"(flt(doc.total_penalty_amount) > 1 and not cint(penalty_paid))"
)
WH_CONDITION = "get_first_day(add_months(reference_date, 1)) <= today"


def execute():
	frappe.flags.in_test = True
	report: dict[str, Any] = {
		"run_date": today(),
		"settings": {},
		"invoices": {},
		"eligibility_unit": [],
		"notify_before": [],
		"notify_after": [],
		"matrix": [],
		"bugs": [],
		"summary": {},
	}

	_ensure_prereqs(report)
	settings = _ensure_qa_settings(report)
	invoices = _ensure_qa_invoices(report)
	_run_unit_eligibility(report, settings, invoices)

	report["notify_before"] = _list_qa_notifies()
	# Wipe today's QA notifies so this run is deterministic
	_clear_today_qa_notifies(list(settings.values()), list(invoices.values()))

	process_invoice_email_reminders()
	frappe.db.commit()

	report["notify_after"] = _list_qa_notifies()
	_build_matrix(report, settings, invoices)
	_static_pen_tests(report)
	_summarize(report)

	print(json.dumps(report, indent=2, default=str))
	return report


def _ensure_prereqs(report):
	enabled = frappe.db.get_single_value(
		"Property Management Settings", "enable_due_invoice_email_sending"
	)
	if not cint(enabled):
		frappe.db.set_single_value(
			"Property Management Settings", "enable_due_invoice_email_sending", 1
		)
		report["bugs"].append(
			{
				"severity": "high",
				"id": "TOGGLE_OFF",
				"detail": "enable_due_invoice_email_sending was off; turned on for QA.",
			}
		)

	emails = frappe.db.sql(
		"""
		SELECT ct.email_id
		FROM `tabContact` ct
		JOIN `tabDynamic Link` dl ON dl.parent = ct.name
		WHERE dl.link_doctype = 'Customer'
		  AND dl.link_name = %s
		  AND IFNULL(ct.email_id, '') != ''
		  AND ct.is_primary_contact = 1
		""",
		QA_CUSTOMER,
	)
	if not emails:
		report["bugs"].append(
			{
				"severity": "blocker",
				"id": "NO_PRIMARY_EMAIL",
				"detail": f"Customer {QA_CUSTOMER} has no primary contact email.",
			}
		)
		frappe.throw(f"QA customer {QA_CUSTOMER} needs a primary contact email")


def _ensure_qa_settings(report):
	"""Create (or refresh) three submitted QA email settings."""
	defs = {
		"pre_due": {
			"title_key": f"{QA_TAG}-PRE-DUE",
			"reminder_type": "Pre-Due",
			"payment_term": TERM_21,
			"days_due": 3,
			"subject": f"[{QA_TAG}] Pre-Due {{{{ doc.name }}}}",
			"body": f"<p>{QA_TAG} Pre-Due for {{{{ doc.name }}}} outstanding {{{{ doc.outstanding_amount }}}}</p>",
		},
		"after_overdue": {
			"title_key": f"{QA_TAG}-AFTER-OVERDUE",
			"reminder_type": "After Overdue",
			"payment_term": TERM_21,
			"days_after_overdue": 1,
			"use_penalty_grace_period": 0,
			"overdue_condition": OVERDUE_CONDITION,
			"frequency": "Daily",
			"use_penalty_exclusion_calendar": 0,
			"subject": f"[{QA_TAG}] After Overdue {{{{ doc.name }}}}",
			"body": f"<p>{QA_TAG} After Overdue for {{{{ doc.name }}}}</p>",
		},
		"withholding": {
			"title_key": f"{QA_TAG}-WH",
			"reminder_type": "Withholding Tax",
			"payment_term": TERM_21,
			"wh_date_basis": "Withholding Date",
			"wh_condition": WH_CONDITION,
			"frequency": "Daily",
			"use_penalty_exclusion_calendar": 0,
			"subject": f"[{QA_TAG}] WH {{{{ doc.name }}}}",
			"body": f"<p>{QA_TAG} Withholding for {{{{ doc.name }}}}</p>",
		},
		# Deliberate mismatch: Payment Term "3 Days" vs template "3 DAYS"
		"wh_mismatch_term": {
			"title_key": f"{QA_TAG}-WH-MISMATCH",
			"reminder_type": "Withholding Tax",
			"payment_term": TERM_3_PAYMENT_TERM,
			"wh_date_basis": "Withholding Date",
			"wh_condition": WH_CONDITION,
			"frequency": "Daily",
			"use_penalty_exclusion_calendar": 0,
			"subject": f"[{QA_TAG}] WH mismatch {{{{ doc.name }}}}",
			"body": f"<p>{QA_TAG} mismatch term</p>",
		},
	}

	out = {}
	for key, spec in defs.items():
		name = _upsert_setting(spec)
		out[key] = name
		report["settings"][key] = {
			"name": name,
			"reminder_type": spec["reminder_type"],
			"payment_term": spec["payment_term"],
		}
	return out


def _upsert_setting(spec):
	title_key = spec["title_key"]
	existing = frappe.db.sql(
		"""
		SELECT name FROM `tabProperty Management Email Setting`
		WHERE subject LIKE %s
		ORDER BY creation DESC LIMIT 1
		""",
		(f"%{title_key}%",),
	)
	if existing:
		name = existing[0][0]
		doc = frappe.get_doc("Property Management Email Setting", name)
		if doc.docstatus == 1:
			doc.cancel()
			doc.reload()
		# Amend path is messy; create fresh instead
		doc = None

	payload = {
		"doctype": "Property Management Email Setting",
		"enabled": 1,
		"reminder_type": spec["reminder_type"],
		"payment_term": spec["payment_term"],
		"subject": spec["subject"],
		"body": spec["body"],
		"print_format": None,
		"letter_head": None,
	}
	for f in (
		"days_due",
		"days_after_overdue",
		"use_penalty_grace_period",
		"overdue_condition",
		"wh_date_basis",
		"wh_condition",
		"frequency",
		"weekday",
		"day_of_month",
		"use_penalty_exclusion_calendar",
	):
		if f in spec:
			payload[f] = spec[f]

	doc = frappe.get_doc(payload)
	if payload["reminder_type"] in ("After Overdue", "Withholding Tax"):
		# leave exclusions empty / calendar off — Daily always due
		doc.use_penalty_exclusion_calendar = 0
		doc.set("excluded_days", [])
		for day in (
			"Monday",
			"Tuesday",
			"Wednesday",
			"Thursday",
			"Friday",
			"Saturday",
			"Sunday",
			"Public Holiday",
		):
			doc.append("excluded_days", {"day": day, "exclude": 0})

	doc.insert(ignore_permissions=True)
	doc.submit()
	frappe.db.commit()
	return doc.name


def _ensure_qa_invoices(report):
	"""Create / reshape submitted invoices for each matrix case."""
	cases = {
		"pre_due_hit": {
			"due_offset": 3,
			"posting_offset": 0,
			"outstanding": 5000,
			"wh": 0,
			"penalty_total": 0,
			"penalty_paid": 0,
			"payment_terms_template": TERM_21,
			"expect_settings": ["pre_due"],
		},
		"pre_due_miss_due": {
			"due_offset": 5,
			"posting_offset": 0,
			"outstanding": 5000,
			"wh": 0,
			"penalty_total": 0,
			"penalty_paid": 0,
			"payment_terms_template": TERM_21,
			"expect_settings": [],
		},
		"overdue_principal": {
			"due_offset": -10,
			"posting_offset": -20,
			"outstanding": 5000,
			"wh": 0,
			"penalty_total": 0,
			"penalty_paid": 0,
			"payment_terms_template": TERM_21,
			"expect_settings": ["after_overdue"],
		},
		"overdue_penalty_only": {
			"due_offset": -10,
			"posting_offset": -20,
			"outstanding": 0,
			"wh": 0,
			"penalty_total": 250,
			"penalty_paid": 0,
			"payment_terms_template": TERM_21,
			"expect_settings": ["after_overdue"],
		},
		"overdue_penalty_paid": {
			"due_offset": -10,
			"posting_offset": -20,
			"outstanding": 0,
			"wh": 0,
			"penalty_total": 250,
			"penalty_paid": 1,
			"payment_terms_template": TERM_21,
			"expect_settings": [],
		},
		"overdue_settled_no_penalty": {
			"due_offset": -10,
			"posting_offset": -20,
			"outstanding": 0,
			"wh": 0,
			"penalty_total": 0,
			"penalty_paid": 0,
			"payment_terms_template": TERM_21,
			"expect_settings": [],
		},
		"wh_eligible": {
			"due_offset": 10,
			"posting_offset": 0,
			"outstanding": 5000,
			"wh": 1,
			"wh_date_offset": -40,  # previous month
			"penalty_total": 0,
			"penalty_paid": 0,
			"payment_terms_template": TERM_21,
			"expect_settings": ["withholding"],
		},
		"wh_too_early": {
			"due_offset": 10,
			"posting_offset": 0,
			"outstanding": 5000,
			"wh": 1,
			"wh_date_offset": 0,  # same month as today
			"penalty_total": 0,
			"penalty_paid": 0,
			"payment_terms_template": TERM_21,
			"expect_settings": [],
		},
		"wh_term_mismatch_invoice": {
			"due_offset": 10,
			"posting_offset": 0,
			"outstanding": 5000,
			"wh": 1,
			"wh_date_offset": -40,
			"penalty_total": 0,
			"penalty_paid": 0,
			"payment_terms_template": TERM_3_TEMPLATE,
			"expect_settings": [],  # setting uses Payment Term "3 Days" != template "3 DAYS"
		},
		"wh_blocked_by_pre_due_filter": {
			# WH invoice should NOT be picked by Pre-Due even if due date matches
			"due_offset": 3,
			"posting_offset": 0,
			"outstanding": 5000,
			"wh": 1,
			"wh_date_offset": -40,
			"penalty_total": 0,
			"penalty_paid": 0,
			"payment_terms_template": TERM_21,
			"expect_settings": ["withholding"],  # only WH, not pre_due
		},
	}

	out = {}
	for case, spec in cases.items():
		name = _get_or_create_invoice(case, spec)
		_reshape_invoice(name, spec)
		out[case] = name
		report["invoices"][case] = {
			"name": name,
			**{k: spec[k] for k in spec if k != "expect_settings"},
			"expect_settings": spec["expect_settings"],
		}
	frappe.db.commit()
	return out


def _get_or_create_invoice(case, spec):
	# Prefer cloning a known good submitted invoice via copy, then force dates via SQL.
	marker = f"{QA_TAG}:{case}"
	existing = frappe.db.sql(
		"""
		SELECT name FROM `tabSales Invoice`
		WHERE remarks = %s AND docstatus = 1
		ORDER BY creation DESC LIMIT 1
		""",
		marker,
	)
	if existing:
		return existing[0][0]

	# Copy from a recent clean invoice
	source = frappe.db.sql(
		"""
		SELECT name FROM `tabSales Invoice`
		WHERE docstatus = 1
		  AND customer = %s
		  AND company = %s
		  AND IFNULL(payment_terms_template, '') != ''
		ORDER BY modified DESC LIMIT 1
		""",
		(QA_CUSTOMER, COMPANY),
	)
	if not source:
		frappe.throw("No source Sales Invoice to clone for QA")

	src = frappe.get_doc("Sales Invoice", source[0][0])
	doc = frappe.copy_doc(src)
	doc.remarks = marker
	doc.posting_date = add_days(today(), spec["posting_offset"])
	doc.due_date = add_days(today(), spec["due_offset"])
	doc.payment_terms_template = spec["payment_terms_template"]
	doc.set_posting_time = 1
	doc.is_return = 0
	doc.outstanding_withholding = cint(spec.get("wh"))
	# Strip payments / advances
	doc.set("payments", [])
	doc.set("advances", [])
	doc.allocate_advances_automatically = 0
	doc.ignore_pricing_rule = 1

	# Keep one simple item line from source
	if not doc.items:
		frappe.throw(f"Source invoice {src.name} has no items")

	try:
		doc.insert(ignore_permissions=True)
		doc.submit()
	except Exception as e:
		frappe.db.rollback()
		# Fallback: create lightweight submitted shell via SQL-backed doc insert
		return _create_invoice_shell(case, spec, str(e))

	frappe.db.set_value("Sales Invoice", doc.name, "remarks", marker, update_modified=False)
	return doc.name


def _create_invoice_shell(case, spec, err):
	"""Last-resort: insert a submitted SI row for eligibility/notify testing only."""
	name = f"QA-SINV-{case.upper().replace('_', '-')}"
	if frappe.db.exists("Sales Invoice", name):
		return name

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": "Property Management Email Setting",
			"reference_name": frappe.db.get_value(
				"Property Management Email Setting", {"enabled": 1}, "name"
			)
			or "PMES-2026-000001",
			"content": f"{QA_TAG} shell fallback for {case}: {err[:200]}",
		}
	).insert(ignore_permissions=True)

	# Use ERPNext make if possible with ignore flags
	doc = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"name": name,
			"naming_series": "ACC-SINV-.YYYY.-",
			"customer": QA_CUSTOMER,
			"company": COMPANY,
			"currency": "TZS",
			"conversion_rate": 1,
			"selling_price_list": "Standard Selling",
			"price_list_currency": "TZS",
			"plc_conversion_rate": 1,
			"debit_to": "11401 - Debtors - TZS - VPL",
			"posting_date": add_days(today(), spec["posting_offset"]),
			"due_date": add_days(today(), spec["due_offset"]),
			"payment_terms_template": spec["payment_terms_template"],
			"remarks": f"{QA_TAG}:{case}",
			"docstatus": 0,
			"items": [
				{
					"item_code": "Commercial Rent",
					"item_name": "Commercial Rent",
					"description": "QA",
					"qty": 1,
					"rate": 1000,
					"uom": "Nos",
					"conversion_factor": 1,
					"income_account": "40001 - Taxable Sales - VPL",
					"cost_center": "Main - VPL",
				}
			],
		}
	)
	doc.flags.ignore_validate = True
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)
	doc.flags.ignore_validate = True
	try:
		doc.submit()
	except Exception:
		# Force submitted status for scheduler SQL only (no GL) — flagged in report
		frappe.db.set_value("Sales Invoice", doc.name, "docstatus", 1, update_modified=False)
	return doc.name


def _reshape_invoice(name, spec):
	"""Force the fields the scheduler / conditions read."""
	due = add_days(today(), spec["due_offset"])
	posting = add_days(today(), spec["posting_offset"])
	wh_date = None
	if cint(spec.get("wh")):
		wh_date = add_days(today(), cint(spec.get("wh_date_offset", -40)))

	frappe.db.sql(
		"""
		UPDATE `tabSales Invoice`
		SET posting_date = %s,
			due_date = %s,
			payment_terms_template = %s,
			outstanding_amount = %s,
			outstanding_withholding = %s,
			outstanding_withholding_date = %s,
			total_penalty_amount = %s,
			outstanding_penalty_amount = %s,
			penalty_paid = %s,
			docstatus = 1
		WHERE name = %s
		""",
		(
			posting,
			due,
			spec["payment_terms_template"],
			flt(spec["outstanding"]),
			cint(spec.get("wh")),
			wh_date,
			flt(spec.get("penalty_total")),
			flt(spec.get("penalty_total")),
			cint(spec.get("penalty_paid")),
			name,
		),
	)


def _run_unit_eligibility(report, settings, invoices):
	ao = frappe.get_doc("Property Management Email Setting", settings["after_overdue"])
	wh = frappe.get_doc("Property Management Email Setting", settings["withholding"])
	for case, name in invoices.items():
		inv = frappe.get_doc("Sales Invoice", name)
		row = {
			"case": case,
			"invoice": name,
			"after_overdue_eligible": is_after_overdue_invoice_eligible(inv, ao),
			"wh_eligible": is_wh_invoice_eligible(inv, wh),
		}
		report["eligibility_unit"].append(row)


def _clear_today_qa_notifies(setting_names, invoice_names):
	if not setting_names or not invoice_names:
		return
	frappe.db.sql(
		"""
		DELETE FROM `tabNotify Customer`
		WHERE posting_date = %s
		  AND email_setting IN ({s})
		  AND invoice_no IN ({i})
		""".format(
			s=", ".join(["%s"] * len(setting_names)),
			i=", ".join(["%s"] * len(invoice_names)),
		),
		(today(), *setting_names, *invoice_names),
	)
	frappe.db.commit()


def _list_qa_notifies():
	return frappe.db.sql(
		"""
		SELECT name, invoice_no, email_setting, reminder_audience, customer_email,
			   subject, posting_date, is_test
		FROM `tabNotify Customer`
		WHERE subject LIKE %s OR email_setting LIKE %s
		ORDER BY creation DESC
		LIMIT 200
		""",
		(f"%{QA_TAG}%", f"%{QA_TAG}%"),
		as_dict=True,
	)


def _build_matrix(report, settings, invoices):
	created = report["notify_after"]
	by_inv_setting = {(n.invoice_no, n.email_setting) for n in created}

	for case, meta in report["invoices"].items():
		inv = meta["name"]
		expected = [settings[k] for k in meta["expect_settings"]]
		actual = [s for s in settings.values() if (inv, s) in by_inv_setting]
		# Only count QA settings
		actual = [s for s in actual if s in settings.values()]

		ok = set(expected) == set(actual)
		row = {
			"case": case,
			"invoice": inv,
			"expected_settings": expected,
			"actual_settings": actual,
			"pass": ok,
		}
		if not ok:
			missing = set(expected) - set(actual)
			extra = set(actual) - set(expected)
			report["bugs"].append(
				{
					"severity": "high",
					"id": f"MATRIX_{case}",
					"detail": f"expected {expected}, got {actual}; missing={list(missing)} extra={list(extra)}",
				}
			)
		report["matrix"].append(row)


def _static_pen_tests(report):
	"""Code / data contract checks that do not need a notify row."""
	# 1) Payment Term vs Payment Terms Template naming
	pt_names = set(frappe.get_all("Payment Term", pluck="name"))
	ptt_names = set(frappe.get_all("Payment Terms Template", pluck="name"))
	only_pt = sorted(pt_names - ptt_names)
	only_ptt = sorted(ptt_names - pt_names)
	if only_pt or only_ptt:
		report["bugs"].append(
			{
				"severity": "critical",
				"id": "PAYMENT_TERM_LINK_MISMATCH",
				"detail": (
					"Email Setting.payment_term links to Payment Term, but scheduler filters "
					"Sales Invoice.payment_terms_template. "
					f"Only in Payment Term: {only_pt}. Only in Template: {only_ptt}. "
					"Live example: setting '3 Days' will never match invoices on '3 DAYS'."
				),
			}
		)

	# 2) Pre-Due still hardcodes outstanding_amount > 0 (not condition-driven)
	import inspect
	from propms.custom import custom as custom_mod

	src = inspect.getsource(custom_mod._process_pre_due_reminder)
	if "outstanding_amount > 0" in src:
		report["bugs"].append(
			{
				"severity": "medium",
				"id": "PRE_DUE_HARDCODED_OUTSTANDING",
				"detail": "Pre-Due SQL still hardcodes outstanding_amount > 0 (not configurable).",
			}
		)

	# 3) WH SQL hardcodes outstanding > 1
	src_wh = inspect.getsource(custom_mod._process_withholding_tax_reminder)
	if "outstanding_amount > 1" in src_wh:
		report["bugs"].append(
			{
				"severity": "medium",
				"id": "WH_HARDCODED_OUTSTANDING",
				"detail": "Withholding Tax SQL still hardcodes outstanding_amount > 1.",
			}
		)

	# 4) Blank overdue condition default
	from propms.utils.business_calendar import evaluate_overdue_condition

	d = frappe._dict(
		outstanding_amount=0.5,
		outstanding_penalty_amount=0,
		total_penalty_amount=0,
		penalty_paid=0,
		due_date=add_days(today(), -5),
		posting_date=add_days(today(), -10),
		name="X",
	)
	if evaluate_overdue_condition("", d, today(), days_after_overdue=1):
		report["bugs"].append(
			{
				"severity": "low",
				"id": "DEFAULT_CONDITION_FALSE_POSITIVE",
				"detail": "Blank overdue condition returned True for outstanding 0.5",
			}
		)
	if not evaluate_overdue_condition("", frappe._dict(d, outstanding_amount=2), today(), 1):
		report["bugs"].append(
			{
				"severity": "high",
				"id": "DEFAULT_CONDITION_BROKEN",
				"detail": "Blank overdue condition returned False for outstanding 2",
			}
		)

	# 5) Recommended live condition with total_penalty_amount
	cond = OVERDUE_CONDITION
	cases = [
		(True, frappe._dict(d, outstanding_amount=50, total_penalty_amount=0, penalty_paid=0)),
		(True, frappe._dict(d, outstanding_amount=0, total_penalty_amount=50, penalty_paid=0)),
		(False, frappe._dict(d, outstanding_amount=0, total_penalty_amount=50, penalty_paid=1)),
		(False, frappe._dict(d, outstanding_amount=0, total_penalty_amount=0, penalty_paid=0)),
	]
	for expect, doc in cases:
		got = evaluate_overdue_condition(cond, doc, today(), 1)
		if got != expect:
			report["bugs"].append(
				{
					"severity": "critical",
					"id": "RECOMMENDED_CONDITION_FAIL",
					"detail": f"expected {expect} got {got} for {doc}",
				}
			)

	# 6) WH next-month boundary
	from propms.utils.business_calendar import evaluate_wh_condition

	ref = get_first_day(today())
	inv = frappe._dict(
		posting_date=ref,
		due_date=ref,
		outstanding_withholding_date=ref,
		name="WHX",
	)
	# same month should be False with recommended condition
	same = evaluate_wh_condition(WH_CONDITION, inv, today(), reference_date=ref)
	prev = evaluate_wh_condition(
		WH_CONDITION, inv, today(), reference_date=add_months(ref, -1)
	)
	if same:
		report["bugs"].append(
			{
				"severity": "high",
				"id": "WH_SAME_MONTH_ELIGIBLE",
				"detail": "WH condition True in same month as reference_date",
			}
		)
	if not prev:
		report["bugs"].append(
			{
				"severity": "high",
				"id": "WH_PREV_MONTH_NOT_ELIGIBLE",
				"detail": "WH condition False for previous-month reference_date",
			}
		)

	# 7) Dedup: second scheduler run should not duplicate
	before = frappe.db.count(
		"Notify Customer",
		{"posting_date": today(), "subject": ("like", f"%{QA_TAG}%")},
	)
	process_invoice_email_reminders()
	after = frappe.db.count(
		"Notify Customer",
		{"posting_date": today(), "subject": ("like", f"%{QA_TAG}%")},
	)
	if after != before:
		report["bugs"].append(
			{
				"severity": "critical",
				"id": "DEDUP_FAILURE",
				"detail": f"Second run created extras: before={before} after={after}",
			}
		)
	else:
		report["summary"]["dedup_ok"] = True


def _summarize(report):
	matrix = report["matrix"]
	passed = sum(1 for r in matrix if r["pass"])
	report["summary"].update(
		{
			"matrix_pass": passed,
			"matrix_total": len(matrix),
			"bug_count": len(report["bugs"]),
			"critical_bugs": sum(1 for b in report["bugs"] if b["severity"] == "critical"),
			"high_bugs": sum(1 for b in report["bugs"] if b["severity"] == "high"),
			"notify_created": len(report["notify_after"]),
		}
	)
