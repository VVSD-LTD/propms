# -*- coding: utf-8 -*-
"""Safe partial-payment amount rules for Sales Invoice Selcom checkout.

Includes accrued late-payment penalty in the payable total so tenants pay
principal + penalty. Settlement uses the existing ``create_penalty_invoice``
feature (no new penalty math).

Goals:
- Never overpay (amount + other open Selcom intents ≤ total due)
- Never leave unpayable dust (tiny leftover < currency precision)
- Round consistently to 2 decimals
- Allocate: principal first, then penalty
"""

from __future__ import unicode_literals

import json

import frappe
from frappe import _
from frappe.utils import cint, flt


CURRENCY_PRECISION = 2
MIN_PAYMENT_AMOUNT = 1.0
OPEN_TXN_STATUSES = ("Pending", "Processing")


def money(value):
	"""Round money to currency precision."""
	return flt(value, CURRENCY_PRECISION)


def open_selcom_committed_amount(invoice_name, exclude_order_id=None):
	"""Sum of open Selcom intents already reserved against this invoice."""
	if not invoice_name:
		return 0.0
	filters = {
		"sales_invoice": invoice_name,
		"status": ["in", list(OPEN_TXN_STATUSES)],
	}
	rows = frappe.get_all(
		"Selcom Payment Transaction Log",
		filters=filters,
		fields=["amount", "order_id"],
		ignore_permissions=True,
	)
	total = 0.0
	exclude = (exclude_order_id or "").strip()
	for row in rows:
		if exclude and (row.get("order_id") or "") == exclude:
			continue
		total += money(row.get("amount"))
	return money(total)


MOBILE_STATUS_LABELS = {
	"cancelled": "Cancelled",
	"paid": "Paid",
	"paid_penalty_pending": "Paid — Penalty Pending",
	"withholding": "WH",
	"overdue": "Overdue",
	"partially_paid": "Partially Paid",
	"unpaid": "Unpaid",
}


def invoice_due_breakdown(invoice_name):
	"""Principal outstanding + penalty due (accrued or billed penalty SI)."""
	row = frappe.db.get_value(
		"Sales Invoice",
		invoice_name,
		[
			"outstanding_amount",
			"outstanding_penalty_amount",
			"total_penalty_amount",
			"penalty_invoice",
			"penalty_paid",
			"outstanding_withholding",
			"currency",
		],
		as_dict=True,
	) or {}

	principal = money(row.get("outstanding_amount"))
	accrued = money(row.get("outstanding_penalty_amount"))
	if accrued <= 0:
		accrued = money(row.get("total_penalty_amount"))

	penalty_invoice = (row.get("penalty_invoice") or "").strip() or None
	penalty_si_outstanding = 0.0
	penalty_si_status = None
	penalty_paid = cint(row.get("penalty_paid"))
	outstanding_withholding = cint(row.get("outstanding_withholding"))

	if penalty_invoice and frappe.db.exists("Sales Invoice", penalty_invoice):
		psi = frappe.db.get_value(
			"Sales Invoice",
			penalty_invoice,
			["docstatus", "outstanding_amount", "status"],
			as_dict=True,
		) or {}
		penalty_si_status = cint(psi.get("docstatus"))
		if penalty_si_status == 1:
			# Already billed — pay whatever is left on the penalty SI (don't double-count accrued)
			penalty_si_outstanding = money(psi.get("outstanding_amount"))
			penalty_due = penalty_si_outstanding
		else:
			# Draft exists — still treat accrued as due; settle will submit it
			penalty_due = accrued
	else:
		penalty_due = accrued

	# Desk: staff may tick Penalty Paid to authorize Create Penalty Invoice, or to
	# waive accrued penalty when no penalty SI exists. Mobile must NOT treat that
	# tick as "penalty settled" while a submitted penalty SI still has outstanding.
	if penalty_si_status == 1 and penalty_si_outstanding > 0:
		penalty_due = penalty_si_outstanding
	elif penalty_paid and penalty_si_status != 0:
		# Waive accrued only when there is no open unpaid penalty bill
		penalty_due = 0.0

	total_due = money(principal + penalty_due)
	return {
		"principal_outstanding": principal,
		"penalty_amount_due": money(penalty_due),
		"accrued_penalty_amount": accrued,
		"penalty_invoice": penalty_invoice,
		"penalty_invoice_docstatus": penalty_si_status,
		# Flutter: treat as unpaid-penalty while penalty SI still outstanding,
		# even if desk ticked penalty_paid to create the bill.
		"penalty_paid": (
			1
			if (
				penalty_paid
				and not (penalty_si_status == 1 and penalty_si_outstanding > 0)
			)
			else 0
		),
		"penalty_paid_desk": penalty_paid,
		"outstanding_withholding": outstanding_withholding,
		"total_amount_due": total_due,
		"currency": row.get("currency") or "TZS",
	}


