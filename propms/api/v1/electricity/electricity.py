# -*- coding: utf-8 -*-
"""Mobile electricity purchase API (Sales Invoice foundation)."""

from __future__ import unicode_literals

import json

import frappe
from frappe import _
from frappe.utils import flt, cint, today

ITEM_TANESCO = "Electricity - TANESCO"
ITEM_GENERATOR = "Electricity - Generator"
LEASE_ITEM_ELECTRICITY = "Electricity"
DEFAULT_PRICE_LIST = "Standard Selling"
DEFAULT_TAX_TEMPLATE = "Incl VAT TZ - VPL"
ALLOCATION_TOLERANCE = 1.0  # TZS
ELECTRICITY_AMOUNT_SERVICE = "Electricity"


def get_electricity_amount_service_name():
	"""POS Amount Service doc used for electricity invoices + TrackSPM."""
	if not frappe.db.exists("DocType", "POS Amount Service"):
		return None
	# Prefer titled Electricity with handler Electricity
	name = frappe.db.get_value(
		"POS Amount Service",
		{"handler": "Electricity", "enabled": 1, "title": ELECTRICITY_AMOUNT_SERVICE},
		"name",
	)
	if name:
		return name
	return frappe.db.get_value(
		"POS Amount Service",
		{"handler": "Electricity", "enabled": 1},
		"name",
	)



def get_electricity_catalog():
	"""ERP electricity items from POS Amount Service (handler=Electricity).

	Afritrack Settings is TrackSPM credentials / meter sync only — never item config.
	Only catalog item_codes are eligible for TrackSPM top-up amounts.
	SI.lease_item tag is fixed to LEASE_ITEM_ELECTRICITY (not user-configurable).
	"""
	tanesco = ITEM_TANESCO
	generator = ITEM_GENERATOR
	lease_item = LEASE_ITEM_ELECTRICITY
	items = []
	source = "defaults"

	try:
		svc_name = get_electricity_amount_service_name()
		if svc_name:
			s = frappe.get_cached_doc("POS Amount Service", svc_name)
			rows = sorted(
				[r for r in (s.get("items") or []) if cint(r.enabled) and (r.item or "").strip()],
				key=lambda r: (cint(r.sort_order), cint(r.idx)),
			)
			t1 = None
			t2 = None
			for row in rows:
				item_code = (row.item or "").strip()
				tariff = (row.trackspm_tariff or "").strip()
				if tariff not in ("t1", "t2"):
					# Electricity catalog ignores untariffed rows (never send to TrackSPM)
					continue
				label = (row.label or "").strip() or item_code
				items.append(
					{
						"item_code": item_code,
						"label": label,
						"trackspm_tariff": tariff,
						"sort_order": cint(row.sort_order),
					}
				)
				if tariff == "t1" and not t1:
					t1 = item_code
				elif tariff == "t2" and not t2:
					t2 = item_code
			if t1:
				tanesco = t1
			if t2:
				generator = t2
			source = "POS Amount Service:{0}".format(svc_name)
	except Exception:
		pass

	if not items:
		items = [
			{
				"item_code": tanesco,
				"label": "TANESCO",
				"trackspm_tariff": "t1",
				"sort_order": 0,
			},
			{
				"item_code": generator,
				"label": "Generator",
				"trackspm_tariff": "t2",
				"sort_order": 1,
			},
		]

	codes = tuple(i["item_code"] for i in items)
	by_tariff = {i["trackspm_tariff"]: i["item_code"] for i in items}
	return {
		"item_tanesco": tanesco,
		"item_generator": generator,
		"lease_item": lease_item,
		"item_codes": codes,
		"item_code_set": set(codes),
		"items": items,
		"by_tariff": by_tariff,
		"amount_service": get_electricity_amount_service_name(),
		"source": source,
	}


def invoice_item_rows(doc):
	"""Child item rows from Sales Invoice Document, frappe._dict, or test double.

	Avoids confusing frappe._dict.items (method) with child table, and
	MagicMock.get(...) with a real items list set as an attribute.
	"""
	if not doc:
		return []
	try:
		from frappe.model.document import Document

		if isinstance(doc, Document):
			return list(doc.get("items") or [])
	except Exception:
		pass

	raw = getattr(doc, "items", None)
	if callable(raw):
		# frappe._dict: .items is dict.items — use key access
		raw = None
		if hasattr(doc, "get"):
			try:
				candidate = doc.get("items")
				if candidate is not None and not callable(candidate):
					raw = candidate
			except Exception:
				raw = None
		if raw is None:
			try:
				raw = doc["items"]
			except Exception:
				raw = []
	if not isinstance(raw, (list, tuple)):
		return []
	return list(raw)


