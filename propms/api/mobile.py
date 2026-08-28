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
