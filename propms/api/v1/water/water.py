# -*- coding: utf-8 -*-
"""Drinking Water cart & checkout API for Property Management Solution (Mobile App).

Cart model: one draft Sales Order per product.
Checkout: pay one Sales Order via Selcom → submit SO → paid POS Sales Invoice.
"""

from __future__ import unicode_literals

import json
import frappe
from frappe import _
from frappe.utils import today, flt, cint, add_days
from erpnext.utilities.product import get_price
from propms.custom.lease import get_customer_from_lease, get_tenant_context_for_user
from propms.api.v1.invoices.invoices import _get_tenant_context, _get_current_user_email
from propms.api.v1.payments.workflows import WORKFLOW_SALES_ORDER_POS

MOBILE_ORDER_TYPE_WATER = "Water"

WATER_CART_DEPRECATED = {
    "status": "error",
    "code": "DEPRECATED",
    "message": (
        "Water cart is retired. Use POS Store Services: "
        "get_pos_store_catalog / get_pos_store_item / checkout_pos_item."
    ),
    "use": "propms.api.mobile.get_pos_store_catalog",
}


def _water_cart_deprecated():
    return dict(WATER_CART_DEPRECATED)


def _parse_request_payload(defaults=None):
    """Safely extract parameters from form_dict or JSON body."""
    payload = dict(defaults or {})
    for k, v in frappe.form_dict.items():
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


def _get_water_item_groups():
    water_item_groups = frappe.get_all(
        "Item Group",
        filters={"name": ["like", "%Water%"]},
        pluck="name",
    )
    if "Water" not in water_item_groups:
        water_item_groups.append("Water")
    if "WATER" not in water_item_groups:
        water_item_groups.append("WATER")
    return water_item_groups


def _is_water_item(item_code):
    item_group = frappe.db.get_value("Item", item_code, "item_group")
    if not item_group:
        return False
    groups = {g.lower() for g in _get_water_item_groups()}
    return item_group.lower() in groups or "water" in (item_group or "").lower()


def _resolve_tenant_billing(target_lease=None):
    """Resolve customer / lease / company / cost center for the logged-in tenant."""
    user_email = _get_current_user_email()
    user_leases, user_customers, lease_to_prop, cost_centers, cc_to_prop = _get_tenant_context(user_email)

    if not user_leases and not user_customers:
        ctx = get_tenant_context_for_user(user_email) or {}
        if ctx.get("lease"):
            user_leases = [ctx["lease"]]
        if ctx.get("customer"):
            user_customers = [ctx["customer"]]

    selected_lease = None
    if target_lease and target_lease.lower() != "unit" and frappe.db.exists("Lease", target_lease):
        if (
            not user_leases
            or target_lease in user_leases
            or "System Manager" in frappe.get_roles(frappe.session.user)
        ):
            selected_lease = target_lease

    if not selected_lease and user_leases:
        selected_lease = user_leases[0]

    customer = None
    if selected_lease:
        # POS utilities (water / shared billing): charge Lease POS Customer
        # (`customer`), not Lease Customer — unit may be leased to one party
        # while another pays for water at POS / in the app.
        if frappe.get_meta("Lease").has_field("customer"):
            customer = frappe.db.get_value("Lease", selected_lease, "customer")
        if not customer:
            customer = get_customer_from_lease(selected_lease) or frappe.db.get_value(
                "Lease", selected_lease, "lease_customer"
            )
    if not customer and user_customers:
        customer = user_customers[0]

    property_name = lease_to_prop.get(selected_lease) if selected_lease else None
    if not property_name and selected_lease:
        property_name = frappe.db.get_value("Lease", selected_lease, "property")

    company = None
    cost_center = None
    if property_name:
        prop_rec = frappe.db.get_value("Property", property_name, ["company", "cost_center"], as_dict=True)
        if prop_rec:
            company = prop_rec.get("company")
            cost_center = prop_rec.get("cost_center")

    if not company and selected_lease:
        company = frappe.db.get_value("Lease", selected_lease, "company")
    if not company:
        company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
            "Company", {}, "name"
        )

    if not cost_center and company:
        cost_center = frappe.db.get_value("Company", company, "cost_center")
    if not cost_center:
        cost_center = frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name")

    return {
        "customer": customer,
        "lease": selected_lease,
        "property": property_name,
        "company": company,
        "cost_center": cost_center,
        "user_email": user_email,
        "user_leases": user_leases or [],
        "user_customers": user_customers or [],
    }