def invoice_foreign_item_codes(doc_or_name):
	"""Item codes on SI that are NOT in the electricity catalog (must never go to TrackSPM)."""
	catalog = get_electricity_catalog()
	allowed = catalog["item_code_set"]
	if isinstance(doc_or_name, str):
		rows = frappe.db.sql(
			"""
			SELECT item_code FROM `tabSales Invoice Item`
			WHERE parent=%s AND docstatus < 2 AND ifnull(item_code,'')!=''
			""",
			doc_or_name,
			as_dict=True,
		)
		codes = [r.item_code for r in rows]
	else:
		codes = [
			(getattr(it, "item_code", None) or "").strip()
			for it in invoice_item_rows(doc_or_name)
			if (getattr(it, "item_code", None) or "").strip()
		]
	return sorted({c for c in codes if c not in allowed})



@frappe.whitelist()
def get_electricity_catalog_api():
    """Desk / mobile helper for configured electricity item names."""
    return get_electricity_catalog()


def validate_allocation(total_amount, tanesco_amount, generator_amount):
    total_amount = flt(total_amount)
    tanesco_amount = flt(tanesco_amount)
    generator_amount = flt(generator_amount)
    if total_amount <= 0:
        frappe.throw(_("total_amount must be greater than zero"), frappe.ValidationError)
    if tanesco_amount < 0 or generator_amount < 0:
        frappe.throw(_("Allocation amounts cannot be negative"), frappe.ValidationError)
    if abs((tanesco_amount + generator_amount) - total_amount) > ALLOCATION_TOLERANCE:
        frappe.throw(
            _("tanesco_amount + generator_amount must equal total_amount"),
            frappe.ValidationError,
        )
    return total_amount, tanesco_amount, generator_amount


def units_from_amount(inclusive_amount, rate):
    inclusive_amount = flt(inclusive_amount)
    rate = flt(rate)
    if inclusive_amount <= 0 or rate <= 0:
        return 0.0
    return flt(inclusive_amount / rate, 9)


def _parse_request_payload(defaults=None):
    """Safely extract parameters from form_dict or JSON body."""
    payload = dict(defaults or {})
    for k, v in (frappe.form_dict or {}).items():
        if k not in ("cmd",):
            payload[k] = v

    if frappe.request and getattr(frappe.request, "data", None):
        try:
            raw = frappe.request.data
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            if raw and raw.strip():
                data = json.loads(raw)
                if isinstance(data, dict):
                    payload.update(data)
        except Exception:
            pass
    return payload


def _require_auth():
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)


def _resolve_tenant_billing(target_lease=None):
    """Resolve billing for electricity — same POS Customer rule as water.

    Prefer Lease.customer (POS Customer). Water's resolver already does this;
    we keep an explicit override so electricity stays correct even if water
    fallbacks change.
    """
    from propms.api.v1.water.water import _resolve_tenant_billing as water_billing

    billing = water_billing(target_lease) or {}
    lease = billing.get("lease")
    if lease and frappe.db.exists("Lease", lease) and frappe.get_meta("Lease").has_field("customer"):
        pos_customer = frappe.db.get_value("Lease", lease, "customer")
        if pos_customer:
            billing["customer"] = pos_customer
            billing["customer_source"] = "lease_pos_customer"
    return billing


def get_item_selling_rate(item_code, price_list=None):
    price_list = (
        price_list
        or frappe.db.get_single_value("Selling Settings", "selling_price_list")
        or DEFAULT_PRICE_LIST
    )
    # Prefer currently valid selling prices (valid_from / valid_upto), then newest.
    rows = frappe.db.sql(
        """
        SELECT price_list_rate
        FROM `tabItem Price`
        WHERE item_code = %s
          AND selling = 1
          AND price_list = %s
          AND (valid_from IS NULL OR valid_from <= CURDATE())
          AND (valid_upto IS NULL OR valid_upto >= CURDATE())
        ORDER BY IFNULL(valid_from, '0001-01-01') DESC, modified DESC
        LIMIT 1
        """,
        (item_code, price_list),
        as_dict=True,
    )
    if not rows:
        rows = frappe.get_all(
            "Item Price",
            filters={"item_code": item_code, "selling": 1, "price_list": price_list},
            fields=["price_list_rate"],
            order_by="valid_from desc, modified desc",
            limit=1,
        )
    rate = rows[0].price_list_rate if rows else 0
    return flt(rate), price_list


