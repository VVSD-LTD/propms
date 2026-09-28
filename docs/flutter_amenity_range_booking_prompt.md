# Master AI Directive: Amenity Exclusive Range Booking — Flutter Implementation

> **Target Audience**: Cursor / Antigravity agent working on the **Viva Towers Flutter Mobile App** (tenant + staff).  
> **Objective**: Replace the old **fixed time-slot chips** amenity booking UX with **exclusive free-form Start→End** booking driven by day availability. Implement tenant book/list/cancel and staff pending approve/reject, including WebSocket + FCM refresh.  
> **Backend**: **Already implemented** on PropMS (`dev15-viva2.vvsdtz.com`). Do **not** invent endpoints, fields, or client-only overlap rules. Server is authoritative.

**Base URL:** `https://dev15-viva2.vvsdtz.com`  
**Prefer:** `propms.api.mobile.*`  
**Also OK:** `propms.api.amenities.*` (same handlers)  
**Response envelope:** Frappe wraps payloads as `{ "message": { ... } }` — unwrap `message` before parsing.

**Backend design:** PropMS `docs/superpowers/specs/2026-09-28-amenity-range-booking-design.md`  
**Backend plan:** PropMS `docs/superpowers/plans/2026-09-28-amenity-range-booking.md`

---

## 0. Mission (read first)

| Do this | Do **not** do this |
|---------|-------------------|
| Rebuild book screen around **day availability** | Drive Party Hall / exclusive amenities from `get_available_slots` chips |
| Snap Start/End to `booking_time_step_mins` | Invent 15-min steps when API says 30 |
| Treat **Pending + Confirmed** as busy (blocking) | Allow overlapping ranges client-side “because UI looks free” |
| Paint busy from `start_time` → `end_time` (no grace after end) | Invent a cleanup/grace gap after bookings |
| Staff: Pending queue + approve/reject | Let tenants call approve/reject |
| Guests = informational field only | Enforce capacity / shared occupancy math |
| Cancel + rebook to change times | Build “edit booking” that PATCHes times |
| Same-day only | Overnight / multi-day ranges |

---

## 1. Product model (what changed)

### Before (wrong for Party Hall)

- Amenity had `slot_duration_mins` → API generated fixed chips (`get_available_slots`).
- Overlap used **shared capacity** (multiple bookings could overlap until guest count filled).

### After (ship this)

- Tenant picks **date**, sees **busy bars + free gaps**, then sets **Start** and **End**.
- One **Pending** or **Confirmed** booking **owns** the amenity for that window.
- Amenity policy fields (from detail / day availability):
  - `booking_time_step_mins` — picker step (e.g. 30)
  - `cleanup_buffer_mins` — **always 0** (no grace after end; next booking may start at previous `end_time`). Field kept for API compat; ignore for UX.
  - `requires_approval` — create → `Pending` vs `Confirmed`
  - `cancel_before_hours` — tenant cancel deadline before start
  - `open_time` / `close_time` / `max_advance_days`
  - `capacity` — **display only** (not enforced)

**Deprecate for exclusive book UX:** `propms.api.mobile.get_available_slots`  
Keep the method in the API client if other legacy code calls it, but **new book flow must not use it**.

---

## 2. Roles & feature gates

| Role | Book | See busy labels | Approve / Reject | Cancel any | See all tenants’ bookings |
|------|------|-----------------|------------------|------------|---------------------------|
| Mobile VIVA Tenant | Yes (own) | Anonymized `"Booked"` | No | Own, within cancel window | No (own only) |
| Mobile Maintenance Manager / Officer | Rare | Tenant name on busy | Yes | Yes | Yes (`is_staff` / `scope=all_tenants`) |
| System Manager | Yes | Tenant name | Yes | Yes | Yes |

Detect staff the same way you already do for maintenance (role list). If `get_amenity_day_availability` returns `is_staff: true`, trust that for busy labels.

---

## 3. Screens to implement / modify

### 3.1 Amenity catalog & detail (update models)

- `get_amenities` / `get_amenity_detail` now include policy fields:
  - `booking_time_step_mins`, `cleanup_buffer_mins`, `requires_approval`, `cancel_before_hours`
  - Keep showing `capacity`, floor, facilities, gallery as today.
