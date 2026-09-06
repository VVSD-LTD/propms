"""Compatibility mobile API endpoints."""

import base64
import json
import random
import re
from datetime import timedelta
from hashlib import sha256

import frappe
from frappe.utils import now_datetime
from frappe.utils.password import update_password

from propms.custom.lease import get_customer_from_lease, get_tenant_context_for_user
from propms.api.v1.job_card.job_card import (
    get_mentionable_support_staff as _get_mentionable_support_staff_v1,
)


@frappe.whitelist()
def get_mentionable_support_staff(ticket_id=None):
    """Backward-compatible wrapper for mentionable support staff."""
    return _get_mentionable_support_staff_v1(ticket_id=ticket_id)


def _current_user_email():
    current_user = frappe.session.user
    return frappe.db.get_value("User", current_user, "email") or current_user


def _get_customer_info_safe(customer_id):
    """Fetch customer info without assuming optional columns exist."""
    if not customer_id:
        return None

    info = {"name": customer_id}
    try:
        meta = frappe.get_meta("Customer")
        preferred_fields = [
            "customer_name",
            "customer_abbr",
            "mobile_no",
            "territory",
            "email_id",
            "email",
        ]
        available = [f for f in preferred_fields if meta.has_field(f)]
        if available:
            row = frappe.db.get_value("Customer", customer_id, available, as_dict=True) or {}
            info.update(row)
    except Exception:
        # keep minimal payload when schema differs
        pass
    return info


def _read_doc_fields_safe(doctype, name, preferred_fields):
    """Read only fields that exist on the doctype."""
    out = {}
    try:
        meta = frappe.get_meta(doctype)
        available = [f for f in preferred_fields if meta.has_field(f)]
        if available:
            out = frappe.db.get_value(doctype, name, available, as_dict=True) or {}
    except Exception:
        pass
    return out


def _get_customer_contacts_and_addresses(customer_id):
    """Get Contact/Address records linked to Customer via Dynamic Link."""
    contacts = []
    addresses = []

    if not customer_id:
        return {"contacts": contacts, "addresses": addresses}

    # Contact links
    try:
        contact_names = frappe.get_all(
            "Dynamic Link",
            filters={"link_doctype": "Customer", "link_name": customer_id, "parenttype": "Contact"},
            pluck="parent",
            distinct=True,
        )
        for contact_name in contact_names or []:
            row = _read_doc_fields_safe(
                "Contact",
                contact_name,
                [
                    "name",
                    "first_name",
                    "last_name",
                    "email_id",
                    "mobile_no",
                    "phone",
                    "designation",
                ],
            )
            if row:
                row["name"] = row.get("name") or contact_name
                # Contact email/phone can live in child tables (email_ids/phone_nos),
                # so enrich top-level values from those rows for API consumers.
                try:
                    contact_doc = frappe.get_doc("Contact", contact_name)
                    email_rows = []
                    for e in (getattr(contact_doc, "email_ids", None) or []):
                        val = getattr(e, "email_id", None)
                        if val:
                            email_rows.append(
                                {
                                    "email_id": val,
                                    "is_primary": int(bool(getattr(e, "is_primary", 0))),
                                }
                            )
                    phone_rows = []
                    for p in (getattr(contact_doc, "phone_nos", None) or []):
                        val = getattr(p, "phone", None)
                        if val:
                            phone_rows.append(
                                {
                                    "phone": val,
                                    "is_primary_phone": int(bool(getattr(p, "is_primary_phone", 0))),
                                    "is_primary_mobile_no": int(bool(getattr(p, "is_primary_mobile_no", 0))),
                                }
                            )

                    primary_email = row.get("email_id")
                    if not primary_email and email_rows:
                        primary_email = next(
                            (e.get("email_id") for e in email_rows if e.get("is_primary")),
                            None,
                        ) or email_rows[0].get("email_id")

                    primary_phone = row.get("phone")
                    if not primary_phone and phone_rows:
                        primary_phone = next(
                            (p.get("phone") for p in phone_rows if p.get("is_primary_phone")),
                            None,
                        ) or phone_rows[0].get("phone")

                    primary_mobile = row.get("mobile_no")
                    if not primary_mobile and phone_rows:
                        primary_mobile = next(
                            (p.get("phone") for p in phone_rows if p.get("is_primary_mobile_no")),
                            None,
                        )

                    row["email_id"] = primary_email
                    row["phone"] = primary_phone
                    row["mobile_no"] = primary_mobile
                    row["emails"] = email_rows
                    row["phones"] = phone_rows
                except Exception:
                    pass

                full_name = " ".join(
                    [x for x in [row.get("first_name"), row.get("last_name")] if x]
                ).strip()
                row["full_name"] = full_name or row.get("first_name") or row.get("email_id") or contact_name
                contacts.append(row)
    except Exception:
        pass

    # Address links
    try:
        address_names = frappe.get_all(
            "Dynamic Link",
            filters={"link_doctype": "Customer", "link_name": customer_id, "parenttype": "Address"},
            pluck="parent",
            distinct=True,
        )
        for address_name in address_names or []:
            row = _read_doc_fields_safe(
                "Address",
                address_name,
                [
                    "name",
                    "address_title",
                    "address_type",
                    "address_line1",
                    "address_line2",
                    "city",
                    "state",
                    "country",
                    "pincode",
                    "email_id",
                    "phone",
                    "is_primary_address",
                    "is_shipping_address",
                ],
            )
            if row:
                row["name"] = row.get("name") or address_name
                addresses.append(row)
    except Exception:
        pass

    return {"contacts": contacts, "addresses": addresses}