def compute_mobile_invoice_status(
	principal_outstanding=None,
	penalty_amount_due=None,
	grand_total=None,
	due_date=None,
	outstanding_withholding=None,
	docstatus=None,
	is_return=None,
	today_date=None,
):
	"""Flutter-facing status code + label (not raw ERPNext status).

	Priority: cancelled → paid → paid_penalty_pending → withholding → overdue
	→ partially_paid → unpaid.
	"""
	from frappe.utils import getdate, today as _today

	principal = money(principal_outstanding)
	penalty_due = money(penalty_amount_due)
	total = money(grand_total)
	wh = cint(outstanding_withholding)
	today_date = getdate(today_date or _today())
	due_d = getdate(due_date) if due_date else None

	if cint(docstatus) == 2 or cint(is_return):
		code = "cancelled"
	elif principal <= 0 and penalty_due <= 0:
		code = "paid"
	elif principal <= 0 and penalty_due > 0:
		code = "paid_penalty_pending"
	elif wh and principal > 0:
		code = "withholding"
	elif principal > 0 and due_d and due_d < today_date:
		code = "overdue"
	elif principal > 0 and total > 0 and principal < total:
		code = "partially_paid"
	elif principal > 0:
		code = "unpaid"
	else:
		code = "paid"

	return {
		"mobile_status": code,
		"mobile_status_label": MOBILE_STATUS_LABELS.get(code, code),
	}


def available_to_pay(invoice_name, outstanding=None, exclude_order_id=None):
	"""Total due (principal + penalty) minus open Selcom reservations.

	Withholding invoices are not payable on mobile (desk clears WH).
	"""
	breakdown = invoice_due_breakdown(invoice_name)
	if cint(breakdown.get("outstanding_withholding")) and money(breakdown.get("principal_outstanding")) > 0:
		return 0.0
	total_due = breakdown["total_amount_due"]
	# ``outstanding`` arg kept for callers; prefer live breakdown
	if outstanding is not None and money(outstanding) > total_due:
		total_due = money(outstanding)
	committed = open_selcom_committed_amount(invoice_name, exclude_order_id=exclude_order_id)
	return money(max(0.0, total_due - committed))


def payment_limits(invoice_name, outstanding=None):
	"""Flutter-facing limits for an invoice (includes penalty)."""
	breakdown = invoice_due_breakdown(invoice_name)
	available = available_to_pay(invoice_name, exclude_order_id=None)
	committed = open_selcom_committed_amount(invoice_name)
	is_wh = cint(breakdown.get("outstanding_withholding")) and money(
		breakdown.get("principal_outstanding")
	) > 0
	allow = 0 if is_wh else (1 if available >= money(MIN_PAYMENT_AMOUNT) else 0)
	return {
		"allow_partial_payment": 0 if is_wh else 1,
		"allow_payment": allow,
		"min_payment_amount": money(MIN_PAYMENT_AMOUNT) if allow else 0.0,
		"max_payment_amount": available,
		"outstanding_amount": breakdown["principal_outstanding"],
		"principal_outstanding": breakdown["principal_outstanding"],
		"penalty_amount_due": breakdown["penalty_amount_due"],
		"outstanding_penalty_amount": breakdown["accrued_penalty_amount"],
		"total_amount_due": breakdown["total_amount_due"],
		"available_to_pay": available,
		"open_payment_reserved": committed,
		"penalty_invoice": breakdown["penalty_invoice"],
		# Mobile-facing: 0 while penalty SI still outstanding (desk tick ignored)
		"penalty_paid": cint(breakdown.get("penalty_paid")),
		"outstanding_withholding": cint(breakdown.get("outstanding_withholding")),
		"currency_precision": CURRENCY_PRECISION,
		"currency": breakdown["currency"],
	}


