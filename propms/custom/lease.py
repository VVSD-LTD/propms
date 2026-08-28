from __future__ import unicode_literals

import frappe
from frappe.utils.password import update_password


VIVA_TENANT_ROLE = "Mobile VIVA Tenant"


def _get_tenant_detail_rows(doc):
    """Return Tenant Details rows from Lease custom table field."""
    return doc.get("custom_tenant_details") or doc.get("tenant_details") or []


def _get_existing_tenant_detail_for_email(email, current_parent=None, current_row_name=None):
    """Return a Tenant Details row using the email outside current row/parent."""
    if not email:
        return None

    filters = {"user_email": ("=", email)}
    if current_parent:
        filters["parent"] = ("!=", current_parent)
    if current_row_name:
        filters["name"] = ("!=", current_row_name)

    return frappe.db.get_value("Tenant Details", filters, "name")


def _get_customers_for_user_email_across_leases(user_email, exclude_lease=None):
    """Return set of customers for this user across all assigned Leases.

    Customer is resolved via Lease Item 'Service Charge' paid_by.
    """
    parents = frappe.get_all(
        "Tenant Details",
        filters={"user_email": user_email, "parenttype": "Lease"},
        pluck="parent",
        distinct=True,
    )
    customers = set()
    for parent in parents or []:
        if exclude_lease and parent == exclude_lease:
            continue
        c = get_customer_from_lease(parent)
        if c:
            customers.add(c)
    return customers


def _ensure_user_for_row(row):
    """Create or update the Website User backing a single Tenant Details row."""
    if not row.user_email:
        return

    user_name = row.user or row.user_email
    if frappe.db.exists("User", user_name):
        user = frappe.get_doc("User", user_name)
    elif frappe.db.exists("User", row.user_email):
        user = frappe.get_doc("User", row.user_email)
    else:
        user = None

    password = (
        row.get_password("user_password")
        if getattr(row, "user_password", None)
        else None
    )

    if not user:
        user = frappe.new_doc("User")
        user.email = row.user_email
        user.first_name = row.full_name or row.user_email
        user.user_type = "Website User"
        user.send_welcome_email = 0
        user.enabled = 1 if row.get("enabled") else 0
        user.insert(ignore_permissions=True)
        frappe.db.commit()
    else:
        user.first_name = row.full_name or row.user_email
        user.enabled = 1 if row.get("enabled") else 0

    if password:
        update_password(user.name, password)
        frappe.db.commit()

    if frappe.db.exists("Role", VIVA_TENANT_ROLE) and VIVA_TENANT_ROLE not in [
        r.role for r in user.roles
    ]:
        user.add_roles(VIVA_TENANT_ROLE)

    user.save(ignore_permissions=True)
    frappe.db.commit()

    if row.user != user.name:
        row.db_set("user", user.name)
        frappe.db.commit()


def _get_row_user_name(row):
    """Return the linked User docname for a Tenant Details row."""
    return row.get("user") or row.get("user_email")


def _share_lease_with_tenant_user(lease_name: str, row) -> None:
    """Grant doc-level read permission for this specific Lease to the tenant user."""
    user_name = _get_row_user_name(row)
    if not user_name:
        return

    # If tenant is disabled, we remove any existing share for safety.
    enabled = 1 if row.get("enabled") else 0
    try:
        import frappe.share

        if not enabled:
            frappe.share.remove("Lease", lease_name, user_name)
            return

        # Ignore share permission checks (this hook runs server-side).
        frappe.share.add_docshare(
            "Lease",
            lease_name,
            user=user_name,
            read=1,
            write=0,
            submit=0,
            share=0,
            flags={"ignore_share_permission": True},
        )
    except Exception:
        # Avoid failing save if share fails for environment reasons.
        pass