@frappe.whitelist()
def get_customer_user_info():
    """PropMS-compatible replacement for real_estate_support_system.api.mobile.get_customer_user_info.

    Response keeps legacy top-level keys where possible:
    - status
    - user_type
    - company
    - customers
    - customer_branches
    - summary
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw("Authentication required", frappe.AuthenticationError)

        current_user = frappe.session.user
        user_email = _current_user_email()

        # 1) Tenant-side context (primary "customer info" path in PropMS)
        tenant_ctx = get_tenant_context_for_user(current_user) or {}
        if tenant_ctx.get("customer"):
            customer_id = tenant_ctx.get("customer")
            customer_info = _get_customer_info_safe(customer_id) or {"name": customer_id}
            contact_section = _get_customer_contacts_and_addresses(customer_id)
            customer_info["contact"] = contact_section

            # In PropMS, tenants are tied to Lease records (apartments), not customer branches.
            # Build apartment/lease list for this user email.
            tenant_email = tenant_ctx.get("user_email") or user_email
            lease_names = frappe.get_all(
                "Tenant Details",
                filters={"user_email": tenant_email, "parenttype": "Lease"},
                pluck="parent",
                distinct=True,
            )

            apartments = []
            for lease_name in lease_names or []:
                if not frappe.db.exists("Lease", lease_name):
                    continue
                lease_doc = frappe.get_doc("Lease", lease_name)
                lease_customer = get_customer_from_lease(lease_name)
                # Keep only leases for the same derived customer context.
                if customer_id and lease_customer and lease_customer != customer_id:
                    continue
                property_name = getattr(lease_doc, "property", None)
                apartments.append(
                    {
                        "name": lease_name,
                        "lease": lease_name,
                        "apartment_name": property_name or lease_name,
                        "property": property_name,
                        "company": getattr(lease_doc, "company", None),
                        "lease_start_date": str(getattr(lease_doc, "start_date", None) or "") or None,
                        "lease_end_date": str(getattr(lease_doc, "end_date", None) or "") or None,
                    }
                )

            return {
                "status": "success",
                "user_type": "tenant",
                "tenant": {
                    "user_email": user_email,
                    "tenant_detail": tenant_ctx.get("tenant_detail"),
                    "lease": tenant_ctx.get("lease"),
                },
                "company": apartments[0].get("company") if apartments else None,
                "customers": [customer_info],
                "apartments": apartments,
                "total_apartments": len(apartments),
                "summary": {
                    "total_customers": 1,
                    "total_apartments": len(apartments),
                },
            }

        # 2) Maintenance-side users
        maintenance_user = frappe.db.get_value(
            "Maintenance Users",
            {"enabled": 1, "user_email": user_email},
            ["name", "full_name", "role", "company", "employee", "employee_name"],
            as_dict=True,
        )
        if maintenance_user:
            return {
                "status": "success",
                "user_type": "maintenance_staff",
                "maintenance_user": maintenance_user,
                "company": maintenance_user.get("company"),
                "customers": [],
                "customer_branches": [],
                "summary": {
                    "total_customers": 0,
                    "total_branches": 0,
                    "branches_with_active_amc": 0,
                    "branches_without_amc": 0,
                },
            }

        # 3) Sub-contractor-side users
        sub_contractor_user = frappe.db.get_value(
            "Sub Contractor User",
            {"enabled": 1, "user_email": user_email},
            ["name", "full_name", "sub_contractor", "sub_contractor_name"],
            as_dict=True,
        )
        if sub_contractor_user:
            return {
                "status": "success",
                "user_type": "sub_contractor",
                "sub_contractor_user": sub_contractor_user,
                "company": None,
                "customers": [],
                "customer_branches": [],
                "summary": {
                    "total_customers": 0,
                    "total_branches": 0,
                    "branches_with_active_amc": 0,
                    "branches_without_amc": 0,
                },
            }

        return {
            "status": "error",
            "message": "No tenant/maintenance/sub-contractor record found for this login",
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_customer_user_info")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def set_user_profile_image(file_data=None, filename=None, file_url=None):
    """Set the logged-in user's profile image (compat endpoint for mobile app)."""
    try:
        if frappe.session.user == "Guest":
            frappe.throw("Authentication required", frappe.AuthenticationError)

        final_file_url = None
        file_doc = None

        if file_data and filename:
            content = base64.b64decode(file_data)
            file_doc = frappe.get_doc(
                {
                    "doctype": "File",
                    "file_name": filename,
                    "content": content,
                    "is_private": 0,
                }
            )
            file_doc.attached_to_doctype = "User"
            file_doc.attached_to_name = frappe.session.user
            file_doc.save(ignore_permissions=True)
            final_file_url = file_doc.file_url
        elif file_url:
            name = frappe.db.get_value("File", {"file_url": file_url}, "name")
            if name:
                file_doc = frappe.get_doc("File", name)
                if (
                    file_doc.attached_to_doctype != "User"
                    or file_doc.attached_to_name != frappe.session.user
                ):
                    file_doc.attached_to_doctype = "User"
                    file_doc.attached_to_name = frappe.session.user
                    file_doc.save(ignore_permissions=True)
            final_file_url = file_url
        else:
            return {"status": "error", "message": "Provide either file_data+filename or file_url"}

        if not final_file_url:
            return {"status": "error", "message": "Could not resolve file URL"}

        user = frappe.get_doc("User", frappe.session.user)
        user.user_image = final_file_url
        if hasattr(user, "image"):
            user.image = final_file_url
        user.save(ignore_permissions=True)

        return {"status": "success", "user": frappe.session.user, "file_url": final_file_url}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "set_user_profile_image")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def update_user_image(params):
    """Update a user's profile image using existing file_url (compat endpoint)."""
    try:
        payload = json.loads(params) if isinstance(params, str) else (params or {})
        image_url = payload.get("user_image")
        target_user = payload.get("user") or frappe.session.user

        if not image_url:
            return {"status": "error", "message": "user_image (file_url) is required"}

        # Allow self updates; editing other users requires System Manager
        if target_user != frappe.session.user and ("System Manager" not in frappe.get_roles(frappe.session.user)):
            frappe.throw("Not permitted to update other users' images", frappe.PermissionError)

        current_image_url = frappe.db.get_value("User", target_user, "user_image")

        # If file exists, attach to target user for consistency
        file_name = frappe.db.get_value("File", {"file_url": image_url}, "name")
        if file_name:
            file_doc = frappe.get_doc("File", file_name)
            file_doc.attached_to_doctype = "User"
            file_doc.attached_to_name = target_user
            file_doc.save(ignore_permissions=True)

        doc = frappe.get_doc("User", target_user)
        doc.user_image = image_url
        if hasattr(doc, "image"):
            doc.image = image_url
        doc.save(ignore_permissions=True)

        # Best-effort cleanup of old profile file
        if current_image_url and current_image_url != image_url:
            old_name = frappe.db.get_value("File", {"file_url": current_image_url}, "name")
            if old_name:
                try:
                    frappe.delete_doc("File", old_name, force=True, ignore_permissions=True)
                except Exception:
                    frappe.log_error(frappe.get_traceback(), "update_user_image_delete_old")

        return {"status": "success", "user": target_user, "file_url": image_url}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "update_user_image")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def change_user_password(old_password, new_password):
    """Change the logged-in user's password (compat endpoint for mobile app)."""
    try:
        if frappe.session.user == "Guest":
            frappe.throw("Authentication required", frappe.AuthenticationError)

        if not old_password or not new_password:
            return {"status": "error", "message": "Both old_password and new_password are required"}

        user = frappe.get_doc("User", frappe.session.user)

        if not frappe.utils.password.check_password(user.name, old_password):
            return {"status": "error", "message": "Current password is incorrect"}

        if len(new_password) < 8:
            return {"status": "error", "message": "Password must be at least 8 characters long."}

        has_letter = any(c.isalpha() for c in new_password)
        has_digit = any(c.isdigit() for c in new_password)
        if not has_letter or not has_digit:
            return {
                "status": "error",
                "message": "Password must contain both letters and numbers.",
            }

        user.new_password = new_password
        user.save(ignore_permissions=True)

        frappe.logger().info(f"Password changed for user: {frappe.session.user}")
        return {"status": "success", "message": "Password updated successfully"}
    except frappe.AuthenticationError:
        return {"status": "error", "message": "Authentication failed"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "change_user_password")
        return {"status": "error", "message": str(e)}