def resolve_invoice_pay_amount(amount_val, outstanding, invoice_name=None, exclude_order_id=None):
	"""Validate/normalize pay amount for initiate (principal + penalty total).

	Returns (pay_amount, error_message). error_message is None on success.
	"""
	if not invoice_name:
		# Legacy path without invoice — principal only
		outstanding = money(outstanding)
		if outstanding <= 0:
			return 0.0, _("Invoice is already fully paid.")
		available = outstanding
		breakdown = {
			"principal_outstanding": outstanding,
			"penalty_amount_due": 0.0,
			"total_amount_due": outstanding,
		}
	else:
		breakdown = invoice_due_breakdown(invoice_name)
		if cint(breakdown.get("outstanding_withholding")) and money(
			breakdown.get("principal_outstanding")
		) > 0:
			return 0.0, _(
				"This balance is marked as withholding (W/H). "
				"It cannot be paid in the app — contact management."
			)
		available = available_to_pay(
			invoice_name, exclude_order_id=exclude_order_id
		)
		outstanding = breakdown["total_amount_due"]

	if available <= 0:
		if breakdown.get("total_amount_due", 0) <= 0:
			return 0.0, _("Invoice is already fully paid.")
		if cint(breakdown.get("outstanding_withholding")) and money(
			breakdown.get("principal_outstanding")
		) > 0:
			return 0.0, _(
				"This balance is marked as withholding (W/H). "
				"It cannot be paid in the app — contact management."
			)
		return 0.0, _(
			"Another payment for this invoice is already in progress. "
			"Wait for it to finish or cancel it before paying again."
		)

	if amount_val is None or amount_val == "" or flt(amount_val) <= 0:
		pay_amount = available
	else:
		pay_amount = money(amount_val)

	min_amt = money(MIN_PAYMENT_AMOUNT)
	if pay_amount < min_amt:
		return 0.0, _("Payment amount must be at least {0}.").format(
			frappe.format_value(min_amt, {"fieldtype": "Currency"})
		)

	if pay_amount > available:
		return 0.0, _(
			"Payment amount must be between {0} and {1} "
			"(invoice {2} + penalty {3}, already reserved {4})."
		).format(
			frappe.format_value(min_amt, {"fieldtype": "Currency"}),
			frappe.format_value(available, {"fieldtype": "Currency"}),
			frappe.format_value(breakdown["principal_outstanding"], {"fieldtype": "Currency"}),
			frappe.format_value(breakdown["penalty_amount_due"], {"fieldtype": "Currency"}),
			frappe.format_value(money(breakdown["total_amount_due"] - available), {"fieldtype": "Currency"}),
		)

	leftover = money(available - pay_amount)
	epsilon = money(10 ** (-CURRENCY_PRECISION))
	if leftover > 0 and leftover < epsilon:
		pay_amount = available
		leftover = 0.0

	if leftover > 0 and leftover < min_amt:
		return 0.0, _(
			"This amount would leave {0} unpaid, which is below the minimum payment of {1}. "
			"Pay the full available amount ({2}) instead."
		).format(
			frappe.format_value(leftover, {"fieldtype": "Currency"}),
			frappe.format_value(min_amt, {"fieldtype": "Currency"}),
			frappe.format_value(available, {"fieldtype": "Currency"}),
		)

	return pay_amount, None


def split_payment_toward_principal_and_penalty(paid_amount, invoice_name):
	"""Allocate a Selcom receipt: principal first, then penalty."""
	breakdown = invoice_due_breakdown(invoice_name)
	paid = money(paid_amount)
	principal = breakdown["principal_outstanding"]
	to_principal = money(min(paid, principal))
	remainder = money(max(0.0, paid - to_principal))
	to_penalty = money(min(remainder, breakdown["penalty_amount_due"]))
	return {
		"to_principal": to_principal,
		"to_penalty": to_penalty,
		"unallocated": money(max(0.0, paid - to_principal - to_penalty)),
		"breakdown": breakdown,
	}