def _get_mobile_cart_stock_context(company=None):
    """Resolve warehouse used for mobile cart stock display / SO lines.

    Prefers the warehouse on Selcom Settings → Mobile Cart POS Profile.
    Falls back to Stock Settings default only if no POS Profile is linked.
    """
    profile = None
    if frappe.db.exists("DocType", "Selcom Settings"):
        meta = frappe.get_meta("Selcom Settings")
        if meta.has_field("mobile_cart_pos_profile"):
            profile = frappe.db.get_single_value("Selcom Settings", "mobile_cart_pos_profile")
    if profile and frappe.db.exists("POS Profile", profile):
        warehouse = frappe.db.get_value("POS Profile", profile, ["warehouse", "company"], as_dict=True) or {}
        return {
            "pos_profile": profile,
            "warehouse": warehouse.get("warehouse"),
            "company": warehouse.get("company") or company,
            "source": "pos_profile",
        }

    return {
        "pos_profile": None,
        "warehouse": frappe.db.get_single_value("Stock Settings", "default_warehouse"),
        "company": company,
        "source": "stock_settings",
    }


def _get_pos_warehouse(company=None):
    """Warehouse from the accountant-configured Mobile Cart POS Profile (fallback: Stock Settings)."""
    return _get_mobile_cart_stock_context(company).get("warehouse")


def _get_bin_qty(item_code, warehouse):
    """Actual stock qty for an item in a warehouse."""
    if not item_code or not warehouse:
        return 0.0
    return flt(
        frappe.db.get_value(
            "Bin",
            {"item_code": item_code, "warehouse": warehouse},
            "actual_qty",
        )
        or 0
    )


def _serialize_cart_so(so):
    item = (so.items or [None])[0]
    return {
        "sales_order": so.name,
        "status": so.status,
        "docstatus": so.docstatus,
        "customer": so.customer,
        "lease": getattr(so, "lease", None),
        "item_code": item.item_code if item else None,
        "item_name": item.item_name if item else None,
        "quantity": flt(item.qty) if item else 0,
        "rate": flt(item.rate) if item else 0,
        "amount": flt(item.amount) if item else 0,
        "stock_uom": item.uom if item else None,
        "delivery_instructions": getattr(so, "delivery_instructions", None) or "",
        "net_total": flt(so.net_total),
        "tax_amount": flt(so.total_taxes_and_charges),
        "grand_total": flt(so.grand_total),
        "currency": so.currency,
        "payment_ready": so.docstatus == 0 and flt(so.grand_total) > 0,
    }


def _find_draft_water_so(customer, item_code, lease=None):
    filters = {
        "customer": customer,
        "docstatus": 0,
        "mobile_order_type": MOBILE_ORDER_TYPE_WATER,
    }
    if lease and frappe.get_meta("Sales Order").has_field("lease"):
        filters["lease"] = lease

    candidates = frappe.get_all("Sales Order", filters=filters, pluck="name", order_by="creation desc")
    for name in candidates:
        so = frappe.get_doc("Sales Order", name)
        if so.items and so.items[0].item_code == item_code:
            return so
    return None


def _assert_so_access(so, billing):
    roles = frappe.get_roles(frappe.session.user)
    if "System Manager" in roles or "Accounts Manager" in roles:
        return
    if so.customer and so.customer in (billing.get("user_customers") or []):
        return
    if so.customer == billing.get("customer"):
        return
    frappe.throw(_("You are not permitted to access this cart item."), frappe.PermissionError)


@frappe.whitelist(methods=["GET", "POST"])
def get_drinking_water_products():
    """Deprecated — use get_pos_store_catalog."""
    return _water_cart_deprecated()


@frappe.whitelist(methods=["POST"])
def add_to_cart(item_code=None, quantity=1, delivery_instructions=None, lease=None):
    """Deprecated — use checkout_pos_item."""
    return _water_cart_deprecated()


@frappe.whitelist(methods=["POST"])
def update_cart_item(sales_order=None, quantity=None, delivery_instructions=None):
    """Deprecated — water cart retired."""
    return _water_cart_deprecated()


@frappe.whitelist(methods=["POST"])
def remove_from_cart(sales_order=None):
    """Deprecated — water cart retired."""
    return _water_cart_deprecated()


@frappe.whitelist(methods=["GET", "POST"])
def get_cart(lease=None):
    """Deprecated — water cart retired."""
    return _water_cart_deprecated()


@frappe.whitelist(methods=["POST"])
def checkout_water_order(sales_order=None, payment_method="MOBILE_MONEY", phone_number=None, amount=None):
    """Deprecated — use checkout_pos_item."""
    return _water_cart_deprecated()