def _parse_request_payload(defaults=None):
    data = dict(defaults or {})
    try:
        form = getattr(frappe, "form_dict", None) or getattr(frappe.local, "form_dict", None) or {}
        if isinstance(form, dict):
            data.update({k: v for k, v in form.items() if v is not None})
    except Exception:
        pass

    try:
        req = getattr(frappe, "request", None) or getattr(frappe.local, "request", None)
        if req and callable(getattr(req, "get_json", None)):
            body = req.get_json(silent=True)
            if isinstance(body, dict):
                data.update({k: v for k, v in body.items() if v is not None})
    except Exception:
        pass
    return data


def _ensure_password_reset_otp_doctype():
    if not frappe.db.exists("DocType", "Password Reset OTP"):
        raise Exception(
            "Missing DocType 'Password Reset OTP'. Please create/migrate this DocType first."
        )


def _email_is_valid(email):
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email or ""))


def _password_is_strong(new_password):
    if not new_password or len(new_password) < 8:
        return False, "Password must be at least 8 characters"
    has_letter = any(c.isalpha() for c in new_password)
    has_digit = any(c.isdigit() for c in new_password)
    if not has_letter or not has_digit:
        return False, "Password must contain both letters and numbers"
    return True, ""


@frappe.whitelist(allow_guest=True)
def request_password_reset_otp(email=None):
    """Request OTP for password reset (guest-safe, enumeration-safe)."""
    try:
        _ensure_password_reset_otp_doctype()
        payload = _parse_request_payload({"email": email})
        email = (payload.get("email") or "").strip().lower()
        if not _email_is_valid(email):
            return {"status": "error", "message": "Invalid email format"}

        user_exists = frappe.db.exists("User", email)
        if not user_exists:
            # Enumeration-safe response
            return {"status": "success", "message": "If the email exists, an OTP has been sent"}

        enabled = frappe.db.get_value("User", email, "enabled")
        if not enabled:
            return {"status": "error", "message": "User account is disabled"}

        one_hour_ago = now_datetime() - timedelta(hours=1)
        recent_count = frappe.db.count(
            "Password Reset OTP",
            {"email": email, "creation": [">", one_hour_ago]},
        )
        if recent_count >= 5:
            return {
                "status": "error",
                "message": "Too many OTP requests. Please wait 1 hour before requesting another.",
            }

        otp = f"{random.randint(0, 999999):06d}"
        otp_hash = sha256(otp.encode()).hexdigest()
        expires_at = now_datetime() + timedelta(minutes=10)
        ip_addr = (
            frappe.local.request.environ.get("REMOTE_ADDR", "unknown")
            if getattr(frappe.local, "request", None)
            else "unknown"
        )
        user_agent = (
            frappe.local.request.environ.get("HTTP_USER_AGENT", "unknown")
            if getattr(frappe.local, "request", None)
            else "unknown"
        )

        frappe.get_doc(
            {
                "doctype": "Password Reset OTP",
                "email": email,
                "otp_hash": otp_hash,
                "expires_at": expires_at,
                "verified": 0,
                "failed_attempts": 0,
                "ip_address": ip_addr,
                "user_agent": user_agent,
            }
        ).insert(ignore_permissions=True)

        frappe.sendmail(
            recipients=[email],
            subject="Password Reset OTP - Viva Towers",
            message=f"""
                <p>Your OTP for password reset is:</p>
                <h2>{otp}</h2>
                <p>This OTP expires in 10 minutes.</p>
                <p>If you did not request this, you can ignore this email.</p>
            """,
            now=True,
        )
        return {"status": "success", "message": "OTP sent to your email", "expires_in_minutes": 10}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "request_password_reset_otp")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True)