def allocate_settlement_amount(paid_amount, invoice_name, exclude_order_id=None):
	"""At settle time: never allocate more than live principal outstanding.

	Penalty is handled separately after principal PE.
	Returns (allocated, outstanding_before, error_message).
	"""
	outstanding = money(
		frappe.db.get_value("Sales Invoice", invoice_name, "outstanding_amount") or 0
	)
	paid = money(paid_amount)

	if paid <= 0:
		return 0.0, outstanding, _("Paid amount must be greater than zero.")

	# Allow paid > principal when penalty is included — only allocate principal slice here
	split = split_payment_toward_principal_and_penalty(paid, invoice_name)
	allocated = split["to_principal"]

	if outstanding <= 0 and split["to_penalty"] <= 0:
		return 0.0, outstanding, _("Invoice is already fully paid.")

	if outstanding <= 0:
		# Principal clear; penalty-only settle
		return 0.0, outstanding, None

	if money(outstanding - allocated) < money(10 ** (-CURRENCY_PRECISION)) and allocated + money(
		10 ** (-CURRENCY_PRECISION)
	) >= outstanding:
		allocated = outstanding

	return allocated, outstanding, None


def ensure_submitted_penalty_invoice(source_invoice_name):
	"""Create (existing API) + submit penalty SI so it can be paid.

	Uses ``create_penalty_invoice`` from the desk penalty feature.
	"""
	if not source_invoice_name:
		return None

	source = frappe.get_doc("Sales Invoice", source_invoice_name)
	from propms.custom.sales_invoice_penalty import create_penalty_invoice, penalty_amount

	existing = (source.get("penalty_invoice") or "").strip()
	if existing and frappe.db.exists("Sales Invoice", existing):
		psi = frappe.get_doc("Sales Invoice", existing)
		if cint(psi.docstatus) == 0:
			psi.flags.ignore_permissions = True
			psi.flags.ignore_push_and_realtime = True
			psi.submit()
			frappe.db.commit()
		return psi.name

	if penalty_amount(source) <= 0:
		return None

	# Desk create_penalty_invoice still requires penalty_paid — set for mobile settle only.
	if not cint(source.penalty_paid):
		source.db_set("penalty_paid", 1, update_modified=True)

	try:
		result = create_penalty_invoice(source_invoice_name)
		name = (result or {}).get("penalty_invoice")
		if not name:
			return None
		psi = frappe.get_doc("Sales Invoice", name)
		if cint(psi.docstatus) == 0:
			psi.flags.ignore_permissions = True
			# Suppress "New Invoice Issued" — often paid in the same Selcom PE
			psi.flags.ignore_push_and_realtime = True
			psi.submit()
			frappe.db.commit()
		return name
	except Exception:
		# Concurrent IPN + poll: peer may have just created the penalty SI
		source.reload()
		existing = (source.get("penalty_invoice") or "").strip()
		if existing and frappe.db.exists("Sales Invoice", existing):
			psi = frappe.get_doc("Sales Invoice", existing)
			if cint(psi.docstatus) == 0:
				try:
					psi.flags.ignore_permissions = True
					psi.flags.ignore_push_and_realtime = True
					psi.submit()
					frappe.db.commit()
				except Exception:
					frappe.log_error(
						frappe.get_traceback(), "ensure_submitted_penalty_invoice submit"
					)
			return psi.name
		frappe.log_error(frappe.get_traceback(), "ensure_submitted_penalty_invoice")
		return None


def _normalize_pe_allocations(allocations):
	"""Build validated allocation rows against live invoice outstanding."""
	rows = []
	for raw in allocations or []:
		inv_name = (raw.get("invoice_name") or "").strip()
		amt = money(raw.get("amount"))
		if not inv_name or amt <= 0:
			continue
		if not frappe.db.exists("Sales Invoice", inv_name):
			continue
		inv = frappe.get_doc("Sales Invoice", inv_name)
		if money(inv.outstanding_amount) <= 0:
			continue
		amt = money(min(amt, inv.outstanding_amount))
		if amt <= 0:
			continue
		rows.append(
			{
				"invoice_name": inv.name,
				"amount": amt,
				"grand_total": money(inv.grand_total),
				"outstanding_amount": money(inv.outstanding_amount),
				"customer": inv.customer,
				"company": inv.company,
				"currency": inv.currency or "TZS",
			}
		)
	return rows