# --- Legacy implementations kept below for reference / possible rollback ---
# (active entrypoints above soft-fail; settle handler sales_order_pos still works for Pending txns)


def _legacy_get_drinking_water_products():
    """Fetch active drinking water items for the mobile catalog.

    Stock (`actual_qty` / `in_stock`) is read from the warehouse on the
    Mobile Cart POS Profile configured in Selcom Settings.
    """
    _require_auth()

    company = frappe.db.get_single_value("Global Defaults", "default_company") or frappe.db.get_value(
        "Company", {}, "name"
    )
    currency = frappe.db.get_value("Company", company, "default_currency") or "TZS"
    price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
    stock_ctx = _get_mobile_cart_stock_context(company)
    warehouse = stock_ctx.get("warehouse")
    pos_profile = stock_ctx.get("pos_profile")

    water_item_groups = _get_water_item_groups()
    items = frappe.get_all(
        "Item",
        filters={
            "item_group": ["in", water_item_groups],
            "disabled": 0,
            "is_sales_item": 1,
        },
        fields=[
            "name",
            "item_name",
            "item_code",
            "item_group",
            "description",
            "stock_uom",
            "image",
            "standard_rate",
            "is_stock_item",
        ],
        order_by="item_name asc",
    )

    products = []
    for item in items:
        rate = flt(item.get("standard_rate")) or 0.0
        price_rec = get_price(item["item_code"], price_list, None, company)
        if price_rec and price_rec.get("price_list_rate"):
            rate = flt(price_rec["price_list_rate"])

        is_stock_item = cint(item.get("is_stock_item"))
        actual_qty = 0.0
        in_stock = True
        if is_stock_item:
            actual_qty = _get_bin_qty(item["item_code"], warehouse) if warehouse else 0.0
            in_stock = bool(warehouse) and actual_qty > 0

        products.append(
            {
                "item_code": item["item_code"],
                "item_name": item["item_name"] or item["item_code"],
                "item_group": item["item_group"],
                "description": item["description"] or "",
                "stock_uom": item["stock_uom"] or "Nos",
                "rate": rate,
                "formatted_rate": f"{currency} {rate:,.2f}",
                "currency": currency,
                "image": item.get("image") or "",
                "is_stock_item": is_stock_item,
                "actual_qty": actual_qty,
                "in_stock": in_stock,
                "warehouse": warehouse or "",
            }
        )

    return {
        "status": "success",
        "currency": currency,
        "count": len(products),
        "pos_profile": pos_profile,
        "warehouse": warehouse,
        "stock_source": stock_ctx.get("source"),
        "products": products,
    }


