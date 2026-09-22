# Staff Mobile Notifications API — Design

**Date:** 2026-09-22  
**Status:** Approved for planning  
**Approach:** v1 module + mobile wrappers (reuse Mobile Notifications DocType)

## Goal

Allow **Mobile Maintenance Manager / Officer** (and System Manager) to create and send resident notifications from the mobile app, without using the Frappe Desk UI.

## Non-goals (v1)

- Recipient add/remove after create (filters define recipients)
- Edit draft body or cancel/list endpoints
- Changing tenant inbox APIs or Desk DocType behavior
- New DocTypes or schema migrations

## Decisions

| Topic | Choice |
|-------|--------|
| Who can create/send | Mobile Maintenance Manager, Mobile Maintenance Officer, System Manager |
| Targeting | Same as Desk: Company and/or Property and/or Customer (AND when multiple set) |
| Lifecycle | Draft first, then explicit submit/send |
| Preview | `create_notification` returns resolved recipients in the same response |
| Recipient edits | None — change filters and create again |
| Attachments | Supported via already-uploaded file URLs |
| API surface | `create_notification` + `submit_notification` only |

## Architecture

```
Mobile app (staff)
  → propms.api.mobile.create_notification / submit_notification
  → propms.api.v1.notifications.staff.*
  → DocType "Mobile Notifications"
       before_save → fetch_users() (lease Tenant Details emails)
       before_submit → WebSocket notification_received + FCM enqueue_app_notification_push
```

- **New file:** `propms/api/v1/notifications/staff.py`
- **Wrappers:** `propms/api/mobile.py` (thin pass-through)
- **Reuse:** existing recipient resolution, submit delivery, User Device / FCM stack
- **Uploads:** existing chunked upload / `upload_attachment`; pass resulting URLs into create

## Endpoints

### `create_notification`

**Paths:**  
`propms.api.v1.notifications.staff.create_notification`  
`propms.api.mobile.create_notification`

**Auth:** Manager / Officer / System Manager only.

**Inputs:**

| Param | Required | Notes |
|-------|----------|--------|
| `subject` | yes | |
| `message` | yes | |
| `company` | at least one of company/property/customer | Link Company |
| `property` | | Link Property |
| `customer` | | Link Customer (lease_customer) |
| `category` | no | Default `Notice` |
| `target_audience` | no | Default `Everyone` |
| `target_floor` | no | When audience is Specific Floor |
| `target_unit` | no | When audience is Specific Unit |
| `attachments` | no | JSON list `[{ "title", "attachment" }]` — `attachment` is file URL |

**Behavior:**

1. Reject non-staff.
2. Validate subject, message, and at least one scope filter.
3. Insert Mobile Notifications draft (`docstatus=0`), `sender` = session user.
4. Append attachment child rows if provided.
5. Save → `fetch_users()` populates Notified Users from Active leases.
6. If zero recipients: delete the draft (or roll back) and return error — do not leave an orphan draft.
7. Return preview payload including recipients.

**Success response:**

```json
{
  "status": "success",
  "notification_id": "NTF-2026-00012",
  "docstatus": 0,
  "subject": "...",
  "message": "...",
  "company": "...",
  "property": "...",
  "customer": "...",
  "category": "Notice",
  "target_audience": "Everyone",
  "recipient_count": 12,
  "recipients": [
    { "tenant": "a@example.com", "read_status": "Unread" }
  ],
  "attachments": [
    { "title": "Notice PDF", "attachment": "/files/notice.pdf" }
  ]
}
```

### `submit_notification`

**Paths:**  
`propms.api.v1.notifications.staff.submit_notification`  
`propms.api.mobile.submit_notification`

**Auth:** Same staff gate.

**Inputs:** `notification_id` (required)

**Behavior:**

1. Reject non-staff.
2. Load doc; must exist and `docstatus == 0`.
3. Call `doc.submit()` — existing `before_submit` requires recipients and runs WebSocket + FCM.
4. Return delivery status.

**Success response:**

```json
{
  "status": "success",
  "notification_id": "NTF-2026-00012",
  "docstatus": 1,
  "delivery": "Sent",
  "recipient_count": 12
}
```

## Auth helper

Staff-only check (not Technician):

- Allowed roles: `Mobile Maintenance Manager`, `Mobile Maintenance Officer`, `System Manager`
- Guest / Tenant / Technician / Sub Contractor → not permitted

Prefer a dedicated `_require_notification_staff()` in `staff.py` (or shared auth helper) so ticket Technician access is not reused by mistake.

## Error cases

| Case | Response |
|------|----------|
| Not authenticated | Authentication error |
| Wrong role | Not permitted |
| Missing subject/message | Validation error |
| No company/property/customer | Validation error |
| Zero recipients after resolve | Error; no leftover draft |
| Submit missing id / not found | Error |
| Submit already submitted | Error |
| Attachment missing title or URL | Validation error |

## Mobile app flow

1. Staff uploads files (existing upload APIs) → file URLs.
2. `create_notification` with subject, message, filters, attachment URLs.
3. App shows `recipients` / `recipient_count`.
4. Staff confirms → `submit_notification(notification_id)`.
5. Residents receive `notification_received` + FCM `type: app_notification`; existing tenant list/detail/mark-read APIs unchanged.

## Verification

- Officer/Manager create draft with filters → non-empty recipients + attachments in response.
- Tenant (and Technician) cannot create or submit.
- Submit sets `docstatus=1`, `delivery` Sent/Failed; tenants see notification via existing APIs / push.
- Postman: add both methods under Notifications (mobile + v1 paths).

## Implementation notes

- `ignore_permissions=True` on insert/submit only after staff gate (DocType permissions are System Manager–centric today).
- Parse `attachments` if sent as JSON string (mobile form data).
- Keep wrappers thin; business logic lives in `staff.py`.