def create_payment_entry_for_allocations(
	allocations,
	paid_to_account,
	reference_no,
	mode_of_payment=None,
	paid_amount=None,
	submit=True,
):
	"""Create one Payment Entry with one or more Sales Invoice allocations.

	``allocations``: list of ``{"invoice_name": ..., "amount": ...}``.
	``paid_amount`` may exceed allocated total (desk unallocated / advance for
	penalty clearance on the same PE after the penalty SI is created).
	``submit=False`` keeps a draft PE (main invoice → PE → penalty SI → clear).
	"""
	rows = _normalize_pe_allocations(allocations)
	if not rows:
		return None

	allocated_total = money(sum(r["amount"] for r in rows))
	if allocated_total <= 0:
		return None
	total = money(paid_amount) if paid_amount is not None else allocated_total
	if total < allocated_total:
		total = allocated_total

	party = rows[0]["customer"]
	company = rows[0]["company"]
	currency = rows[0]["currency"]

	# Prefer ERPNext helper for the first row, then append remaining refs
	try:
		from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

		pe = get_payment_entry(
			"Sales Invoice", rows[0]["invoice_name"], party_amount=rows[0]["amount"]
		)
		pe.reference_no = str(reference_no)
		pe.reference_date = frappe.utils.today()
		pe.paid_to = paid_to_account
		if mode_of_payment:
			pe.mode_of_payment = mode_of_payment
		# Ensure first row allocation matches (helper sometimes uses full outstanding)
		if pe.references:
			pe.references[0].allocated_amount = rows[0]["amount"]
		for extra in rows[1:]:
			pe.append(
				"references",
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": extra["invoice_name"],
					"total_amount": extra["grand_total"],
					"outstanding_amount": extra["outstanding_amount"],
					"allocated_amount": extra["amount"],
				},
			)
		pe.paid_amount = total
		pe.received_amount = total
	except Exception:
		pe = frappe.new_doc("Payment Entry")
		pe.payment_type = "Receive"
		pe.party_type = "Customer"
		pe.party = party
		pe.company = company
		pe.paid_amount = total
		pe.received_amount = total
		pe.paid_to_account_currency = currency
		pe.reference_no = str(reference_no)
		pe.reference_date = frappe.utils.today()
		pe.paid_to = paid_to_account
		if mode_of_payment:
			pe.mode_of_payment = mode_of_payment
		for row in rows:
			pe.append(
				"references",
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": row["invoice_name"],
					"total_amount": row["grand_total"],
					"outstanding_amount": row["outstanding_amount"],
					"allocated_amount": row["amount"],
				},
			)

	pe.insert(ignore_permissions=True)
	if submit:
		pe.submit()
	else:
		frappe.db.commit()
	return pe.name


def append_allocation_to_payment_entry(pe_name, invoice_name, allocated_amt, submit=True):
	"""Add a Sales Invoice row to an existing draft PE and optionally submit.

	Desk order: main SI → PE (draft, principal allocated) → penalty SI →
	same PE cleared against penalty → submit.
	"""
	allocated_amt = money(allocated_amt)
	if not pe_name or not invoice_name or allocated_amt <= 0:
		return None
	if not frappe.db.exists("Payment Entry", pe_name):
		return None
	if not frappe.db.exists("Sales Invoice", invoice_name):
		return None

	pe = frappe.get_doc("Payment Entry", pe_name)
	if cint(pe.docstatus) != 0:
		frappe.throw(
			_("Payment Entry {0} is not a draft; cannot append penalty allocation").format(
				pe_name
			)
		)

	# Already allocated to this invoice?
	for ref in pe.references or []:
		if ref.reference_doctype == "Sales Invoice" and ref.reference_name == invoice_name:
			if submit and cint(pe.docstatus) == 0:
				pe.flags.ignore_permissions = True
				pe.submit()
			return pe.name

	inv = frappe.get_doc("Sales Invoice", invoice_name)
	outstanding = money(inv.outstanding_amount)
	if outstanding <= 0:
		if submit and cint(pe.docstatus) == 0:
			pe.flags.ignore_permissions = True
			pe.submit()
		return pe.name

	allocated_amt = money(min(allocated_amt, outstanding))
	pe.append(
		"references",
		{
			"reference_doctype": "Sales Invoice",
			"reference_name": inv.name,
			"total_amount": money(inv.grand_total),
			"outstanding_amount": outstanding,
			"allocated_amount": allocated_amt,
		},
	)
	# Keep paid_amount covering all allocations (already set high for unallocated hold)
	allocated_total = money(sum(flt(r.allocated_amount) for r in pe.references))
	if money(pe.paid_amount) < allocated_total:
		pe.paid_amount = allocated_total
		pe.received_amount = allocated_total
	pe.flags.ignore_permissions = True
	pe.save(ignore_permissions=True)
	if submit:
		pe.submit()
	else:
		frappe.db.commit()
	return pe.name