def verify_otp(email=None, otp=None):
    """Verify OTP only (for 3-screen flow)."""
    try:
        _ensure_password_reset_otp_doctype()
        payload = _parse_request_payload({"email": email, "otp": otp})
        email = (payload.get("email") or "").strip().lower()
        otp = (payload.get("otp") or "").strip()

        if not _email_is_valid(email) or not otp or not otp.isdigit() or len(otp) != 6:
            return {"status": "error", "message": "Invalid OTP format"}

        now_ts = now_datetime()
        rows = frappe.get_all(
            "Password Reset OTP",
            filters={"email": email, "verified": 0},
            fields=["name", "otp_hash", "expires_at", "failed_attempts"],
            order_by="creation desc",
            limit=1,
        )
        if not rows:
            return {"status": "error", "message": "Invalid or expired OTP"}

        row = rows[0]
        if row.expires_at and row.expires_at < now_ts:
            return {"status": "error", "message": "Invalid or expired OTP"}
        if (row.failed_attempts or 0) >= 5:
            return {"status": "error", "message": "Too many failed attempts. Please request a new OTP."}

        provided_hash = sha256(otp.encode()).hexdigest()
        if provided_hash != row.otp_hash:
            frappe.db.set_value(
                "Password Reset OTP",
                row.name,
                "failed_attempts",
                (row.failed_attempts or 0) + 1,
                update_modified=True,
            )
            return {"status": "error", "message": "Invalid or expired OTP"}

        frappe.db.set_value("Password Reset OTP", row.name, "verified", 1, update_modified=True)
        frappe.db.set_value("Password Reset OTP", row.name, "verified_at", now_ts, update_modified=True)
        return {"status": "success", "message": "OTP verified successfully", "verified": True}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "verify_otp")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True)
def reset_password_after_otp(email=None, new_password=None):
    """Reset password after successful OTP verification (3-screen flow)."""
    try:
        _ensure_password_reset_otp_doctype()
        payload = _parse_request_payload({"email": email, "new_password": new_password})
        email = (payload.get("email") or "").strip().lower()
        new_password = payload.get("new_password") or ""

        strong, msg = _password_is_strong(new_password)
        if not strong:
            return {"status": "error", "message": msg}
        if not _email_is_valid(email):
            return {"status": "error", "message": "Invalid email format"}

        rows = frappe.get_all(
            "Password Reset OTP",
            filters={"email": email, "verified": 1},
            fields=["name", "verified_at"],
            order_by="creation desc",
            limit=1,
        )
        if not rows:
            return {
                "status": "error",
                "message": "OTP not verified or verification expired. Please verify OTP first.",
            }

        latest = rows[0]
        if not latest.verified_at or latest.verified_at < (now_datetime() - timedelta(minutes=15)):
            return {
                "status": "error",
                "message": "OTP not verified or verification expired. Please verify OTP first.",
            }

        if not frappe.db.exists("User", email):
            return {"status": "error", "message": "User not found"}

        update_password(user=email, pwd=new_password)
        # Best effort cleanup of OTP rows for this email
        otp_names = frappe.get_all("Password Reset OTP", filters={"email": email}, pluck="name")
        for name in otp_names or []:
            frappe.delete_doc("Password Reset OTP", name, ignore_permissions=True, force=True)
        return {"status": "success", "message": "Password reset successfully"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "reset_password_after_otp")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True)