def resolve_electricity_meter(property_name):
    """Return Active non-Cooking-Gas meter for property.

    If several match, pick the most recent by installation_date, then modified,
    then creation (newest wins).
    """
    if not property_name:
        frappe.throw(_("Property is required to resolve electricity meter"), frappe.ValidationError)

    rows = frappe.get_all(
        "Property Meter Reading",
        filters={"parent": property_name, "parenttype": "Property", "status": "Active"},
        fields=["meter_number", "meter_type", "installation_date", "modified", "creation", "idx"],
        order_by="installation_date desc, modified desc, creation desc, idx desc",
    )
    candidates = []
    seen = set()
    for r in rows:
        mn = (r.get("meter_number") or "").strip()
        if not mn or mn in seen:
            continue
        mt = (r.get("meter_type") or "").strip()
        if mt == "Cooking Gas":
            continue
        seen.add(mn)
        candidates.append(mn)

    if len(candidates) == 0:
        frappe.throw(
            _("No electricity meter found on property {0}. Contact building management.").format(
                property_name
            ),
            frappe.ValidationError,
        )
    # First row is newest after order_by (duplicates already skipped)
    return candidates[0]


def _build_allocation_lines(tanesco_amount, generator_amount, price_list=None):
    lines = []
    catalog = get_electricity_catalog()
    for item_code, amount in (
        (catalog["item_tanesco"], tanesco_amount),
        (catalog["item_generator"], generator_amount),
    ):
        amount = flt(amount)
        if amount <= 0:
            continue
        rate, price_list = get_item_selling_rate(item_code, price_list)
        if rate <= 0:
            frappe.throw(_("No selling price for {0}").format(item_code), frappe.ValidationError)
        lines.append(
            {
                "item_code": item_code,
                "amount_inclusive": amount,
                "rate": rate,
                "qty": units_from_amount(amount, rate),
            }
        )
    if not lines:
        frappe.throw(
            _("At least one of tanesco_amount or generator_amount must be > 0"),
            frappe.ValidationError,
        )
    return lines, price_list


def _get_tax_template(company):
    if frappe.db.exists("Sales Taxes and Charges Template", DEFAULT_TAX_TEMPLATE):
        return DEFAULT_TAX_TEMPLATE
    # fallback: first selling template for company
    return frappe.db.get_value(
        "Sales Taxes and Charges Template",
        {"company": company, "disabled": 0},
        "name",
    )


def build_electricity_sales_invoice(billing, lines, meter_number, submit=False):
    """Create SI in memory or insert. VAT via taxes_and_charges — do not hardcode.

    Runs document construction as Administrator because ERPNext
    ``set_missing_values`` requires Customer read permission that
    Mobile VIVA Tenant users do not have. Caller must already have
    validated tenant access to ``billing``.
    """
    tax_template = _get_tax_template(billing["company"])
    if not tax_template:
        frappe.throw(_("Sales Taxes and Charges Template not configured"), frappe.ValidationError)

    original_user = frappe.session.user
    try:
        frappe.set_user("Administrator")

        si = frappe.new_doc("Sales Invoice")
        si.flags.ignore_permissions = True
        si.flags.ignore_push_and_realtime = True
        si.customer = billing["customer"]
        si.company = billing["company"]
        si.posting_date = today()
        si.due_date = today()
        si.is_pos = 0
        si.update_stock = 0
        if billing.get("cost_center"):
            si.cost_center = billing["cost_center"]
        if billing.get("lease") and si.meta.has_field("lease"):
            si.lease = billing["lease"]
        if si.meta.has_field("lease_item"):
            si.lease_item = get_electricity_catalog()["lease_item"]
        if si.meta.has_field("meter_number"):
            si.meter_number = meter_number
        si.taxes_and_charges = tax_template
        si.remarks = _("Mobile electricity purchase. Meter: {0}").format(meter_number)

        for line in lines:
            si.append(
                "items",
                {
                    "item_code": line["item_code"],
                    "qty": line["qty"],
                    "rate": line["rate"],
                    "cost_center": billing.get("cost_center"),
                },
            )

        si.set_missing_values()
        if tax_template:
            si.set_taxes()
        si.calculate_taxes_and_totals()

        if submit:
            si.insert(ignore_permissions=True)
            si.flags.ignore_permissions = True
            si.submit()
        return si
    finally:
        frappe.set_user(original_user)


