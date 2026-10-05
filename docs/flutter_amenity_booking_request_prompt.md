# Master AI Directive: Amenity Booking Request + Auto-Approval — Flutter Update

> **Target Audience**: Cursor / Antigravity agent on the **Viva Towers Flutter Mobile App** (tenant + staff).  
> **Objective**: Update amenity booking UX for the new **Request-first** backend: Amenity `auto_approval`, create returns Request (+ optional Booking), staff approve/reject **Open Requests**, and My Bookings mixes Requests + Bookings.  
> **Backend**: **Already implemented** on PropMS. Do **not** invent endpoints or Desk-only Series child-table UI. Server is authoritative.  
> **Builds on:** `docs/flutter_amenity_range_booking_prompt.md` (day availability + Start/End UX still applies).

**Base URL:** `https://dev15-viva2.vvsdtz.com`  
**Prefer:** `propms.api.mobile.*`  
**Also OK:** `propms.api.amenities.*`  
**Response envelope:** `{ "message": { ... } }` — unwrap `message` before parsing.

**Backend design:** PropMS `docs/superpowers/specs/2026-10-05-amenity-booking-request-auto-approval-design.md`  
**Backend plan:** PropMS `docs/superpowers/plans/2026-10-05-amenity-booking-request-auto-approval.md`

---

## 0. Mission (read first)

| Do this | Do **not** do this |
|---------|-------------------|
| Prefer Amenity field `auto_approval` | Keep treating `requires_approval` as the source of truth |
| Parse create: `request_id`, `booking_id`, `request_status`, `auto_approved` | Assume create always returns a Confirmed booking id |
| Staff approve/reject **Request** ids (`VABR-…`) | Require a Confirmed Booking id to approve |
| My list: handle `kind: "request" \| "booking"` | Assume every row is an Amenity Booking |
| Open requests = pending-like UX (`display_status` / `status: Open`) | Hide Open rows or treat them as Confirmed |
| Cancel with `booking_id` **or** `request_id` | Build a Desk Series “Bookings” child-table screen |

---

## 1. Product model (what changed)

### Before (range-booking prompt)

- Amenity had `requires_approval` → create → Pending or Confirmed **Amenity Booking**.
- Staff approved/rejected Booking ids (`VAB-…`).
- My Bookings listed Bookings only.

### After (ship this)

1. **Amenity `auto_approval`** (shown when amenity requires booking):
   - `auto_approval == 1` → create Open Request **and** Confirmed Booking in one call (`auto_approved: true`).
   - `auto_approval == 0` → create **Open Request** only (`booking_id: null`); staff must approve.
2. **Request DocType** holds the slot while Open (blocks day availability like a booking).
3. **Staff queue** works on **Amenity Booking Request** ids; param alias `booking_id` still accepted for Flutter compat.
4. **My list** may mix Open (and other) Requests with Bookings via `kind`.

**Compat:** Some series responses still emit inverted `requires_approval` for older clients. Prefer `auto_approval` from `get_amenities` / `get_amenity_detail`, and `auto_approved` / `request_status` on create.

---

## 2. Screens to update

### 2.1 Amenity catalog & detail

- Models: replace `requiresApproval` with `autoApproval` (int 0/1 or bool).
- Book CTA copy:
  - `auto_approval == 1` → “Confirm booking”
  - `auto_approval == 0` → “Request booking”

### 2.2 Tenant — Book Range (same UX as range prompt)

- Still use `get_amenity_day_availability` + Start/End pickers.
- Busy may include Open Requests (`status: "Open"`, often `source: "request"`, `request_id` set) — treat as blocking.
- **Success UX:**
  - `auto_approved == true` → “Booking confirmed” (use `booking_id`).
  - Else → “Submitted — awaiting staff approval” (use `request_id`; status Open).

### 2.3 Tenant — My Bookings

- `get_my_amenity_bookings` rows include:
  - `kind`: `"request"` | `"booking"`
  - `request_id` / `booking_id` (request rows: `booking_id` may be null until Approved)
  - Open requests: `status: "Open"`; API may also set `display_status: "Pending"` — show as **Pending** badge.
- Filters: `pending` includes Open Requests; keep existing chips.
- Cancel: pass `request_id` for Open requests, or `booking_id` for Confirmed bookings (either param name accepted for Requests).

### 2.4 Staff — Pending queue

- Default tab: Open / Pending requests.
- Approve → `approve_amenity_booking` with `request_id` (or legacy `booking_id` = Request name).
- Reject → require `rejection_reason` → `reject_amenity_booking`.
- Success approve returns new Confirmed `booking_id` + `request_id`.

### 2.5 Series (if app already has recurring)

- Same Request-first semantics; create returns `request_ids`, `booking_ids`, `auto_approved`.
- **Do not** build UI for Series Desk child table `bookings` — staff Desk only / backend sync.

### 2.6 Realtime

Same events as range prompt; payloads may include `request_id` and/or `booking_id`. Refresh lists + day availability on any of them.

---

## 3. API contracts (delta)

All via `POST` (or GET where noted) `/api/method/propms.api.mobile.<method>`.

### 3.1 Catalog — `auto_approval`

`get_amenities` / `get_amenity_detail` include `auto_approval` (0|1). Drop client dependency on `requires_approval`.

### 3.2 `create_amenity_booking`

Same inputs as before (`amenity`, `booking_date`, `start_time`, `end_time`, optional guests/notes/lease/unit).

**Success (`message`):**