def verify_otp_and_reset_password(email=None, otp=None, new_password=None):
    """Combined 2-screen flow: verify OTP and reset password in one call."""
    verify_result = verify_otp(email=email, otp=otp)
    if verify_result.get("status") != "success":
        return verify_result
    return reset_password_after_otp(email=email, new_password=new_password)


@frappe.whitelist()
def cleanup_expired_otps():
    """Cleanup expired and old OTP rows."""
    try:
        _ensure_password_reset_otp_doctype()
        now_ts = now_datetime()
        deleted_expired = 0
        deleted_verified_old = 0

        expired = frappe.get_all(
            "Password Reset OTP",
            filters={"expires_at": ["<", now_ts]},
            pluck="name",
        )
        for name in expired or []:
            frappe.delete_doc("Password Reset OTP", name, ignore_permissions=True, force=True)
            deleted_expired += 1

        verified_old_cutoff = now_ts - timedelta(days=1)
        verified_old = frappe.get_all(
            "Password Reset OTP",
            filters={"verified": 1, "creation": ["<", verified_old_cutoff]},
            pluck="name",
        )
        for name in verified_old or []:
            frappe.delete_doc("Password Reset OTP", name, ignore_permissions=True, force=True)
            deleted_verified_old += 1

        return {
            "status": "success",
            "message": f"Cleaned up {deleted_expired + deleted_verified_old} OTPs ({deleted_expired} expired, {deleted_verified_old} old verified)",
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "cleanup_expired_otps")
        return {"status": "error", "message": str(e)}


# -------------------------------------------------------------------------
# Invoice API Wrappers (v1)
# -------------------------------------------------------------------------
@frappe.whitelist(methods=["GET", "POST"])
def get_tenant_invoices(status="all", lease=None, page=1, page_length=20):
    from propms.api.v1.invoices import invoices as v1_invoices
    return v1_invoices.get_tenant_invoices(status=status, lease=lease, page=page, page_length=page_length)


@frappe.whitelist(methods=["GET", "POST"])
def get_invoice_details(invoice_name=None):
    from propms.api.v1.invoices import invoices as v1_invoices
    return v1_invoices.get_invoice_details(invoice_name=invoice_name)


@frappe.whitelist(methods=["GET"])
def download_invoice_pdf(invoice_name=None):
    from propms.api.v1.invoices import invoices as v1_invoices
    return v1_invoices.download_invoice_pdf(invoice_name=invoice_name)


@frappe.whitelist(methods=["POST"])
def initiate_invoice_payment(invoice_name=None, amount=None, phone_number=None, payment_channel="SELCOM_PUSH"):
    from propms.api.v1.invoices import invoices as v1_invoices
    return v1_invoices.initiate_invoice_payment(
        invoice_name=invoice_name,
        amount=amount,
        phone_number=phone_number,
        payment_channel=payment_channel,
    )


# -------------------------------------------------------------------------
# Visitor Gate Pass API Wrappers (v1)
# -------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def create_visitor_pass(
    visitor_name=None,
    phone_number=None,
    visitor_type="Guest",
    expected_arrival_date=None,
    expected_arrival_time=None,
    vehicle_plate=None,
    validity_type="One-Time Entry",
    lease=None,
    property_unit=None,
    notes=None,
):
    from propms.api.v1.gate_pass import gate_pass as v1_gate_pass
    return v1_gate_pass.create_visitor_pass(
        visitor_name=visitor_name,
        phone_number=phone_number,
        visitor_type=visitor_type,
        expected_arrival_date=expected_arrival_date,
        expected_arrival_time=expected_arrival_time,
        vehicle_plate=vehicle_plate,
        validity_type=validity_type,
        lease=lease,
        property_unit=property_unit,
        notes=notes,
    )


@frappe.whitelist(methods=["GET", "POST"])
def get_tenant_visitor_passes(status="all", lease=None, page=1, page_length=20):
    from propms.api.v1.gate_pass import gate_pass as v1_gate_pass
    return v1_gate_pass.get_tenant_visitor_passes(
        status=status,
        lease=lease,
        page=page,
        page_length=page_length,
    )


@frappe.whitelist(methods=["GET", "POST"])
def get_visitor_pass_details(pass_id=None):
    from propms.api.v1.gate_pass import gate_pass as v1_gate_pass
    return v1_gate_pass.get_visitor_pass_details(pass_id=pass_id)


@frappe.whitelist(methods=["POST"])
def cancel_visitor_pass(pass_id=None):
    from propms.api.v1.gate_pass import gate_pass as v1_gate_pass
    return v1_gate_pass.cancel_visitor_pass(pass_id=pass_id)


@frappe.whitelist(methods=["POST"])
def validate_and_checkin_visitor(pass_id=None, notes=None):
    from propms.api.v1.gate_pass import gate_pass as v1_gate_pass
    return v1_gate_pass.validate_and_checkin_visitor(pass_id=pass_id, notes=notes)