- Detail CTA **Book** → new **Book Range** screen §3.2 (not slot picker).

### 3.2 Tenant — Book Range (NEW primary screen)

**Layout (hybrid UX):**

1. **Date** picker (disable past; clamp to `max_advance_days` from amenity / error messages).
2. On date change → call `get_amenity_day_availability`.
3. **Day strip / busy timeline**
   - Open–close as full bar.
   - Paint each `busy[]` from `start_time` → `end_time` (back-to-back OK; `end_with_buffer` equals `end_time` — no grace period).
   - Tenant: show `label` (usually `"Booked"`). Staff: may show tenant name.
4. **Free gaps** list or tappable regions — tap → prefill Start/End.
5. **Start time** / **End time** pickers
   - Only times aligned to `booking_time_step_mins`.
   - Within `open_time`–`close_time`.
   - End must be after Start.
6. Optional **Guests** (Int) + **Notes**.
7. Optional unit/lease if your app already passes them for multi-lease tenants.
8. Primary button **Request booking** / **Confirm booking** (copy depends on `requires_approval`).
9. Loading / error / success states.

**Success UX:**

- `requires_approval == 1` → “Submitted — awaiting staff approval” (status Pending).
- Else → “Booking confirmed”.
- Navigate to My Bookings detail or list.

**Error UX (map API `message` string + fields):**

| Situation | UI |
|-----------|-----|
| Overlap | “Overlaps existing booking {conflict_start}–{conflict_end}” + reload day availability |
| Step | “Times must be on {step}-minute steps” |
| Hours | “Must be within open hours …” |
| Past start | “Cannot book a time that has already started” |
| Advance | Max advance message from API |
| Unpublished / inactive | Show API message |

### 3.3 Tenant — My Bookings (UPDATE)

- `get_my_amenity_bookings` with filters:
  - `status`: `all` | `pending` | `confirmed` | `upcoming` | `completed` | `cancelled` | `rejected` | `no show`
  - Optional: `amenity`, `booking_date`, `booking_date_from`, `booking_date_to`, `page`, `page_length`
- Chips/tabs for statuses including **Pending** and **Rejected**.
- Row: amenity name, date, start–end, status badge, unit.
- Detail: notes, guests, `rejection_reason` (if Rejected), `approved_by` / `approved_on` if present.
- **Cancel** button:
  - Visible for Pending/Confirmed when tenant and before cancel deadline.
  - If API returns cancel-too-late error, show toast and hide/disable.
  - Optional: hide Cancel when `now >= start - cancel_before_hours` using amenity policy if you cached it; still trust API.

**No edit times** — cancel + create new booking.

### 3.4 Staff — Bookings / Pending queue (NEW or extend)

- Same `get_my_amenity_bookings` — staff get **all tenants** (`is_staff: true`, `scope: "all_tenants"`).
- Default filter or tab: **Pending**.
- Row actions:
  - **Approve** → `approve_amenity_booking`
  - **Reject** → dialog requiring `rejection_reason` → `reject_amenity_booking`
  - **Cancel** → `cancel_amenity_booking` (optional reason)
- Building calendar / day view can reuse day availability with staff labels.

### 3.5 Realtime

On events below: invalidate day availability cache for that date/amenity; refresh lists.

| Event | Who typically gets it | App action |
|-------|----------------------|------------|
| `amenity_booked` (also `amenity_booking_created`, `new_amenity_booking`) | Staff | Refresh Pending / calendar |
| `amenity_booking_cancelled` | Staff or tenant | Refresh |
| `amenity_booking_approved` | Tenant | Update booking status → Confirmed |
| `amenity_booking_rejected` | Tenant | Update → Rejected; show reason if in payload |

FCM data payload uses `type` / `notification_type` / `update_type` equal to those event names; includes `booking_id`, `amenity`, `amenity_name`, times, `route`: `/amenity_bookings`.

Subscribe to user room as you already do via `initialize_app_websocket`. Staff also join amenity rooms (`amenities`, `amenity_bookings`, …) from backend init — listen for the same event names.

---

## 4. API contracts (exact)

