import json
import frappe
from frappe import _
from frappe.utils import now, get_datetime
import base64
import os
import shutil
import hashlib
import urllib.parse
from propms.property_management_solution.doctype.lease.lease import (
    get_tenant_context_for_user,
)


USER_TYPE_ROLES = (
    "Mobile Maintenance Manager",
    "Mobile Maintenance Officer",
    "Mobile Technician",
    "Mobile VIVA Tenant",
    "Mobile Sub Contractor",
)

MOBILE_MAINTENANCE_ROLES = (
    "Mobile Maintenance Manager",
    "Mobile Maintenance Officer",
    "Mobile Technician",
)

# Officer / Manager: full visibility, assignment, and cross-party chat (not tenant↔tech direct).
MOBILE_OFFICER_MANAGER_ROLES = (
    "Mobile Maintenance Manager",
    "Mobile Maintenance Officer",
)

# Communication channels on Issue.custom_support_communication.channel
# - tenant_support: Tenant ↔ Officer/Manager only (technician/subcontractor excluded)
# - technician_support: Officer/Manager ↔ Technician + Subcontractor (sender_type distinguishes who sent)
VALID_TICKET_CHANNELS = (
    "tenant_support",
    "technician_support",
)

# In-process cache for typing indicator user display info (cleared on worker restart)
_TYPING_USER_CACHE = {}

def _support_comm_sender_type_for_role(user_type):
    """Map session mobile role to Support Communication.sender_type (support_communication.json)."""
    if user_type == "Mobile VIVA Tenant":
        return "tenant"
    if user_type == "Mobile Sub Contractor":
        return "subcontractor"
    if user_type == "Mobile Technician":
        return "technician"
    return "maintenance"


def _get_user_type(roles):
    for role in USER_TYPE_ROLES:
        if role in roles:
            return role
    return None


def _get_logged_in_subcontractor_supplier(user=None):
    """Resolve Supplier for logged-in Sub Contractor user.

    Uses Sub Contractor User doctype by matching user_email to the current user's email.
    """
    current = user or frappe.session.user
    if not current or current == "Guest":
        return None

    user_email = frappe.db.get_value("User", current, "email") or current
    if not user_email:
        return None

    return frappe.db.get_value("Sub Contractor User", {"user_email": user_email, "enabled": 1}, "sub_contractor")


def _get_subcontractor_recipients_for_supplier(supplier):
    """Return set of user identifiers (emails) for subcontractor users under a Supplier."""
    recipients = set()
    if not supplier:
        return recipients
    try:
        rows = frappe.get_all(
            "Sub Contractor User",
            filters={"sub_contractor": supplier, "enabled": 1},
            fields=["user_email", "user"],
        )
        for r in rows or []:
            if r.get("user_email"):
                recipients.add(r["user_email"])
            if r.get("user"):
                recipients.add(r["user"])
    except Exception:
        pass
    return recipients


def _get_issue_subcontractor_supplier(issue):
    """Return the Supplier linked on an Issue for subcontractor assignment."""
    if not issue:
        return None
    return getattr(issue, "sub_contractor", None)


def _get_logged_in_maintenance_employee(user=None):
    """Resolve Employee for the logged-in Maintenance Users row (Officer, Manager, or Technician)."""
    current = user or frappe.session.user
    if not current or current == "Guest":
        return None
    user_email = frappe.db.get_value("User", current, "email") or current
    if not user_email:
        return None
    return frappe.db.get_value(
        "Maintenance Users",
        {"user_email": user_email, "enabled": 1},
        "employee",
    )


def _issue_assigned_technician_employee(issue):
    """Employee id for the technician assigned to this Issue (Issue.person_in_charge)."""
    if not issue:
        return None
    return getattr(issue, "person_in_charge", None) or None


def _get_officer_manager_recipients():
    """User identifiers for Mobile Maintenance Officer/Manager app users (Maintenance Users.role)."""
    recipients = set()
    try:
        rows = frappe.get_all(
            "Maintenance Users",
            filters={
                "enabled": 1,
                "role": ["in", ["Mobile Maintenance Officer", "Mobile Maintenance Manager"]],
            },
            fields=["user_email", "user"],
        )
        for r in rows or []:
            if r.get("user_email"):
                recipients.add(r["user_email"])
            if r.get("user"):
                recipients.add(r["user"])
    except Exception:
        pass
    return recipients


def _get_technician_user_recipients_for_employee(employee_id):
    """User emails/ids for Maintenance Users rows linked to this Employee (Mobile Technician)."""
    recipients = set()
    if not employee_id:
        return recipients
    try:
        rows = frappe.get_all(
            "Maintenance Users",
            filters={"employee": employee_id, "enabled": 1},
            fields=["user_email", "user"],
        )
        for r in rows or []:
            if r.get("user_email"):
                recipients.add(r["user_email"])
            if r.get("user"):
                recipients.add(r["user"])
    except Exception:
        pass
    return recipients


def _to_user_email(user_identifier):
    """Resolve a user identifier (User.name or email) to canonical email."""
    if not user_identifier:
        return None
    ident = str(user_identifier).strip()
    if not ident:
        return None
    try:
        if frappe.db.exists("User", ident):
            return frappe.db.get_value("User", ident, "email") or ident
        email = frappe.db.get_value("User", {"email": ident}, "email")
        return email or None
    except Exception:
        return None


def _get_maintenance_mentionable_users(issue, exclude_user=None):
    """Maintenance mentionables for an Issue (officer/manager, assigned technician, subcontractor)."""
    recipients = set()
    recipients |= _get_officer_manager_recipients()
    tech_emp = _issue_assigned_technician_employee(issue)
    if tech_emp:
        recipients |= _get_technician_user_recipients_for_employee(tech_emp)
    supplier = _get_issue_subcontractor_supplier(issue)
    if supplier:
        recipients |= _get_subcontractor_recipients_for_supplier(supplier)

    exclude_email = _to_user_email(exclude_user) if exclude_user else None
    users_by_email = {}
    for recipient in recipients:
        email = _to_user_email(recipient)
        if not email:
            continue
        if exclude_email and email == exclude_email:
            continue
        info = frappe.db.get_value("User", {"email": email}, ["full_name", "user_image"], as_dict=True) or {}
        users_by_email[email] = {
            "email": email,
            "full_name": info.get("full_name") or email,
            "user_image": info.get("user_image"),
        }

    return [users_by_email[k] for k in sorted(users_by_email.keys())]


def _parse_mentioned_emails_input(mentioned_emails):
    """Safely parse mention payload from list/tuple or JSON string."""
    if mentioned_emails is None:
        return set()
    parsed = mentioned_emails
    if isinstance(parsed, str):
        if not parsed.strip():
            return set()
        try:
            parsed = json.loads(parsed)
        except Exception:
            return set()
    if not isinstance(parsed, (list, tuple)):
        return set()
    out = set()
    for item in parsed:
        if item is None:
            continue
        value = str(item).strip()
        if value:
            out.add(value)
    return out


def _normalize_mentioned_emails_for_issue(issue, mentioned_emails, exclude_user=None):
    """Normalize mention inputs and keep only allowed maintenance users for this issue."""
    requested = _parse_mentioned_emails_input(mentioned_emails)
    if not requested:
        return set()

    allowed_users = _get_maintenance_mentionable_users(issue, exclude_user=exclude_user)
    allowed_lookup = {}
    for user in allowed_users:
        email = (user.get("email") or "").strip()
        if email:
            allowed_lookup[email.lower()] = email

    normalized = set()
    for item in requested:
        item_email = _to_user_email(item) or item
        key = item_email.lower()
        if key in allowed_lookup:
            normalized.add(allowed_lookup[key])
    return normalized


def _normalize_recipient_emails(recipients):
    """Convert recipient identifiers to canonical user emails."""
    out = set()
    for recipient in recipients or set():
        email = _to_user_email(recipient)
        if email:
            out.add(email)
    return out


def _require_officer_or_manager():
    """Enforce Officer/Manager (or System Manager) — for assignment and cross-role coordination APIs."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    roles = frappe.get_roles(frappe.session.user)
    user_type = _get_user_type(roles)

    if user_type in MOBILE_OFFICER_MANAGER_ROLES or "System Manager" in roles:
        return user_type or "System Manager"

    frappe.throw(_("Not permitted"), frappe.PermissionError)


def _infer_mobile_user_type_from_doctypes(user=None):
    """When Frappe roles are missing, infer Sub Contractor / Tenant / Maintenance from linked doctypes."""
    try:
        if _get_logged_in_subcontractor_supplier(user):
            return "Mobile Sub Contractor"
        if get_tenant_context_for_user(user):
            return "Mobile VIVA Tenant"
        uid = user or frappe.session.user
        uem = frappe.db.get_value("User", uid, "email") or uid
        mu = frappe.db.get_value(
            "Maintenance Users",
            {"user_email": uem, "enabled": 1},
            ["role"],
            as_dict=True,
        )
        if mu and mu.get("role") == "Mobile Technician":
            return "Mobile Technician"
        if mu and mu.get("role") in ("Mobile Maintenance Officer", "Mobile Maintenance Manager"):
            return mu.get("role")
    except Exception:
        pass
    return None


def _parse_request_payload(defaults=None):
    """Best-effort payload parser (form_dict, JSON body, or x-www-form-urlencoded)."""
    defaults = defaults or {}
    form = getattr(frappe, "form_dict", None) or getattr(frappe.local, "form_dict", None) or {}

    def _unwrap(payload):
        if not isinstance(payload, dict):
            return payload
        for key in ("data", "args", "message"):
            if key in payload and payload[key] is not None:
                return payload[key]
        return payload

    payload = _unwrap(form)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    if not isinstance(payload, dict) or not payload:
        try:
            req = getattr(frappe, "request", None) or getattr(frappe.local, "request", None)
            body = None
            raw = None
            if req is not None and callable(getattr(req, "get_json", None)):
                body = req.get_json(silent=True)
            if body is None and req is not None and callable(getattr(req, "get_data", None)):
                raw = req.get_data(as_text=True, cache=False)
                if raw and raw.strip():
                    if raw.strip().startswith("{"):
                        try:
                            body = json.loads(raw)
                        except Exception:
                            body = None
                    elif "=" in raw:
                        body = {}
                        for part in raw.split("&"):
                            if "=" not in part:
                                continue
                            k, v = part.split("=", 1)
                            body[urllib.parse.unquote(k)] = urllib.parse.unquote(v)
            body = _unwrap(body)
            if isinstance(body, dict):
                payload = body
        except Exception:
            payload = {}

    out = dict(defaults)
    if isinstance(payload, dict):
        out.update(payload)
    return out


def _resolve_maintenance_employee(assigned_to):
    """Resolve an Employee for assignment from Maintenance Users.

    `assigned_to` can be:
    - Maintenance Users docname (autoname is employee_name)
    - a Maintenance Users.user_email
    - an Employee name
    """
    if not assigned_to:
        return None

    val = str(assigned_to).strip()
    if not val:
        return None

    # Direct employee id
    if frappe.db.exists("Employee", val):
        return val

    # By Maintenance Users name
    mu = frappe.db.get_value("Maintenance Users", val, ["employee", "enabled"], as_dict=True)
    if mu and mu.get("enabled") and mu.get("employee"):
        return mu.employee

    # By Maintenance Users email
    mu = frappe.db.get_value(
        "Maintenance Users",
        {"user_email": val, "enabled": 1},
        ["employee"],
        as_dict=True,
    )
    if mu and mu.get("employee"):
        return mu.employee

    return None


def _require_maintenance_staff():
    """Enforce that current user is Maintenance staff (or System Manager)."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Authentication required"), frappe.AuthenticationError)

    roles = frappe.get_roles(frappe.session.user)
    user_type = _get_user_type(roles)

    if user_type in MOBILE_MAINTENANCE_ROLES or "System Manager" in roles:
        return user_type or "System Manager"

    frappe.throw(_("Not permitted"), frappe.PermissionError)