def _serialize_preview(si, lines, meter_number, billing, price_list):
    return {
        "status": "success",
        "currency": si.currency or "TZS",
        "price_list": price_list,
        "taxes_and_charges": si.taxes_and_charges,
        "meter_number": meter_number,
        "lease": billing.get("lease"),
        "property": billing.get("property"),
        "customer": billing.get("customer"),
        "net_total": flt(si.net_total, 2),
        "total_taxes_and_charges": flt(si.total_taxes_and_charges, 2),
        "grand_total": flt(si.grand_total, 2),
        "lines": [
            {
                "item_code": ln["item_code"],
                "amount_inclusive": flt(ln["amount_inclusive"], 2),
                "rate": flt(ln["rate"], 4),
                "qty": flt(ln["qty"], 6),
            }
            for ln in lines
        ],
    }


@frappe.whitelist(methods=["GET", "POST"])
def get_electricity_rates(lease=None):
    _require_auth()
    payload = _parse_request_payload({"lease": lease})
    billing = _resolve_tenant_billing((payload.get("lease") or "").strip() or None)
    if not billing.get("customer"):
        return {"status": "error", "message": "No customer/lease context for tenant"}

    items = []
    price_list = None
    catalog = get_electricity_catalog()
    for code in catalog["item_codes"]:
        rate, price_list = get_item_selling_rate(code, price_list)
        items.append({"item_code": code, "item_name": code, "rate": flt(rate, 4), "uom": "Nos"})

    meter = None
    meter_error = None
    try:
        if billing.get("property"):
            meter = {
                "meter_number": resolve_electricity_meter(billing["property"]),
                "property": billing["property"],
                "lease": billing.get("lease"),
            }
    except Exception as e:
        meter_error = str(e)

    return {
        "status": "success",
        "currency": "TZS",
        "price_list": price_list or DEFAULT_PRICE_LIST,
        "taxes_and_charges": _get_tax_template(billing["company"]),
        "items": items,
        "meter": meter,
        "meter_warning": meter_error,
    }