@frappe.whitelist(methods=["POST"])
def add_to_cart(item_code=None, quantity=1, delivery_instructions=None, lease=None):
    """Add a water product to cart as a draft Sales Order (one item per SO).

    If a draft SO already exists for the same item+tenant, quantity is increased.
    """
    _require_auth()
    payload = _parse_request_payload(
        {
            "item_code": item_code,
            "quantity": quantity,
            "delivery_instructions": delivery_instructions,
            "lease": lease,
        }
    )

    target_item_code = (payload.get("item_code") or "").strip()
    qty = flt(payload.get("quantity") or 1)
    notes = (payload.get("delivery_instructions") or payload.get("notes") or "").strip()
    target_lease = (payload.get("lease") or "").strip()

    if not target_item_code:
        return {"status": "error", "message": "item_code is required"}
    if qty <= 0:
        return {"status": "error", "message": "quantity must be greater than 0"}
    if not frappe.db.exists("Item", target_item_code):
        return {"status": "error", "message": f"Item '{target_item_code}' not found"}
    if not _is_water_item(target_item_code):
        return {"status": "error", "message": f"Item '{target_item_code}' is not a drinking water product"}

    item_doc = frappe.get_doc("Item", target_item_code)
    if item_doc.disabled:
        return {"status": "error", "message": f"Item '{target_item_code}' is currently unavailable"}

    billing = _resolve_tenant_billing(target_lease)
    if not billing.get("customer"):
        return {"status": "error", "message": "Customer account could not be resolved for billing"}

    company = billing["company"]
    price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Standard Selling"
    customer_group = frappe.db.get_value("Customer", billing["customer"], "customer_group")
    price_rec = get_price(target_item_code, price_list, customer_group, company)
    rate = flt(price_rec.get("price_list_rate")) if price_rec else flt(item_doc.standard_rate)

    existing = _find_draft_water_so(billing["customer"], target_item_code, billing.get("lease"))
    current_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        if existing:
            existing.items[0].qty = flt(existing.items[0].qty) + qty
            existing.items[0].rate = rate
            if notes:
                existing.delivery_instructions = notes
                existing.items[0].description = notes
            existing.flags.ignore_permissions = True
            existing.save(ignore_permissions=True)
            frappe.db.commit()
            return {
                "status": "success",
                "message": "Cart item quantity updated",
                "cart_item": _serialize_cart_so(existing),
            }

        delivery_date = add_days(today(), 1)
        so = frappe.get_doc(
            {
                "doctype": "Sales Order",
                "company": company,
                "customer": billing["customer"],
                "transaction_date": today(),
                "delivery_date": delivery_date,
                "cost_center": billing.get("cost_center"),
                "lease": billing.get("lease"),
                "mobile_order_type": MOBILE_ORDER_TYPE_WATER,
                "delivery_instructions": notes,
                "order_type": "Sales",
                "items": [
                    {
                        "item_code": target_item_code,
                        "item_name": item_doc.item_name or target_item_code,
                        "description": notes or (item_doc.description or item_doc.item_name or target_item_code),
                        "qty": qty,
                        "rate": rate,
                        "uom": item_doc.stock_uom or "Nos",
                        "delivery_date": delivery_date,
                        "cost_center": billing.get("cost_center"),
                        "warehouse": _get_pos_warehouse(company),
                    }
                ],
            }
        )
        so.flags.ignore_permissions = True
        so.flags.ignore_mandatory = True
        so.insert(ignore_permissions=True)
        frappe.db.commit()
        return {
            "status": "success",
            "message": "Item added to cart",
            "cart_item": _serialize_cart_so(so),
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "add_to_cart")
        return {"status": "error", "message": f"Failed to add item to cart: {str(e)}"}
    finally:
        frappe.set_user(current_user)


@frappe.whitelist(methods=["POST"])
def order_drinking_water(item_code=None, quantity=1, delivery_instructions=None, lease=None):
    """Alias for add_to_cart (replaces the old immediate Sales Invoice workflow)."""
    return add_to_cart(
        item_code=item_code,
        quantity=quantity,
        delivery_instructions=delivery_instructions,
        lease=lease,
    )


@frappe.whitelist(methods=["POST"])
def update_cart_item(sales_order=None, quantity=None, delivery_instructions=None):
    """Update quantity (and optional notes) on a draft water cart Sales Order."""
    _require_auth()
    payload = _parse_request_payload(
        {
            "sales_order": sales_order,
            "quantity": quantity,
            "delivery_instructions": delivery_instructions,
        }
    )
    so_name = (payload.get("sales_order") or payload.get("sales_order_name") or "").strip()
    qty = payload.get("quantity")
    notes = payload.get("delivery_instructions")

    if not so_name:
        return {"status": "error", "message": "sales_order is required"}
    if qty is None:
        return {"status": "error", "message": "quantity is required"}
    qty = flt(qty)
    if qty <= 0:
        return {"status": "error", "message": "quantity must be greater than 0 (use remove_from_cart to delete)"}

    if not frappe.db.exists("Sales Order", so_name):
        return {"status": "error", "message": f"Sales Order '{so_name}' not found"}

    billing = _resolve_tenant_billing()
    so = frappe.get_doc("Sales Order", so_name)
    _assert_so_access(so, billing)

    if so.docstatus != 0:
        return {"status": "error", "message": "Only draft cart items can be updated"}
    if getattr(so, "mobile_order_type", None) != MOBILE_ORDER_TYPE_WATER:
        return {"status": "error", "message": "Not a water cart order"}

    current_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        so.items[0].qty = qty
        if notes is not None:
            so.delivery_instructions = (notes or "").strip()
            so.items[0].description = (notes or "").strip() or so.items[0].description
        so.flags.ignore_permissions = True
        so.save(ignore_permissions=True)
        frappe.db.commit()
        so.reload()
        return {
            "status": "success",
            "message": "Cart item updated",
            "cart_item": _serialize_cart_so(so),
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "update_cart_item")
        return {"status": "error", "message": f"Failed to update cart item: {str(e)}"}
    finally:
        frappe.set_user(current_user)