def allocate_unallocated_pe_to_invoice(pe_name, invoice_name, amount=None):
	"""Clear a Sales Invoice from unallocated amount on a submitted Payment Entry.

	Uses Payment Reconciliation (same as desk) so references are added on the
	existing PE after it was submitted against the main invoice first.
	"""
	if not pe_name or not invoice_name:
		return None
	if not frappe.db.exists("Payment Entry", pe_name):
		return None
	if not frappe.db.exists("Sales Invoice", invoice_name):
		return None

	pe = frappe.get_doc("Payment Entry", pe_name)
	if cint(pe.docstatus) != 1:
		frappe.throw(_("Payment Entry {0} must be submitted before reconciliation").format(pe_name))

	# Already linked?
	for ref in pe.references or []:
		if ref.reference_doctype == "Sales Invoice" and ref.reference_name == invoice_name:
			return pe_name

	inv = frappe.get_doc("Sales Invoice", invoice_name)
	pe.reload()
	unallocated = money(getattr(pe, "unallocated_amount", 0) or 0)
	outstanding = money(inv.outstanding_amount)
	to_alloc = money(amount) if amount is not None else unallocated
	to_alloc = money(min(to_alloc, unallocated, outstanding))
	if to_alloc <= 0:
		return pe_name

	pr = frappe.get_doc("Payment Reconciliation")
	pr.company = pe.company
	pr.party_type = "Customer"
	pr.party = pe.party
	pr.receivable_payable_account = inv.debit_to
	if pr.meta.has_field("default_advance_account"):
		pr.default_advance_account = frappe.db.get_value(
			"Party Account",
			{"parent": pe.party, "parenttype": "Customer", "company": pe.company},
			"advance_account",
		)

	pr.from_invoice_date = inv.posting_date
	pr.to_invoice_date = inv.posting_date
	pr.from_payment_date = pe.posting_date
	pr.to_payment_date = pe.posting_date
	pr.get_unreconciled_entries()

	invoices = [
		x.as_dict() for x in (pr.invoices or []) if x.invoice_number == invoice_name
	]
	payments = [
		x.as_dict() for x in (pr.payments or []) if x.reference_name == pe_name
	]
	if not invoices or not payments:
		frappe.throw(
			_(
				"Could not reconcile Payment Entry {0} to penalty invoice {1} "
				"(unallocated={2}, outstanding={3})"
			).format(pe_name, invoice_name, unallocated, outstanding)
		)

	# Limit allocation to to_alloc
	for inv_row in invoices:
		inv_row["outstanding_amount"] = min(flt(inv_row.get("outstanding_amount")), to_alloc)
	for pay_row in payments:
		pay_row["amount"] = min(flt(pay_row.get("amount")), to_alloc)

	pr.allocate_entries(frappe._dict({"invoices": invoices, "payments": payments}))
	# Cap allocation rows if tool allocated more
	for row in pr.allocation or []:
		if money(row.allocated_amount) > to_alloc:
			row.allocated_amount = to_alloc
	pr.reconcile()
	frappe.db.commit()
	return pe_name