@frappe.whitelist(methods=["GET", "POST"])
def get_electricity_meter_status(lease=None, force_refresh=None):
    """Live TrackSPM balance / power status for the tenant's electricity meter.

    Read-only (units/list). Does not purchase. Requires Afritrack username/password.
    """
    _require_auth()
    payload = _parse_request_payload({"lease": lease, "force_refresh": force_refresh})
    billing = _resolve_tenant_billing((payload.get("lease") or "").strip() or None)
    if not billing.get("customer"):
        return {"status": "error", "message": "No customer/lease context for tenant"}
    if not billing.get("property"):
        return {"status": "error", "message": "No property on lease"}

    try:
        meter_serial = resolve_electricity_meter(billing["property"])
    except Exception as e:
        return {"status": "error", "message": str(e)}

    trackspm_meter_id = None
    if frappe.db.exists("Meter", meter_serial) and frappe.get_meta("Meter").has_field(
        "trackspm_meter_id"
    ):
        trackspm_meter_id = frappe.db.get_value("Meter", meter_serial, "trackspm_meter_id")

    from propms.api.v1.electricity.trackspm import (
        TrackSPMError,
        find_meter_row,
        get_settings,
        serialize_meter_status,
    )

    try:
        settings = get_settings()
    except Exception:
        return {"status": "error", "message": "Afritrack Settings not configured"}

    if not (settings.username and settings.password):
        return {
            "status": "error",
            "message": "Afritrack credentials not configured",
            "meter_number": meter_serial,
        }

    force = cint(payload.get("force_refresh"))
    try:
        # Default: read Afritrack Meter Sync (15-min). force_refresh=1 hits TrackSPM live.
        row = find_meter_row(
            meter_serial=meter_serial,
            meter_id=trackspm_meter_id,
            force_refresh=bool(force),
        )
        sync_meta = None
        if not force:
            from propms.property_management_solution.doctype.afritrack_meter_sync.afritrack_meter_sync import (
                get_latest_units_list_payload,
            )

            stored = get_latest_units_list_payload()
            if stored:
                sync_meta = {
                    "sync_name": stored.get("_sync_name"),
                    "synced_on": str(stored.get("_synced_on") or ""),
                    "data_source": "afritrack_meter_sync",
                }
    except TrackSPMError as e:
        frappe.log_error(frappe.get_traceback(), "Afritrack meter status")
        return {
            "status": "error",
            "message": "Unable to fetch meter status from TrackSPM: {0}".format(e),
            "meter_number": meter_serial,
            "trackspm_meter_id": trackspm_meter_id,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Afritrack meter status")
        return {
            "status": "error",
            "message": "Unable to fetch meter status: {0}".format(e),
            "meter_number": meter_serial,
        }

    if not row:
        return {
            "status": "error",
            "message": (
                "Meter {0} not found in latest Afritrack Meter Sync. "
                "Wait for the 15-minute sync or run Sync Now in Afritrack Settings."
            ).format(meter_serial),
            "meter_number": meter_serial,
            "trackspm_meter_id": trackspm_meter_id,
        }

    status = serialize_meter_status(row, propms_serial=meter_serial)
    out = {
        "status": "success",
        "lease": billing.get("lease"),
        "property": billing.get("property"),
        "customer": billing.get("customer"),
        "meter_number": meter_serial,
        "trackspm_meter_id": status.get("meter_id") or trackspm_meter_id,
        "meter": status,
        "data_source": "trackspm_live" if force else "afritrack_meter_sync",
    }
    if sync_meta:
        out.update(sync_meta)
    return out


@frappe.whitelist(methods=["POST"])
def preview_electricity_purchase(
    total_amount=None, tanesco_amount=None, generator_amount=None, lease=None
):
    _require_auth()
    payload = _parse_request_payload(
        {
            "total_amount": total_amount,
            "tanesco_amount": tanesco_amount,
            "generator_amount": generator_amount,
            "lease": lease,
        }
    )
    total, tanesco, generator = validate_allocation(
        payload.get("total_amount"),
        payload.get("tanesco_amount"),
        payload.get("generator_amount"),
    )
    billing = _resolve_tenant_billing((payload.get("lease") or "").strip() or None)
    if not billing.get("customer"):
        return {"status": "error", "message": "No customer/lease context for tenant"}

    meter_number = resolve_electricity_meter(billing.get("property"))
    lines, price_list = _build_allocation_lines(tanesco, generator)
    si = build_electricity_sales_invoice(billing, lines, meter_number, submit=False)
    return _serialize_preview(si, lines, meter_number, billing, price_list)


def _invoice_split_amounts(invoice_name):
	"""Return inclusive line amounts for TANESCO(t1) / Generator(t2) on an SI.

	Only sums lines whose item_code is in the electricity POS Amount Service catalog.
	Any other SI items are ignored for TrackSPM (never topped up).
	"""
	catalog = get_electricity_catalog()
	rows = frappe.db.sql(
		"""
		SELECT item_code, amount
		FROM `tabSales Invoice Item`
		WHERE parent = %s AND docstatus < 2
		""",
		invoice_name,
		as_dict=True,
	)
	tanesco = 0.0
	generator = 0.0
	allowed = catalog["item_code_set"]
	tanesco_code = catalog["item_tanesco"]
	generator_code = catalog["item_generator"]
	for r in rows:
		code = (r.item_code or "").strip()
		if code not in allowed:
			continue
		if code == tanesco_code:
			tanesco += flt(r.amount)
		elif code == generator_code:
			generator += flt(r.amount)
		else:
			# Extra catalog item with same tariff as t1/t2 first match — map via items list
			for entry in catalog["items"]:
				if entry["item_code"] == code:
					if entry["trackspm_tariff"] == "t1":
						tanesco += flt(r.amount)
					elif entry["trackspm_tariff"] == "t2":
						generator += flt(r.amount)
					break
	return flt(tanesco, 2), flt(generator, 2)


def _find_recent_duplicate(billing, total_amount, tanesco_amount, generator_amount):
    """Unpaid Electricity SI same lease/customer within 10 minutes with same total and split."""
    if not billing.get("customer"):
        return None
    filters = {
        "customer": billing["customer"],
        "docstatus": 1,
        "outstanding_amount": [">", 0],
        "lease_item": get_electricity_catalog()["lease_item"],
        "grand_total": flt(total_amount, 2),
    }
    if billing.get("lease"):
        filters["lease"] = billing["lease"]
    candidates = frappe.get_all(
        "Sales Invoice",
        filters=filters,
        fields=["name", "creation"],
        order_by="creation desc",
        limit=10,
    )
    want_t = flt(tanesco_amount, 2)
    want_g = flt(generator_amount, 2)
    now = frappe.utils.now_datetime()
    for row in candidates:
        if not row.creation or frappe.utils.time_diff_in_seconds(now, row.creation) > 600:
            continue
        got_t, got_g = _invoice_split_amounts(row.name)
        if abs(got_t - want_t) <= ALLOCATION_TOLERANCE and abs(got_g - want_g) <= ALLOCATION_TOLERANCE:
            return row.name
    return None


def _prepare_electricity_checkout_intent(
    total_amount=None, tanesco_amount=None, generator_amount=None, lease=None
):
    """Validate allocation and return billing, lines, meter, totals for checkout."""
    payload = _parse_request_payload(
        {
            "total_amount": total_amount,
            "tanesco_amount": tanesco_amount,
            "generator_amount": generator_amount,
            "lease": lease,
        }
    )
    total, tanesco, generator = validate_allocation(
        payload.get("total_amount"),
        payload.get("tanesco_amount"),
        payload.get("generator_amount"),
    )
    billing = _resolve_tenant_billing((payload.get("lease") or "").strip() or None)
    if not billing.get("customer"):
        frappe.throw(_("No customer/lease context for tenant"), frappe.ValidationError)

    meter_number = resolve_electricity_meter(billing.get("property"))
    lines, price_list = _build_allocation_lines(tanesco, generator)
    return {
        "total": total,
        "tanesco": tanesco,
        "generator": generator,
        "billing": billing,
        "meter_number": meter_number,
        "lines": lines,
        "price_list": price_list,
        "payment_method": (payload.get("payment_method") or payload.get("channel") or "MOBILE_MONEY"),
        "phone_number": payload.get("phone_number") or payload.get("phone") or payload.get("msisdn"),
        "amount": payload.get("amount"),
    }


@frappe.whitelist(methods=["POST"])
def checkout_electricity(
    total_amount=None,
    tanesco_amount=None,
    generator_amount=None,
    lease=None,
    payment_method="MOBILE_MONEY",
    phone_number=None,
    amount=None,
):
    """Start Selcom payment for electricity; Paid POS SI is created on success.

    No Sales Invoice is created until payment succeeds (workflow: electricity_pos).
    """
    _require_auth()
    from propms.api.v1.payments.checkout import start_selcom_checkout
    from propms.api.v1.payments.workflows import WORKFLOW_ELECTRICITY_POS

    # Merge explicit kwargs into form for _prepare
    if payment_method:
        frappe.form_dict["payment_method"] = payment_method
    if phone_number:
        frappe.form_dict["phone_number"] = phone_number
    if amount is not None:
        frappe.form_dict["amount"] = amount

    intent = _prepare_electricity_checkout_intent(
        total_amount=total_amount,
        tanesco_amount=tanesco_amount,
        generator_amount=generator_amount,
        lease=lease,
    )
    billing = intent["billing"]
    lines = intent["lines"]
    total = intent["total"]
    pay_amount = flt(intent.get("amount")) if intent.get("amount") and flt(intent.get("amount")) > 0 else total
    if abs(pay_amount - total) > ALLOCATION_TOLERANCE:
        return {
            "status": "error",
            "message": _("Payment amount must equal total_amount ({0})").format(total),
        }

    ref_name = billing.get("lease") or billing.get("customer") or "ELEC"
    extra = {
        "electricity_purchase": True,
        "payment_workflow": WORKFLOW_ELECTRICITY_POS,
        "lease": billing.get("lease"),
        "property": billing.get("property"),
        "customer": billing.get("customer"),
        "company": billing.get("company"),
        "cost_center": billing.get("cost_center"),
        "meter_number": intent["meter_number"],
        "tanesco_amount": intent["tanesco"],
        "generator_amount": intent["generator"],
        "total_amount": total,
        "lines": [
            {
                "item_code": ln["item_code"],
                "qty": flt(ln["qty"]),
                "rate": flt(ln["rate"]),
                "amount_inclusive": flt(ln["amount_inclusive"]),
            }
            for ln in lines
        ],
        "price_list": intent.get("price_list"),
    }

    return start_selcom_checkout(
        reference_doctype="Lease" if billing.get("lease") else "Customer",
        reference_name=ref_name,
        customer=billing["customer"],
        amount=pay_amount,
        currency="TZS",
        payment_workflow=WORKFLOW_ELECTRICITY_POS,
        payment_method=intent.get("payment_method") or "MOBILE_MONEY",
        phone_number=intent.get("phone_number"),
        buyer_remarks=f"Electricity {intent['meter_number']}",
        merchant_remarks="Viva Towers Electricity",
        extra_raw_request=extra,
    )


@frappe.whitelist(methods=["POST"])
def create_electricity_invoice(
    total_amount=None, tanesco_amount=None, generator_amount=None, lease=None
):
    """Deprecated: unpaid SI path removed. Use checkout_electricity (POS on payment success)."""
    return {
        "status": "error",
        "message": (
            "create_electricity_invoice is deprecated. "
            "Use propms.api.mobile.checkout_electricity — payment first, then a Paid POS invoice."
        ),
        "use": "propms.api.mobile.checkout_electricity",
    }


@frappe.whitelist(methods=["POST"])
def pay_electricity_with_stored_card(
    total_amount=None,
    tanesco_amount=None,
    generator_amount=None,
    lease=None,
    card_token=None,
    amount=None,
    cvv=None,
):
    """Charge saved card for electricity; settles via electricity_pos (Paid POS SI)."""
    _require_auth()
    import base64
    import json
    import re
    from propms.api.v1.payments.selcom_client import SelcomClient
    from propms.api.v1.payments.workflows import WORKFLOW_ELECTRICITY_POS
    from propms.api.v1.payments.checkout import normalize_phone_number

    payload = _parse_request_payload(
        {
            "total_amount": total_amount,
            "tanesco_amount": tanesco_amount,
            "generator_amount": generator_amount,
            "lease": lease,
            "card_token": card_token,
            "amount": amount,
            "cvv": cvv,
        }
    )
    card_token = (payload.get("card_token") or payload.get("token") or "").strip()
    if not card_token:
        return {"status": "error", "message": "card_token is required"}

    intent = _prepare_electricity_checkout_intent(
        total_amount=payload.get("total_amount"),
        tanesco_amount=payload.get("tanesco_amount"),
        generator_amount=payload.get("generator_amount"),
        lease=payload.get("lease"),
    )
    billing = intent["billing"]
    total = intent["total"]
    pay_amount = flt(payload.get("amount")) if payload.get("amount") and flt(payload.get("amount")) > 0 else total

    client = SelcomClient()
    if not client.enabled:
        return {"status": "error", "message": "Selcom payments are currently disabled."}

    rand_suffix = frappe.generate_hash(length=6).upper()
    clean_ref = re.sub(r"[^A-Za-z0-9]", "", (billing.get("lease") or billing["customer"] or "ELEC"))[:12]
    order_id = f"ORD-{clean_ref}-{rand_suffix}"

    user_info = frappe.db.get_value(
        "User", frappe.session.user, ["full_name", "email", "mobile_no", "phone"], as_dict=True
    ) or {}
    buyer_email = user_info.get("email") or frappe.session.user
    buyer_name = user_info.get("full_name") or billing["customer"]
    buyer_phone = normalize_phone_number(
        user_info.get("mobile_no") or user_info.get("phone") or "255700000000"
    ) or "255700000000"
    buyer_uuid = frappe.defaults.get_user_default("selcom_gateway_buyer_uuid", buyer_email) or ""

    extra = {
        "electricity_purchase": True,
        "payment_workflow": WORKFLOW_ELECTRICITY_POS,
        "lease": billing.get("lease"),
        "property": billing.get("property"),
        "customer": billing.get("customer"),
        "company": billing.get("company"),
        "cost_center": billing.get("cost_center"),
        "meter_number": intent["meter_number"],
        "tanesco_amount": intent["tanesco"],
        "generator_amount": intent["generator"],
        "total_amount": total,
        "lines": [
            {
                "item_code": ln["item_code"],
                "qty": flt(ln["qty"]),
                "rate": flt(ln["rate"]),
                "amount_inclusive": flt(ln["amount_inclusive"]),
            }
            for ln in intent["lines"]
        ],
        "card_token": card_token[:6] + "..." if len(card_token) > 6 else card_token,
    }

    txn = frappe.get_doc(
        {
            "doctype": "Selcom Payment Transaction Log",
            "order_id": order_id,
            "payment_workflow": WORKFLOW_ELECTRICITY_POS,
            "reference_doctype": "Lease" if billing.get("lease") else "Customer",
            "reference_name": billing.get("lease") or billing["customer"],
            "customer": billing["customer"],
            "amount": pay_amount,
            "currency": "TZS",
            "payment_channel": "CARD",
            "phone_number": buyer_phone,
            "status": "Pending",
            "raw_request": json.dumps(extra),
        }
    )
    txn.insert(ignore_permissions=True)
    frappe.db.commit()

    name_parts = (buyer_name or "Viva Tenant").strip().split(" ", 1)
    first_name = name_parts[0] if name_parts else "Viva"
    last_name = name_parts[1] if len(name_parts) > 1 else "Tenant"
    site_url = (frappe.utils.get_url() or "https://dev15-viva2.vvsdtz.com").rstrip("/")
    webhook_b64 = base64.b64encode(
        f"{site_url}/api/method/propms.api.v1.payments.selcom_ipn_webhook".encode("utf-8")
    ).decode("utf-8")
    redirect_b64 = base64.b64encode(f"{site_url}/payment-success".encode("utf-8")).decode("utf-8")
    cancel_b64 = base64.b64encode(f"{site_url}/payment-cancel".encode("utf-8")).decode("utf-8")

    order_payload = {
        "vendor": client.vendor_id,
        "order_id": order_id,
        "buyer_email": buyer_email,
        "buyer_name": buyer_name,
        "buyer_userid": buyer_email,
        "buyer_phone": buyer_phone,
        "gateway_buyer_uuid": buyer_uuid,
        "amount": int(round(pay_amount)),
        "currency": "TZS",
        "no_of_items": 1,
        "payment_methods": "CARD",
        "webhook": webhook_b64,
        "redirect_url": redirect_b64,
        "cancel_url": cancel_b64,
        "buyer_remarks": f"Electricity {intent['meter_number']}",
        "merchant_remarks": "Viva Towers Electricity Card",
        "billing.firstname": first_name,
        "billing.lastname": last_name,
        "billing.address_1": "Viva Towers",
        "billing.address_2": "",
        "billing.city": "Dar es Salaam",
        "billing.state_or_region": "Dar es Salaam",
        "billing.postcode_or_pobox": "10000",
        "billing.country": "TZ",
        "billing.phone": buyer_phone,
    }
    order_res = client.post("/v1/checkout/create-order", order_payload)
    result_status = (order_res.get("result") or "").upper()
    result_code = (order_res.get("resultcode") or "").strip()
    if result_status not in ("SUCCESS", "COMPLETED", "200") and result_code not in ("000", "200"):
        error_msg = order_res.get("message") or "Failed to initialize order with Selcom"
        txn.status = "Failed"
        txn.error_message = str(error_msg)
        txn.raw_response = json.dumps(order_res)
        txn.save(ignore_permissions=True)
        frappe.db.commit()
        return {"status": "error", "message": error_msg, "order_id": order_id}

    card_res = client.post(
        "/v1/checkout/card-payment",
        {
            "transid": f"TXN-{order_id}",
            "vendor": client.vendor_id,
            "order_id": order_id,
            "card_token": card_token,
            "buyer_userid": buyer_email,
            "gateway_buyer_uuid": buyer_uuid,
        },
    )
    txn.raw_response = json.dumps({"order_minimal": order_res, "card_payment": card_res})

    card_result = (card_res.get("result") or "").upper()
    card_code = (card_res.get("resultcode") or "").strip()
    gateway_url = None
    data_field = card_res.get("data")
    if isinstance(data_field, list) and data_field:
        gateway_url = (
            data_field[0].get("payment_gateway_url")
            or data_field[0].get("form_url")
            or data_field[0].get("gateway_url")
        )
    elif isinstance(data_field, dict):
        gateway_url = (
            data_field.get("payment_gateway_url")
            or data_field.get("form_url")
            or data_field.get("gateway_url")
        )
    if gateway_url and not str(gateway_url).startswith("http"):
        try:
            decoded = base64.b64decode(gateway_url).decode("utf-8")
            if decoded.startswith("http"):
                gateway_url = decoded
        except Exception:
            pass
    txn.gateway_url = gateway_url or ""

    if card_result in ("SUCCESS", "COMPLETED", "000") or card_code in ("000", "200"):
        if gateway_url:
            txn.save(ignore_permissions=True)
            frappe.db.commit()
            return {
                "status": "success",
                "action": "OPEN_3DS_WEBVIEW",
                "message": "Please complete 3D-Secure authentication",
                "order_id": order_id,
                "gateway_url": gateway_url,
                "payment_workflow": WORKFLOW_ELECTRICITY_POS,
            }

        from propms.api.v1.payments.webhook import process_successful_payment

        process_successful_payment(
            order_id=order_id,
            selcom_ref=card_res.get("reference") or order_id,
            amount=pay_amount,
            raw_payload=card_res,
        )
        txn.reload()
        return {
            "status": "success",
            "action": "PAYMENT_COMPLETED",
            "message": "Card payment processed successfully",
            "order_id": order_id,
            "invoice_name": txn.sales_invoice,
            "payment_workflow": WORKFLOW_ELECTRICITY_POS,
        }

    err = card_res.get("message") or "Failed to charge stored card"
    txn.status = "Failed"
    txn.error_message = str(err)
    txn.save(ignore_permissions=True)
    frappe.db.commit()
    return {"status": "error", "message": err, "order_id": order_id, "gateway": card_res}