All JSON POST bodies work; GET query params also OK where noted.

### 4.1 `get_amenity_day_availability` ⭐ primary

`GET|POST /api/method/propms.api.mobile.get_amenity_day_availability`

```json
{ "amenity": "Pool Side Private Party", "booking_date": "2026-10-05" }
```

**Success (`message`):**

```json
{
  "status": "success",
  "amenity": "Pool Side Private Party",
  "amenity_name": "Pool Side Private Party",
  "booking_date": "2026-10-05",
  "open_time": "06:00:00",
  "close_time": "22:00:00",
  "booking_time_step_mins": 30,
    "cleanup_buffer_mins": 0,
  "requires_approval": 1,
  "cancel_before_hours": 2,
  "capacity": 50,
  "busy": [
    {
      "booking_id": "VAB-2026-00010",
      "start_time": "18:00:00",
      "end_time": "21:00:00",
        "end_with_buffer": "21:00:00",
      "status": "Pending",
      "label": "Booked"
    }
  ],
  "free_gaps": [
    { "start_time": "06:00:00", "end_time": "18:00:00" },
      { "start_time": "21:00:00", "end_time": "22:00:00" }
  ],
  "is_staff": false
}
```

Times are `"HH:MM:SS"`. Parse flexibly if you get `"HH:MM"`.

### 4.2 `create_amenity_booking`

`POST /api/method/propms.api.mobile.create_amenity_booking`

```json
{
  "amenity": "Pool Side Private Party",
  "booking_date": "2026-10-05",
  "start_time": "14:00:00",
  "end_time": "17:00:00",
  "guests_count": 20,
  "notes": "Birthday",
  "lease": optional,
  "property_unit": optional
}
```

**Success:** `status`, `message`, `booking_id`, `doc` (includes `status` Pending|Confirmed).

**Overlap error (must handle):**

```json
{
  "status": "error",
  "message": "This time overlaps an existing booking (18:00:00–21:00:00).",
  "conflict_booking_id": "VAB-2026-00010",
  "conflict_start": "18:00:00",
  "conflict_end": "21:00:00"
}
```

### 4.3 `cancel_amenity_booking`

```json
{ "booking_id": "VAB-2026-00010", "cancellation_reason": "optional" }
```

Tenant too late → error like:  
`Cancellation is only allowed until 2 hour(s) before start.`

### 4.4 `approve_amenity_booking` (staff)

```json
{ "booking_id": "VAB-2026-00010" }
```

### 4.5 `reject_amenity_booking` (staff)

```json
{ "booking_id": "VAB-2026-00010", "rejection_reason": "Hall reserved for building event" }
```

Empty reason → error `rejection_reason is required`.

### 4.6 `get_my_amenity_bookings`

```json
{
  "status": "pending",
  "page": 1,
  "page_length": 20,
  "amenity": null,
  "booking_date": null,
  "booking_date_from": null,
  "booking_date_to": null
}
```

Expect list of bookings with at least:  
`name`, `amenity`, `amenity_name`, `booking_date`, `start_time`, `end_time`, `status`, `guests_count`, `tenant`, `tenant_name`, `property_unit`, `lease`, `notes`, `cancellation_reason`, `rejection_reason`, `approved_by`, `approved_on`, cover/category/floor enrichments.

Also: `is_staff`, `scope`, pagination, optional status summary counts.

### 4.7 Catalog (unchanged paths, richer fields)

- `get_amenities` / `get_amenity_detail` — include new policy fields in your Dart models.
- Staff may use `set_amenity_published` if you already have publish UI (out of scope for this booking UX unless already started).

---

## 5. Dart models (suggested)