@frappe.whitelist(methods=["POST"])
def checkout_visitor(pass_id=None):
    from propms.api.v1.gate_pass import gate_pass as v1_gate_pass
    return v1_gate_pass.checkout_visitor(pass_id=pass_id)


@frappe.whitelist(methods=["GET", "POST"])
def get_security_gate_passes(date=None, status=None, search=None, page=1, page_length=50):
    from propms.api.v1.gate_pass import gate_pass as v1_gate_pass
    return v1_gate_pass.get_security_gate_passes(
        date=date,
        status=status,
        search=search,
        page=page,
        page_length=page_length,
    )


@frappe.whitelist(methods=["GET", "POST"])
def get_tenant_apartments():
    from propms.api.v1.gate_pass import gate_pass as v1_gate_pass
    return v1_gate_pass.get_tenant_apartments()


# -------------------------------------------------------------------------
# Viva Amenities & Booking API Wrappers (v1)
# -------------------------------------------------------------------------
@frappe.whitelist(methods=["GET", "POST"])
def get_amenities(category=None):
    from propms.api.v1.amenities import get_amenities as v1_get_amenities
    return v1_get_amenities(category=category)


@frappe.whitelist(methods=["GET", "POST"])
def get_amenity_detail(amenity=None):
    from propms.api.v1.amenities import get_amenity_detail as v1_get_amenity_detail
    return v1_get_amenity_detail(amenity=amenity)


@frappe.whitelist(methods=["GET", "POST"])
def get_available_slots(amenity=None, booking_date=None):
    from propms.api.v1.amenities import get_available_slots as v1_get_available_slots
    return v1_get_available_slots(amenity=amenity, booking_date=booking_date)


@frappe.whitelist(methods=["POST"])
def create_amenity_booking(
    amenity=None,
    booking_date=None,
    start_time=None,
    end_time=None,
    guests_count=1,
    notes=None,
    lease=None,
    property_unit=None,
):
    from propms.api.v1.amenities import create_booking as v1_create_booking
    return v1_create_booking(
        amenity=amenity,
        booking_date=booking_date,
        start_time=start_time,
        end_time=end_time,
        guests_count=guests_count,
        notes=notes,
        lease=lease,
        property_unit=property_unit,
    )


@frappe.whitelist(methods=["GET", "POST"])
def get_my_amenity_bookings(status="all", page=1, page_length=20):
    from propms.api.v1.amenities import get_my_bookings as v1_get_my_bookings
    return v1_get_my_bookings(status=status, page=page, page_length=page_length)


@frappe.whitelist(methods=["POST"])
def cancel_amenity_booking(booking_id=None, cancellation_reason=None):
    from propms.api.v1.amenities import cancel_booking as v1_cancel_booking
    return v1_cancel_booking(booking_id=booking_id, cancellation_reason=cancellation_reason)


# -------------------------------------------------------------------------
# Viva Emergency Incidents API Wrappers (v1)
# -------------------------------------------------------------------------
@frappe.whitelist(methods=["POST"])
def report_emergency(incident_type=None, property_unit=None, location_details=None, details=None):
    from propms.api.v1.emergency import report_emergency as v1_report_emergency
    return v1_report_emergency(
        incident_type=incident_type,
        property_unit=property_unit,
        location_details=location_details,
        details=details,
    )


@frappe.whitelist(methods=["POST"])
def update_emergency_status(incident_id=None, status=None, resolution_notes=None):
    from propms.api.v1.emergency import update_incident_status as v1_update_incident_status
    return v1_update_incident_status(
        incident_id=incident_id,
        status=status,
        resolution_notes=resolution_notes,
    )


@frappe.whitelist(methods=["GET", "POST"])
def get_emergency_incidents(status="all", page=1, page_length=20):
    from propms.api.v1.emergency import get_emergency_incidents as v1_get_emergency_incidents
    return v1_get_emergency_incidents(status=status, page=page, page_length=page_length)


# -------------------------------------------------------------------------
# Viva Building Directory API Wrappers (v1)
# -------------------------------------------------------------------------
@frappe.whitelist(methods=["GET", "POST"])
def get_directory_contacts(department=None):
    from propms.api.v1.directory import get_directory_contacts as v1_get_directory_contacts
    return v1_get_directory_contacts(department=department)


# -------------------------------------------------------------------------
# Viva Voice & Video Calling API Wrappers (v1)
# -------------------------------------------------------------------------
@frappe.whitelist(methods=["GET", "POST"])
def get_call_config():
    from propms.api.v1.calls import get_livekit_config as v1_get_livekit_config
    cfg = v1_get_livekit_config()
    return {
        "status": "success",
        "enabled": cfg.get("enabled", True),
        "server_url": cfg.get("url", ""),
    }


@frappe.whitelist(methods=["POST"])
def initiate_call(receiver=None, call_type="Voice"):
    from propms.api.v1.calls import initiate_call as v1_initiate_call
    return v1_initiate_call(receiver=receiver, call_type=call_type)


@frappe.whitelist(methods=["POST"])
def answer_call(call_id=None):
    from propms.api.v1.calls import answer_call as v1_answer_call
    return v1_answer_call(call_id=call_id)