@frappe.whitelist(methods=["POST"])
def remove_from_cart(sales_order=None):
    """Remove a draft water cart item (delete draft Sales Order)."""
    _require_auth()
    payload = _parse_request_payload({"sales_order": sales_order})
    so_name = (payload.get("sales_order") or payload.get("sales_order_name") or "").strip()
    if not so_name:
        return {"status": "error", "message": "sales_order is required"}
    if not frappe.db.exists("Sales Order", so_name):
        return {"status": "error", "message": f"Sales Order '{so_name}' not found"}

    billing = _resolve_tenant_billing()
    so = frappe.get_doc("Sales Order", so_name)
    _assert_so_access(so, billing)

    if getattr(so, "mobile_order_type", None) != MOBILE_ORDER_TYPE_WATER:
        return {"status": "error", "message": "Not a water cart order"}

    current_user = frappe.session.user
    try:
        frappe.set_user("Administrator")
        if so.docstatus == 0:
            frappe.delete_doc("Sales Order", so_name, ignore_permissions=True, force=1)
            frappe.db.commit()
            return {"status": "success", "message": "Item removed from cart", "sales_order": so_name}

        if so.docstatus == 1:
            so.cancel()
            frappe.db.commit()
            return {"status": "success", "message": "Sales Order cancelled", "sales_order": so_name}

        return {"status": "error", "message": "Sales Order is already cancelled"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "remove_from_cart")
        return {"status": "error", "message": f"Failed to remove cart item: {str(e)}"}
    finally:
        frappe.set_user(current_user)


@frappe.whitelist(methods=["GET", "POST"])
def get_cart(lease=None):
    """List draft water cart Sales Orders for the authenticated tenant."""
    _require_auth()
    payload = _parse_request_payload({"lease": lease})
    target_lease = (payload.get("lease") or "").strip() or None
    billing = _resolve_tenant_billing(target_lease)
    if not billing.get("customer"):
        return {"status": "error", "message": "Customer account could not be resolved", "items": [], "count": 0}

    filters = {
        "customer": billing["customer"],
        "docstatus": 0,
        "mobile_order_type": MOBILE_ORDER_TYPE_WATER,
    }
    if billing.get("lease") and frappe.get_meta("Sales Order").has_field("lease"):
        # Show cart for this lease, plus any without lease set
        pass

    names = frappe.get_all("Sales Order", filters=filters, pluck="name", order_by="creation desc")
    items = []
    total = 0.0
    currency = "TZS"
    for name in names:
        so = frappe.get_doc("Sales Order", name)
        if billing.get("lease") and getattr(so, "lease", None) and so.lease != billing["lease"]:
            # If tenant asked for a specific lease context, skip other leases
            if target_lease:
                continue
        row = _serialize_cart_so(so)
        items.append(row)
        total += flt(row["grand_total"])
        currency = row["currency"] or currency

    return {
        "status": "success",
        "count": len(items),
        "currency": currency,
        "grand_total": total,
        "items": items,
    }


@frappe.whitelist(methods=["POST"])
def checkout_water_order(sales_order=None, payment_method="MOBILE_MONEY", phone_number=None, amount=None):
    """Pay one water cart Sales Order via Selcom (no bulk pay).

    On Selcom success the shared payment engine submits the SO and creates a
    paid POS Sales Invoice (workflow: sales_order_pos).
    """
    _require_auth()
    payload = _parse_request_payload(
        {
            "sales_order": sales_order,
            "payment_method": payment_method,
            "phone_number": phone_number,
            "amount": amount,
        }
    )
    so_name = (payload.get("sales_order") or payload.get("sales_order_name") or "").strip()
    if not so_name:
        return {"status": "error", "message": "sales_order is required"}

    billing = _resolve_tenant_billing()
    if not frappe.db.exists("Sales Order", so_name):
        return {"status": "error", "message": f"Sales Order '{so_name}' not found"}

    so = frappe.get_doc("Sales Order", so_name)
    _assert_so_access(so, billing)

    if so.docstatus != 0:
        return {"status": "error", "message": "Only draft cart items can be checked out"}
    if getattr(so, "mobile_order_type", None) != MOBILE_ORDER_TYPE_WATER:
        return {"status": "error", "message": "Not a water cart order"}
    if flt(so.grand_total) <= 0:
        return {"status": "error", "message": "Cart item has no payable amount"}

    from propms.api.v1.payments.services import initiate_reference_payment

    return initiate_reference_payment(
        reference_doctype="Sales Order",
        reference_name=so.name,
        payment_workflow=WORKFLOW_SALES_ORDER_POS,
        amount=payload.get("amount"),
        payment_method=payload.get("payment_method") or "MOBILE_MONEY",
        phone_number=payload.get("phone_number"),
    )
