# Staff Mobile Notifications API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Mobile Maintenance Manager / Officer create draft Mobile Notifications (with recipient preview + attachments) and submit them from the mobile app.

**Architecture:** New `propms/api/v1/notifications/staff.py` owns auth, validation, draft create, and submit. Thin wrappers on `propms/api/mobile.py` expose Flutter-friendly paths. Reuse DocType **Mobile Notifications** (`fetch_users` / `before_submit` → WebSocket + FCM). Attachments are file URLs from existing upload APIs.

**Tech Stack:** Frappe whitelist APIs, Mobile Notifications / Notified Users / Notifications Attachment DocTypes, existing FCM enqueue path.

**Spec:** `docs/superpowers/specs/2026-09-22-staff-mobile-notifications-design.md`

---

## File map

| File | Responsibility |
|------|----------------|
| Create: `propms/api/v1/notifications/staff.py` | Staff auth gate, `create_notification`, `submit_notification` |
| Modify: `propms/api/mobile.py` | Thin wrappers for both methods |
| Modify: `docs/postman/PropMS_Full_API.postman_collection.json` | Add create/submit under Notifications |
| Test: bench console / curl against site | Smoke auth, create, submit, deny tenant |

No DocType JSON changes. No new migrations.

---

### Task 1: Implement `staff.py` (create + submit)

**Files:**
- Create: `propms/api/v1/notifications/staff.py`
- Test: bench console / curl

- [ ] **Step 1: Confirm method paths are missing**

```bash
# From bench root, with site set (example):
bench --site dev15-viva2.vvsdtz.com execute \
  "print('ok')" 
# Then:
curl -s -b /tmp/staff_cookies.txt -X POST \
  "https://dev15-viva2.vvsdtz.com/api/method/propms.api.v1.notifications.staff.create_notification" \
  -H "Content-Type: application/json" \
  -d '{"subject":"t","message":"m","property":"SOME-PROPERTY"}'
```

Expected before code: import / method not found error.

- [ ] **Step 2: Create `staff.py` with full implementation**

Create `propms/api/v1/notifications/staff.py` with exactly this content:

```python
# Copyright (c) 2026, VV Systems Developer LTD and contributors
# For license information, please see license.txt

"""Staff APIs to create/submit Mobile Notifications from the mobile app."""

from __future__ import annotations

import json

import frappe
from frappe import _


DOCTYPE = "Mobile Notifications"
NOTIFICATION_STAFF_ROLES = (
	"Mobile Maintenance Manager",
	"Mobile Maintenance Officer",
)


def _require_notification_staff():
	"""Manager / Officer / System Manager only (not Technician / Tenant)."""
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)

	roles = frappe.get_roles(frappe.session.user)
	if "System Manager" in roles:
		return "System Manager"
	for role in NOTIFICATION_STAFF_ROLES:
		if role in roles:
			return role

	frappe.throw(_("Not permitted"), frappe.PermissionError)


def _parse_attachments(attachments):
	"""Normalize attachments from list or JSON string -> list[dict]."""
	if attachments is None or attachments == "":
		return []
	if isinstance(attachments, str):
		try:
			attachments = json.loads(attachments)
		except Exception:
			frappe.throw(_("Invalid attachments JSON"))
	if not isinstance(attachments, (list, tuple)):
		frappe.throw(_("attachments must be a list"))
	out = []
	for row in attachments:
		if not isinstance(row, dict):
			frappe.throw(_("Each attachment must be an object with title and attachment"))
		title = (row.get("title") or "").strip()
		file_url = (row.get("attachment") or row.get("file_url") or "").strip()
		if not title or not file_url:
			frappe.throw(_("Each attachment requires title and attachment (file URL)"))
		out.append({"title": title, "attachment": file_url})
	return out


def _serialize_preview(doc):
	recipients = [
		{"tenant": r.tenant, "read_status": getattr(r, "read_status", None) or "Unread"}
		for r in (doc.recipients or [])
		if getattr(r, "tenant", None)
	]
	attachments = [
		{"title": a.title, "attachment": a.attachment}
		for a in (doc.attachment or [])
		if getattr(a, "attachment", None)
	]
	return {
		"status": "success",
		"notification_id": doc.name,
		"docstatus": doc.docstatus,
		"subject": doc.subject,
		"message": doc.message,
		"company": doc.company,
		"property": doc.property,
		"customer": doc.customer,
		"category": getattr(doc, "category", None) or "Notice",
		"target_audience": getattr(doc, "target_audience", None) or "Everyone",
		"recipient_count": len(recipients),
		"recipients": recipients,
		"attachments": attachments,
	}


@frappe.whitelist(methods=["POST"])
def create_notification(
	subject=None,
	message=None,
	company=None,
	property=None,
	customer=None,
	category=None,
	target_audience=None,
	target_floor=None,
	target_unit=None,
	attachments=None,
):
	"""Create a draft Mobile Notification and return resolved recipients for preview."""
	_require_notification_staff()

	# Accept JSON body fields if form args empty
	if frappe.request and getattr(frappe.request, "is_json", False):
		payload = frappe.request.get_json(silent=True) or {}
		subject = subject if subject is not None else payload.get("subject")
		message = message if message is not None else payload.get("message")
		company = company if company is not None else payload.get("company")
		property = property if property is not None else payload.get("property")
		customer = customer if customer is not None else payload.get("customer")
		category = category if category is not None else payload.get("category")
		target_audience = (
			target_audience if target_audience is not None else payload.get("target_audience")
		)
		target_floor = target_floor if target_floor is not None else payload.get("target_floor")
		target_unit = target_unit if target_unit is not None else payload.get("target_unit")
		attachments = attachments if attachments is not None else payload.get("attachments")

	subject = (subject or "").strip()
	message = (message or "").strip()
	company = (company or "").strip() or None
	property = (property or "").strip() or None
	customer = (customer or "").strip() or None

	if not subject:
		return {"status": "error", "message": "subject is required"}
	if not message:
		return {"status": "error", "message": "message is required"}
	if not (company or property or customer):
		return {
			"status": "error",
			"message": "At least one of company, property, or customer is required",
		}

	att_rows = _parse_attachments(attachments)

	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"subject": subject,
			"message": message,
			"company": company,
			"property": property,
			"customer": customer,
			"category": category or "Notice",
			"target_audience": target_audience or "Everyone",
			"target_floor": target_floor,
			"target_unit": target_unit,
			"sender": frappe.session.user,
		}
	)
	for row in att_rows:
		doc.append("attachment", row)

	doc.insert(ignore_permissions=True)
	# before_save already ran fetch_users; reload child rows
	doc.reload()

	if not doc.recipients:
		frappe.delete_doc(DOCTYPE, doc.name, force=1, ignore_permissions=True)
		return {
			"status": "error",
			"message": "No recipients for these filters",
		}

	return _serialize_preview(doc)


@frappe.whitelist(methods=["POST"])
def submit_notification(notification_id=None):
	"""Submit a draft Mobile Notification (fires WebSocket + FCM via DocType hooks)."""
	_require_notification_staff()

	if frappe.request and getattr(frappe.request, "is_json", False):
		payload = frappe.request.get_json(silent=True) or {}
		notification_id = notification_id or payload.get("notification_id")

	notification_id = (notification_id or "").strip()
	if not notification_id:
		return {"status": "error", "message": "notification_id is required"}

	if not frappe.db.exists(DOCTYPE, notification_id):
		return {"status": "error", "message": "Notification not found"}

	doc = frappe.get_doc(DOCTYPE, notification_id)
	if doc.docstatus != 0:
		return {
			"status": "error",
			"message": "Notification is already submitted",
		}

	doc.submit()
	doc.reload()

	return {
		"status": "success",
		"notification_id": doc.name,
		"docstatus": doc.docstatus,
		"delivery": getattr(doc, "delivery", None) or "",
		"recipient_count": len(doc.recipients or []),
	}
```