def _cleanup_lease_shares(lease_name: str, current_user_names) -> None:
    """Remove Lease shares for mobile tenant users not present on current rows."""
    try:
        shared_users = frappe.get_all(
            "DocShare",
            filters={"share_doctype": "Lease", "share_name": lease_name, "read": 1},
            pluck="user",
        )
    except Exception:
        return

    for user in shared_users or []:
        if user in current_user_names:
            continue
        try:
            # Only cleanup for mobile tenant role to avoid interfering with other shares.
            if VIVA_TENANT_ROLE in (frappe.get_roles(user) or []):
                import frappe.share

                frappe.share.remove("Lease", lease_name, user)
        except Exception:
            pass


def get_customer_from_lease(lease_name: str, service_charge_keyword: str = "Service Charge"):
    """Resolve Customer for a Lease using Lease Item paid_by.

    Business rule:
    - Find Lease Item rows where `lease_item` contains "Service Charge"
    - Take its `paid_by` as the Customer.
    """
    if not lease_name:
        return None

    # `lease_item` is a Link field, stored as Item name.
    # We match by substring because item names may be like "Service Charge - <rate>".
    rows = frappe.get_all(
        "Lease Item",
        filters={
            "parent": lease_name,
            "lease_item": ["like", f"%{service_charge_keyword}%"],
        },
        fields=["paid_by"],
        distinct=True,
    )
    if not rows:
        return None
    paid_bys = [r.get("paid_by") for r in rows if r.get("paid_by")]
    return paid_bys[0] if paid_bys else None


def get_customer_from_lease_doc(doc, service_charge_keyword: str = "Service Charge"):
    """Resolve customer from the current Lease doc rows first, then DB fallback.

    This avoids false negatives during before_insert where child rows can exist in-memory
    but are not yet queryable via `frappe.get_all("Lease Item", ...)`.
    """
    if not doc:
        return None

    keyword = (service_charge_keyword or "").strip().lower()
    # Prefer in-memory child rows from the document being validated/saved.
    for row in doc.get("lease_item") or []:
        lease_item_value = (getattr(row, "lease_item", None) or "").strip()
        if keyword and keyword not in lease_item_value.lower():
            continue
        paid_by = getattr(row, "paid_by", None)
        if paid_by:
            return paid_by

    # Fallback to DB lookup for already-saved leases.
    return get_customer_from_lease(getattr(doc, "name", None), service_charge_keyword=service_charge_keyword)


def get_tenant_context_for_user(user=None):
    """Resolve Lease/Tenant Details/Customer for a user.

    Prefers Tenant Details rows under Lease (new architecture) and falls back to
    Tenant parent rows for backward compatibility.
    """
    current = user or frappe.session.user
    if not current or current == "Guest":
        return {}

    user_email = frappe.db.get_value("User", current, "email") or current
    if not user_email:
        return {}

    td = frappe.db.get_value(
        "Tenant Details",
        {"user_email": user_email, "parenttype": "Lease"},
        ["name", "parent"],
        as_dict=True,
    )
    if td and td.get("parent"):
        lease = frappe.get_doc("Lease", td.parent)
        customer = get_customer_from_lease(lease.name)
        return {
            "lease": lease.name,
            "customer": customer,
            "tenant_detail": td.name,
            "user_email": user_email,
        }

    td_legacy = frappe.db.get_value(
        "Tenant Details",
        {"user_email": user_email, "parenttype": "Tenant"},
        ["name", "parent"],
        as_dict=True,
    )
    if td_legacy and td_legacy.get("parent"):
        tenant = frappe.get_doc("Tenant", td_legacy.parent)
        return {
            "tenant": tenant.name,
            "customer": getattr(tenant, "customer", None),
            "customer_name": getattr(tenant, "customer_name", None),
            "tenant_detail": td_legacy.name,
            "user_email": user_email,
        }
    return {}