```dart
class AmenityDayAvailability {
  final String amenity;
  final String amenityName;
  final String bookingDate; // yyyy-MM-dd
  final String openTime;
  final String closeTime;
  final int bookingTimeStepMins;
  final int cleanupBufferMins;
  final bool requiresApproval;
  final int cancelBeforeHours;
  final int capacity;
  final List<BusyInterval> busy;
  final List<TimeGap> freeGaps;
  final bool isStaff;
}

class BusyInterval {
  final String bookingId;
  final String startTime;
  final String endTime;
  final String endWithBuffer;
  final String status; // Pending | Confirmed
  final String label;
}

class TimeGap {
  final String startTime;
  final String endTime;
}

class AmenityBooking {
  final String name;
  final String amenity;
  final String? amenityName;
  final String bookingDate;
  final String startTime;
  final String endTime;
  final String status;
  final int? guestsCount;
  final String? tenant;
  final String? tenantName;
  final String? propertyUnit;
  final String? lease;
  final String? notes;
  final String? cancellationReason;
  final String? rejectionReason;
  final String? approvedBy;
  final String? approvedOn;
}
```

Parse times with a small helper that accepts `HH:mm:ss` and `HH:mm`.

---

## 6. Client logic rules (must mirror server)

1. **Exclusive overlap:** do not allow UI submit if selected `[start, end)` overlaps any busy `[start, endWithBuffer)` — still submit and trust server (optimistic block reduces friction).
2. **Half-open:** booking ending at 21:00 may start next at 21:00 (no cleanup grace). `end_with_buffer` == `end_time`.
3. **Step:** only offer picker values where minutes % step == 0.
4. **Same day only:** end date = start date; no overnight.
5. **Pending blocks** like Confirmed on the strip.
6. **Server wins** on race — show conflict fields and reload availability.

---

## 7. Implementation checklist for the agent

Work in this order:

1. **API layer** — add methods + models for day availability, create, cancel, approve, reject; extend amenity + booking models with new fields.  
2. **Replace Book UI** — remove/hide slot-chip flow for amenities; build hybrid strip + pickers.  
3. **My Bookings** — Pending/Rejected badges, rejection reason, cancel policy UX.  
4. **Staff Pending queue** — approve/reject dialogs.  
5. **Realtime + FCM** — listen for four amenity events; deep link `route` `/amenity_bookings` if you support notification routing.  
6. **Regression** — amenity catalog/detail still works; multi-lease unit selector still passes `lease` / `property_unit` if present.  
7. **Remove dead code** — slot-chip widgets unused by exclusive flow (or feature-flag behind “legacy slots” only if product asks — default off).

---

## 8. Acceptance criteria

- [ ] Party Hall (and all amenities) book screen uses **`get_amenity_day_availability`**, not slot chips.  
- [ ] Busy strip paints `start_time`–`end_time` only (no grace gap after end).  
- [ ] Tap free gap prefills Start/End; pickers snap to step.  
- [ ] Create with overlap shows **conflict_start–conflict_end**.  
- [ ] Amenity with `requires_approval=1` → Pending messaging; staff can Approve/Reject.  
- [ ] Reject requires reason; Rejected shows `rejection_reason`.  
- [ ] Tenant cancel blocked inside `cancel_before_hours` with clear error.  
- [ ] Guests optional/informational; no capacity error from guest count.  
- [ ] Staff list shows all tenants; tenant list shows own only.  
- [ ] WS/FCM events refresh UI without full app restart.  
- [ ] No overnight / edit-in-place booking.

---

## 9. Manual QA script (staging)

1. Staff Desk: set Party Hall `requires_approval=1`, `booking_time_step_mins=30`, `cancel_before_hours=2`, published.  
2. Tenant phone: open Book → pick future date → see free gaps → book 14:00–17:00 → Pending.  
3. Second attempt 15:00–16:00 → overlap error.  
4. Attempt start exactly at 17:00 (previous end) → **allowed** (no grace after end).  
5. Staff phone: Pending list → Approve → tenant gets push / status Confirmed.  
6. Tenant cancel far-future OK; near-start fails.  
7. Staff Reject another Pending with reason → tenant sees Rejected + reason.

---

## 10. Out of scope (do not build in this pass)

- Min/max duration limits  
- Recurring bookings  
- Paid amenity checkout  
- Shared-capacity concurrent bookings  
- Overnight ranges  
- Desk-only admin screens  

---

## 11. Definition of done

Flutter app lets a tenant reserve an exclusive amenity window with the hybrid day strip + Start/End UX, respects approval/cancel rules from the API, and lets staff approve/reject Pending bookings with live updates — **without** calling `get_available_slots` for the new flow.