- [ ] **Step 3: Smoke create as staff (Manager or Officer session)**

Login as a Manager/Officer user, then:

```bash
curl -s -c /tmp/staff_cookies.txt -b /tmp/staff_cookies.txt \
  -X POST "https://dev15-viva2.vvsdtz.com/api/method/login" \
  -H "Content-Type: application/json" \
  -d '{"usr":"<staff_email>","pwd":"<password>"}'

curl -s -b /tmp/staff_cookies.txt -X POST \
  "https://dev15-viva2.vvsdtz.com/api/method/propms.api.v1.notifications.staff.create_notification" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Water outage tonight",
    "message": "Maintenance from 8pm-10pm",
    "property": "<active_property_with_tenants>",
    "attachments": [{"title": "Notice", "attachment": "/files/example.pdf"}]
  }'
```

Expected: `message.status == "success"`, `docstatus == 0`, `recipient_count > 0`, recipients list present.  
If property has no active lease tenants: `status == "error"`, `No recipients for these filters`, and no leftover `Mobile Notifications` draft for that subject.

- [ ] **Step 4: Smoke deny tenant**

Login as a Mobile VIVA Tenant and call the same create URL.

Expected: Permission / Not permitted (HTTP 403 or thrown PermissionError).

- [ ] **Step 5: Commit**

```bash
git add propms/api/v1/notifications/staff.py
git commit -m "feat(notifications): add staff create/submit Mobile Notifications API"
```

---

### Task 2: Mobile wrappers

**Files:**
- Modify: `propms/api/mobile.py` (append after the Push Notification Wrappers section ~line 1394)
- Test: curl `propms.api.mobile.create_notification`

- [ ] **Step 1: Confirm mobile paths missing**

```bash
curl -s -b /tmp/staff_cookies.txt -X POST \
  "https://dev15-viva2.vvsdtz.com/api/method/propms.api.mobile.create_notification" \
  -H "Content-Type: application/json" \
  -d '{"subject":"t","message":"m","property":"X"}'
```

Expected before wrappers: method not found.

- [ ] **Step 2: Add wrappers to `mobile.py`**

Append after the existing push wrappers (after `enqueue_ticket_created_push`):

```python
@frappe.whitelist(methods=["POST"])
def create_notification(*args, **kwargs):
    from propms.api.v1.notifications.staff import create_notification as v1_create_notification
    return v1_create_notification(*args, **kwargs)


@frappe.whitelist(methods=["POST"])
def submit_notification(*args, **kwargs):
    from propms.api.v1.notifications.staff import submit_notification as v1_submit_notification
    return v1_submit_notification(*args, **kwargs)
```

Ensure `frappe` is already imported at top of `mobile.py` (it is).

- [ ] **Step 3: Smoke mobile create + submit**

```bash
# create
curl -s -b /tmp/staff_cookies.txt -X POST \
  "https://dev15-viva2.vvsdtz.com/api/method/propms.api.mobile.create_notification" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Lift maintenance",
    "message": "Lift B offline tomorrow",
    "property": "<active_property_with_tenants>"
  }'
# note notification_id from response, then:
curl -s -b /tmp/staff_cookies.txt -X POST \
  "https://dev15-viva2.vvsdtz.com/api/method/propms.api.mobile.submit_notification" \
  -H "Content-Type: application/json" \
  -d '{"notification_id":"<NTF-...>"}'
```

Expected create: success draft with recipients.  
Expected submit: `docstatus == 1`, `delivery` is `Sent` or `Failed`.  
Tenant `get_user_notifications` for a recipient should include the new notice after submit.

- [ ] **Step 4: Commit**

