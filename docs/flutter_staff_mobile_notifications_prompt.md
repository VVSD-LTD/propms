# Master AI Directive: Staff Mobile Notifications — UX & API Update

> **Target Audience**: Cursor agent on the **Viva Towers Flutter** app.  
> **Objective**: Update the staff Notices feature to match backend changes: **no Company field**, **Customer dropdown**, **Property filtered by Customer**, and a **staff list/detail** of created notices.  
> **Backend**: Already updated on PropMS. Do **not** invent endpoints.

**Base URL:** `https://dev15-viva2.vvsdtz.com`  
Prefer `propms.api.mobile.*`. Response envelope: `{ "message": { ... } }`.

---

## 1. What changed (replace previous compose UX)

| Before | After |
|--------|--------|
| Show Company / Property / Customer filters | **Do not show Company** (server auto-fills) |
| Customer typed as free text | **Customer dropdown** from API (`name` + `customer_name`) |
| Property independent | **Property dropdown loads only after Customer**; filtered by that customer |
| Create/send only | **List + detail** of notices staff created (drafts + sent) |

**Roles (unchanged):** show only for `Mobile Maintenance Manager` / `Mobile Maintenance Officer` (and System Manager if you already gate QA that way).

---

## 2. Screens

### A) Staff Notices list (new home for the feature)
- Entry: “Notices” / “Resident Notices”
- Load: `list_staff_notifications`
- Tabs or filter chips: **All** / **Draft** / **Submitted** → `status=all|draft|submitted`
- Row: subject, customer, property, `status_label`, `creation`, `recipient_count`
- Tap row → Detail
- FAB / “+” → Compose

### B) Detail
- Load: `get_staff_notification(notification_id)`
- Show subject, message, customer, property, category, delivery, recipients, attachments
- If `docstatus == 0` (Draft): show **Send** → `submit_notification`
- If already submitted: hide Send

### C) Compose (updated)
1. Subject, message, category (optional), attachments (optional)
2. **Customer** — searchable dropdown (required)
3. **Property** — searchable dropdown (optional), enabled only after Customer selected; reload when Customer changes; clear Property when Customer changes
4. Preview recipients → `create_notification`
5. Confirm → `submit_notification` → then open Detail or List

**Do not** show a Company field.

---

## 3. API contracts

All staff-only. Strip/ignore `cmd` is handled server-side.

### 3.1 `get_notification_customers`
`GET|POST /api/method/propms.api.mobile.get_notification_customers`

Params: `search` (optional), `limit`, `offset`

```json
{
  "status": "success",
  "customers": [
    { "name": "CUST-0001", "customer_name": "Acme Ltd" }
  ],
  "total_count": 42,
  "has_more": true
}
```

**Flutter:** display `customer_name`, submit `name` as `customer`.

---

### 3.2 `get_notification_properties`
`GET|POST /api/method/propms.api.mobile.get_notification_properties`

Params: `customer` (**required**, Customer `name`), `search`, `limit`, `offset`

```json
{
  "status": "success",
  "customer": "CUST-0001",
  "properties": [
    { "name": "A-101", "property_name": "Apartment 101" }
  ],
  "total_count": 3,
  "has_more": false
}
```

**Flutter:** display `property_name`, submit `name` as `property`. If Customer empty → empty list / disabled control.

---

### 3.3 `list_staff_notifications`
`GET|POST /api/method/propms.api.mobile.list_staff_notifications`

Params: `status` = `all` | `draft` | `submitted`, `limit`, `offset`

```json
{
  "status": "success",
  "notifications": [
    {
      "notification_id": "NTF-2026-00012",
      "subject": "Water outage",
      "message": "...",
      "customer": "CUST-0001",
      "property": "A-101",
      "category": "Notice",
      "docstatus": 1,
      "delivery": "Sent",
      "sender": "officer@example.com",
      "creation": "2026-09-23 10:00:00.000000",
      "recipient_count": 12,
      "status_label": "Sent"
    }
  ],
  "total_count": 10,
  "has_more": false
}
```

`docstatus`: `0` = Draft, `1` = Submitted, `2` = Cancelled.

---

### 3.4 `get_staff_notification`
`GET|POST /api/method/propms.api.mobile.get_staff_notification`

Params: `notification_id`

Returns same shape as create preview (includes `recipients`, `attachments`, `delivery`, etc.).

---

### 3.5 `create_notification` (updated body)
`POST /api/method/propms.api.mobile.create_notification`

```json
{
  "subject": "Water outage tonight",
  "message": "Maintenance 8pm–10pm",
  "customer": "CUST-0001",
  "property": "A-101",
  "category": "Notice",
  "attachments": [
    { "title": "Notice PDF", "attachment": "/files/notice.pdf" }
  ]
}
```

- **Required:** `subject`, `message`, `customer` (Customer **name**)
- **Optional:** `property` (must belong to that customer’s Active leases), `category`, `attachments`
- **Do not send `company`** (ignored; server sets default)

Errors to handle:
- `customer is required`
- `Invalid customer: ...`
- `Property does not belong to the selected customer (Active lease)`
- `No recipients for these filters`

---

### 3.6 `submit_notification` (unchanged)
`POST /api/method/propms.api.mobile.submit_notification`  
Body: `{ "notification_id": "NTF-..." }`

---

## 4. UX rules (must implement)

1. Customer picker uses **API list**, not free-text typing of IDs.
2. Changing Customer **clears** Property and reloads property options.
3. Property picker disabled until Customer is selected.
4. After successful Send, refresh list (or navigate to detail showing `delivery: Sent`).
5. Drafts from abandoned Preview may appear under Draft — allow opening and Send from Detail.
6. Keep existing attachment upload flow (upload first → pass file URLs).

---

## 5. Acceptance checklist

- [ ] No Company field anywhere in staff Notices UI
- [ ] Customer is a searchable dropdown from `get_notification_customers`
- [ ] Property options come from `get_notification_properties` for selected customer only
- [ ] List shows drafts + submitted notices via `list_staff_notifications`
- [ ] Detail + Send for drafts works
- [ ] Create requires Customer document `name`, not display label alone
- [ ] Tenant / Technician cannot access these endpoints / screens
- [ ] `flutter analyze` clean for touched files

---

## 6. Method path cheat-sheet

| Action | Method |
|--------|--------|
| Customers dropdown | `propms.api.mobile.get_notification_customers` |
| Properties dropdown | `propms.api.mobile.get_notification_properties` |
| Staff list | `propms.api.mobile.list_staff_notifications` |
| Staff detail | `propms.api.mobile.get_staff_notification` |
| Create draft / preview | `propms.api.mobile.create_notification` |
| Send | `propms.api.mobile.submit_notification` |