##############################################################
# Get User Tickets
##############################################################
@frappe.whitelist()
def get_user_tickets(status_filter=None, limit=50, offset=0):
    """Return Issues for the logged-in user (mobile jobcards/tickets).

    - Uses role-based user_type (Mobile Maintenance roles / Mobile VIVA Tenant)
    - Data source: DocType Issue
    """
    try:
        # Ensure limit and offset are integers (they may come as strings from API)
        limit = int(limit) if limit is not None else 50
        offset = int(offset) if offset is not None else 0
        
        if frappe.session.user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        roles = frappe.get_roles(frappe.session.user)
        user_type = _get_user_type(roles)

        filters = {}
        customer_id = None
        ctx = None

        # Access rules:
        # - Officer/Manager: all tickets
        # - Mobile Technician: tickets where they are Issue.person_in_charge
        # - Mobile VIVA Tenant: tickets for their Tenant's customer only
        # - Mobile Sub Contractor: tickets for their supplier only, and only after a technician is assigned
        if user_type == "Mobile VIVA Tenant":
            ctx = get_tenant_context_for_user()
            customer_id = ctx.get("customer") if ctx else None
            if not customer_id:
                return {"status": "error", "message": "Tenant not found for this login"}
            filters["customer"] = customer_id
        elif user_type == "Mobile Technician":
            emp = _get_logged_in_maintenance_employee()
            if not emp:
                return {"status": "error", "message": "Maintenance staff profile not found for this login"}
            filters["person_in_charge"] = emp
        elif user_type == "Mobile Sub Contractor":
            supplier = _get_logged_in_subcontractor_supplier()
            if not supplier:
                return {"status": "error", "message": "Sub contractor not found for this login"}
            filters["sub_contractor"] = supplier
            filters["person_in_charge"] = ["is", "set"]
        elif user_type in MOBILE_OFFICER_MANAGER_ROLES or "System Manager" in roles:
            pass  # full ticket list
        else:
            return {"status": "error", "message": "Not permitted"}

        if status_filter:
            filters["status"] = status_filter

        meta = frappe.get_meta("Issue")
        candidate_fields = [
            "name",
            "subject",
            "description",
            "status",
            "priority",
            "agreement_status",
            "company",
            "customer",
            "property_name",
            "issue_type",
            "opening_date",
            "opening_time",
            "raised_by",
            "owner",
            "creation",
            "modified",
            "modified_by",
            "workflow_state",
            "person_in_charge",
            "person_in_charge_name",
            "response_by",
            "service_level_agreement",
            "sla_resolution_by",
            "sla_resolution_date",
            "first_responded_on",
            "first_response_time",
            "resolution_time",
            "user_resolution_time",
            "total_hold_time",
            "resolution_details",
            "customer_feedback",
            "defect_found",
            "via_customer_portal",
            "sub_contractor",
        ]
        # Always include standard meta fields even if not defined as DocFields
        fields = [
            f
            for f in candidate_fields
            if f in ("name", "creation", "modified", "modified_by") or meta.has_field(f)
        ]

        issues = frappe.get_all(
            "Issue",
            filters=filters,
            fields=fields,
            order_by="modified desc",
            limit=limit,
            start=offset,
        )

        total_count = frappe.db.count("Issue", filters)

        # Enhance Issue data with messaging basics (latest comm, counts) if table exists
        for issue in issues:
            issue["ticket_id"] = issue.get("name")
            issue["issue_id"] = issue.get("name")

            try:
                if meta.has_field("custom_support_communication"):
                    latest_comm = frappe.db.get_all(
                        "Support Communication",
                        filters={
                            "parent": issue["name"],
                            "parenttype": "Issue",
                            "parentfield": "custom_support_communication",
                        },
                        fields=["message_content", "time_stamp", "sender", "status", "delivery"],
                        order_by="creation desc",
                        limit=1,
                    )
                    issue["latest_communication"] = latest_comm[0] if latest_comm else None
                    issue["unread_count"] = frappe.db.count(
                        "Support Communication",
                        {
                            "parent": issue["name"],
                            "parenttype": "Issue",
                            "parentfield": "custom_support_communication",
                            "status": "Sent",
                        },
                    )
                    issue["total_communications"] = frappe.db.count(
                        "Support Communication",
                        {
                            "parent": issue["name"],
                            "parenttype": "Issue",
                            "parentfield": "custom_support_communication",
                        },
                    )
            except Exception:
                # Don't fail the entire list if comm table is not present/migrated
                issue["latest_communication"] = None

            # Owner info (for mobile UI header)
            try:
                owner_email = issue.get("raised_by") or issue.get("owner")
                owner_info = None
                if owner_email:
                    owner_info = frappe.db.get_value(
                        "User",
                        owner_email,
                        ["full_name", "user_image", "email"],
                        as_dict=True,
                    )

                if owner_email:
                    issue["owner"] = {
                        "user_id": owner_email,
                        "email": (owner_info.email if owner_info and owner_info.email else owner_email),
                        "full_name": (owner_info.full_name if owner_info and owner_info.full_name else owner_email),
                        "user_image": owner_info.user_image if owner_info else None,
                    }
                else:
                    issue["owner"] = None
            except Exception:
                issue["owner"] = None

        return {
            "status": "success",
            "tickets": issues,
            "total_count": total_count,
            "has_more": (offset + limit) < total_count,
            "user_type": user_type,
            # kept for backward-compat: Issue.customer is still Customer
            "customer": customer_id,
            "tenant": (ctx.get("tenant") if user_type == "Mobile VIVA Tenant" and ctx else None),
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_user_tickets")
        return {"status": "error", "message": str(e)}


##############################################################
# Get Issue Types
##############################################################
@frappe.whitelist()
def get_issue_types():
    """Return list of Issue Type options from the Issue Type doctype (for dropdowns/filters)."""
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        meta = frappe.get_meta("Issue Type")
        fields = ["name"]
        if meta.has_field("description"):
            fields.append("description")

        issue_types = frappe.get_all(
            "Issue Type",
            fields=fields,
            order_by="name asc",
        )
        return {"status": "success", "issue_types": issue_types}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_issue_types")
        return {"status": "error", "message": str(e)}


##############################################################
# Get Property (owned/leased by logged-in user's customer)
##############################################################
@frappe.whitelist()
def get_property():
    """Return properties for the logged-in app user.

    - Mobile Maintenance roles: returns all *available* properties (no lease checks).
    - Mobile VIVA Tenant: returns properties on *active* leases for the tenant's customer
      (Tenant Details -> Tenant -> Customer; Issue/Lease still uses Customer).
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        roles = frappe.get_roles(frappe.session.user)
        user_type = _get_user_type(roles)

        meta = frappe.get_meta("Property")
        candidate_fields = [
            "name",
            "unit_owner",
            "company",
            "parent_property",
            "type",
            "status",
        ]
        fields = [f for f in candidate_fields if f == "name" or meta.has_field(f)]

        # Officer/Manager (and System Manager): all properties
        if user_type in MOBILE_OFFICER_MANAGER_ROLES or "System Manager" in frappe.get_roles(frappe.session.user):
            properties = frappe.get_all(
                "Property",
                filters={},
                fields=fields,
                order_by="name asc",
            )
            return {"status": "success", "properties": properties, "user_type": user_type}

        # Mobile Technician: properties only from Issues assigned to them
        if user_type == "Mobile Technician":
            emp = _get_logged_in_maintenance_employee()
            if not emp:
                return {"status": "error", "message": "Maintenance staff profile not found for this login", "properties": []}
            property_names = list(
                set(
                    frappe.get_all(
                        "Issue",
                        filters={"person_in_charge": emp},
                        pluck="property_name",
                    )
                    or []
                )
            )
            property_names = [p for p in property_names if p]
            if not property_names:
                return {"status": "success", "properties": [], "user_type": user_type}
            properties = frappe.get_all(
                "Property",
                filters={"name": ["in", property_names]},
                fields=fields,
                order_by="name asc",
            )
            return {"status": "success", "properties": properties, "user_type": user_type}

        # Tenant: properties on active lease only
        ctx = get_tenant_context_for_user()
        customer_id = ctx.get("customer") if ctx else None
        if not customer_id:
            return {"status": "error", "message": "Tenant not found for this login", "properties": []}

        leased_active = frappe.get_all(
            "Lease",
            filters={"lease_customer": customer_id, "lease_status": "Active"},
            pluck="property",
        )
        property_names = list(set([p for p in (leased_active or []) if p]))
        if not property_names:
            return {
                "status": "success",
                "properties": [],
                "customer": customer_id,  # backward compat
                "tenant": ctx.get("tenant") if ctx else None,
                "user_type": user_type,
            }

        properties = frappe.get_all(
            "Property",
            filters={"name": ["in", property_names]},
            fields=fields,
            order_by="name asc",
        )
        return {
            "status": "success",
            "properties": properties,
            "customer": customer_id,  # backward compat
            "tenant": ctx.get("tenant") if ctx else None,
            "user_type": user_type,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_property")
        return {"status": "error", "message": str(e)}


##############################################################
# Create Ticket (Issue) from mobile
##############################################################
@frappe.whitelist()
def create_ticket(subject=None, description=None, property_name=None, issue_type=None):
    """Create an Issue (jobcard/ticket) from the mobile app.

    Expects: subject, description, property_name (optional if user has one property), issue_type.
    - Mobile VIVA Tenant: tenant resolved from logged-in user (Tenant Details -> Tenant -> Customer).
    - Mobile Maintenance roles: can create tickets for any property; customer is derived from the property
      (active lease customer if available, else unit_owner).
    Reads from frappe.form_dict / request body so Postman JSON is supported.
    """
    try:
        # Do NOT rely on Python args (Frappe /api/method may not pass JSON as kwargs).
        # Always pull from form_dict first, then fall back to parsing raw body.
        form = getattr(frappe, "form_dict", None) or getattr(frappe.local, "form_dict", None) or {}

        def _unwrap_payload(payload):
            if not isinstance(payload, dict):
                return payload
            # common wrappers
            for key in ("data", "args", "message"):
                if key in payload and payload[key] is not None:
                    return payload[key]
            return payload

        payload = _unwrap_payload(form)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                pass

        # If still not a dict / empty, parse request body
        if not isinstance(payload, dict) or not payload:
            try:
                req = getattr(frappe, "request", None) or getattr(frappe.local, "request", None)
                body = None
                raw = None
                if req is not None and callable(getattr(req, "get_json", None)):
                    body = req.get_json(silent=True)
                if body is None and req is not None and callable(getattr(req, "get_data", None)):
                    raw = req.get_data(as_text=True, cache=False)
                    if raw and raw.strip():
                        # JSON
                        if raw.strip().startswith("{"):
                            try:
                                body = json.loads(raw)
                            except Exception:
                                body = None
                        # form-encoded
                        elif "=" in raw:
                            body = {}
                            for part in raw.split("&"):
                                if "=" not in part:
                                    continue
                                k, v = part.split("=", 1)
                                k = urllib.parse.unquote(k)
                                v = urllib.parse.unquote(v)
                                body[k] = v
                body = _unwrap_payload(body)
                if isinstance(body, str):
                    try:
                        body = json.loads(body)
                    except Exception:
                        body = None
                if isinstance(body, dict):
                    payload = body
            except Exception:
                pass

        subject = payload.get("subject") if isinstance(payload, dict) else subject
        description = payload.get("description") if isinstance(payload, dict) else description
        property_name = payload.get("property_name") if isinstance(payload, dict) else property_name
        issue_type = payload.get("issue_type") if isinstance(payload, dict) else issue_type

        if frappe.session.user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        if not subject or not str(subject).strip():
            return {"status": "error", "message": "subject is required"}
        if not issue_type or not str(issue_type).strip():
            return {"status": "error", "message": "issue_type is required"}

        roles = frappe.get_roles(frappe.session.user)
        user_type = _get_user_type(roles)

        if user_type == "Mobile Sub Contractor":
            return {"status": "error", "message": "Not permitted"}

        ctx = None
        customer_id = None

        if user_type == "Mobile VIVA Tenant":
            ctx = get_tenant_context_for_user()
            customer_id = ctx.get("customer") if ctx else None
            if not customer_id:
                return {"status": "error", "message": "Tenant not found for this login"}

        # Property rules depend on role
        if user_type in MOBILE_MAINTENANCE_ROLES:
            # Maintenance must explicitly specify the property (they can see many)
            if not property_name or not str(property_name).strip():
                return {"status": "error", "message": "property_name is required"}
            property_name = str(property_name).strip()
            if not frappe.db.exists("Property", property_name):
                return {"status": "error", "message": "Invalid property"}

            # Derive customer from active lease first, else unit_owner
            lease_customer = frappe.db.get_value(
                "Lease",
                {"property": property_name, "lease_status": ["in", ["Active", "Vacating"]]},
                "lease_customer",
                order_by="start_date desc",
            )
            customer_id = lease_customer or frappe.db.get_value("Property", property_name, "unit_owner")
            if not customer_id:
                return {"status": "error", "message": "Customer not found for selected property"}

        else:
            # Default/tenant flow: property must belong to this tenant customer.
            if not property_name or not str(property_name).strip():
                owned = frappe.get_all("Property", filters={"unit_owner": customer_id}, pluck="name")
                leased = frappe.get_all("Lease", filters={"lease_customer": customer_id}, pluck="property")
                all_properties = list(set(owned) | set(leased))
                if len(all_properties) == 0:
                    return {"status": "error", "message": "No property found for this customer"}
                if len(all_properties) > 1:
                    return {
                        "status": "error",
                        "message": "property_name is required when you have more than one property",
                    }
                property_name = all_properties[0]
            else:
                property_name = str(property_name).strip()
                owned = frappe.get_all(
                    "Property",
                    filters={"unit_owner": customer_id, "name": property_name},
                    pluck="name",
                )
                leased = frappe.get_all(
                    "Lease",
                    filters={"lease_customer": customer_id, "property": property_name},
                    pluck="property",
                )
                if not owned and not leased:
                    return {"status": "error", "message": "Invalid property for this customer"}

        company = frappe.db.get_value("Property", property_name, "company")
        issue = frappe.get_doc(
            {
                "doctype": "Issue",
                "subject": str(subject).strip(),
                "description": str(description).strip() if description else "",
                "property_name": property_name,
                "issue_type": str(issue_type).strip(),
                "customer": customer_id,
                "raised_by": frappe.session.user,
                "company": company or None,
            }
        )
        # person_in_charge may be mandatory on Issue; allow creation without it
        issue.insert(ignore_permissions=True, ignore_mandatory=True)

        # Seed chat thread: ticket description is the first communication message.
        first_message = str(description).strip() if description else ""
        if first_message:
            try:
                meta = frappe.get_meta("Issue")
                if meta.has_field("custom_support_communication"):
                    issue.append(
                        "custom_support_communication",
                        {
                            "message_content": first_message,
                            "sender": frappe.session.user,
                            # Ticket creators are Tenant or Officer/Manager.
                            # Initial ticket narration belongs in tenant_support.
                            "channel": "tenant_support",
                            "sender_type": _support_comm_sender_type_for_role(user_type),
                            "status": "Sent",
                            "delivery": "Delivered",
                            "time_stamp": now(),
                        },
                    )
                    issue.flags.ignore_mandatory = True
                    issue.save(ignore_permissions=True)
            except Exception:
                # Never block ticket creation if communication child-table write fails.
                frappe.log_error(
                    frappe.get_traceback(),
                    f"create_ticket first message append failed: {issue.name}",
                )

        return {
            "status": "success",
            "message": "Issue created successfully",
            "ticket_id": issue.name,
            "issue_id": issue.name,
            "ticket": {
                "name": issue.name,
                "subject": issue.subject,
                "status": issue.status,
                "creation": str(issue.creation),
                "issue_type": issue.issue_type,
                "property_name": issue.property_name,
                "customer": issue.customer,
            },
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "create_ticket")
        return {"status": "error", "message": str(e)}


##############################################################

# Ticket Communication (Issue.custom_support_communication)
##############################################################
@frappe.whitelist()
def send_ticket_communication(
    issue_id=None,
    ticket_id=None,
    message_content=None,
    image_attachment=None,
    attachment=None,
    file_attachments=None,
    reply_to_idx=None,
    mentioned_emails=None,
):
    """Send a message to an Issue using the Support Communication child table.

    Accepts `issue_id` or `ticket_id` (Issue name like ISS-2026-00001).
    """
    try:
        # Align with real_estate behaviour: do NOT rely on Python args only.
        # Always read from form_dict / raw body so JSON payload works.
        form = getattr(frappe, "form_dict", None) or getattr(frappe.local, "form_dict", None) or {}

        def _unwrap_payload(payload):
            if not isinstance(payload, dict):
                return payload
            for key in ("data", "args", "message"):
                if key in payload and payload[key] is not None:
                    return payload[key]
            return payload

        payload = _unwrap_payload(form)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = None

        if not isinstance(payload, dict) or not payload:
            try:
                req = getattr(frappe, "request", None) or getattr(frappe.local, "request", None)
                body = None
                raw = None
                if req is not None and callable(getattr(req, "get_json", None)):
                    body = req.get_json(silent=True)
                if body is None and req is not None and callable(getattr(req, "get_data", None)):
                    raw = req.get_data(as_text=True, cache=False)
                    if raw and raw.strip():
                        try:
                            body = json.loads(raw) if raw.strip().startswith("{") else None
                        except Exception:
                            body = None
                body = _unwrap_payload(body)
                if isinstance(body, str):
                    try:
                        body = json.loads(body)
                    except Exception:
                        body = None
                if isinstance(body, dict):
                    payload = body
            except Exception:
                payload = payload

        channel = None
        if isinstance(payload, dict):
            if issue_id is None:
                issue_id = payload.get("issue_id") or payload.get("ticket_id")
            if ticket_id is None:
                ticket_id = payload.get("ticket_id") or payload.get("issue_id")
            if message_content is None:
                message_content = payload.get("message_content")
            if channel is None:
                channel = payload.get("channel")
            if file_attachments is None:
                file_attachments = payload.get("file_attachments")
            if mentioned_emails is None:
                mentioned_emails = payload.get("mentioned_emails")
            if image_attachment is None:
                image_attachment = payload.get("image_attachment")
            if attachment is None:
                attachment = payload.get("attachment")

        if frappe.session.user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        issue_name = (issue_id or ticket_id or "").strip()
        if not issue_name:
            return {"status": "error", "message": "issue_id (or ticket_id) is required"}
        if not message_content or not str(message_content).strip():
            return {"status": "error", "message": "message_content is required"}

        if not frappe.db.exists("Issue", issue_name):
            return {"status": "error", "message": "Issue not found"}

        issue = frappe.get_doc("Issue", issue_name)

        # Permission + channel enforcement
        roles = frappe.get_roles(frappe.session.user)
        user_type = _get_user_type(roles)
        # Desk admins often use this chat UI but may not have mobile roles
        if not user_type and "System Manager" in roles:
            user_type = "Mobile Maintenance Manager"
        # Some app users may be missing explicit roles; infer from linked doctypes
        if not user_type:
            user_type = _infer_mobile_user_type_from_doctypes()

        # Normalize channel (two lanes: tenant↔officer, officer/technician/subcontractor shared)
        channel = (channel or "").strip()
        if not channel:
            if user_type == "Mobile VIVA Tenant":
                channel = "tenant_support"
            else:
                channel = "technician_support"

        if channel not in VALID_TICKET_CHANNELS:
            return {"status": "error", "message": "Invalid channel"}

        tech_emp = _issue_assigned_technician_employee(issue)
        logged_emp = _get_logged_in_maintenance_employee()

        if user_type == "Mobile VIVA Tenant":
            channel = "tenant_support"
            ctx = get_tenant_context_for_user()
            customer_id = ctx.get("customer") if ctx else None
            if not customer_id or issue.customer != customer_id:
                frappe.throw(_("Not permitted"), frappe.PermissionError)

        elif user_type == "Mobile Technician":
            channel = "technician_support"
            if not logged_emp or tech_emp != logged_emp:
                frappe.throw(_("Not permitted"), frappe.PermissionError)

        elif user_type == "Mobile Sub Contractor":
            channel = "technician_support"
            supplier = _get_logged_in_subcontractor_supplier()
            if not supplier:
                frappe.throw(_("Not permitted"), frappe.PermissionError)
            if _get_issue_subcontractor_supplier(issue) != supplier:
                frappe.throw(_("Not permitted"), frappe.PermissionError)
            if not tech_emp:
                return {"status": "error", "message": "No technician assigned to this ticket"}

        elif user_type in MOBILE_OFFICER_MANAGER_ROLES:
            if channel == "technician_support" and not tech_emp:
                return {"status": "error", "message": "No technician assigned to this ticket"}
        else:
            frappe.throw(_("Not permitted"), frappe.PermissionError)

        meta = frappe.get_meta("Issue")
        if not meta.has_field("custom_support_communication"):
            return {
                "status": "error",
                "message": "Issue communication table not configured (custom_support_communication missing)",
            }

        # reply-to (optional)
        reply_to_idx_val = None
        quoted_sender = ""
        quoted_content = ""
        if reply_to_idx is not None and str(reply_to_idx).strip():
            try:
                reply_to_idx_val = int(reply_to_idx)
            except Exception:
                reply_to_idx_val = None
        if reply_to_idx_val and reply_to_idx_val > 0:
            found = False
            for c in issue.get("custom_support_communication") or []:
                if c.idx == reply_to_idx_val:
                    quoted_sender = c.sender or ""
                    quoted_content = (c.message_content or "")[:200]
                    found = True
                    break
            if not found:
                return {"status": "error", "message": "Invalid reply_to_idx"}

        communication_data = {
            "message_content": str(message_content).strip(),
            "sender": frappe.session.user,
            "channel": channel,
            "sender_type": _support_comm_sender_type_for_role(user_type),
            "status": "Sent",
            "delivery": "Delivered",
            "time_stamp": now(),
        }
        if reply_to_idx_val and reply_to_idx_val > 0:
            communication_data["reply_to_idx"] = reply_to_idx_val
            communication_data["quoted_sender"] = quoted_sender
            communication_data["quoted_content"] = quoted_content

        # Attachments: support both legacy (attachment/image_attachment URL) and file_attachments list/json string
        file_url = attachment or image_attachment
        if file_attachments:
            try:
                parsed = file_attachments
                if isinstance(parsed, str):
                    parsed = json.loads(parsed) if parsed.strip() else []
                if isinstance(parsed, dict):
                    parsed = [parsed]
                if isinstance(parsed, list):
                    for file_info in parsed:
                        if not (isinstance(file_info, dict) and file_info.get("name")):
                            continue
                        if frappe.db.exists("File", file_info["name"]):
                            file_doc = frappe.get_doc("File", file_info["name"])
                            file_doc.attached_to_doctype = "Issue"
                            file_doc.attached_to_name = issue_name
                            file_doc.save()
                            file_url = file_info.get("file_url") or file_doc.file_url or file_url
                            if file_url:
                                communication_data["attachment"] = file_url
                                lower = str(file_url).lower()
                                if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                                    communication_data["image"] = file_url
                                    communication_data["image_attachment"] = file_url
            except Exception:
                pass
        elif file_url:
            # legacy url -> attach file if exists
            try:
                file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
                if file_name and frappe.db.exists("File", file_name):
                    file_doc = frappe.get_doc("File", file_name)
                    file_doc.attached_to_doctype = "Issue"
                    file_doc.attached_to_name = issue_name
                    file_doc.save()
                communication_data["attachment"] = file_url
                lower = str(file_url).lower()
                if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                    communication_data["image"] = file_url
                    communication_data["image_attachment"] = file_url
            except Exception:
                pass

        # Direct child-row INSERT — bypasses document-level optimistic locking so concurrent
        # sends from multiple users never raise TimestampMismatchError or lose each other's data.
        # Each row gets a unique `name` hash; the only benign race is on `idx` (two simultaneous
        # messages may share an idx value, but both save successfully — time_stamp is the real
        # sort key for chat ordering).
        row_name = frappe.generate_hash("Support Communication", 10)
        now_val = communication_data.get("time_stamp") or now()

        try:
            _idx_res = frappe.db.sql(
                "SELECT COALESCE(MAX(idx), 0) + 1 FROM `tabSupport Communication` WHERE parent = %s",
                issue_name,
            )
            next_idx = int((_idx_res or [[1]])[0][0])
        except Exception:
            next_idx = 1

        frappe.db.sql(
            """INSERT INTO `tabSupport Communication`
               (name, parent, parenttype, parentfield, idx,
                owner, creation, modified, modified_by,
                message_content, channel, sender, sender_type,
                status, delivery, time_stamp,
                attachment, reply_to_idx, quoted_sender, quoted_content,
                is_edited, edited_at)
               VALUES (%s, %s, 'Issue', 'custom_support_communication', %s,
                       %s, %s, %s, %s,
                       %s, %s, %s, %s,
                       %s, %s, %s,
                       %s, %s, %s, %s,
                       %s, %s)""",
            (
                row_name, issue_name, next_idx,
                frappe.session.user, now_val, now_val, frappe.session.user,
                communication_data["message_content"],
                communication_data["channel"],
                communication_data["sender"],
                communication_data["sender_type"],
                communication_data.get("status", "Sent"),
                communication_data.get("delivery", "Delivered"),
                now_val,
                communication_data.get("attachment"),
                communication_data.get("reply_to_idx") or 0,
                communication_data.get("quoted_sender") or "",
                communication_data.get("quoted_content") or "",
                int(communication_data.get("is_edited", 0)),
                communication_data.get("edited_at"),
            ),
        )

        saved = frappe._dict({
            "name": row_name,
            "idx": next_idx,
            "message_content": communication_data["message_content"],
            "channel": communication_data["channel"],
            "sender": communication_data["sender"],
            "sender_type": communication_data["sender_type"],
            "status": communication_data.get("status", "Sent"),
            "delivery": communication_data.get("delivery", "Delivered"),
            "time_stamp": now_val,
            "attachment": communication_data.get("attachment"),
            "reply_to_idx": communication_data.get("reply_to_idx") or 0,
            "quoted_sender": communication_data.get("quoted_sender") or "",
            "quoted_content": communication_data.get("quoted_content") or "",
            "is_edited": 0,
            "edited_at": None,
        })

        sender_info = frappe.db.get_value(
            "User", frappe.session.user, ["full_name", "user_image", "email"], as_dict=True
        ) or {}

        payload = {
            "ticket_id": issue_name,
            "issue_id": issue_name,
            "communication": {
                "idx": saved.idx,
                "message_content": saved.message_content,
                "sender": saved.sender,
                "channel": getattr(saved, "channel", None) or channel,
                "sender_type": getattr(saved, "sender_type", None)
                or _support_comm_sender_type_for_role(user_type),
                "status": getattr(saved, "status", None),
                "delivery": getattr(saved, "delivery", None),
                "time_stamp": getattr(saved, "time_stamp", None),
                "attachment": getattr(saved, "attachment", None),
                "sender_full_name": sender_info.get("full_name") or saved.sender,
                "sender_profile_image": sender_info.get("user_image"),
            },
            "timestamp": frappe.utils.now(),
        }
        # Include reply metadata in realtime payload (parity with vsd_helpdesk).
        # Without this, clients only see quoted preview after refetch/reopen.
        if getattr(saved, "reply_to_idx", None):
            payload["communication"]["reply_to_idx"] = getattr(saved, "reply_to_idx", None)
            payload["communication"]["quoted_sender"] = getattr(saved, "quoted_sender", None) or ""
            payload["communication"]["quoted_content"] = getattr(saved, "quoted_content", None) or ""

        # Parse + validate mentions against maintenance-only mentionables for this issue
        mentioned_set = _normalize_mentioned_emails_for_issue(
            issue, mentioned_emails, exclude_user=frappe.session.user
        )

        # Flags for clients (aligned with Support Communication.sender_type: tenant vs non-tenant)
        row_sender_type = getattr(saved, "sender_type", None) or _support_comm_sender_type_for_role(
            user_type
        )
        is_customer_message = row_sender_type == "tenant"
        is_support_message = row_sender_type != "tenant"

        # recipients for realtime + push
        recipients = set()
        if issue.raised_by:
            recipients.add(issue.raised_by)
        # Keep assigned technician in the recipient set so they receive websocket/FCM
        # updates the same way tenant users do (even if client is listening by user room).
        if tech_emp:
            recipients |= _get_technician_user_recipients_for_employee(tech_emp)
        if channel == "tenant_support" and issue.customer:
            lease_names = frappe.get_all(
                "Lease", filters={"lease_customer": issue.customer}, pluck="name"
            )
            if lease_names:
                tenant_users = frappe.get_all(
                    "Tenant Details",
                    filters={"parent": ["in", lease_names], "parenttype": "Lease"},
                    pluck="user_email",
                )
                for email in tenant_users or []:
                    if email:
                        recipients.add(email)
            recipients |= _get_officer_manager_recipients()
        elif channel == "technician_support":
            recipients |= _get_officer_manager_recipients()
            if tech_emp:
                recipients |= _get_technician_user_recipients_for_employee(tech_emp)
            supplier = _get_issue_subcontractor_supplier(issue)
            if supplier:
                recipients |= _get_subcontractor_recipients_for_supplier(supplier)

        recipients.discard(frappe.session.user)

        # Rooms to emit to
        rooms = set()
        # Global support room (maintenance staff subscribe here)
        rooms.add("support_team")
        # Emit to ticket rooms for all channels so assigned technicians (and other participants)
        # subscribed on ticket rooms receive realtime events as reliably as tenants.
        rooms |= {
            f"doc:Issue/{issue_name}",
            f"doc:Ticket/{issue_name}",
            issue_name,
            f"ticket:{issue_name}",
        }
        # Echo to sender room as well so mobile can confirm send immediately
        rooms.add(f"user:{frappe.session.user}")
        for u in recipients:
            rooms.add(f"user:{u}")

        # Log for debugging
        try:
            frappe.logger().info(
                f"🎯 TICKET MESSAGE EMIT: ticket_id={issue_name}, rooms={list(rooms)}, sender={frappe.session.user}"
            )
        except Exception:
            pass

        for room in rooms:
            try:
                frappe.publish_realtime(
                    event="ticket_message",
                    message=payload,
                    room=room,
                    after_commit=True,
                )
            except Exception as emit_err:
                try:
                    frappe.logger().error(
                        f"❌ Error emitting ticket_message to room {room}: {str(emit_err)}"
                    )
                except Exception:
                    pass

        # Mention event (websocket)
        if mentioned_set:
            mention_payload = {
                "ticket_id": issue_name,
                "issue_id": issue_name,
                "message_idx": saved.idx,
                "mentioned_emails": sorted(list(mentioned_set)),
                "timestamp": frappe.utils.now(),
            }
            for u in mentioned_set:
                frappe.publish_realtime(
                    event="ticket_mention",
                    message=mention_payload,
                    room=f"user:{u}",
                    after_commit=True,
                )

        # FCM push (background job) - dedupe mention vs generic recipients
        try:
            from propms.api.v1.notifications.notifications import enqueue_ticket_message_push

            preview = (saved.message_content or "")[:80] + ("..." if len(saved.message_content or "") > 80 else "")
            title = "New message"
            body = f"{sender_info.get('full_name') or frappe.session.user}: {preview}"
            fcm_recipients = _normalize_recipient_emails(recipients)
            generic_recipients = fcm_recipients - mentioned_set

            for u in generic_recipients:
                frappe.enqueue(
                    enqueue_ticket_message_push,
                    user=u,
                    ticket_id=issue_name,
                    title=title,
                    body=body,
                    message_idx=saved.idx,
                    notification_type="ticket_message",
                    queue="short",
                )

            # Mention pushes (if any)
            for u in mentioned_set:
                mention_body = f"{body} - Mentioned You"
                frappe.enqueue(
                    enqueue_ticket_message_push,
                    user=u,
                    ticket_id=issue_name,
                    title=title,
                    body=mention_body,
                    message_idx=saved.idx,
                    notification_type="mention",
                    queue="short",
                )
        except Exception:
            pass

        return {
            "status": "success",
            "message": "Communication sent successfully",
            "ticket_id": issue_name,
            "issue_id": issue_name,
            "communication_idx": saved.idx,
            "sender_type": row_sender_type,
            "channel": getattr(saved, "channel", None) or channel,
            "message_status": getattr(saved, "status", None),
            "message_delivery": getattr(saved, "delivery", None),
            "is_customer_message": is_customer_message,
            "is_support_message": is_support_message,
            "mentioned_emails": sorted(list(mentioned_set)),
            "communication": payload["communication"],
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "send_ticket_communication")
        return {"status": "error", "message": str(e)}


##############################################################

@frappe.whitelist()
def get_ticket_communications(ticket_id, limit=20, offset=0, channel=None):
    """Get communications for a specific Issue (mobile-optimised).

    ticket_id is the Issue name (e.g. ISS-2026-00001).
    """
    try:
        # Convert to ints (come as strings)
        limit = int(limit) if limit is not None else 20
        offset = int(offset) if offset is not None else 0

        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        issue = frappe.get_doc("Issue", ticket_id)
        current_user = frappe.session.user

        # Access control + channel enforcement (see VALID_TICKET_CHANNELS)
        roles = frappe.get_roles(current_user)
        user_type = _get_user_type(roles)
        if not user_type and "System Manager" in roles:
            user_type = "Mobile Maintenance Manager"
        if not user_type:
            user_type = _infer_mobile_user_type_from_doctypes(current_user)

        channel = (channel or "").strip()
        if not channel:
            if user_type == "Mobile VIVA Tenant":
                channel = "tenant_support"
            else:
                channel = "technician_support"

        if channel not in VALID_TICKET_CHANNELS:
            return {"status": "error", "message": "Invalid channel"}

        tech_emp = _issue_assigned_technician_employee(issue)
        logged_emp = _get_logged_in_maintenance_employee(current_user)

        if user_type == "Mobile VIVA Tenant":
            channel = "tenant_support"
            ctx = get_tenant_context_for_user(current_user)
            customer_id = ctx.get("customer") if ctx else None
            if not customer_id or issue.customer != customer_id:
                return {"status": "error", "message": "Access denied"}
        elif user_type == "Mobile Technician":
            channel = "technician_support"
            if not logged_emp or tech_emp != logged_emp:
                return {"status": "error", "message": "Access denied"}
        elif user_type == "Mobile Sub Contractor":
            channel = "technician_support"
            supplier = _get_logged_in_subcontractor_supplier(current_user)
            if not supplier or _get_issue_subcontractor_supplier(issue) != supplier:
                return {"status": "error", "message": "Access denied"}
            if not tech_emp:
                return {"status": "success", "communications": [], "total_count": 0, "has_more": False}
        elif user_type in MOBILE_OFFICER_MANAGER_ROLES:
            if channel == "technician_support" and not tech_emp:
                return {"status": "success", "communications": [], "total_count": 0, "has_more": False}
        else:
            return {"status": "error", "message": "Access denied"}

        meta = frappe.get_meta("Issue")
        if not meta.has_field("custom_support_communication"):
            return {
                "status": "error",
                "message": "Issue communication table not configured (custom_support_communication missing)",
            }

        # Filter messages by channel. Treat missing channel as tenant_support for backward compatibility.
        comm_table_all = issue.get("custom_support_communication") or []
        comm_table = [
            c
            for c in comm_table_all
            if (getattr(c, "channel", None) or "tenant_support") == channel
        ]
        total_count = len(comm_table)

        # Build reply map: parent_idx -> [child_idxs] (within the filtered channel)
        reply_map = {}
        for c in comm_table:
            parent_idx = getattr(c, "reply_to_idx", None)
            if parent_idx:
                reply_map.setdefault(parent_idx, []).append(c.idx)

        start_idx = offset
        end_idx = min(offset + limit, total_count)

        communications = []
        for i in range(start_idx, end_idx):
            comm = comm_table[i]

            # Sender info
            sender_full_name = None
            sender_profile_image = None
            if comm.sender:
                try:
                    user_info = frappe.db.get_value(
                        "User", comm.sender, ["full_name", "user_image"], as_dict=True
                    )
                    if user_info:
                        sender_full_name = user_info.get("full_name")
                        sender_profile_image = user_info.get("user_image")
                except Exception:
                    pass

            out = {
                "message_content": comm.message_content,
                "sender": comm.sender,
                "channel": getattr(comm, "channel", None) or "tenant_support",
                "sender_type": getattr(comm, "sender_type", None) or "maintenance",
                "sender_full_name": sender_full_name or comm.sender,
                "sender_profile_image": sender_profile_image,
                "time_stamp": getattr(comm, "time_stamp", None),
                "status": getattr(comm, "status", None),
                "delivery": getattr(comm, "delivery", None),
                "image_attachment": getattr(comm, "image", None),
                "attachment": getattr(comm, "attachment", None),
                "image": getattr(comm, "image", None),
                "idx": comm.idx,
                "is_edited": getattr(comm, "is_edited", None),
                "edited_at": getattr(comm, "edited_at", None),
            }

            # Reply info
            reply_to_idx_val = getattr(comm, "reply_to_idx", None)
            if reply_to_idx_val:
                out["reply_to_idx"] = reply_to_idx_val
                out["quoted_sender"] = getattr(comm, "quoted_sender", "") or ""
                out["quoted_content"] = getattr(comm, "quoted_content", "") or ""

            replies_to_this = reply_map.get(comm.idx, [])
            out["reply_count"] = len(replies_to_this)
            out["replied_by_idxs"] = replies_to_this

            communications.append(out)

        return {
            "status": "success",
            "communications": communications,
            "total_count": total_count,
            "has_more": end_idx < total_count,
            "channel": channel,
        }
    except ValueError:
        frappe.log_error(frappe.get_traceback(), "get_ticket_communications")
        return {"status": "error", "message": "Invalid limit or offset parameter"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_ticket_communications")
        return {"status": "error", "message": str(e)}


##############################################################

@frappe.whitelist()
def edit_ticket_communication(ticket_id, communication_idx, new_message_content, mentioned_emails=None):
    """Edit a Support Communication message (WhatsApp-style edit).

    Only the original sender can edit their own message.
    Uses a direct SQL UPDATE to avoid the document-level race condition.

    Args:
        ticket_id: Issue name (e.g. ISS-2026-00001)
        communication_idx: idx (integer) of the row to edit
        new_message_content: replacement text
    """
    try:
        if frappe.session.user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        payload = _parse_request_payload(
            {
                "ticket_id": ticket_id,
                "communication_idx": communication_idx,
                "new_message_content": new_message_content,
                "mentioned_emails": mentioned_emails,
            }
        )
        ticket_id = payload.get("ticket_id") or payload.get("issue_id") or ticket_id
        communication_idx = payload.get("communication_idx", communication_idx)
        new_message_content = payload.get("new_message_content", new_message_content)
        mentioned_emails = payload.get("mentioned_emails", mentioned_emails)

        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        issue_name = str(ticket_id).strip()
        current_user = frappe.session.user

        try:
            comm_idx = int(communication_idx)
        except (ValueError, TypeError):
            return {"status": "error", "message": "Invalid communication_idx — must be an integer"}

        if not new_message_content or not str(new_message_content).strip():
            return {"status": "error", "message": "Message content cannot be empty"}

        # Permission: same role enforcement as send_ticket_communication
        roles = frappe.get_roles(current_user)
        user_type = _get_user_type(roles)
        if not user_type and "System Manager" in roles:
            user_type = "Mobile Maintenance Manager"
        if not user_type:
            user_type = _infer_mobile_user_type_from_doctypes(current_user)
        if not user_type:
            frappe.throw(_("Not permitted"), frappe.PermissionError)

        # Fetch the target row directly (avoids loading the entire Issue + child table)
        rows = frappe.db.sql(
            """SELECT name, idx, message_content, sender, channel, sender_type,
                      status, delivery, time_stamp, attachment,
                      reply_to_idx, quoted_sender, quoted_content
               FROM `tabSupport Communication`
               WHERE parent = %s AND idx = %s
               LIMIT 1""",
            (issue_name, comm_idx),
            as_dict=True,
        )
        if not rows:
            return {"status": "error", "message": f"No communication with idx {comm_idx} on this ticket"}

        row = rows[0]

        if row.sender != current_user:
            return {"status": "error", "message": "You can only edit your own messages"}

        new_content = str(new_message_content).strip()
        now_val = frappe.utils.now()

        # Direct UPDATE — atomic, no document-level race condition
        frappe.db.sql(
            """UPDATE `tabSupport Communication`
               SET message_content = %s, is_edited = 1, edited_at = %s,
                   modified = %s, modified_by = %s
               WHERE parent = %s AND idx = %s AND sender = %s""",
            (new_content, now_val, now_val, current_user, issue_name, comm_idx, current_user),
        )

        sender_info = frappe.db.get_value(
            "User", current_user, ["full_name", "user_image"], as_dict=True
        ) or {}

        # Build reply_count for this message from DB
        reply_rows = frappe.db.sql(
            "SELECT idx, reply_to_idx FROM `tabSupport Communication` WHERE parent = %s AND reply_to_idx > 0",
            issue_name,
            as_dict=True,
        )
        reply_map = {}
        for r in reply_rows or []:
            reply_map.setdefault(r.reply_to_idx, []).append(r.idx)

        updated_communication = {
            "idx": comm_idx,
            "message_content": new_content,
            "sender": current_user,
            "channel": row.get("channel"),
            "sender_type": row.get("sender_type"),
            "sender_full_name": sender_info.get("full_name") or current_user,
            "sender_profile_image": sender_info.get("user_image"),
            "time_stamp": str(row.get("time_stamp")) if row.get("time_stamp") else None,
            "status": row.get("status"),
            "delivery": row.get("delivery"),
            "attachment": row.get("attachment"),
            "is_edited": 1,
            "edited_at": now_val,
            "reply_count": len(reply_map.get(comm_idx, [])),
            "replied_by_idxs": reply_map.get(comm_idx, []),
        }
        if row.get("reply_to_idx"):
            updated_communication["reply_to_idx"] = row["reply_to_idx"]
            updated_communication["quoted_sender"] = row.get("quoted_sender") or ""
            updated_communication["quoted_content"] = row.get("quoted_content") or ""

        # Build recipient + room sets (same logic as send_ticket_communication)
        issue = frappe.get_doc("Issue", issue_name)
        tech_emp = _issue_assigned_technician_employee(issue)
        channel = row.get("channel") or "tenant_support"
        mentioned_set = _normalize_mentioned_emails_for_issue(
            issue, mentioned_emails, exclude_user=current_user
        )

        recipients = set()
        if issue.raised_by:
            recipients.add(issue.raised_by)
        if tech_emp:
            recipients |= _get_technician_user_recipients_for_employee(tech_emp)
        if channel == "tenant_support" and issue.customer:
            lease_names = frappe.get_all(
                "Lease", filters={"lease_customer": issue.customer}, pluck="name"
            )
            if lease_names:
                tenant_users = frappe.get_all(
                    "Tenant Details",
                    filters={"parent": ["in", lease_names], "parenttype": "Lease"},
                    pluck="user_email",
                )
                for email in tenant_users or []:
                    if email:
                        recipients.add(email)
            recipients |= _get_officer_manager_recipients()
        elif channel == "technician_support":
            recipients |= _get_officer_manager_recipients()
            if tech_emp:
                recipients |= _get_technician_user_recipients_for_employee(tech_emp)
            supplier = _get_issue_subcontractor_supplier(issue)
            if supplier:
                recipients |= _get_subcontractor_recipients_for_supplier(supplier)
        recipients.discard(current_user)

        rooms = {
            "support_team",
            f"doc:Issue/{issue_name}",
            f"doc:Ticket/{issue_name}",
            issue_name,
            f"ticket:{issue_name}",
            f"user:{current_user}",
        }
        for u in recipients:
            rooms.add(f"user:{u}")

        edit_payload = {
            "ticket_id": issue_name,
            "issue_id": issue_name,
            "communication": updated_communication,
            "timestamp": frappe.utils.now(),
        }

        # Emit to ticket/user rooms first (same room strategy as send flow), immediately.
        for room in rooms:
            try:
                frappe.publish_realtime(
                    event="ticket_message_edited",
                    message=edit_payload,
                    room=room,
                    after_commit=False,
                )
            except Exception as emit_err:
                try:
                    frappe.logger().error(
                        f"❌ Error emitting ticket_message_edited to {room}: {str(emit_err)}"
                    )
                except Exception:
                    pass

        # Flutter parity with vsd_helpdesk: also emit by explicit user routing.
        # Some mobile clients receive user-routed events more reliably than room-only emits.
        user_targets = _normalize_recipient_emails(recipients)
        sender_email = _to_user_email(current_user)
        if sender_email:
            user_targets.add(sender_email)  # echo back edit to sender client
        for user_email in user_targets:
            try:
                frappe.publish_realtime(
                    event="ticket_message_edited",
                    message=edit_payload,
                    user=user_email,
                    after_commit=False,
                )
            except Exception as emit_err:
                try:
                    frappe.logger().error(
                        f"❌ Error emitting ticket_message_edited to user {user_email}: {str(emit_err)}"
                    )
                except Exception:
                    pass

        # FCM push for edit (dedupe mention vs generic recipients)
        try:
            from propms.api.v1.notifications.notifications import enqueue_ticket_message_push

            preview = new_content[:80] + ("..." if len(new_content) > 80 else "")
            body = f"{sender_info.get('full_name') or current_user}: {preview}"
            fcm_recipients = _normalize_recipient_emails(recipients)
            generic_recipients = fcm_recipients - mentioned_set

            for u in generic_recipients:
                frappe.enqueue(
                    enqueue_ticket_message_push,
                    user=u,
                    ticket_id=issue_name,
                    title="Message edited",
                    body=body,
                    message_idx=comm_idx,
                    notification_type="ticket_message_edited",
                    queue="short",
                )
            for u in mentioned_set:
                mention_body = f"{body} - Mentioned You"
                frappe.enqueue(
                    enqueue_ticket_message_push,
                    user=u,
                    ticket_id=issue_name,
                    title="Message edited",
                    body=mention_body,
                    message_idx=comm_idx,
                    notification_type="mention",
                    queue="short",
                )
        except Exception:
            pass

        return {
            "status": "success",
            "message": "Message edited successfully",
            "communication": updated_communication,
            "mentioned_emails": sorted(list(mentioned_set)),
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "edit_ticket_communication")
        return {"status": "error", "message": str(e)}


##############################################################

@frappe.whitelist()
def send_typing_indicator(ticket_id, is_typing=True):
    """Send a transient 'user is typing' indicator for a ticket (no DB write).

    Emits a ticket_typing websocket event to all participants on the ticket.
    After-commit is False so the event fires immediately without waiting for a DB transaction.
    """
    try:
        current_user = frappe.session.user
        if current_user == "Guest":
            return {"status": "error", "message": "Authentication required"}

        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        issue_name = str(ticket_id).strip()

        # Coerce is_typing (comes as string from API calls)
        if isinstance(is_typing, str):
            is_typing_flag = is_typing.strip().lower() in ("1", "true", "yes", "on")
        else:
            is_typing_flag = bool(is_typing)

        # Permission check
        roles = frappe.get_roles(current_user)
        user_type = _get_user_type(roles)
        if not user_type and "System Manager" in roles:
            user_type = "Mobile Maintenance Manager"
        if not user_type:
            user_type = _infer_mobile_user_type_from_doctypes(current_user)
        if not user_type:
            return {"status": "error", "message": "Access denied"}

        # Tenant: verify they belong to this issue
        if user_type == "Mobile VIVA Tenant":
            issue = frappe.get_doc("Issue", issue_name)
            ctx = get_tenant_context_for_user(current_user)
            customer_id = ctx.get("customer") if ctx else None
            if not customer_id or issue.customer != customer_id:
                return {"status": "error", "message": "Access denied"}

        # User display info (small in-process cache; cleared on worker restart)
        user_full_name = None
        user_image = None
        cached = _TYPING_USER_CACHE.get(current_user)
        if cached:
            user_full_name, user_image = cached
        else:
            try:
                user_info = frappe.db.get_value(
                    "User", current_user, ["full_name", "user_image"], as_dict=True
                )
                if user_info:
                    user_full_name = user_info.get("full_name")
                    user_image = user_info.get("user_image")
                    _TYPING_USER_CACHE[current_user] = (user_full_name, user_image)
            except Exception:
                pass

        typing_payload = {
            "ticket_id": issue_name,
            "issue_id": issue_name,
            "user": current_user,
            "user_full_name": user_full_name or current_user,
            "user_image": user_image,
            "user_type": user_type,
            "is_typing": is_typing_flag,
            "timestamp": frappe.utils.now(),
        }

        # Emit to all ticket rooms (same set used by send_ticket_communication)
        rooms = {
            "support_team",
            f"doc:Issue/{issue_name}",
            f"doc:Ticket/{issue_name}",
            issue_name,
            f"ticket:{issue_name}",
            f"user:{current_user}",
        }
        for room in rooms:
            try:
                frappe.publish_realtime(
                    event="ticket_typing",
                    message=typing_payload,
                    room=room,
                    after_commit=False,  # no DB write — fire immediately
                )
            except Exception as e:
                try:
                    frappe.logger().error(
                        f"❌ Error emitting ticket_typing to {room}: {str(e)}"
                    )
                except Exception:
                    pass

        return {"status": "success", "typing": typing_payload}

    except Exception as e:
        frappe.logger().error(f"send_typing_indicator error: {str(e)}")
        return {"status": "error", "message": str(e)}


##############################################################

@frappe.whitelist()
def initialize_app_websocket():
    """Initialize websocket connection and subscribe to relevant rooms for the logged-in user.

    This mirrors the real_estate behaviour but works on Issue instead of Job Card/Ticket.
    """
    try:
        current_user = frappe.session.user
        if current_user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        roles = frappe.get_roles(current_user)
        user_type_role = _get_user_type(roles)

        # Determine support vs customer_user semantics for response
        is_support_staff = (
            user_type_role in MOBILE_MAINTENANCE_ROLES
            or "System Manager" in roles
            or current_user == "Administrator"
        )

        # Collect relevant Issues (same visibility rules as get_user_tickets)
        issues = []
        if user_type_role in MOBILE_OFFICER_MANAGER_ROLES or "System Manager" in roles or current_user == "Administrator":
            issues = frappe.get_all("Issue", pluck="name")
        elif user_type_role == "Mobile Technician":
            emp = _get_logged_in_maintenance_employee(current_user)
            if emp:
                issues = frappe.get_all("Issue", filters={"person_in_charge": emp}, pluck="name")
        elif user_type_role == "Mobile Sub Contractor":
            supplier = _get_logged_in_subcontractor_supplier(current_user)
            if supplier:
                issues = frappe.get_all(
                    "Issue",
                    filters={"sub_contractor": supplier, "person_in_charge": ["is", "set"]},
                    pluck="name",
                )
        elif user_type_role == "Mobile VIVA Tenant":
            ctx = get_tenant_context_for_user(current_user)
            customer_id = ctx.get("customer") if ctx else None
            if customer_id:
                issues = frappe.get_all("Issue", filters={"customer": customer_id}, pluck="name")

        user_tickets = issues or []

        # Build rooms to join (keep legacy ticket room names for mobile compatibility)
        rooms_to_join = set()
        rooms_to_join.add(f"user:{current_user}")
        for name in user_tickets:
            rooms_to_join.add(f"doc:Issue/{name}")
            rooms_to_join.add(f"doc:Ticket/{name}")
            rooms_to_join.add(name)
            rooms_to_join.add(f"ticket:{name}")

        if is_support_staff:
            rooms_to_join.add("support_team")

        # Emit subscription events
        subscription_results = []
        for room in rooms_to_join:
            try:
                frappe.publish_realtime(
                    event="room_subscribe",
                    message={
                        "room": room,
                        "user": current_user,
                        "timestamp": frappe.utils.now(),
                    },
                    user=current_user,
                )
                subscription_results.append(f"SUCCESS: {room}")
            except Exception as room_error:
                subscription_results.append(f"ERROR: {room} - {str(room_error)}")

        return {
            "status": "success",
            "message": "Websocket connection initialized",
            "user": current_user,
            "user_type": "support_staff" if is_support_staff else "customer_user",
            "rooms_subscribed": list(rooms_to_join),
            "total_tickets": len(user_tickets),
            "subscription_results": subscription_results,
            "websocket_config": {
                "server_url": frappe.utils.get_url(),
                "namespace": frappe.local.site,
                "events": {
                    "ticket_message": "New message in ticket",
                    "ticket_update": "Ticket status/assignment change",
                    "ticket_created": "New ticket created",
                    "notification": "General app notification",
                },
            },
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "initialize_app_websocket")
        return {"status": "error", "message": str(e)}


##############################################################

@frappe.whitelist()
def subscribe_to_ticket_room(ticket_id):
    """Subscribe to a specific Issue room for real-time updates (mobile expects ticket_id)."""
    try:
        current_user = frappe.session.user
        if current_user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        issue = frappe.get_doc("Issue", ticket_id)

        roles = frappe.get_roles(current_user)
        user_type_role = _get_user_type(roles)
        if not user_type_role and "System Manager" in roles:
            user_type_role = "Mobile Maintenance Manager"
        if not user_type_role:
            user_type_role = _infer_mobile_user_type_from_doctypes(current_user)
        tech_emp = _issue_assigned_technician_employee(issue)
        logged_emp = _get_logged_in_maintenance_employee(current_user)

        if user_type_role == "Mobile VIVA Tenant":
            ctx = get_tenant_context_for_user(current_user)
            customer_id = ctx.get("customer") if ctx else None
            if not customer_id or issue.customer != customer_id:
                return {"status": "error", "message": "Access denied"}
        elif user_type_role == "Mobile Technician":
            if not logged_emp or tech_emp != logged_emp:
                return {"status": "error", "message": "Access denied"}
        elif user_type_role == "Mobile Sub Contractor":
            supplier = _get_logged_in_subcontractor_supplier(current_user)
            if not supplier or _get_issue_subcontractor_supplier(issue) != supplier or not tech_emp:
                return {"status": "error", "message": "Access denied"}
        elif user_type_role not in MOBILE_OFFICER_MANAGER_ROLES:
            return {"status": "error", "message": "Access denied"}

        # Emit subscription event to legacy doc:Ticket room (for mobile client)
        room = f"doc:Ticket/{ticket_id}"
        frappe.publish_realtime(
            event="room_subscribe",
            message={
                "room": room,
                "user": current_user,
                "ticket_id": ticket_id,
                "timestamp": frappe.utils.now(),
            },
            user=current_user,
        )

        return {
            "status": "success",
            "message": f"Subscribed to ticket room: {ticket_id}",
            "room": room,
            "ticket_id": ticket_id,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "subscribe_to_ticket_room")
        return {"status": "error", "message": str(e)}


##############################################################

@frappe.whitelist()
def unsubscribe_from_ticket_room(ticket_id):
    """Unsubscribe from a specific Issue room (mobile still passes ticket_id)."""
    try:
        current_user = frappe.session.user
        if current_user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)

        room = f"doc:Ticket/{ticket_id}"
        frappe.publish_realtime(
            event="room_unsubscribe",
            message={
                "room": room,
                "user": current_user,
                "ticket_id": ticket_id,
                "timestamp": frappe.utils.now(),
            },
            user=current_user,
        )

        return {
            "status": "success",
            "message": f"Unsubscribed from ticket room: {ticket_id}",
            "room": room,
            "ticket_id": ticket_id,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "unsubscribe_from_ticket_room")
        return {"status": "error", "message": str(e)}


##############################################################

@frappe.whitelist()
def get_user_image(user=None):
    """Return the user's profile image URL (defaults to current user).

    Migrated from real_estate_support_system.api.mobile.get_user_image.
    """
    try:
        target_user = user or frappe.session.user
        if not target_user or target_user == "Guest":
            frappe.throw(_("Authentication required"), frappe.AuthenticationError)
        user_doc = frappe.get_doc("User", target_user)
        return user_doc.user_image
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_user_image")
        return None


##############################################################
# Support Staff Endpoints (Maintenance Users)
##############################################################
@frappe.whitelist()
def get_support_staff_list():
    """Return list of enabled Mobile Technicians for ticket assignment."""
    try:
        _require_maintenance_staff()

        rows = frappe.get_all(
            "Maintenance Users",
            filters={"enabled": 1, "role": "Mobile Technician"},
            fields=[
                "name",
                "employee",
                "employee_name",
                "user",
                "user_email",
                "role",
                "department",
                "company",
            ],
            order_by="employee_name asc",
        )
        return {"status": "success", "staff": rows}
    except frappe.PermissionError:
        return {"status": "error", "message": "Not permitted", "staff": []}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_support_staff_list")
        return {"status": "error", "message": str(e), "staff": []}


@frappe.whitelist()
def get_mentionable_support_staff(ticket_id=None):
    """Return maintenance mentionables for an issue (officer/manager, technician, subcontractor)."""
    try:
        _require_maintenance_staff()
        ticket_id = (ticket_id or "").strip()
        if not ticket_id:
            return {"status": "error", "message": "ticket_id is required", "support_staff": []}
        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found", "support_staff": []}

        issue = frappe.get_doc("Issue", ticket_id)
        support_staff = _get_maintenance_mentionable_users(issue, exclude_user=frappe.session.user)
        return {
            "status": "success",
            "ticket_id": ticket_id,
            "support_staff": support_staff,
            "total_count": len(support_staff),
        }
    except frappe.PermissionError:
        return {"status": "error", "message": "Not permitted", "support_staff": []}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_mentionable_support_staff")
        return {"status": "error", "message": str(e), "support_staff": []}


@frappe.whitelist(methods=["POST"])
def assign_ticket_to_staff(ticket_id=None, assigned_to=None):
    """Assign an Issue to a maintenance staff by setting Issue.person_in_charge (Employee).

    Body example:
    {
      "ticket_id": "ISS-2026-00713",
      "assigned_to": "Support"
    }

    NOTE: `assigned_to` must resolve to an Employee via Maintenance Users (docname or user_email) or be an Employee id.
    """
    try:
        _require_maintenance_staff()

        payload = _parse_request_payload({"ticket_id": ticket_id, "assigned_to": assigned_to})
        ticket_id = (payload.get("ticket_id") or payload.get("issue_id") or "").strip()
        assigned_to = (payload.get("assigned_to") or "").strip()

        if not ticket_id:
            return {"status": "error", "message": "ticket_id is required"}
        if not assigned_to:
            return {"status": "error", "message": "assigned_to is required"}
        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        employee = _resolve_maintenance_employee(assigned_to)
        if not employee:
            return {"status": "error", "message": "assigned_to not found in Maintenance Users / Employee"}

        issue = frappe.get_doc("Issue", ticket_id)
        issue.person_in_charge = employee
        issue.flags.ignore_mandatory = True
        issue.save(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Ticket assigned",
            "ticket_id": ticket_id,
            "person_in_charge": employee,
        }
    except frappe.PermissionError:
        return {"status": "error", "message": "Not permitted"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "assign_ticket_to_staff")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def check_person_in_charge(ticket_id=None):
    """Check whether an Issue has been assigned (person_in_charge set).

    POST body example:
    {
      "ticket_id": "ISS-2026-00713"
    }
    """
    try:
        _require_maintenance_staff()

        payload = _parse_request_payload({"ticket_id": ticket_id})
        ticket_id = (payload.get("ticket_id") or payload.get("issue_id") or "").strip()
        if not ticket_id:
            return {"status": "error", "message": "ticket_id is required"}

        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        person_in_charge = frappe.db.get_value("Issue", ticket_id, "person_in_charge")
        assigned = bool(person_in_charge)

        out = {
            "status": "success",
            "ticket_id": ticket_id,
            "is_assigned": assigned,
            "person_in_charge": person_in_charge,
        }
        # Include employee name if possible (nice for mobile UI)
        if person_in_charge and frappe.db.exists("Employee", person_in_charge):
            out["person_in_charge_name"] = frappe.db.get_value(
                "Employee", person_in_charge, "employee_name"
            )
        return out
    except frappe.PermissionError:
        return {"status": "error", "message": "Not permitted"}
    except frappe.AuthenticationError:
        return {"status": "error", "message": "Authentication failed"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "check_person_in_charge")
        return {"status": "error", "message": str(e)}


@frappe.whitelist()
def get_ticket_priorities(ticket_id=None):
    """Return all Issue Priority options and current Issue.priority for a ticket.

    Maintenance-only.
    """
    try:
        _require_maintenance_staff()

        ticket_id = (ticket_id or "").strip()
        if not ticket_id:
            return {"status": "error", "message": "ticket_id is required"}
        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        priorities = []
        try:
            priorities = frappe.get_all("Issue Priority", pluck="name", order_by="name asc")
        except Exception:
            # If Issue Priority doctype doesn't exist, fall back to Issue meta options
            meta = frappe.get_meta("Issue")
            if meta and meta.has_field("priority"):
                df = meta.get_field("priority")
                if getattr(df, "options", None):
                    priorities = [p for p in str(df.options).split("\n") if p.strip()]

        current_priority = frappe.db.get_value("Issue", ticket_id, "priority")
        return {
            "status": "success",
            "ticket_id": ticket_id,
            "priorities": priorities or [],
            "current_priority": current_priority,
        }
    except frappe.PermissionError:
        return {"status": "error", "message": "Not permitted"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_ticket_priorities")
        return {"status": "error", "message": str(e), "priorities": []}


@frappe.whitelist(methods=["POST"])
def change_ticket_priority(ticket_id=None, ticket_priority=None):
    """Change Issue.priority for a ticket.

    Body example:
    {
      "ticket_id": "ISS-2026-00711",
      "ticket_priority": "High"
    }

    Maintenance-only.
    """
    try:
        _require_maintenance_staff()

        payload = _parse_request_payload({"ticket_id": ticket_id, "ticket_priority": ticket_priority})
        ticket_id = (payload.get("ticket_id") or payload.get("issue_id") or "").strip()
        ticket_priority = (payload.get("ticket_priority") or payload.get("priority") or "").strip()

        if not ticket_id:
            return {"status": "error", "message": "ticket_id is required"}
        if not ticket_priority:
            return {"status": "error", "message": "ticket_priority is required"}
        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        # Validate priority value against Issue Priority doctype (if present)
        try:
            if frappe.db.table_exists("tabIssue Priority"):
                if not frappe.db.exists("Issue Priority", ticket_priority):
                    return {"status": "error", "message": "Invalid ticket_priority"}
        except Exception:
            pass

        issue = frappe.get_doc("Issue", ticket_id)
        issue.priority = ticket_priority
        issue.flags.ignore_mandatory = True
        issue.save(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Priority updated",
            "ticket_id": ticket_id,
            "ticket_priority": issue.priority,
        }
    except frappe.PermissionError:
        return {"status": "error", "message": "Not permitted"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "change_ticket_priority")
        return {"status": "error", "message": str(e)}


##############################################################
# Sub Contractor Assignment Endpoints (Maintenance-only)
##############################################################


def _resolve_supplier(val):
    """Resolve a Supplier by name or supplier_name."""
    if not val:
        return None
    v = str(val).strip()
    if not v:
        return None
    # By document name
    if frappe.db.exists("Supplier", v):
        return v
    # By supplier_name
    try:
        name = frappe.db.get_value("Supplier", {"supplier_name": v}, "name")
        if name:
            return name
    except Exception:
        pass
    return None


@frappe.whitelist()
def get_subcontractor_list():
    """Return list of subcontractor app users for assignment. Maintenance-only.

    Source of truth: DocType `Sub Contractor User` (enabled=1).
    """
    try:
        _require_officer_or_manager()

        rows = frappe.get_all(
            "Sub Contractor User",
            filters={"enabled": 1},
            fields=[
                "name",
                "sub_contractor",
                "sub_contractor_name",
                "full_name",
                "user_email",
                "user",
            ],
            order_by="sub_contractor_name asc, full_name asc",
        )
        return {"status": "success", "subcontractors": rows or []}
    except frappe.PermissionError:
        return {"status": "error", "message": "Not permitted", "subcontractors": []}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "get_subcontractor_list")
        return {"status": "error", "message": str(e), "subcontractors": []}


@frappe.whitelist(methods=["POST"])
def assign_subcontractor(ticket_id=None, assigned_to=None):
    """Assign a subcontractor to an Issue by setting Issue.sub_contractor (Supplier).

    Body example:
    {
      "ticket_id": "ISS-2026-00713",
      "assigned_to": "Some Supplier"
    }
    """
    try:
        _require_officer_or_manager()

        payload = _parse_request_payload({"ticket_id": ticket_id, "assigned_to": assigned_to})
        ticket_id = (payload.get("ticket_id") or payload.get("issue_id") or "").strip()
        assigned_to = (payload.get("assigned_to") or "").strip()

        if not ticket_id:
            return {"status": "error", "message": "ticket_id is required"}
        if not assigned_to:
            return {"status": "error", "message": "assigned_to is required"}
        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        supplier = _resolve_supplier(assigned_to)
        if not supplier:
            return {"status": "error", "message": "assigned_to not found in Supplier"}

        issue = frappe.get_doc("Issue", ticket_id)
        issue.sub_contractor = supplier
        issue.flags.ignore_mandatory = True
        issue.save(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status": "success",
            "message": "Subcontractor assigned",
            "ticket_id": ticket_id,
            "sub_contractor": supplier,
        }
    except frappe.PermissionError:
        return {"status": "error", "message": "Not permitted"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "assign_subcontractor")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(methods=["POST"])
def check_subcontractor(ticket_id=None):
    """Check whether an Issue has a subcontractor assigned (sub_contractor set). Maintenance-only."""
    try:
        _require_officer_or_manager()

        payload = _parse_request_payload({"ticket_id": ticket_id})
        ticket_id = (payload.get("ticket_id") or payload.get("issue_id") or "").strip()
        if not ticket_id:
            return {"status": "error", "message": "ticket_id is required"}
        if not frappe.db.exists("Issue", ticket_id):
            return {"status": "error", "message": "Issue not found"}

        sub_contractor = frappe.db.get_value("Issue", ticket_id, "sub_contractor")
        assigned = bool(sub_contractor)

        out = {
            "status": "success",
            "ticket_id": ticket_id,
            "is_assigned": assigned,
            "sub_contractor": sub_contractor,
        }
        if sub_contractor and frappe.db.exists("Supplier", sub_contractor):
            out["sub_contractor_name"] = frappe.db.get_value("Supplier", sub_contractor, "supplier_name")
        return out
    except frappe.PermissionError:
        return {"status": "error", "message": "Not permitted"}
    except frappe.AuthenticationError:
        return {"status": "error", "message": "Authentication failed"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "check_subcontractor")
        return {"status": "error", "message": str(e)}


##############################################################