def settle_principal_and_penalty_same_pe(
	source_invoice_name,
	to_principal,
	to_penalty,
	paid_to_account,
	reference_no,
	mode_of_payment=None,
):
	"""Desk order: main SI → submit PE → penalty SI → same PE clears penalty.

	PE is submitted first (principal allocated, penalty held as unallocated) so the
	penalty Sales Invoice is created only after payment exists — combined with
	``is_penalty_reimbursement_invoice`` skips so it does not email “please pay”.

	Returns ``{payment_entry, penalty_invoice, amount}`` (penalty amount cleared).
	"""
	to_principal = money(to_principal)
	to_penalty = money(to_penalty)
	if to_principal <= 0 and to_penalty <= 0:
		return None

	# Penalty-only (principal already clear): bill then one PE on penalty SI
	if to_principal <= 0 and to_penalty > 0:
		return settle_penalty_portion(
			source_invoice_name,
			to_penalty,
			paid_to_account,
			reference_no=reference_no,
			mode_of_payment=mode_of_payment,
		)

	# Principal only in this payment — optional unpaid penalty SI for later
	if to_penalty <= 0:
		pe_name = create_payment_entry_for_allocations(
			[{"invoice_name": source_invoice_name, "amount": to_principal}],
			paid_to_account=paid_to_account,
			reference_no=reference_no,
			mode_of_payment=mode_of_payment,
			submit=True,
		)
		return {
			"payment_entry": pe_name,
			"penalty_invoice": None,
			"amount": 0.0,
		}

	# 1) Submit PE against main invoice first; hold penalty as unallocated
	total = money(to_principal + to_penalty)
	pe_name = create_payment_entry_for_allocations(
		[{"invoice_name": source_invoice_name, "amount": to_principal}],
		paid_to_account=paid_to_account,
		reference_no=reference_no,
		mode_of_payment=mode_of_payment,
		paid_amount=total,
		submit=True,
	)
	if not pe_name:
		return None

	# 2) Create + submit penalty SI (FCM/email pay-nudge suppressed)
	pen_si = ensure_submitted_penalty_invoice(source_invoice_name)
	if not pen_si:
		return {"payment_entry": pe_name, "penalty_invoice": None, "amount": 0.0}

	# 3) Same PE: reconcile unallocated onto penalty SI
	allocate_unallocated_pe_to_invoice(pe_name, pen_si, to_penalty)
	from propms.custom.sales_invoice_penalty import sync_penalty_paid_flag

	sync_penalty_paid_flag(source_invoice_name=source_invoice_name, penalty_invoice_name=pen_si)
	return {
		"payment_entry": pe_name,
		"penalty_invoice": pen_si,
		"amount": to_penalty,
	}


def create_payment_entry_for_invoice(
	invoice_name,
	allocated_amt,
	paid_to_account,
	reference_no,
	mode_of_payment=None,
):
	"""Create and submit a Payment Entry for one Sales Invoice allocation."""
	return create_payment_entry_for_allocations(
		[{"invoice_name": invoice_name, "amount": allocated_amt}],
		paid_to_account=paid_to_account,
		reference_no=reference_no,
		mode_of_payment=mode_of_payment,
		submit=True,
	)


def settle_penalty_portion(source_invoice_name, penalty_amount_to_pay, paid_to_account, reference_no, mode_of_payment=None):
	"""Bill penalty SI then create a PE against it (penalty-only payments)."""
	to_pay = money(penalty_amount_to_pay)
	if to_pay <= 0:
		return None

	penalty_si = ensure_submitted_penalty_invoice(source_invoice_name)
	if not penalty_si:
		return None

	pe_name = create_payment_entry_for_invoice(
		penalty_si,
		to_pay,
		paid_to_account,
		reference_no=reference_no,
		mode_of_payment=mode_of_payment,
	)
	from propms.custom.sales_invoice_penalty import sync_penalty_paid_flag

	sync_penalty_paid_flag(
		source_invoice_name=source_invoice_name, penalty_invoice_name=penalty_si
	)
	return {"penalty_invoice": penalty_si, "payment_entry": pe_name, "amount": to_pay}


def maybe_create_penalty_invoice_after_principal_paid(invoice_name):
	"""When principal SI is cleared, ensure penalty SI exists (submitted for payability)."""
	if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
		return None

	source = frappe.get_doc("Sales Invoice", invoice_name)
	if money(source.outstanding_amount) > 0:
		return None

	from propms.custom.sales_invoice_penalty import penalty_amount

	if penalty_amount(source) <= 0 and not source.get("penalty_invoice"):
		return None

	name = ensure_submitted_penalty_invoice(invoice_name)
	if not name:
		return None
	return {"penalty_invoice": name, "created": 1}


def parse_txn_intent(txn):
	"""Read principal/penalty split hints from txn.raw_request if present."""
	raw = getattr(txn, "raw_request", None) if txn else None
	if not raw:
		return {}
	try:
		data = json.loads(raw) if isinstance(raw, str) else (raw or {})
	except Exception:
		return {}
	if not isinstance(data, dict):
		return {}
	return data