```bash
git add propms/api/mobile.py
git commit -m "feat(mobile): wrap staff notification create and submit"
```

---

### Task 3: Postman collection entries

**Files:**
- Modify: `docs/postman/PropMS_Full_API.postman_collection.json`
- Note: `docs/` is gitignored — use `git add -f` for this file if committing docs

- [ ] **Step 1: Locate folder `12. Notifications & FCM` → `Mobile / App wrappers`**

- [ ] **Step 2: Add two requests under Mobile wrappers** (same shape as existing items in that folder)

**create_notification**
- Method: `POST`
- URL: `{{base_url}}/api/method/propms.api.mobile.create_notification`
- Body (raw JSON):

```json
{
  "subject": "Water outage tonight",
  "message": "Maintenance from 8pm-10pm",
  "company": "{{company}}",
  "property": "{{property}}",
  "customer": "",
  "category": "Notice",
  "attachments": [
    { "title": "Notice", "attachment": "{{file_url}}" }
  ]
}
```

Description must mention: staff-only (Manager/Officer), creates draft, returns recipients.

**submit_notification**
- Method: `POST`
- URL: `{{base_url}}/api/method/propms.api.mobile.submit_notification`
- Body:

```json
{
  "notification_id": "{{notification_id}}"
}
```

- [ ] **Step 3: Add matching Direct v1 requests** under the v1 subfolder of Notifications:

- `propms.api.v1.notifications.staff.create_notification`
- `propms.api.v1.notifications.staff.submit_notification`

- [ ] **Step 4: Update folder description count** if it says “13 endpoints” — bump to include the 4 new entries (2 mobile + 2 v1), or rephrase to avoid a stale count.

- [ ] **Step 5: Commit**

```bash
git add -f docs/postman/PropMS_Full_API.postman_collection.json
git commit -m "docs(postman): add staff mobile notification create/submit"
```

---

### Task 4: End-to-end verification checklist

**Files:** none (verification only)

- [ ] **Step 1: Auth matrix**

| User | create | submit |
|------|--------|--------|
| Mobile Maintenance Manager | allowed | allowed |
| Mobile Maintenance Officer | allowed | allowed |
| Mobile Technician | denied | denied |
| Mobile VIVA Tenant | denied | denied |
| Guest | denied | denied |

- [ ] **Step 2: Zero recipients**

Create with a property/company that has no Active lease tenants → error, no draft left in list (`frappe.get_all("Mobile Notifications", {"subject": ...})` empty for that attempt).

- [ ] **Step 3: Double submit**

Submit same `notification_id` twice → second call returns already submitted error.

- [ ] **Step 4: Delivery path**

After successful submit, confirm:
- Doc `docstatus == 1`
- Recipient can call  
  `propms.property_management_solution.doctype.mobile_notifications.mobile_notifications.get_user_notifications`
  and see the notice
- Optional: User Device token present → FCM job enqueued (check Error Log / worker if push fails)

- [ ] **Step 5: Final commit only if fixes were needed during verification**

```bash
# only if code changed:
git add propms/api/v1/notifications/staff.py propms/api/mobile.py
git commit -m "fix(notifications): staff create/submit edge cases"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|------------------|------|
| Manager/Officer/System Manager only | Task 1 `_require_notification_staff` |
| Filters company/property/customer | Task 1 `create_notification` |
| Draft then submit | Task 1 create + submit |
| Recipients in create response | Task 1 `_serialize_preview` |
| No recipient edits | No update API (by design) |
| Attachments via file URLs | Task 1 `_parse_attachments` |
| Mobile wrappers | Task 2 |
| Reuse DocType submit delivery | Task 1 `doc.submit()` |
| Postman | Task 3 |
| Zero recipients → no orphan draft | Task 1 delete + Task 4 |
| Tenant denied | Task 1 Step 4 + Task 4 |

No TBD/placeholder steps remaining.