def lease_before_insert(doc, method=None):
    """Validate tenant details rows before insert.

    Important: allow the same user_email to exist in multiple Lease documents
    (same person / same login across multiple apartments).
    """
    seen_emails = set()
    current_customer = get_customer_from_lease_doc(doc)
    if not current_customer:
        frappe.throw(
            frappe._(
                "Cannot determine customer for this Lease. Ensure Lease Item contains 'Service Charge' with Paid By (Customer)."
            )
        )
    for row in _get_tenant_detail_rows(doc):
        if not row.user_email:
            continue

        email_norm = str(row.user_email).strip().lower()
        if email_norm in seen_emails:
            frappe.throw(
                frappe._("Email {0} is used more than once in this Lease.").format(
                    row.user_email
                )
            )
        seen_emails.add(email_norm)

        existing_customers = _get_customers_for_user_email_across_leases(
            email_norm, exclude_lease=doc.name
        )
        # Allow same user on multiple apartments ONLY if they belong to same customer.
        if existing_customers and existing_customers != {current_customer}:
            frappe.throw(
                frappe._(
                    "This user email is already assigned to another customer. Please use a different email for this customer."
                )
            )


def lease_validate(doc, method=None):
    """Validate lease tenant rows.

    Important: user_email uniqueness is enforced only within a single Lease,
    not globally across leases.
    """
    seen_emails = set()
    current_customer = get_customer_from_lease_doc(doc)
    if not current_customer:
        frappe.throw(
            frappe._(
                "Cannot determine customer for this Lease. Ensure Lease Item contains 'Service Charge' with Paid By (Customer)."
            )
        )
    for row in _get_tenant_detail_rows(doc):
        if not row.user_email:
            continue

        email_norm = str(row.user_email).strip().lower()
        if email_norm in seen_emails:
            frappe.throw(
                frappe._("Email {0} is used more than once in this Lease.").format(
                    row.user_email
                )
            )
        seen_emails.add(email_norm)

        existing_customers = _get_customers_for_user_email_across_leases(
            email_norm, exclude_lease=doc.name
        )
        if existing_customers and existing_customers != {current_customer}:
            frappe.throw(
                frappe._(
                    "This user email is already assigned to another customer. Please use a different email for this customer."
                )
            )


def lease_after_insert(doc, method=None):
    """After creating Lease, create users for tenant-detail rows."""
    lease_name = doc.name
    current_user_names = set()

    for row in _get_tenant_detail_rows(doc):
        if not row.user_email:
            continue

        _ensure_user_for_row(row)
        _share_lease_with_tenant_user(lease_name, row)

        if row.get("enabled"):
            user_name = _get_row_user_name(row)
            if user_name:
                current_user_names.add(user_name)

    _cleanup_lease_shares(lease_name, current_user_names)


def lease_on_update(doc, method=None):
    """On Lease update, sync users for all tenant rows reliably."""
    lease_name = doc.name
    current_user_names = set()

    for row in _get_tenant_detail_rows(doc):
        if not row.user_email:
            continue

        _ensure_user_for_row(row)
        _share_lease_with_tenant_user(lease_name, row)

        if row.get("enabled"):
            user_name = _get_row_user_name(row)
            if user_name:
                current_user_names.add(user_name)

    _cleanup_lease_shares(lease_name, current_user_names)


@frappe.whitelist()
def sync_lease_tenant_users(lease_name):
    """Manual recovery utility to sync/create tenant users for a Lease."""
    if not lease_name:
        frappe.throw(frappe._("lease_name is required"))

    lease = frappe.get_doc("Lease", lease_name)
    count = 0
    current_user_names = set()

    for row in _get_tenant_detail_rows(lease):
        if not row.user_email:
            continue

        _ensure_user_for_row(row)
        _share_lease_with_tenant_user(lease_name, row)

        if row.get("enabled"):
            user_name = _get_row_user_name(row)
            if user_name:
                current_user_names.add(user_name)

        count += 1

    _cleanup_lease_shares(lease_name, current_user_names)
    return {"status": "success", "processed_rows": count, "lease": lease_name}