```json
{
  "status": "success",
  "message": "…",
  "request_id": "VABR-2026-00001",
  "booking_id": "VAB-2026-00010",
  "request_status": "Approved",
  "auto_approved": true,
  "doc": { }
}
```

| Field | When `auto_approval=1` | When `auto_approval=0` |
|-------|------------------------|------------------------|
| `request_id` | always | always |
| `booking_id` | Confirmed Booking name | `null` |
| `request_status` | `"Approved"` | `"Open"` |
| `auto_approved` | `true` | `false` |
| `doc` | Booking dict | Request dict |

**Overlap errors** unchanged (`conflict_booking_id`, `conflict_start`, `conflict_end`) — Open Requests also conflict.

### 3.3 `approve_amenity_booking` (staff)

Prefer:

```json
{ "request_id": "VABR-2026-00001" }
```

Still accepted:

```json
{ "booking_id": "VABR-2026-00001" }
```

### 3.4 `reject_amenity_booking` (staff)

```json
{ "request_id": "VABR-2026-00001", "rejection_reason": "Hall reserved for building event" }
```

(`booking_id` alias OK.) Empty reason → error.

### 3.5 `get_my_amenity_bookings`

Same filters as before. Each row:

```json
{
  "kind": "request",
  "name": "VABR-2026-00001",
  "request_id": "VABR-2026-00001",
  "booking_id": null,
  "status": "Open",
  "display_status": "Pending",
  "amenity": "…",
  "booking_date": "2026-10-05",
  "start_time": "14:00:00",
  "end_time": "17:00:00"
}
```

Booking rows: `kind: "booking"`, `booking_id` = `name`, `request_id` usually null.

### 3.6 `cancel_amenity_booking`

```json
{ "request_id": "VABR-2026-00001" }
```

or

```json
{ "booking_id": "VAB-2026-00010", "cancellation_reason": "optional" }
```

`booking_id` may also be a Request name (compat).

### 3.7 Day availability (unchanged path)

`get_amenity_day_availability` — busy intervals may include Open Requests (`request_id`, `status: "Open"`). Prefer amenity list/detail for `auto_approval` when choosing Confirm vs Request copy.

---

## 4. Dart model deltas (suggested)

```dart
// Amenity: prefer this over requiresApproval
final bool autoApproval; // from auto_approval 0|1

class AmenityCreateResult {
  final String requestId;
  final String? bookingId;
  final String requestStatus; // Open | Approved | …
  final bool autoApproved;
}

class AmenityListItem {
  final String kind; // request | booking
  final String? requestId;
  final String? bookingId;
  final String status;
  final String? displayStatus; // Pending for Open requests
  // … existing amenity/date/time/tenant fields
}
```

Approve/reject/cancel: send `requestId` when `kind == request` or after create with `autoApproved == false`.

---

## 5. Postman / API note

Collection folder: **Amenities** (mobile). Useful methods — no new path names; semantics changed:

| Method | Notes |
|--------|--------|
| `propms.api.mobile.get_amenities` | Field `auto_approval` |
| `propms.api.mobile.get_amenity_detail` | Field `auto_approval` |
| `propms.api.mobile.get_amenity_day_availability` | Busy includes Open Requests |
| `propms.api.mobile.create_amenity_booking` | Returns `request_id` + `booking_id` + `auto_approved` |
| `propms.api.mobile.get_my_amenity_bookings` | Mixed list; `kind` |
| `propms.api.mobile.cancel_amenity_booking` | `booking_id` or `request_id` |
| `propms.api.mobile.approve_amenity_booking` | Target Request id (`request_id` or alias `booking_id`) |
| `propms.api.mobile.reject_amenity_booking` | Same + `rejection_reason` |
| `propms.api.mobile.create_recurring_amenity_booking` | Request-first; `request_ids` / `booking_ids` |

Same handlers also under `propms.api.amenities.*` if the collection uses that namespace.

---

## 6. Implementation checklist

1. Amenity models: `auto_approval`; update Confirm vs Request copy.  
2. Create response parser: never treat missing `booking_id` as failure when `auto_approved == false`.  
3. My Bookings: render `kind`; Pending chip includes Open; cancel uses correct id.  
4. Staff queue: approve/reject Request ids; keep `booking_id` param fallback.  
5. Day strip: Open request busy blocks like Confirmed.  
6. Series: only if already in app — no Desk child-table screen.  
7. Regression: range book UX, overlap errors, cancel window, WS/FCM refresh.

---

## 7. Acceptance criteria

- [ ] Amenity detail uses **`auto_approval`**, not `requires_approval`, for CTA copy.  
- [ ] Create with auto on → toast confirmed + navigate with `booking_id`.  
- [ ] Create with auto off → awaiting approval + store `request_id`; My List shows Open/Pending.  
- [ ] Staff Approve Open Request → Confirmed Booking; tenant list updates.  
- [ ] Staff Reject with reason → Rejected; reason visible.  
- [ ] Cancel Open Request and Confirmed Booking both work.  
- [ ] No Flutter UI for Series Desk **Bookings** child table.  
- [ ] Day availability still drives Start/End (no slot chips).

---

## 8. Out of scope

- Desk Amenity / Request / Series form work  
- Series child-table editing in mobile  
- Paid amenity checkout  
- Changing day-availability hybrid UX (already shipped)

---

## 9. Definition of done

Flutter treats create as Request-first, shows Open requests in My Bookings, staff approve/reject by Request id (with `booking_id` alias), and uses Amenity `auto_approval` for messaging — without inventing Desk Series child-table UI.
