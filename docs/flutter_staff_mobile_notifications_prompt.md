# Master AI Directive: Staff Mobile Notifications (Compose & Send)

> **Target Audience**: Cursor / Antigravity agent working on the **Viva Towers Flutter Mobile App**.  
> **Objective**: Build the **staff-only** UI and API integration so Mobile Maintenance **Manager / Officer** can compose a notice, preview resolved resident recipients, attach files, and send — without using Frappe Desk.  
> **Backend**: Already implemented on PropMS (`propms`). Do **not** invent new backend endpoints.

---

## 1. Scope & Roles

### Who sees this feature
Show the staff “Compose Notification” entry point **only** when the logged-in user’s roles include any of:
- `Mobile Maintenance Manager`
- `Mobile Maintenance Officer`
- `System Manager` (optional for QA)

**Hide** from: `Mobile VIVA Tenant`, `Mobile Technician`, `Mobile Sub Contractor`, Guest.

Use existing auth / `get_user_roles` (or whatever the app already uses for role gates). Do not hardcode emails.

### Out of scope (v1)
- Editing the recipient list (add/remove people)
- Saving drafts for later edit beyond the create→preview→send flow
- Staff inbox/history of sent notifications
- Changing tenant notification inbox behavior

---

## 2. Backend Base URL & Auth

- **Base URL**: `https://dev15-viva2.vvsdtz.com`
- **Prefer mobile facade**:
  - `POST /api/method/propms.api.mobile.create_notification`
  - `POST /api/method/propms.api.mobile.submit_notification`
- Equivalent v1 (same payloads):
  - `POST /api/method/propms.api.v1.notifications.staff.create_notification`
  - `POST /api/method/propms.api.v1.notifications.staff.submit_notification`
- Auth: existing Frappe session cookie / token used by the app (same as tickets).
- All responses are wrapped: `{ "message": { ... } }`. Read fields from `message`.

Upload attachments **before** create, using existing file APIs (reuse ticket upload patterns):
- `propms.api.mobile.upload_attachment` / `upload_mobile_image`, **or**
- Chunked: `start_upload_session` → `upload_chunk` → `finalize_upload`

Pass resulting **file URL** strings (e.g. `/files/...`) into create.

---

## 3. Product Flow (exact)

```
[Staff home] → Compose Notice
  → Form: subject, message, filters (company / property / customer), optional category, optional attachments
  → Tap "Preview recipients"
       → POST create_notification  (creates DRAFT on server)
       → Show recipient_count + list of tenant emails
       → If error "No recipients for these filters" → stay on form, change filters
  → Tap "Send"
       → POST submit_notification(notification_id)
       → Success → toast + navigate back
  → Tap "Cancel" on preview → discard UX-side; draft may remain on server (v1 has no cancel API — optional: just leave it)
```

**Do not** call submit until the user confirms the recipient list.

---

## 4. API Contracts

### 4.1 Create draft + preview — `create_notification`

**Method:** `POST`  
**Content-Type:** `application/json`

**Request body:**
```json
{
  "subject": "Water outage tonight",
  "message": "Maintenance from 8pm–10pm on Tower A.",
  "company": "",
  "property": "PROPERTY-NAME",
  "customer": "",
  "category": "Notice",
  "target_audience": "Everyone",
  "attachments": [
    { "title": "Notice PDF", "attachment": "/files/notice.pdf" }
  ]
}
```

| Field | Required | Notes |
|-------|----------|--------|
| `subject` | yes | Non-empty string |
| `message` | yes | Non-empty string |
| `company` | at least one of company / property / customer | Frappe Company name |
| `property` | | Property name |
| `customer` | | Customer (lease customer) name |
| `category` | no | Default `Notice`. Options: `Notice`, `Maintenance`, `Safety`, `Event`, `General`, `Emergency` |
| `target_audience` | no | Default `Everyone` |
| `target_floor` / `target_unit` | no | Only if you expose those UI fields |
| `attachments` | no | List of `{ title, attachment }` where `attachment` is a file URL |