@frappe.whitelist(methods=["POST"])
def end_call(call_id=None, reason="ended"):
    from propms.api.v1.calls import end_call as v1_end_call
    return v1_end_call(call_id=call_id, reason=reason)


@frappe.whitelist(methods=["GET", "POST"])
def get_call_history(status="all", page=1, page_length=20):
    from propms.api.v1.calls import get_call_history as v1_get_call_history
    return v1_get_call_history(status=status, page=page, page_length=page_length)


# Chunked File Upload Endpoints
@frappe.whitelist()
def start_upload_session(filename=None, total_chunks=1, total_size=0, file_type=None, doctype=None, docname=None, fieldname=None, is_private=0):
    from propms.api.v1.files.chunked_upload import start_upload_session as v1_start_upload_session
    return v1_start_upload_session(
        filename=filename,
        total_chunks=total_chunks,
        total_size=total_size,
        file_type=file_type,
        doctype=doctype,
        docname=docname,
        fieldname=fieldname,
        is_private=is_private,
    )


@frappe.whitelist()
def upload_chunk(session_id=None, chunk_index=None):
    from propms.api.v1.files.chunked_upload import upload_chunk as v1_upload_chunk
    return v1_upload_chunk(session_id=session_id, chunk_index=chunk_index)


@frappe.whitelist()
def finalize_upload(session_id=None, doctype=None, docname=None, fieldname=None, is_private=None):
    from propms.api.v1.files.chunked_upload import finalize_upload as v1_finalize_upload
    return v1_finalize_upload(
        session_id=session_id,
        doctype=doctype,
        docname=docname,
        fieldname=fieldname,
        is_private=is_private,
    )


@frappe.whitelist()
def get_upload_session_status(session_id=None):
    from propms.api.v1.files.chunked_upload import get_upload_session_status as v1_get_upload_session_status
    return v1_get_upload_session_status(session_id=session_id)


@frappe.whitelist()
def abort_upload_session(session_id=None):
    from propms.api.v1.files.chunked_upload import abort_upload_session as v1_abort_upload_session
    return v1_abort_upload_session(session_id=session_id)


@frappe.whitelist()
def upload_attachment(file_data=None, filename=None, ticket_id=None):
    """Upload a single file (base64) matching vsd_helpdesk mobile API."""
    try:
        import base64
        req = getattr(frappe, "form_dict", None) or {}
        file_data = file_data or req.get("file_data")
        filename = filename or req.get("filename") or "upload.bin"
        ticket_id = ticket_id or req.get("ticket_id")

        if not file_data:
            return {"status": "error", "message": "file_data is required"}

        file_content = base64.b64decode(file_data)
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": filename,
            "content": file_content,
            "is_private": 0,
        })
        if ticket_id:
            file_doc.attached_to_doctype = "Viva Job Card" if frappe.db.exists("DocType", "Viva Job Card") else "Job Card"
            file_doc.attached_to_name = ticket_id
        file_doc.insert(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status": "success",
            "file_url": file_doc.file_url,
            "file_name": file_doc.name,
            "name": file_doc.name,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "upload_attachment")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def upload_mobile_image(file_data=None, filename=None, ticket_id=None):
    return upload_attachment(file_data=file_data, filename=filename, ticket_id=ticket_id)


# Ticket Status Lifecycle Endpoints
@frappe.whitelist(methods=["GET", "POST"])
def put_ticket_on_hold(ticket_id=None, hold_reason=None):
    from propms.api.v1.job_card.job_card import put_ticket_on_hold as v1_put_ticket_on_hold
    return v1_put_ticket_on_hold(ticket_id=ticket_id, hold_reason=hold_reason)


@frappe.whitelist(methods=["GET", "POST"])
def resume_ticket_from_hold(ticket_id=None):
    from propms.api.v1.job_card.job_card import resume_ticket_from_hold as v1_resume_ticket_from_hold
    return v1_resume_ticket_from_hold(ticket_id=ticket_id)


@frappe.whitelist(methods=["GET", "POST"])
def resolve_ticket(ticket_id=None, defect_found=None, resolution_details=None):
    from propms.api.v1.job_card.job_card import resolve_ticket as v1_resolve_ticket
    return v1_resolve_ticket(
        ticket_id=ticket_id,
        defect_found=defect_found,
        resolution_details=resolution_details,
    )


@frappe.whitelist(methods=["GET", "POST"])
def close_ticket_with_feedback(ticket_id=None, rating=None, customer_feedback=None):
    from propms.api.v1.job_card.job_card import close_ticket_with_feedback as v1_close_ticket_with_feedback
    return v1_close_ticket_with_feedback(
        ticket_id=ticket_id,
        rating=rating,
        customer_feedback=customer_feedback,
    )


@frappe.whitelist(methods=["GET", "POST"])
def change_ticket_status(ticket_id=None, status=None, reason=None, defect_found=None, resolution_details=None):
    from propms.api.v1.job_card.job_card import change_ticket_status as v1_change_ticket_status
    return v1_change_ticket_status(
        ticket_id=ticket_id,
        status=status,
        reason=reason,
        defect_found=defect_found,
        resolution_details=resolution_details,
    )