**Success (`message.status == "success"`):**
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
    { "tenant": "resident@example.com", "read_status": "Unread" }
  ],
  "attachments": [
    { "title": "Notice PDF", "attachment": "/files/notice.pdf" }
  ]
}
```

**Errors (handle in UI):**
- `subject is required` / `message is required`
- `At least one of company, property, or customer is required`
- `No recipients for these filters` — show friendly copy: no active lease tenants match; change filters
- HTTP 403 / `Not permitted` — user is not Manager/Officer

Store `notification_id` in state for the send step.

---

### 4.2 Send — `submit_notification`

**Method:** `POST`  
**Body:**
```json
{
  "notification_id": "NTF-2026-00012"
}
```

**Success:**
```json
{
  "status": "success",
  "notification_id": "NTF-2026-00012",
  "docstatus": 1,
  "delivery": "Sent",
  "recipient_count": 12
}
```

**Errors:**
- `notification_id is required`
- `Notification not found`
- `Notification is already submitted` — treat as already done / disable Send

On success, residents receive:
- Realtime WebSocket event `notification_received` (tenant apps)
- FCM push with `data.type == "app_notification"` and `notification_id`

Staff compose UI does **not** need to listen for those events for v1.

---

## 5. Flutter UI Requirements

### Screens / steps
1. **Compose** — form with validation (subject, message, ≥1 filter).
2. **Preview** — show subject, message, attachment chips, `recipient_count`, scrollable recipient emails; primary CTA **Send**; secondary **Back** to edit filters (creating again is OK if they change filters — call create again with new filters; do not reuse old draft if filters changed).
3. Loading / error states for both API calls.

### Filter pickers
- Use existing property / company / customer data sources already in the app if available (desk Links are Frappe document names).
- Filters are **AND** when multiple are set (same as Desk).
- Prefer starting with **Property** as the primary filter for mobile UX if you only expose one field initially; still send empty string / omit unused keys.

### Attachments
- Allow 0..N files.
- Upload first → collect `{ title, attachment: fileUrl }`.
- `title` required by backend (use filename if user doesn’t enter one).

### Permissions UX
- If role gate fails when opening Compose → snackbar “Only maintenance managers and officers can send notices.”

### Design notes
- Follow existing Viva staff screens (tickets / maintenance) — don’t invent a new visual language.
- Preview must make it obvious this will notify **residents**, not staff.

---

## 6. Suggested Dart Models (minimal)

```dart
class StaffNotificationPreview {
  final String notificationId;
  final int docstatus;
  final String subject;
  final String message;
  final String? company;
  final String? property;
  final String? customer;
  final String category;
  final int recipientCount;
  final List<StaffNotificationRecipient> recipients;
  final List<StaffNotificationAttachment> attachments;

  // fromJson: read from response['message']
}

class StaffNotificationRecipient {
  final String tenant; // email
  final String readStatus;
}

class StaffNotificationAttachment {
  final String title;
  final String attachment; // file URL
}
```

API client methods:
```dart
Future<StaffNotificationPreview> createNotification({...});
Future<Map<String, dynamic>> submitNotification(String notificationId);
```

---

## 7. Acceptance Checklist

- [ ] Compose entry visible only for Manager / Officer (and SM if you include it)
- [ ] Tenant / Technician cannot open or successfully call create/submit
- [ ] Preview shows `recipient_count` and emails from create response
- [ ] Zero-recipient error is handled without calling submit
- [ ] Attachments upload then appear in preview
- [ ] Send calls submit with `notification_id`; success closes flow
- [ ] Double-tap Send does not crash (handle already submitted)
- [ ] Uses `propms.api.mobile.*` paths
- [ ] `flutter analyze` clean for touched files

---

## 8. Reference

| Item | Location |
|------|----------|
| Design spec | `docs/superpowers/specs/2026-09-22-staff-mobile-notifications-design.md` |
| Implementation plan | `docs/superpowers/plans/2026-09-22-staff-mobile-notifications.md` |
| Backend staff API | `propms/api/v1/notifications/staff.py` |
| Mobile wrappers | `propms/api/mobile.py` → `create_notification`, `submit_notification` |
| Tenant inbox (existing) | `Mobile Notifications` DocType methods `get_user_notifications`, `get_notification_details`, `mark_as_read` |
| Postman | Collection folder **12. Notifications & FCM** |

**Do not** call ticket push helpers (`enqueue_ticket_*_push`) for this feature — those are server-side workers, not staff compose APIs.