# Selcom Payment Endpoints
@frappe.whitelist(methods=["POST"])
def initiate_payment(invoice_name=None, amount=None, payment_method="MOBILE_MONEY", phone_number=None):
    from propms.api.v1.payments.services import initiate_payment as v1_initiate_payment
    return v1_initiate_payment(
        invoice_name=invoice_name,
        amount=amount,
        payment_method=payment_method,
        phone_number=phone_number,
    )


@frappe.whitelist(methods=["GET", "POST"])
def get_payment_status(order_id=None, transaction_id=None):
    from propms.api.v1.payments.services import get_payment_status as v1_get_payment_status
    return v1_get_payment_status(order_id=order_id, transaction_id=transaction_id)


@frappe.whitelist(methods=["POST"])
def cancel_payment(order_id=None):
    from propms.api.v1.payments.services import cancel_payment as v1_cancel_payment
    return v1_cancel_payment(order_id=order_id)


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_payment_methods():
    from propms.api.v1.payments.services import get_payment_methods as v1_get_payment_methods
    return v1_get_payment_methods()


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def selcom_ipn_webhook(*args, **kwargs):
    from propms.api.v1.payments.webhook import selcom_ipn_webhook as v1_selcom_ipn_webhook
    return v1_selcom_ipn_webhook(*args, **kwargs)


# Job Card / Ticket Communication & Management Endpoints (100% Helpdesk Parity)
@frappe.whitelist(methods=["GET", "POST"])
def send_ticket_communication(*args, **kwargs):
    from propms.api.v1.job_card.job_card import send_ticket_communication as v1_send_comm
    return v1_send_comm(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def edit_ticket_communication(*args, **kwargs):
    from propms.api.v1.job_card.job_card import edit_ticket_communication as v1_edit_comm
    return v1_edit_comm(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def mark_communications_as_read(*args, **kwargs):
    from propms.api.v1.job_card.job_card import mark_communications_as_read as v1_mark_read
    return v1_mark_read(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def send_typing_indicator(*args, **kwargs):
    from propms.api.v1.job_card.job_card import send_typing_indicator as v1_send_typing
    return v1_send_typing(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_ticket_communications(*args, **kwargs):
    from propms.api.v1.job_card.job_card import get_ticket_communications as v1_get_comms
    return v1_get_comms(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_user_tickets(*args, **kwargs):
    from propms.api.v1.job_card.job_card import get_user_tickets as v1_get_user_tickets
    return v1_get_user_tickets(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_support_staff_list(*args, **kwargs):
    from propms.api.v1.job_card.job_card import get_support_staff_list as v1_get_staff
    return v1_get_staff(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_subcontractor_list(*args, **kwargs):
    from propms.api.v1.job_card.job_card import get_subcontractor_list as v1_get_sub
    return v1_get_sub(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_ticket_priorities(*args, **kwargs):
    from propms.api.v1.job_card.job_card import get_ticket_priorities as v1_get_priorities
    return v1_get_priorities(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def get_issue_types(*args, **kwargs):
    from propms.api.v1.job_card.job_card import get_issue_types as v1_get_issue_types
    return v1_get_issue_types(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def change_ticket_priority(*args, **kwargs):
    from propms.api.v1.job_card.job_card import change_ticket_priority as v1_change_priority
    return v1_change_priority(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def assign_ticket_to_staff(*args, **kwargs):
    from propms.api.v1.job_card.job_card import assign_ticket_to_staff as v1_assign_staff
    return v1_assign_staff(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def assign_subcontractor(*args, **kwargs):
    from propms.api.v1.job_card.job_card import assign_subcontractor as v1_assign_sub
    return v1_assign_sub(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def create_ticket(*args, **kwargs):
    from propms.api.v1.job_card.job_card import create_ticket as v1_create_ticket
    return v1_create_ticket(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def initialize_app_websocket(*args, **kwargs):
    from propms.api.v1.job_card.job_card import initialize_app_websocket as v1_init_ws
    return v1_init_ws(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def subscribe_to_ticket_room(*args, **kwargs):
    from propms.api.v1.job_card.job_card import subscribe_to_ticket_room as v1_sub_room
    return v1_sub_room(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def unsubscribe_from_ticket_room(*args, **kwargs):
    from propms.api.v1.job_card.job_card import unsubscribe_from_ticket_room as v1_unsub_room
    return v1_unsub_room(*args, **kwargs)


# Push Notification Wrappers
@frappe.whitelist(methods=["GET", "POST"])
def enqueue_ticket_message_push(*args, **kwargs):
    from propms.api.v1.notifications.notifications import enqueue_ticket_message_push as v1_push
    return v1_push(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def enqueue_ticket_assigned_push(*args, **kwargs):
    from propms.api.v1.notifications.notifications import enqueue_ticket_assigned_push as v1_assign_push
    return v1_assign_push(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def enqueue_ticket_status_push(*args, **kwargs):
    from propms.api.v1.notifications.notifications import enqueue_ticket_status_push as v1_status_push
    return v1_status_push(*args, **kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def enqueue_ticket_created_push(*args, **kwargs):
    from propms.api.v1.notifications.notifications import enqueue_ticket_created_push as v1_created_push
    return v1_created_push(*args, **kwargs)












